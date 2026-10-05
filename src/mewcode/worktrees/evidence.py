"""将子证据转交父缓存，映射只用于结构化引用而不开放任意文件恢复。"""

from ..tools.base import ToolResult
from ..types import Message


class WorktreeJournal:
    """借用主日志单写者，子缓存路径只作为隔离来源登记。"""

    def __init__(self, parent, record, workspace_root):
        self.parent, self.id = parent, parent.id
        self.record, self.workspace_root = record, str(workspace_root)

    def append(self, kind, payload):
        if kind in {'history_checkpoint', 'checkpoint'}:
            kind = 'history_checkpoint'
            payload = dict(payload, covers_seq=self.parent.seq)
        return self.parent.append('child_event', {
            'parent_task_id': self.record.parent_task_id, 'run_id': self.record.task_id,
            'skill': 'agent:' + self.record.role, 'kind': kind, 'payload': payload,
            'cache_identity': self.record.task_id, 'workspace_root': self.workspace_root})


def journal_event(parent, record, tree, stage, info, mapping):
    if parent.journal is not None:
        parent.journal.append('worktree_event', {
            'parent_task_id': record.parent_task_id, 'run_id': record.task_id, 'stage': stage,
            'workspace_root': str(tree.workspace_root), 'worktree': info, 'cache_mappings': mapping})


def archive_cache(source, parent, task_id):
    paths = sorted(source.paths)
    if len(paths) > 512:
        raise ValueError('子缓存归档文件数超限')
    mapping, indices, total = {}, [], 0
    for path in paths:
        header = source.describe(path)
        total += header['characters']
        if header['characters'] > 16 * 1024 * 1024 or total > 64 * 1024 * 1024:
            raise ValueError('子缓存归档容量超限')
        if header.get('tool_name') == 'context_result_index':
            indices.append((path, header))
            continue
        result = source.restore(path, max_characters=16 * 1024 * 1024)
        mapping[str(source.root / path)] = _save(parent, source, path, header, result, task_id)
    for path, header in indices:
        result = source.restore(path, max_characters=16 * 1024 * 1024)
        files = result.data.get('files') if isinstance(result.data, dict) else None
        if not isinstance(files, list) or any(str(source.root / item) not in mapping for item in files):
            raise ValueError('缓存索引含未登记引用')
        result = ToolResult(result.ok, dict(result.data, files=[mapping[str(source.root/item)] for item in files]),
                            result.error, result.truncated)
        mapping[str(source.root / path)] = _save(parent, source, path, header, result, task_id)
    return mapping


def _save(parent, source, path, header, result, task_id):
    message = Message('tool', tool_call_id=header.get('tool_call_id'), tool_result=result, id=header['source'])
    return parent.save(message, header.get('tool_name', ''), namespace=task_id,
                       provenance={'task_id': task_id, 'source_root': str(source.root), 'source_path': path})
