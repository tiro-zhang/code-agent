"""不可变网页审批快照；回答仍交回原权限模块复核和应用。"""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import re
import time
from types import MappingProxyType
from uuid import uuid4

from ..permissions.runtime import cancelled
from ..sessions.codec import json_value
from ..tools.base import ToolError
from .errors import WebError


MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_PENDING = 32
MAX_PAGE_BYTES = 32 * 1024
FILE_TOOLS = {'read_file', 'write_file', 'edit_file', 'glob_files', 'search_code'}
DECISIONS = {'deny', 'once', 'session', 'permanent'}


@dataclass
class _Pending:
    identity: str
    request: object
    arguments: dict
    context: dict
    summary: dict
    sections: object
    size: int
    deadline: float
    future: asyncio.Future
    cancel_event: asyncio.Event
    status: str = 'pending'
    decision: str | None = None


class ApprovalBroker:
    """只有当前服务、运行代次和任务能够决定一份固定审批。"""

    def __init__(self, *, root, server_instance_id, context, notify, redact,
                 ttl=600, clock=None):
        if type(ttl) not in (int, float) or not 0 < ttl <= 600:
            raise ValueError('审批期限必须在零到十分钟之间')
        self.root = Path(root).resolve(strict=True)
        self.server_instance_id = server_instance_id
        self.context, self.notify, self.redact = context, notify, redact
        self.ttl, self.clock = ttl, clock or time.monotonic
        self._pending = {}
        self._closed = OrderedDict()
        self._bytes = 0

    def _identity(self):
        value = self.context()
        return {'session_id': value.get('session_id'), 'generation': value.get('generation'),
                'run_id': value.get('run_id') or ''}

    def _emit(self, entry, status):
        self.notify({'kind': 'approval', 'id': entry.identity,
                     'session_id': entry.context['session_id'], 'status': status})

    def _finish(self, entry, status, decision=None):
        if entry.status != 'pending':
            return
        entry.status, entry.decision = status, decision
        self._pending.pop(entry.identity, None)
        self._bytes -= entry.size
        self._closed[entry.identity] = status
        while len(self._closed) > 256:
            self._closed.popitem(last=False)
        if status == 'cancelled':
            entry.cancel_event.set()
        if not entry.future.done():
            entry.future.set_result(decision)
        self._emit(entry, status)

    def _sweep(self):
        now, identity = self.clock(), self._identity()
        for entry in list(self._pending.values()):
            if entry.cancel_event.is_set() or identity != entry.context:
                self._finish(entry, 'cancelled')
            elif now >= entry.deadline:
                self._finish(entry, 'expired')

    def _get(self, identity):
        self._sweep()
        entry = self._pending.get(identity)
        if entry is not None:
            return entry
        status = self._closed.get(identity)
        if status == 'resolved':
            raise WebError('approval_resolved', '该审批已经处理，不能重复决定')
        if status == 'expired':
            raise WebError('approval_expired', '该审批已到期，请重新发起明确操作', 410)
        if status == 'cancelled':
            raise WebError('approval_cancelled', '该审批所属操作已取消或运行身份已变化', 410)
        raise WebError('approval_not_found', '审批不存在或已不在保留范围', 404)

    def _arguments_text(self, arguments):
        serialized = json.dumps(arguments, ensure_ascii=False, indent=2, allow_nan=False)
        # 在解码的 JSON 字符串上脱敏，包含反斜线／引号的秘密也不会被转义掩盖。
        return re.sub(r'"(?:\\.|[^"\\])*"',
                      lambda match: json.dumps(self.redact(json.loads(match[0])), ensure_ascii=False),
                      serialized)

    def _sections(self, request, arguments):
        argument_text = self._arguments_text(arguments)
        if len(argument_text.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
            raise ToolError('approval_too_large', '完整审批参数超过保留上限，工具未启动', not_started=True)
        external = request.external
        scope = ['本次：只批准当前调用、完整参数与目标，使用一次。']
        if external:
            target_values = []
            for target in request.targets:
                try:
                    target_values.append(self._arguments_text(json.loads(target)))
                except (ValueError, TypeError):
                    target_values.append(self.redact(target))
            targets = '\n'.join([f'项目根：{self.root}', f'外部 Server：{external[0]}',
                f'原始工具：{external[1]}', f'稳定别名：{request.tool}',
                '外部 path 仅是调用参数，未视为本地真实文件。',
                '精确授权身份：', *target_values])
            scope += ['会话／永久：绑定当前真实项目根、Server 有效配置身份、原始工具及规范完整参数。',
                      '连接身份或参数变化即失效，不扩展到整个 Server 或其他工具。',
                      '永久批准保存完整调用参数，不保存连接 env、headers 或凭据。']
        elif request.tool in FILE_TOOLS:
            targets = '\n'.join([f'项目根：{self.root}', f'全部真实目标（{len(request.targets)}）：', *request.targets])
            scope += ['会话／永久：只绑定此真实项目根、工具和列出的真实文件。',
                      '同一路径未来内容或搜索条件可以变化，不扩展到目录或其他工具。']
        elif request.tool == 'execute_command':
            targets = f'项目根：{self.root}\n完整精确命令：\n{arguments.get("command", "")}'
            scope += ['会话／永久：仅绑定当前真实项目根、工具及本次完整精确命令。']
        else:
            targets = f'项目根：{self.root}\n完整参数授权对象：\n' + argument_text
            scope += ['会话／永久：仅绑定当前真实项目根、工具与完整 JSON 参数，参数改变即失效。']
        scope += ['会话批准在本次运行代次内有效。',
                  '永久批准写入项目本地权限文件，可用 /permissions revoke 撤销；保存失败由原权限模块报告。',
                  '批准后仍重新检查拒绝规则、当前目标和规划模式。']
        if request.origin:
            targets += f'\n来源：Hook {request.origin[0]} · {request.origin[1]}'
        if not external and request.tool == 'write_file':
            content = str(arguments.get('content', ''))
        elif not external and request.tool == 'edit_file':
            old, new = str(arguments.get('old_text', '')), str(arguments.get('new_text', ''))
            old_lines, new_lines = old.split('\n'), new.split('\n')
            difference = difflib.unified_diff(old_lines, new_lines, fromfile='old_text', tofile='new_text',
                                              n=max(len(old_lines), len(new_lines)), lineterm='')
            content = '局部编辑差异：仅比较当前调用提供的原文和新文。\n' + '\n'.join(difference)
            content += '\n原文末尾换行：' + ('有' if old.endswith('\n') else '无')
            content += '\n新文末尾换行：' + ('有' if new.endswith('\n') else '无')
        else:
            content = '此调用没有本地写入或编辑差异；请审阅完整有效参数。'
        sections = {'targets': self.redact(targets).encode('utf-8'),
                    'arguments': argument_text.encode('utf-8'),
                    'content': self.redact(content).encode('utf-8'),
                    'scope': self.redact('\n'.join(scope)).encode('utf-8')}
        if sum(map(len, sections.values())) > MAX_SNAPSHOT_BYTES:
            raise ToolError('approval_too_large', '完整审批快照超过保留上限，工具未启动', not_started=True)
        return MappingProxyType(sections)

    async def respond(self, request, cancel_event):
        """保存完整展示快照并可取消地等待，返回原核心接受的四种字符串。"""
        self._sweep()
        if cancel_event.is_set():
            raise cancelled()
        identity = self._identity()
        if not identity['session_id'] or type(identity['generation']) is not int:
            raise ToolError('approval_unavailable', '审批没有可用的运行身份，工具未启动', not_started=True)
        original = deepcopy(request)
        arguments = json_value(original.arguments)
        sections = await asyncio.to_thread(self._sections, original, arguments)
        if cancel_event.is_set() or identity != self._identity():
            cancel_event.set()
            raise cancelled()
        size = sum(map(len, sections.values()))
        if len(self._pending) >= MAX_PENDING or self._bytes + size > MAX_SNAPSHOT_BYTES:
            raise ToolError('approval_capacity', '审批快照保留空间已满，工具未启动', not_started=True)
        key = uuid4().hex
        summary = {'id': key, **identity, 'tool': self.redact(original.tool),
                   'reason': self.redact(original.reason),
                   'expires_at': datetime.fromtimestamp(time.time() + self.ttl, timezone.utc).isoformat()}
        entry = _Pending(key, request, arguments, identity, summary, sections, size,
                         self.clock() + self.ttl, asyncio.get_running_loop().create_future(), cancel_event)
        self._pending[key] = entry
        self._bytes += size
        watcher = asyncio.create_task(cancel_event.wait())
        try:
            self._emit(entry, 'pending')
            await asyncio.wait((entry.future, watcher), timeout=max(0, entry.deadline - self.clock()),
                               return_when=asyncio.FIRST_COMPLETED)
            if cancel_event.is_set():
                self._finish(entry, 'cancelled')
                raise cancelled()
            if entry.status == 'pending':
                self._finish(entry, 'expired')
            if entry.status == 'expired':
                return 'deny'
            if entry.status != 'resolved':
                raise cancelled()
            return entry.decision
        except asyncio.CancelledError:
            cancel_event.set()
            self._finish(entry, 'cancelled')
            raise
        finally:
            if entry.status == 'pending':
                self._finish(entry, 'cancelled')
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    def summaries(self):
        self._sweep()
        return [dict(entry.summary) for entry in self._pending.values()]

    def preview(self, identity, section='arguments', offset=0, limit=32768):
        entry = self._get(identity)
        if section not in entry.sections:
            raise WebError('invalid_section', '审批预览分区无效', 400)
        if type(offset) is not int or type(limit) is not int or offset < 0 or limit <= 0:
            raise WebError('invalid_page', '审批分页参数无效', 400)
        data = entry.sections[section]
        if offset > len(data) or (offset < len(data) and data[offset] & 0xC0 == 0x80):
            raise WebError('invalid_page', '审批分页位置不在字符边界', 400)
        end = min(len(data), offset + min(limit, MAX_PAGE_BYTES))
        while end > offset and end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        if end == offset and offset < len(data):
            raise WebError('invalid_page', '分页大小不足以容纳一个字符', 400)
        return {**entry.summary, 'server_instance_id': self.server_instance_id,
                'request_id': entry.request.id, 'root': self.redact(str(self.root)),
                'mode': entry.request.mode, 'sections': list(entry.sections), 'section': section,
                'content': data[offset:end].decode('utf-8'), 'next_offset': end if end < len(data) else None,
                'total_bytes': len(data)}

    def decide(self, identity, decision, *, server_instance_id, generation, run_id=None):
        if server_instance_id != self.server_instance_id:
            raise WebError('stale_server', '审批不属于本次服务')
        if decision not in DECISIONS:
            raise WebError('invalid_decision', '审批决定只接受拒绝、本次、会话或永久', 400)
        entry = self._get(identity)
        if (type(generation) is not int or generation != entry.context['generation']
                or (run_id is not None and run_id != entry.context['run_id'])):
            raise WebError('stale_approval', '审批不属于指定的运行代次或任务')
        if entry.request.arguments != entry.arguments:
            self._finish(entry, 'cancelled')
            raise WebError('approval_changed', '原调用参数已改变，请重新发起审批')
        self._finish(entry, 'resolved', decision)
        return {'id': identity, 'decision': decision}

    def invalidate_all(self):
        """代次切换或停止时唤醒原权限等待者，不能转成默认批准。"""
        for entry in list(self._pending.values()):
            self._finish(entry, 'cancelled')
