"""静态命令元数据及共享名称空间。"""
from dataclasses import dataclass, replace
from inspect import iscoroutinefunction
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .ports import CommandContext, CommandResult


class RegistrationError(ValueError):
    """定义错误必须在启动资源前报告。"""


@dataclass(frozen=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    description: str
    usage: str
    kind: str
    handler: Callable[[str, 'CommandContext'], Awaitable['CommandResult']]
    argument_hint: str | None = None
    hidden: bool = False
    accepts_arguments: bool = False
    argument_choices: tuple[tuple[str, ...], ...] = ()


class CommandRegistry:
    """名称、帮助及补全共用不可变定义；冻结后禁止登记。"""

    def __init__(self, definitions=()):
        self._definitions = []
        self._lookup = {}
        self._frozen = False
        for definition in definitions:
            self.register(definition)

    def register(self, definition: CommandSpec) -> None:
        if self._frozen:
            raise RegistrationError('命令注册表已冻结')
        if (not isinstance(definition, CommandSpec) or definition.kind not in {'local', 'state', 'prompt'}
                or not iscoroutinefunction(definition.handler)
                or not isinstance(definition.aliases, tuple)
                or not definition.description or not definition.usage):
            raise RegistrationError('命令定义非法：需要合法类型、描述、用法、别名元组和异步处理函数')
        names = (definition.name, *definition.aliases)
        if any(not isinstance(name, str) or not name or '/' in name or
               any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in name) for name in names):
            raise RegistrationError('命令名称或别名非法：不能包含斜杠、空白或控制字符')
        names = tuple(name.lower() for name in names)
        seen = set()
        for name in names:
            if name in self._lookup or name in seen:
                previous = self._lookup[name].name if name in self._lookup else names[0]
                raise RegistrationError(f'命令冲突：{name} 同时属于 {previous} 和 {names[0]}')
            seen.add(name)
        definition = replace(definition, name=names[0], aliases=names[1:],
                             argument_choices=tuple(tuple(row) for row in definition.argument_choices))
        self._definitions.append(definition)
        self._lookup.update((name, definition) for name in names)

    def freeze(self):
        self._frozen = True
        return self

    @property
    def definitions(self) -> tuple[CommandSpec, ...]:
        return tuple(self._definitions)

    def find(self, name: str) -> CommandSpec | None:
        return self._lookup.get(name.lower())

    def completions(self, text: str) -> list[str]:
        if '\n' in text or '\r' in text:
            return []
        content = text.lstrip()
        if not content.startswith('/') or content.startswith('//'):
            return []
        leading = text[:len(text) - len(content)]
        parts = content.split()
        if content[-1].isspace():
            parts.append('')
        if len(parts) == 1:
            prefix = parts[0][1:].lower()
            return [leading + '/' + name for spec in self._definitions if not spec.hidden
                    for name in (spec.name, *spec.aliases) if name.startswith(prefix)]
        spec = self.find(parts[0][1:])
        if spec is None or spec.hidden:
            return []
        before, prefix = tuple(parts[1:-1]), parts[-1]
        choices = dict.fromkeys(row[len(before)] for row in spec.argument_choices
                                if len(row) > len(before) and row[:len(before)] == before
                                and row[len(before)].startswith(prefix))
        return [leading + ' '.join((*parts[:-1], choice)) for choice in choices]

    def help_text(self, *, enhanced: bool) -> str:
        kinds = {'local': '本地处理', 'state': '状态操作', 'prompt': '对话提示词'}
        lines = ['斜杠命令（仅单行草稿生效）']
        for spec in self._definitions:
            if spec.hidden:
                continue
            lines.append(f'  {spec.usage} · {kinds[spec.kind]} · {spec.description}')
            if spec.argument_hint:
                lines.append('    参数：' + spec.argument_hint)
            for alias in spec.aliases:
                lines.append(f'    别名：/{alias}')
            for name in (spec.name, *spec.aliases):
                lines.extend('    /' + name + ' ' + ' '.join(row) for row in spec.argument_choices)
        lines.extend([
            '以 // 开头的单行消息去掉一个 / 后作为普通消息发送；多行中的命令文字作为普通正文。',
            '启动选项：--list-sessions 列出当前项目存档；--resume ID|latest 显式恢复；默认新建。',
            ('发送与换行：Enter 发送完整草稿；Esc 后 Enter 或 Alt+Enter 换行；粘贴只编辑草稿。\n'
             '输入 / 自动发现命令及描述；Tab 补全只修改草稿；菜单中 Enter 确认候选，再按 Enter 执行；Esc 关闭菜单。'
             if enhanced else '发送：纯文本兼容模式逐行读取，Enter 提交当前行；不支持多行草稿编辑。'),
            ('取消与退出：空闲 Ctrl+C 清除非空草稿，空草稿时退出；运行中 Ctrl+C 显示正在停止，清理结束后恢复输入。'
             if enhanced else '取消与退出：空闲 Ctrl+C 或 EOF（终端通常为 Ctrl+D）退出会话；任务运行中 Ctrl+C 取消整轮。'),
            '授权阶段：回车／1 拒绝本次操作，本轮可继续；Ctrl+C 取消整轮；EOF 取消未发送调用并收尾退出。',
        ])
        if enhanced:
            lines.append('Ctrl+B 在等待前台子 Agent 时切到后台；自动接续保留未提交草稿。/tasks 查看完整结果及取消。')
            lines.append('F2 查看最近任务的调用／思考，Tab 切换、PgUp/PgDn 翻页、Esc/F2 返回；浏览保留草稿，不提交。')
            lines.append('增强审批中输入 results 可分页查看全部旁路结果，scope 查看批准范围；浏览不批准。')
        return '\n'.join(lines)
