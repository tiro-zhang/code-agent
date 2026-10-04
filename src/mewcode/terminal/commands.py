"""聊天草稿的本地命令识别与纯文本帮助，不执行任何会话操作。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    """解析结果；任务正文与权限命令分别交给对应的会话接口。"""

    kind: str
    text: str = ""


_COMMANDS = ("/help", "/status", "/compact", "/exit", "/plan", "/do", "/permissions")
_PERMISSION_MODES = ("strict", "default", "bypass")
_REVOKE_SCOPES = ("session", "permanent")
_PERMISSION_USAGE = (
    "用法：/permissions；/permissions mode strict|default|bypass；"
    "/permissions revoke session|permanent"
)


def parse_command(draft: str) -> Command:
    """只识别单行命令；普通消息保留原文，转义仅去掉首个斜杠。"""
    stripped = draft.strip()
    if not stripped:
        return Command("empty")
    if "\n" in draft or "\r" in draft:
        return Command("message", draft)
    if stripped.startswith("//"):
        slash_index = len(draft) - len(draft.lstrip())
        return Command("message", draft[:slash_index] + draft[slash_index + 1:])
    if not stripped.startswith("/"):
        return Command("message", draft)

    parts = stripped.split()
    name = parts[0]
    if name not in _COMMANDS:
        return Command("error", "未知命令。用法：输入 /help 查看支持的本地命令。")
    if name == "/plan":
        task = stripped[len(name):].strip()
        return Command("plan", task)
    if name == "/permissions":
        if len(parts) == 1:
            return Command("permissions", name)
        if len(parts) == 3:
            if parts[1] == "mode" and parts[2] in _PERMISSION_MODES:
                return Command("permissions", " ".join(parts))
            if parts[1] == "revoke" and parts[2] in _REVOKE_SCOPES:
                return Command("permissions", " ".join(parts))
        return Command("error", _PERMISSION_USAGE)
    if len(parts) != 1:
        return Command("error", f"此命令不接受参数。用法：{name}")
    return Command(name[1:])


def help_text(*, enhanced: bool) -> str:
    """展示完整语法和当前终端下发送、取消与退出的实际手势。"""
    commands = (
        "本地命令（仅单行草稿生效）\n"
        "  /help                       查看帮助\n"
        "  /status                     查看模式、待执行计划和最近任务详情\n"
        "  /compact                    压缩较早历史，保留当前任务和近期原文\n"
        "  /exit                       退出会话\n"
        "  /plan [任务]                进入只读规划模式，可直接提供任务\n"
        "  /do                         执行最新有效计划，仅执行一次\n"
        "  /permissions                查看权限与授权\n"
        "  /permissions mode strict    切换为严格权限模式\n"
        "  /permissions mode default   切换为默认权限模式\n"
        "  /permissions mode bypass    切换为绕过权限模式\n"
        "  /permissions revoke session 撤销当前项目的会话授权\n"
        "  /permissions revoke permanent 撤销当前项目的永久授权\n"
        "以 // 开头的单行消息去掉一个 / 后作为普通消息发送。\n"
    )
    editing = (
        "发送与换行：Enter 发送完整草稿；先按 Esc 再按 Enter 换行；"
        "支持等效按键的终端可用 Alt+Enter。粘贴只编辑草稿，仍需明确发送。\n"
        "命令补全仅修改草稿，不会执行命令；多行中的命令文字作为普通正文。\n"
        if enhanced else
        "发送：纯文本兼容模式逐行读取，Enter 提交当前行；不支持多行草稿编辑。\n"
    )
    controls = (
        "取消与退出：空闲 Ctrl+C 或 EOF（终端通常为 Ctrl+D）退出会话；"
        "任务运行中 Ctrl+C 取消整轮，清理后可继续输入。\n"
        "授权阶段：回车／1 拒绝本次操作，本轮可继续；Ctrl+C 取消整轮；"
        "EOF 取消本轮未发送的调用并收尾退出。"
    )
    review = "\n增强审批中输入 results 可分页查看全部旁路结果；浏览不批准。" if enhanced else ""
    return commands + editing + controls + review


def command_completions(text: str) -> list[str]:
    """返回完整草稿替换候选，只补全命令位置的已知语法。"""
    if "\n" in text or "\r" in text:
        return []
    content = text.lstrip()
    if not content.startswith("/") or content.startswith("//"):
        return []
    leading = text[:len(text) - len(content)]
    parts = content.split()
    if content[-1].isspace():
        parts.append("")

    if len(parts) == 1:
        choices = _COMMANDS
    elif parts[0] != "/permissions":
        return []
    elif len(parts) == 2:
        choices = ("mode", "revoke")
    elif len(parts) == 3 and parts[1] == "mode":
        choices = _PERMISSION_MODES
    elif len(parts) == 3 and parts[1] == "revoke":
        choices = _REVOKE_SCOPES
    else:
        return []
    prefix = parts[-1]
    return [leading + " ".join((*parts[:-1], choice))
            for choice in choices if choice.startswith(prefix)]
