"""父任务、子任务及后台事件到可见输出的统一投影。"""

from collections import Counter
from dataclasses import replace
from pathlib import PurePath

from ..types import AgentEvent


class TerminalProjection:
    def __init__(self, state):
        self.state = state
        self._generation = None
        self._batch = []
        self._source = ''
        self._skills = set()
        self._child_answers = set()

    @staticmethod
    def line(text):
        return AgentEvent('display_line', text=text)

    def reset(self):
        self._batch.clear()
        self._skills.clear()
        self._child_answers.clear()
        self._source = ''
        self._generation = self.state.details

    def flush(self, *, consume=True):
        if not self._batch:
            return []
        counts = Counter(item[0] for item in self._batch)
        pieces = []
        reads = [path for name, path, _ in self._batch if name == 'read_file']
        if reads:
            if all(reads):
                pieces.append(f'已读取 {len(set(reads))} 个文件（{len(reads)} 次调用）')
            else:
                pieces.append(f'完成 {len(reads)} 次读取')
        searches = counts.pop('search_code', 0) + counts.pop('glob_files', 0)
        if searches:
            pieces.append(f'完成 {searches} 次搜索')
        counts.pop('read_file', None)
        for name, count in counts.items():
            label = {'write_file': '写入', 'edit_file': '编辑', 'execute_command': '命令执行'}.get(name, name)
            if name in self.state._tool_identities:
                server, original = self.state._tool_identities[name]
                label = f'MCP {server} / {original} 调用'
            if name == 'execute_command':
                numbers = {number for tool, _, number in self._batch if tool == name}
                codes = {str((tool.result.data or {}).get('exit_code', '未知')) for tool in self.state._tools.values()
                         if tool.number in numbers}
                label += '（退出码 ' + '、'.join(sorted(codes)) + '）'
            targets = [path for tool, path, _ in self._batch if tool == name]
            if name in {'write_file', 'edit_file'} and all(targets):
                pieces.append(f'已{label} {len(set(targets))} 个文件（{count} 次调用）')
            else:
                pieces.append(f'完成 {count} 次{self.state.safe(label)}')
        members = '、'.join('#' + str(number) for _, _, number in self._batch)
        source = f'Skill {self.state.safe(self._source)} · ' if self._source else ''
        if consume:
            self._batch.clear()
        return [self.line(f'工具> {source}' + '、'.join(pieces) + f' · 成功 · {members}（F2 / /status 详情）')]

    @staticmethod
    def needs_attention(event):
        result = event.result
        data = result.data if result and isinstance(result.data, dict) else {}
        error_details = (result.error or {}).get('details') or {} if result else {}
        return bool(event.warning or result and (not result.ok or result.truncated or
                    data.get('permission_limited') or data.get('side_effects_may_have_occurred') or
                    error_details.get('side_effects_may_have_occurred')))

    @staticmethod
    def maintenance_events(event):
        if event.kind == 'usage':
            return []
        alert = event.phase in {'failed', 'conflict', 'incomplete', 'cancelled', 'partial', 'skipped', 'rejected'} or any(
            word in event.text for word in ('失败', '冲突', '未完成', '未提交', '取消', '不可', '无效', '忽略'))
        return [event] if alert else []

    def accept(self, event, *, maintenance=False):
        if event.kind == 'hook_notice':
            text = f'Hook> {self.state.safe(event.hook_source)} · {self.state.safe(event.hook_event)} · {self.state.safe(event.text)}'
            return [replace(event, kind='display_line', text=text)]
        if maintenance:
            return self.maintenance_events(event)
        if self._generation is not self.state.details:
            self.reset()
        child = event.child_event if event.kind == 'skill_event' else None
        if event.kind == 'skill_event' and child is None:
            return []
        source = child.skill_name if child else event.skill_name
        source_id = child.run_id if child else event.run_id
        normalized = event
        if child:
            identity = child.run_id + ':' + (child.tool_call_id or (child.call.id if child.call else ''))
            normalized = replace(child, run_id=event.run_id, tool_call_id=identity,
                call=replace(child.call, id=identity) if child.call else None)
        if child and child.kind == 'finished':
            if event.run_id != self.state.run_id:
                return []
            self.state.details.finish_thinking()
            return self.flush()
        if self.state._finished and normalized.run_id == self.state.run_id and normalized.kind in {
                'finished', 'text_delta', 'thinking_delta'}:
            return []
        lines = self.state.update(normalized)
        if normalized.run_id != self.state.run_id:
            return []
        if self._generation is not self.state.details:
            self.reset()
        visible = []
        if source != self._source:
            visible += self.flush()
            self._source = source
        if child and source_id not in self._skills:
            self._skills.add(source_id)
            visible.append(self.line(f'Skill> {self.state.safe(source)} · 子运行 {self.state.safe(source_id)}'))
        kind = normalized.kind
        if kind in {'text_delta', 'permission_requested', 'finished'} or self.needs_attention(normalized):
            visible += self.flush()
        if kind != 'thinking_delta':
            self.state.details.finish_thinking()
        if kind == 'thinking_delta':
            if normalized.purpose == 'work':
                self.state.details.think((source_id, normalized.iteration),
                    f'{source or "MewCode"} · 请求 {normalized.iteration} · API 思考', normalized.text)
                if self.state.phase not in {'permission', 'approval', 'cancelling'}:
                    self.state.phase = 'thinking'
        elif kind == 'text_delta':
            if child and normalized.text:
                self._child_answers.add(source_id)
            if not normalized.replay_of or normalized.replay_of not in self._child_answers:
                visible.append(normalized)
            if self.state.phase not in {'permission', 'approval', 'cancelling'}:
                self.state.phase = 'answer'
        elif kind == 'tool_result' and lines:
            if self.needs_attention(normalized):
                visible += [self.line(line) for line in lines]
            else:
                tool = self.state._tools[normalized.tool_call_id]
                data = normalized.result.data if isinstance(normalized.result.data, dict) else {}
                path = data.get('path')
                trusted = path if (isinstance(path, str) and not tool.external and
                    (PurePath(path).is_absolute() or ('..' not in PurePath(path).parts and (
                     {'start_line', 'end_line', 'content'} <= data.keys() or 'bytes_written' in data or 'replacements' in data)))) else None
                self._batch.append((tool.name, trusted, tool.number))
        elif kind == 'permission_resolved':
            visible += [self.line(line) for line in lines]
        elif kind == 'finished':
            visible.append(self.line(self.state.final_summary()))
        elif kind not in {'progress', 'usage', 'tool_call', 'tool_started', 'tool_result', 'permission_requested'}:
            visible.append(normalized)
        return visible
