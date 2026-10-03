"""只分析可见 shell 结构与已知危险文本，不执行展开或命令。"""

from dataclasses import dataclass
import re

import bashlex

from ..tools.base import ToolError


@dataclass(frozen=True)
class ShellAnalysis:
    subjects: tuple[str, ...]
    simple: bool


# 正则只覆盖已知形式；引用文本可能保守命中，不能据此宣称进程隔离。
_RM = re.compile(r"(?<![\w./-])(?:/(?:[\w.+-]+/)*)?rm(?=\s)([^\n;&|()]*)")
_RECURSIVE = re.compile(r"(?:^|\s)(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?=\s|$)")
_HOME_SUFFIX = r"(?:/(?:\./)*(?:\*|\.)?)?"
_ROOT_OR_HOME = re.compile(
    r'''(?:^|\s)['"]?(?:/+(?:\.\.?/)*(?:\*|\.{1,2})?|~''' + _HOME_SUFFIX + r"|" +
    r"\$(?:HOME|\{HOME\})" + _HOME_SUFFIX + r"|/root" + _HOME_SUFFIX + r"|" +
    r'''/(?:home|Users)/[^/\s'"]+''' + _HOME_SUFFIX + r")" +
    r'''['"]?(?=\s|$)'''
)
_START = r"(?:^|[;&|()\n])\s*(?:sudo\s+)?(?:/(?:[\w.+-]+/)*)?"
_DEVICE = r"/dev/(?:r?disk\d[\w]*|[shv]d[a-z]\w*|nvme\d[\w]*|mmcblk\d[\w]*|mapper/[^\s;'\"|&<>]+)"
_BLACKLIST = (
    ("disk_format_or_erase", "命令涉及磁盘格式化或擦除", re.compile(
        _START + r"(?:mkfs(?:\.[\w+-]+)?|mke2fs|newfs(?:_[\w+-]+)?|"
        r"wipefs|blkdiscard)\b|" + _START +
        r"diskutil\s+(?:eraseDisk|eraseVolume|partitionDisk|resetFusion|secureErase)\b|" +
        _START + r"sgdisk\s+[^\n;&|]*--zap(?:-all)?\b"
    )),
    ("raw_disk_write", "命令涉及向原始磁盘设备写入", re.compile(
        r"\bof\s*=\s*['\"]?" + _DEVICE + r"(?=[\s;'\"|&<>]|$)|"
        r">(?:[>|&])?\s*['\"]?" + _DEVICE + r"(?=[\s;'\"|&<>]|$)|" +
        _START + r"tee\b[^\n;&|]*?" + _DEVICE + r"(?=[\s;'\"|&<>]|$)"
    )),
    ("fork_bomb", "命令命中已知 fork bomb 形式", re.compile(
        r"(?P<name>:|[A-Za-z_]\w*)\s*\(\s*\)\s*\{\s*"
        r"(?P=name)\s*\|\s*(?P=name)\s*&\s*\}\s*;\s*(?P=name)(?=\s|$|[;&|])"
    )),
)
_PARAMETER = re.compile(r"(?:[A-Za-z_]\w*|[0-9]+|[@*#?$!_-])\Z", re.ASCII)
_REDIRECTS = frozenset({">", ">>", "<", "<>", ">|", ">&", "<&"})
_LAYOUT = re.compile(r"(?:\s+|#[^\n]*(?:\n|$))*\Z")


def check_blacklist(command: str) -> tuple[str, str] | None:
    """返回稳定规则标识与原因；输入始终只作为文本处理。"""
    for match in _RM.finditer(command):
        arguments = match.group(1)
        if _RECURSIVE.search(arguments) and _ROOT_OR_HOME.search(arguments):
            return "root_recursive_delete", "命令涉及递归删除根目录或用户主目录"
    for identifier, reason, pattern in _BLACKLIST:
        if pattern.search(command):
            return identifier, reason
    return None


def _deny_blacklist(subject: str) -> None:
    match = check_blacklist(subject)
    if match is not None:
        identifier, reason = match
        raise ToolError("permission_denied", reason, source="blacklist",
                        rule_id=identifier, not_started=True)


def _unsupported() -> ToolError:
    return ToolError("permission_check_failed", "无法完整检查此 shell 结构，请改用受支持的简单命令",
                     not_started=True)


def _literal_word(source: str) -> str | None:
    """静态移除 POSIX 引号和转义；不做变量、通配符或命令展开。"""
    result: list[str] = []
    quote = None
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\" and quote != "'":
            index += 1
            if index == len(source):
                raise _unsupported()
            escaped = source[index]
            if escaped != "\n":
                if quote == '"' and escaped not in '$`"\\':
                    result.append("\\")
                result.append(escaped)
        elif char in "'\"" and (quote is None or quote == char):
            quote = char if quote is None else None
        elif quote != "'" and char in "$`":
            return None
        elif quote is None and (char in "*?[" or (char == "~" and index == 0)):
            return None
        else:
            result.append(char)
        index += 1
    if quote is not None:
        raise _unsupported()
    return "".join(result)


