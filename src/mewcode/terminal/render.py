"""终端安全文本展示；展示策略和聚合由投影层统一决定。"""

from typing import TextIO
from ..types import AgentEvent
from ..session import operation_summary
from .text import terminal_text, usage_text, StreamText


class _Renderer:
    def __init__(self, output: TextIO, secret: str) -> None:
        self.output, self.secret = output, secret
        self.section = None
        self.source = None
        self.calls = {}
        self._text = StreamText(secret)

    def safe(self, value, limit=200):
        return terminal_text(str(value), self.secret, limit=limit)

    def line(self, text):
        if self.section is not None:
            self.output.write(self._text.feed('', final=True))
            self.output.write("\n")
            self.section = None
            if hasattr(self.output, 'response_style'):
                self.output.response_style(False)
        self.output.write(text + "\n")
        self.output.flush()

    usage_text = staticmethod(usage_text)

    def show(self, event: AgentEvent):
        if event.kind == 'display_line':
            self.line(event.text)
            return
        if event.kind == 'skill_event':
            event = event.child_event
            if event is None:
                return
            if event.kind not in {'text_delta', 'thinking_delta'}:
                self.line(f'Skill> {self.safe(event.skill_name, 80)} · 子运行 {self.safe(event.run_id, 40)} · 当前项目')
        if event.kind in {"text_delta", "thinking_delta"} and event.text:
            if event.kind != self.section or event.skill_name != self.source:
                if self.section is not None:
                    self.output.write(self._text.feed('', final=True))
                    self.output.write("\n")
                label = f'Skill {self.safe(event.skill_name, 80)}> ' if event.skill_name else 'MewCode> '
                if hasattr(self.output, 'response_style'):
                    self.output.response_style(True)
                self.output.write("思考> " if event.kind == "thinking_delta" else label)
                self.section = event.kind
                self.source = event.skill_name
            self.output.write(self._text.feed(event.text))
            self.output.flush()
        elif event.kind == "progress":
            if event.phase == "model":
                self.calls.clear()
            mode = "规划" if event.mode == "plan" else "执行"
            phase = {"model": "请求模型", "tools": "权限检查／执行工具", "permissions": "权限检查",
                     "permission": "等待授权"}.get(event.phase, event.phase)
            self.line(f"进度> {mode} · 权限 {event.permission_mode} · 请求 {event.iteration}/{event.max_iterations} · {phase}")
        elif event.kind == "permission_requested":
            request = event.permission_request
            request_id = self.safe(request.id, 60) if request is not None else ""
            self.line(f"权限> [{self.safe(event.tool_call_id, 60)}] {self.safe(event.tool_name, 60)} · 等待授权 · 请求 {request_id}")
        elif event.kind == "permission_resolved":
            labels = {"deny": "拒绝", "once": "本次批准", "session": "会话批准", "permanent": "永久批准"}
            if event.warning and event.permission_decision == "permanent":
                labels["permanent"] = "本次批准（永久未保存）"
            self.line(f"权限> [{self.safe(event.tool_call_id, 60)}] {labels.get(event.permission_decision, self.safe(event.permission_decision))}")
            if event.warning:
                self.line(f"提示> {self.safe(event.warning, 400)}")
        elif event.kind == 'context_compaction':
            estimates = (f' · 输入估算 {event.estimated_before} → {event.estimated_after}'
                         if event.estimated_after is not None else '')
            self.line(f'压缩> {self.safe(event.text, 400)}{estimates} · 落盘 {event.spilled} · 连续失败 {event.failures}/3'
                      + (' · 自动摘要已熔断' if event.circuit_open else ''))
        elif event.kind == "history_trimmed":
            self.line(f"提示> {self.safe(event.text)}")
        elif event.kind == "usage":
            label = {'summary': '摘要', 'restore': '恢复摘要', 'memory': '记忆维护'}.get(event.purpose, '本次')
            self.line(f"用量> {label} Token · {self.usage_text(event.usage)}")
        elif event.kind == 'memory_update':
            self.line(f'记忆> {self.safe(event.text, 400)}')
        elif event.kind == 'team_update':
            self.line(f'团队> {self.safe(event.text, 800)}')
        elif event.kind == 'skill_loaded':
            self.line(f'Skill> {self.safe(event.text, 800)}')
        elif event.kind in {"tool_call", "tool_started", "tool_result"}:
            name = self.safe(event.tool_name, 60)
            id = self.safe(event.tool_call_id, 60)
            prefix = f"工具> [{id}] {name}"
            if event.kind == "tool_call":
                self.calls[event.tool_call_id] = event.call
                self.line(f"{prefix} · 已接收 · {self.safe(operation_summary(event.call), 160)}")
            elif event.kind == "tool_started":
                call = self.calls.get(event.tool_call_id)
                summary = operation_summary(call) if call else "执行工具"
                self.line(f"{prefix} · 开始 · {self.safe(summary, 160)}")
            else:
                result = event.result
                if result.ok:
                    status = "成功"
                else:
                    code = result.error["code"]
                    label = {"timeout": "超时", "cancelled": "取消", "permission_denied": "权限拒绝",
                             "permission_blacklisted": "权限拒绝", "permission_protected": "权限拒绝"}.get(code, "失败")
                    status = f"{label} · {self.safe(code + '：' + result.error['message'])}"
                suffix = " · 输出已截断" if result.truncated else ""
                if result.ok and isinstance(result.data, dict) and result.data.get("permission_limited"):
                    suffix += f" · 搜索范围受限，跳过 {result.data.get('skipped_files', 0)} 个文件"
                self.line(f"{prefix} · {status}{suffix}")
        elif event.kind == "finished":
            label = ("子任务结束" if event.reason == "model_done" else "子任务未完成") if event.skill_name else (
                "结束" if event.reason == "model_done" else "本轮未完成")
            self.line(f"{label}> {event.reason} · {self.safe(event.text, 300)} · 请求 {event.iteration}/{event.max_iterations}")
            self.line(f"用量> 累计已知 Token · {self.usage_text(event.usage)}")
