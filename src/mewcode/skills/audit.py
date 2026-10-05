"""子运行借用主日志单写者，并显式登记证据归属。"""


class ChildJournal:
    def __init__(self, parent, parent_task_id, run_id, skill):
        self.parent, self.id = parent, parent.id
        self.parent_task_id, self.run_id, self.skill = parent_task_id, run_id, skill

    def append(self, kind, payload):
        if kind in {'history_checkpoint', 'checkpoint'}:
            payload = dict(payload, covers_seq=self.parent.seq)
        return self.parent.append('child_event', {'parent_task_id': self.parent_task_id,
            'run_id': self.run_id, 'skill': self.skill, 'kind': kind, 'payload': payload})


class ChildResultCache:
    """子估算状态独立，实际文件和关闭生命周期由父缓存持有。"""

    def __init__(self, parent, run_id):
        self.parent = parent
        self.namespace = 'child_' + run_id
        self.failed = False
        self.attempted = set()
        self._paths = set()

    @property
    def paths(self):
        return set(self._paths)

    @property
    def count(self):
        return len(self._paths)

    def save(self, message, tool_name, *, _index=False):
        path = self.parent.save(message, tool_name, _index=_index, namespace=self.namespace)
        self._paths.add(path)
        return path

    def write_index(self, paths):
        from ..types import Message
        from ..tools.base import ToolResult
        return self.save(Message('tool', tool_result=ToolResult.success({'files': sorted(paths)})),
                         'context_result_index', _index=True)

    def restore(self, path):
        # 背景摘录还可能引用父消息中的已登记证据。
        return self.parent.restore(path)

    def close(self):
        pass