def analyze_command(command: str) -> ShellAnalysis:
    """检查原文和 AST 可见命令；任何失败均发生在实际执行之前。"""
    _deny_blacklist(command)
    if not command.strip() or "\0" in command:
        raise _unsupported()
    try:
        # 发行版 0.18 没有 proceedonerror 参数；默认仍为严格失败行为。
        trees = bashlex.parse(command, strictmode=True, expansionlimit=None)
    except Exception:
        # bashlex 对部分不支持结构抛出非 ParsingError，仍须封闭失败。
        raise _unsupported() from None
    if not trees:
        raise _unsupported()

    subjects: list[str] = [command]
    simple = len(trees) == 1 and trees[0].kind == "command"

    def add(subject: str) -> None:
        if subject and subject not in subjects:
            subjects.append(subject)
        _deny_blacklist(subject)

    def source(node) -> str:
        start, end = node.pos
        if not 0 <= start <= end <= len(command):
            raise _unsupported()
        return command[start:end]

    def visit(node) -> None:
        nonlocal simple
        kind = node.kind
        if kind == "command":
            add(source(node))
            words = [part for part in node.parts if part.kind == "word"]
            literals = [_literal_word(source(word)) for word in words]
            if words:
                # 动态参数不能遮蔽已明确的静态命令词；未知词保留原文，不求值。
                add(" ".join(value if value is not None else source(word)
                             for word, value in zip(words, literals)))
            if any(part.kind == "redirect" for part in node.parts):
                normalized = []
                for part in node.parts:
                    if part.kind == "word":
                        raw = source(part)
                        literal = _literal_word(raw)
                        normalized.append(raw if literal is None else literal)
                    elif part.kind == "redirect":
                        output = part.output
                        if isinstance(output, bashlex.ast.node):
                            raw = source(output)
                            literal = _literal_word(raw)
                            target = raw if literal is None else literal
                        else:
                            target = str(output)
                        # 静态目标也须参与黑名单与规则检查，不能靠引号拆分设备路径。
                        normalized.append(f"{part.type} {target}")
                add(" ".join(normalized))
            if not words:
                simple = False
            for part in node.parts:
                visit(part)
        elif kind in {"word", "assignment"}:
            if _literal_word(source(node)) is None:
                simple = False
            for child in node.parts:
                visit(child)
        elif kind == "parameter":
            simple = False
            if _PARAMETER.fullmatch(node.value) is None:
                raise _unsupported()
        elif kind == "tilde":
            simple = False
        elif kind == "commandsubstitution":
            simple = False
            text = source(node)
            if text.startswith("$(") and text.endswith(")"):
                body_start, body_end = node.pos[0] + 2, node.pos[1] - 1
            elif text.startswith("`") and text.endswith("`"):
                body_start, body_end = node.pos[0] + 1, node.pos[1] - 1
            else:
                raise _unsupported()
            child_start, child_end = node.command.pos
            # 发行版可能只解析替换中的第一行；剩余正文必须全部为布局或注释。
            if not (body_start <= child_start <= child_end <= body_end
                    and _LAYOUT.fullmatch(command[body_start:child_start])
                    and _LAYOUT.fullmatch(command[child_end:body_end])):
                raise _unsupported()
            visit(node.command)
        elif kind == "redirect":
            simple = False
            if node.type not in _REDIRECTS or node.heredoc is not None:
                raise _unsupported()
            if isinstance(node.output, bashlex.ast.node):
                visit(node.output)
        elif kind in {"list", "pipeline"}:
            simple = False
            for child in node.parts:
                visit(child)
        elif kind == "compound":
            simple = False
            # 本阶段只支持完整可遍历的 POSIX 子 shell，不接受函数和控制结构。
            if (len(node.list) < 3 or node.list[0].kind != "reservedword"
                    or node.list[0].word != "(" or node.list[-1].kind != "reservedword"
                    or node.list[-1].word != ")"):
                raise _unsupported()
            for child in [*node.list, *node.redirects]:
                visit(child)
        elif kind == "operator":
            simple = False
            if node.op not in {"&&", "||", ";", "\n", "&"}:
                raise _unsupported()
        elif kind == "pipe":
            simple = False
            if node.pipe != "|":
                raise _unsupported()
        elif kind == "reservedword":
            simple = False
            if node.word not in {"(", ")", "!"}:
                raise _unsupported()
        else:
            raise _unsupported()

    try:
        end = 0
        for tree in trees:
            if _LAYOUT.fullmatch(command[end:tree.pos[0]]) is None:
                raise _unsupported()
            visit(tree)
            end = tree.pos[1]
        if _LAYOUT.fullmatch(command[end:]) is None:
            raise _unsupported()
    except ToolError:
        raise
    except Exception:
        raise _unsupported() from None
    return ShellAnalysis(tuple(subjects), simple)
