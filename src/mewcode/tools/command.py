"""独立非交互 shell 命令。"""

from .base import ToolContext, ToolError, ToolResult, object_schema
from .processes import child_process, output_chunks, output_text


class ExecuteCommand:
    read_only = False
    name = "execute_command"
    description = "执行完整 POSIX shell 命令，支持管道与重定向，每次从工作目录启动独立 /bin/sh。无交互输入，默认超时30秒；命令可访问目录外，勿启动后台守护进程。"
    input_schema = object_schema({"command": {"type": "string", "minLength": 1},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30}}, ["command"])

    def execute(self, arguments: dict, context: ToolContext) -> ToolResult:
        command = arguments["command"]
        if not command.strip() or "\0" in command:
            raise ToolError("invalid_arguments", "命令不能为空或包含 NUL")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        limit = context.output_limit // 2
        truncated = False
        with child_process(["/bin/sh", "-c", command], context.root) as process:
            for name, chunk in output_chunks(process):
                kept = chunk[:max(0, limit - len(buffers[name]))]
                exceeded = len(kept) != len(chunk)
                if context.emit and (kept or (exceeded and not truncated)):
                    context.emit({"kind": "output", "stream": name, "chunk": kept, "truncated": exceeded})
                buffers[name].extend(kept)
                truncated |= exceeded
            code = process.wait()
        decoded = {name: output_text(value, limit) for name, value in buffers.items()}
        data = {"exit_code": code, **{name: value[0] for name, value in decoded.items()}}
        truncated |= any(value[1] for value in decoded.values())
        if code:
            return ToolResult.failure("command_failed", f"命令以非零状态 {code} 退出", data=data, truncated=truncated)
        return ToolResult.success(data, truncated=truncated)
