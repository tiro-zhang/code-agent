"""顶层任务及其独立对话共用的请求额度和实际用量。"""

from dataclasses import dataclass, field

from ..types import TokenUsage


@dataclass
class TaskBudget:
    limit: int
    root_run_id: str
    used: int = 0
    usage: list[TokenUsage] = field(default_factory=list)

    @property
    def remaining(self):
        return self.limit - self.used

    def take(self):
        if self.remaining <= 0:
            raise RuntimeError('任务请求预算已耗尽')
        self.used += 1
