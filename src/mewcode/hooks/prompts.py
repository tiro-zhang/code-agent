"""注入预览不消费；只在实际工作请求开始时领取选定条目。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptInjection:
    sequence: int
    text: str = field(repr=False)
    source: str
    event: str


class PromptQueue:
    def __init__(self):
        self._sequence = 0
        self._pending: list[PromptInjection] = []

    def add(self, text: str, source: str, event: str):
        self._sequence += 1
        self._pending.append(PromptInjection(self._sequence, text, source, event))

    def snapshot(self) -> tuple[PromptInjection, ...]:
        return tuple(self._pending)

    def consume(self, selected):
        sequences = {item.sequence for item in selected}
        self._pending[:] = [item for item in self._pending if item.sequence not in sequences]

    def clear(self):
        self._pending.clear()
