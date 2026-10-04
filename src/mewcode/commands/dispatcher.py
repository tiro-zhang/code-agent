"""命令查找与可恢复错误处理，不拥有任务流。"""
from .parser import ParsedInput
from .ports import CommandContext, CommandResult, CommandUsageError
from .registry import CommandRegistry


async def dispatch(parsed: ParsedInput, registry: CommandRegistry, context: CommandContext) -> CommandResult:
    if parsed.kind == 'empty':
        return CommandResult()
    if parsed.kind == 'message':
        return CommandResult('message', parsed.text)
    spec = registry.find(parsed.name)
    if spec is None:
        context.show_message('提示> 未知命令，请输入 /help 查看支持的命令。')
        return CommandResult()
    try:
        if parsed.text and not spec.accepts_arguments:
            raise CommandUsageError('此命令不接受参数')
        return await spec.handler(parsed.text, context)
    except CommandUsageError as error:
        context.show_message(f'提示> {error}。用法：{spec.usage}')
    except (ValueError, OSError) as error:
        context.show_message(f'提示> 操作失败：{error}')
    return CommandResult()
