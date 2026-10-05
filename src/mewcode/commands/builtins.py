"""静态内置命令；处理函数只调用能力接口或返回待消费结果。"""
import re

from .ports import CommandContext, CommandResult, CommandUsageError
from .registry import CommandRegistry, CommandSpec

async def help_command(args: str, context: CommandContext) -> CommandResult:
    context.show_message(context.registry.help_text(enhanced=context.enhanced))
    return CommandResult()


async def compact_command(args: str, context: CommandContext) -> CommandResult:
    return CommandResult('summary')


async def clear_command(args: str, context: CommandContext) -> CommandResult:
    await context.clear_screen()
    return CommandResult()


async def plan_command(args: str, context: CommandContext) -> CommandResult:
    context.set_mode('plan')
    context.refresh_status()
    if args:
        return CommandResult('message', args)
    context.show_message('提示> 已进入 [PLAN] 只读规划模式，请输入任务；/do 切换执行模式后，再输入明确任务。')
    return CommandResult()


async def do_command(args: str, context: CommandContext) -> CommandResult:
    context.set_mode('execute')
    context.refresh_status()
    context.show_message('提示> 当前为 [DEFAULT] 执行模式，请输入要执行的任务。')
    return CommandResult()


async def session_command(args: str, context: CommandContext) -> CommandResult:
    if args not in {'', 'list'}:
        raise CommandUsageError('只支持查看；恢复请重启并使用 --resume ID|latest')
    context.show_message(context.session_text(listing=args == 'list'))
    return CommandResult()


async def memory_command(args: str, context: CommandContext) -> CommandResult:
    parts = args.split()
    if not parts:
        text = context.memory_text('status')
    elif parts[0] == 'list' and (len(parts) == 1 or len(parts) == 2 and parts[1] in {'project', 'user'}):
        text = context.memory_text('list', parts[1] if len(parts) == 2 else None)
    elif (len(parts) == 3 and parts[0] == 'show' and parts[1] in {'project', 'user'}
          and re.fullmatch(r'[0-9a-f]{32}', parts[2])):
        text = context.memory_text('show', parts[1], parts[2])
    else:
        raise CommandUsageError('需要合法的查看范围及完整笔记 ID')
    context.show_message(text)
    return CommandResult()


async def permission_command(args: str, context: CommandContext) -> CommandResult:
    parts = tuple(args.split())
    if parts and parts not in PERMISSION_CHOICES:
        raise CommandUsageError('未知权限操作')
    context.show_message(context.permission_text(args))
    context.refresh_status()
    return CommandResult()


async def status_command(args: str, context: CommandContext) -> CommandResult:
    context.show_message(context.status_text())
    return CommandResult()


async def skills_command(args: str, context: CommandContext) -> CommandResult:
    context.show_message(context.skills_text(args))
    context.refresh_status()
    return CommandResult()


async def reset_command(args: str, context: CommandContext) -> CommandResult:
    context.reset_session()
    context.refresh_status()
    context.show_message('对话历史和激活 Skill 已清空；会话身份、模式、权限、长期记忆和存档证据保留。')
    return CommandResult()


def skill_command(name):
    async def invoke(args: str, context: CommandContext) -> CommandResult:
        return CommandResult('skill', args, name)
    return invoke


async def exit_command(args: str, context: CommandContext) -> CommandResult:
    return CommandResult('exit')


PERMISSION_CHOICES = (('mode', 'strict'), ('mode', 'default'), ('mode', 'bypass'),
                      ('revoke', 'session'), ('revoke', 'permanent'))


def builtin_definitions() -> tuple[CommandSpec, ...]:
    """元数据是帮助、补全和分发的唯一命令表。"""
    return (
        CommandSpec('help', (), '查看命令和按键帮助', '/help', 'local', help_command),
        CommandSpec('compact', (), '独立模型摘要压缩较早历史，保留近期工作记录', '/compact', 'state', compact_command),
        CommandSpec('clear', (), '仅清屏，保留上下文和输入历史', '/clear', 'state', clear_command),
        CommandSpec('plan', (), '进入或重新进入只读规划模式', '/plan [任务]', 'state', plan_command,
                    argument_hint='可选任务作为普通对话提交一次', accepts_arguments=True),
        CommandSpec('do', (), '切换回执行模式；随后输入明确任务', '/do', 'state', do_command),
        CommandSpec('session', (), '查看当前会话或项目存档', '/session [list]', 'local', session_command,
                    argument_hint='list 列出项目存档；启动用 --resume ID|latest 恢复',
                    accepts_arguments=True, argument_choices=(('list',),)),
        CommandSpec('memory', (), '仅查看自动记忆和维护状态', '/memory [list [project|user] | show <project|user> <id>]',
                    'local', memory_command, argument_hint='list 默认两域；show 需要完整笔记 ID', accepts_arguments=True,
                    argument_choices=(('list', 'project'), ('list', 'user'), ('show', 'project'), ('show', 'user'))),
        CommandSpec('permission', ('permissions',), '查看权限、切换权限模式或撤销授权',
                    '/permission [mode strict|default|bypass | revoke session|permanent]', 'state', permission_command,
                    argument_hint='任务模式与权限模式相互独立', accepts_arguments=True, argument_choices=PERMISSION_CHOICES),
        CommandSpec('status', (), '查看模式、上下文估算及实际 Token 用量', '/status', 'local', status_command),
        CommandSpec('reset', (), '清空模型历史和激活 Skill，保留会话与证据', '/reset', 'state', reset_command),
        CommandSpec('skills', (), '查看或停用已激活 Skill', '/skills [list|active|deactivate <name|--all>]',
                    'state', skills_command, accepts_arguments=True,
                    argument_choices=(('list',), ('active',), ('deactivate', '--all'))),
        CommandSpec('exit', (), '受控退出并保留存档', '/exit', 'state', exit_command),
    )


def build_registry(catalog=None) -> CommandRegistry:
    if catalog is None:
        from pathlib import Path
        from ..skills.catalog import discover_skills
        catalog = discover_skills(Path.cwd())
    registry = CommandRegistry(builtin_definitions())
    catalog.validate_names({name for d in registry.definitions for name in (d.name, *d.aliases)})
    for skill in catalog.skills:
        registry.register(CommandSpec(skill.name, (), f'{skill.description}（{skill.mode}）',
            f'/{skill.name} [参数]', 'prompt', skill_command(skill.name),
            argument_hint='参数原文传给 Skill SOP；遵守当前模式和权限', accepts_arguments=True))
    return registry.freeze()
