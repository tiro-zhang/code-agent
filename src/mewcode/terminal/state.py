"""归并代理事件的展示状态；不读取输入，不执行工具或权限决定。"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
import json
import time

# 展示与 Agent 共用累计口径，避免另写缓存缺失和部分字段规则。
from ..agent import _total_usage
from ..tools.base import ToolResult
from ..types import AgentEvent, TokenUsage, ToolCall
from .text import terminal_text, usage_text


PHASE_LABELS = {
    "starting": "启动中", "idle": "空闲", "running": "任务运行中",
    "approval": "等待授权", "closing": "退出清理", "model": "请求模型",
    "summary": "压缩上下文", "tools": "权限检查／执行工具", "permissions": "权限检查", "permission": "等待授权",
}


@dataclass
class _ToolState:
    """一个任务内的稳定工具编号与已知状态。"""

    number: int
    identifier: str
    name: str
    call: ToolCall | None = None
    stage: str = "已接收"
    result: ToolResult | None = None
    request_id: str = ""
    external: tuple[str, str] | None = None
    resolved_requests: set[str] = field(default_factory=set)
    requested_requests: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    warning_keys: set[tuple[str, str, str]] = field(default_factory=set)


class TerminalState:
    """保留最近任务详情，只从事件读取实际执行状态与真实用量。"""

    def __init__(self, secret: str = "") -> None:
        self.secret = secret
        self.phase = "idle"
        self.mode = "execute"
        self.permission_mode = "default"
        self.iteration = 0
        self.max_iterations = 20
        self.run_id: str | None = None
        self._seen_runs: deque[str] = deque(maxlen=16)
        self._tools: dict[str, _ToolState] = {}
        self._tool_identities: dict[str, tuple[str, str]] = {}
        self._usage: dict[int, TokenUsage] = {}
        self._purposes: dict[int, str] = {}
        self._total_usage: TokenUsage | None = None
        self._started_at: float | None = None
        self._elapsed = 0.0
        self._finished = False
        self._reason = ""
        self._finish_text = ""
        self._base_phase = "idle"

    def safe(self, value: object, *, limit: int | None = None) -> str:
        """展示边界先脱敏，再转义不可信字符。"""
        return terminal_text(str(value), self.secret, limit=limit)

    def set_phase(self, phase: str) -> None:
        """允许控制器展示启动、聊天或退出等输入阶段。"""
        self.phase = phase
        self._base_phase = phase

    def bind_tools(self, tools: Iterable[object]) -> None:
        """登记外部工具的真实展示身份，不保存连接配置或凭据。"""
        identities = {}
        for tool in tools:
            name = getattr(tool, "name", None)
            server = getattr(tool, "server_name", None)
            original = getattr(tool, "original_name", None)
            if all(isinstance(value, str) for value in (name, server, original)):
                identities[name] = (server, original)
        self._tool_identities = identities

    def begin_task(self, *, mode: str, permission_mode: str, max_iterations: int) -> None:
        """真实任务开始时重置展示，允许预取消只产生结束事件。"""
        self.run_id = None
        self.mode, self.permission_mode = mode, permission_mode
        self.max_iterations = max_iterations
        self._tools.clear()
        self._usage.clear()
        self._purposes.clear()
        self._total_usage = None
        self._started_at = time.monotonic()
        self._elapsed = 0.0
        self._finished = False
        self._reason = ""
        self._finish_text = ""
        self.iteration = 0
        self._base_phase = "running"
        self.phase = "running"

    @property
    def elapsed(self) -> float:
        """已用秒数单调增长，任务收尾后冻结；不参与执行期限。"""
        if self._started_at is not None and not self._finished:
            self._elapsed = max(self._elapsed, time.monotonic() - self._started_at, 0.0)
        return self._elapsed

    def _begin(self, event: AgentEvent) -> None:
        if self.run_id is not None or self._started_at is None:
            self.begin_task(mode=event.mode, permission_mode=event.permission_mode,
                            max_iterations=event.max_iterations)
        # 无进度的首事件也携带真实模式与限额；显式开始后的计时不重置。
        self.mode, self.permission_mode = event.mode, event.permission_mode
        self.max_iterations = event.max_iterations
        self.run_id = event.run_id
        self._seen_runs.append(event.run_id)

    def _tool(self, event: AgentEvent) -> _ToolState:
        identifier = event.tool_call_id or (event.call.id if event.call else "")
        if identifier not in self._tools:
            self._tools[identifier] = _ToolState(len(self._tools) + 1, identifier, event.tool_name)
        tool = self._tools[identifier]
        if event.tool_name:
            tool.name = event.tool_name
        if event.call is not None:
            tool.call = event.call
            if not tool.name:
                tool.name = event.call.name
        if tool.name in self._tool_identities:
            tool.external = self._tool_identities[tool.name]
        request = event.permission_request
        if request is not None:
            external = getattr(request, "external", None)
            if external:
                tool.external = external
        return tool

    def _refresh_phase(self) -> None:
        if not self._finished:
            self.phase = "permission" if any(tool.stage == "等待授权" and tool.result is None
                                              for tool in self._tools.values()) else self._base_phase

    def _missing_usage_requests(self) -> list[int]:
        return [iteration for iteration in range(1, self.iteration + 1) if iteration not in self._usage]

    def _refresh_running_usage(self) -> None:
        """按请求覆盖的已知记录累计，缺失请求只影响完整性，结束值不重算。"""
        if not self._finished:
            records = list(self._usage.values())
            records.extend(TokenUsage() for _ in self._missing_usage_requests())
            self._total_usage = _total_usage(records) if records else None

    def update(self, event: AgentEvent) -> list[str]:
        """返回新终态和权限警告；正文、用量及结束日志由控制器处理。"""
        if self.run_id is None:
            if event.run_id in self._seen_runs:
                return []
            self._begin(event)
        elif event.run_id != self.run_id:
            # 新任务只由进度开启，旧任务的迟到事件不得覆盖当前任务。
            if event.kind != "progress" or event.run_id in self._seen_runs:
                return []
            self._begin(event)
        if event.kind == "progress":
            if self._finished or event.iteration < self.iteration:
                return []
            self.mode, self.permission_mode = event.mode, event.permission_mode
            self.iteration = event.iteration
            self.max_iterations = event.max_iterations
            self._base_phase = event.phase or "running"
            self._refresh_phase()
            self._refresh_running_usage()
        elif event.kind == 'context_compaction':
            self._base_phase = 'summary' if event.phase == 'summary' else 'running'
            self._refresh_phase()
        elif event.kind == "usage":
            if event.usage is not None:
                self._usage[event.iteration] = event.usage
                self._purposes[event.iteration] = event.purpose
            self.iteration = max(self.iteration, event.iteration)
            self._refresh_running_usage()
        elif event.kind == "finished":
            if self._finished:
                return []
            self.elapsed
            self._finished = True
            self._reason = event.reason or "未知"
            self._finish_text = event.text
            self._total_usage = event.usage
            self.iteration = max(self.iteration, event.iteration)
            self.phase = "idle"
            self._base_phase = "idle"
        elif event.kind in {"tool_call", "tool_started", "tool_result",
                             "permission_requested", "permission_resolved"}:
            tool = self._tool(event)
            request_id = str(getattr(event.permission_request, "id", ""))
            if event.kind == "tool_result" and event.result is not None:
                if tool.result is not None:
                    return []
                tool.result = event.result
                tool.stage = "已结束"
                self._refresh_phase()
                return [self._tool_line(tool)]
            if event.kind == "permission_resolved":
                tool.resolved_requests.add(request_id)
                if (tool.result is None and tool.stage != "执行中" and not self._finished
                        and (not tool.request_id or tool.request_id == request_id)):
                    tool.stage = "已拒绝，未启动" if event.permission_decision == "deny" else "已批准，未启动"
                self._refresh_phase()
                if event.warning:
                    key = (request_id, event.permission_decision, event.warning)
                    if key not in tool.warning_keys:
                        tool.warning_keys.add(key)
                        label = "本次批准（永久未保存）" if event.permission_decision == "permanent" else "权限警告"
                        line = f"权限> [#{tool.number}] {self.safe(tool.name)} · {label} · {self.safe(event.warning)}"
                        tool.warnings.append(line)
                        return [line]
            elif tool.result is None and not self._finished:
                if event.kind == "tool_started":
                    tool.stage = "执行中"
                elif event.kind == "permission_requested" and tool.stage != "执行中":
                    if (request_id not in tool.resolved_requests
                            and (request_id not in tool.requested_requests or tool.request_id == request_id)):
                        tool.requested_requests.add(request_id)
                        tool.request_id = request_id
                        tool.stage = "等待授权"
                self._refresh_phase()
        return []

    def _operation(self, tool: _ToolState) -> str:
        if tool.call is None:
            return ""
        try:
            arguments = json.loads(tool.call.arguments)
            if isinstance(arguments, dict):
                for key in ("path", "command", "pattern"):
                    if isinstance(arguments.get(key), str):
                        return self.safe(arguments[key], limit=180)
        except (ValueError, RecursionError):
            pass
        return "校验参数"

    def _result_text(self, tool: _ToolState) -> str:
        result = tool.result
        if result is None:
            if self._finished:
                return f"任务已结束，最后状态 {tool.stage}（终态未知）"
            return tool.stage
        if result.ok:
            text = "成功"
        else:
            error = result.error or {}
            code = str(error.get("code", "unknown"))
            label = {"timeout": "超时", "cancelled": "取消", "permission_denied": "权限拒绝",
                     "permission_blacklisted": "权限拒绝", "permission_protected": "权限拒绝"}.get(code, "失败")
            text = f"{label} · {self.safe(code)}：{self.safe(error.get('message', '未知错误'))}"
            details = error.get("details") or {}
            if details.get("not_started"):
                text += " · 未启动"
            if details.get("side_effects_may_have_occurred"):
                text += (" · 远端可能仍在执行或已有副作用" if tool.name.startswith("mcp__")
                         else " · 可能已有副作用，请检查实际状态")
        if result.truncated:
            text += " · 输出已截断"
        if isinstance(result.data, dict) and result.data.get("permission_limited"):
            text += f" · 搜索范围受限，跳过 {self.safe(result.data.get('skipped_files', '未知'))} 个文件"
        return text

    def _tool_line(self, tool: _ToolState, *, detail: bool = False) -> str:
        line = f"工具> [#{tool.number}] {self._result_text(tool)} · {self.safe(tool.name)}"
        operation = self._operation(tool)
        if operation:
            line += f" · {operation}"
        if tool.external:
            line += f" · Server {self.safe(tool.external[0])} · 原始工具 {self.safe(tool.external[1])}"
        if detail:
            line += f" · 完整调用标识 {self.safe(tool.identifier)}"
            if tool.request_id:
                line += f" · 授权请求 {self.safe(tool.request_id)}"
        return line

    def active_lines(self) -> list[str]:
        """返回当前仍在处理的调用，不把授权等待说成实际执行。"""
        if self._finished:
            return []
        return [self._tool_line(tool) for tool in self._tools.values() if tool.result is None]

    @staticmethod
    def _usage_summary(usage: TokenUsage) -> str:
        """折叠重复标签，保留全部计数与完整性，长格式留在详情。"""
        def count(field: str) -> str:
            value = getattr(usage, field)
            if value is None:
                return "未知"
            return str(value) + ("（部分）" if field in usage.incomplete_fields else "")

        incoming = f"入{count('total_input_tokens')}"
        if usage.total_input_tokens is None:
            incoming += f"（基础输入 {count('input_tokens')}）"
        ratio = usage.cache_hit_rate
        rate = "未知" if ratio is None else f"{ratio:.1%}"
        if usage.complete and usage.cache_complete:
            complete = "完整含缓存"
        else:
            base = "统计完整" if usage.complete else "统计不完整"
            cache = "缓存统计完整" if usage.cache_complete else "缓存统计不完整"
            complete = f"{base}/{cache}"
        return (f"{incoming} 出{count('output_tokens')} 命中 {count('cache_read_tokens')} "
                f"未命中 {count('cache_miss_tokens')} 写入 {count('cache_write_tokens')} "
                f"命中率{rate} {complete}")

    def summary(self) -> str:
        """最近请求和累计摘要都保留缓存与不完整标记。"""
        mode = "[PLAN] 规划" if self.mode == "plan" else "[DEFAULT] 执行"
        phase = self.safe(PHASE_LABELS.get(self.phase, self.phase))
        header = f"{mode} · 权限 {self.safe(self.permission_mode)} · {phase}"
        if self._started_at is None:
            return header + " · 暂无任务记录"
        header += f" · 请求 {self.iteration}/{self.max_iterations} · 已用 {self.elapsed:.1f}s"
        latest = self._usage[max(self._usage)] if self._usage else None
        recent = self._usage_summary(latest) if latest is not None else "未知，尚无用量记录"
        if self._total_usage is not None:
            total = self._usage_summary(self._total_usage)
        else:
            total = "未知，结束事件未提供累计用量" if self._finished else "未知，尚无累计记录"
        missing = self._missing_usage_requests()
        if missing:
            total += " · 请求用量缺失 " + "、".join(str(iteration) for iteration in missing)
        if self._finished:
            header += f" · 停止原因 {self.safe(self._reason)}"
        purpose = "（摘要）" if self._usage and self._purposes.get(max(self._usage)) == "summary" else ""
        return f"{header}\n最近请求{purpose} Token · {recent}\n累计已知 Token · {total}"

    def status_text(self, *, root: object, model: str, mode: str, permission_mode: str) -> str:
        """只读展示当前配置和最近任务的逐请求、工具与停止详情。"""
        mode_label = "[PLAN] 规划" if mode == "plan" else "[DEFAULT] 执行"
        lines = [f"项目> {self.safe(root)}", f"模型> {self.safe(model)}",
                 f"模式> {mode_label} · 权限 {self.safe(permission_mode)}"]
        if self._started_at is None:
            lines.append("暂无任务记录")
            return "\n".join(lines)
        identifier = self.safe(self.run_id) if self.run_id is not None else "等待代理标识"
        lines.append(f"最近任务> {identifier}")
        lines.append(self.summary())
        if self._total_usage is not None:
            lines.append(f"累计已知 Token · {usage_text(self._total_usage)}")
        else:
            lines.append("累计用量详情> 未知，尚无累计记录")
        if self._finished:
            lines.append(f"结束详情> {self.safe(self._finish_text)}")
        requests = set(range(1, self.iteration + 1)) | self._usage.keys()
        for iteration in sorted(requests):
            usage = self._usage.get(iteration)
            detail = usage_text(usage) if usage is not None else "用量缺失，未知"
            lines.append(f"请求 {iteration} · {'摘要 · ' if self._purposes.get(iteration) == 'summary' else ''}{detail}")
        if not self._usage:
            lines.append("逐请求用量> 暂无用量记录")
        for tool in self._tools.values():
            lines.append(self._tool_line(tool, detail=True))
            lines.extend(tool.warnings)
        if not self._tools:
            lines.append("工具> 暂无调用记录")
        return "\n".join(lines)
