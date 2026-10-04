"""与终端框架无关的静态命令系统。"""
from .registry import CommandRegistry, CommandSpec, RegistrationError
from .parser import ParsedInput, parse_command
from .ports import CommandContext, CommandResult, CommandUsageError
from .dispatcher import dispatch
