"""父任务跨主执行段保留原目标、身份和预算。"""

import asyncio
from dataclasses import dataclass, field

from ..skills.budget import TaskBudget


@dataclass
class TaskContext:
    task_id: str
    question: str
    budget: TaskBudget
    mode: str
    generation: int
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    children: set[str] = field(default_factory=set)
    wake_allowed: bool = True
    finished: bool = False
    reason: str = ""
    user_message: object | None = None
    final_message: object | None = None
    evidence: list = field(default_factory=list)
    memory_enqueued: bool = False
