"""基于请求快照的纯审批审阅与终端分页，不执行或授权工具。"""

from copy import deepcopy
import difflib
import json
from pathlib import Path
import re

from prompt_toolkit.utils import get_cwidth

from .text import terminal_text


DECISIONS = {"": "deny", "1": "deny", "deny": "deny", "2": "once", "once": "once",
             "3": "session", "session": "session", "4": "permanent", "permanent": "permanent"}
FILE_TOOLS = {"read_file", "write_file", "edit_file", "glob_files", "search_code"}


class ApprovalView:
    """展示同一请求的固定参数、真实目标和批准范围。"""

    sections = ("targets", "arguments", "content", "scope")
    decisions = DECISIONS

    def __init__(self, request, *, root: str | Path, secret: str = ""):
        self._id = str(request.id)
        self._tool = str(request.tool)
        self._reason = str(request.reason)
        self._mode = str(request.mode)
        self._arguments = deepcopy(request.arguments)
        self._targets = tuple(str(target) for target in request.targets)
        external = getattr(request, "external", None)
        self._external = tuple(str(value) for value in external) if external else None
        self._connection = str(getattr(request, "connection_description", ""))
        self._root, self._secret = str(root), secret

    @property
    def request_id(self) -> str:
        """供控制器关联请求的原始完整标识；展示时仍须转义。"""
        return self._id

    def identity(self) -> str:
        """翻到任意页面时仍可辨认当前操作对象。"""
        if self._external:
            return self._field(f'{self._external[0]} / {self._external[1]}')
        count = f' · {len(self._targets)} 个目标' if self._tool in FILE_TOOLS else ''
        return self._field(self._tool) + count

    def _safe(self, text: str) -> str:
        return terminal_text(text, self._secret, multiline=True)

    def _field(self, text: str) -> str:
        return terminal_text(text, self._secret)

    def _preview(self, text: str) -> str:
        return terminal_text(text, self._secret, limit=80)

    def _arguments_json(self) -> str:
        """在 JSON 字符串层脱敏，保留字段和结构，不修改绑定参数。"""
        serialized = json.dumps(self._arguments, ensure_ascii=False, indent=2)
        if not self._secret:
            return serialized

        def hide_string(match: re.Match) -> str:
            value = json.loads(match.group(0))
            return json.dumps(value.replace(self._secret, "[已隐藏]"), ensure_ascii=False)

        # 先解码每个键和值字符串再脱敏；不在 JSON 语法上替换，也不丢失脱敏后同名的字段。
        return re.sub(r'"(?:\\.|[^"\\])*"', hide_string, serialized)

    def _scope(self) -> list[str]:
        lines = ["范围> 本次绑定当前调用、完整参数及目标，仅使用一次。"]
        if self._external:
            lines += [
                "范围> 会话／永久仅绑定当前真实项目根、Server 有效配置身份、原始工具及规范完整 JSON 参数；参数或连接身份改变即失效，不扩展到整个 Server、其他工具或参数通配范围。",
                "保存> 永久批准会将完整调用参数保存在项目本地权限文件；不保存连接 env、headers 或凭据。",
            ]
        elif self._tool == "execute_command":
            lines.append("范围> 会话／永久仅绑定此根目录、工具与本次完整精确命令；可在目标详情查看全文。")
        elif self._tool in FILE_TOOLS:
            lines.append("范围> 会话／永久仅绑定此根目录、工具与列出的真实文件；允许未来同一路径使用不同内容或搜索表达式，不扩展目录或其他工具。")
        else:
            lines += ["范围> 会话／永久仅绑定此根目录、工具与本次完整 JSON 参数；参数改变即失效，不扩展到其他参数或工具。",
                      "保存> 永久批准会将完整调用参数保存在项目本地权限文件。"]
        lines.append("期限> 会话在本次运行有效；永久保存于项目本地权限文件，可用 /permissions revoke 撤销。")
        return lines

    def summary(self) -> str:
        """首页依次展示操作身份、批准范围和完整操作内容；决定栏由终端负责。"""
        labels = {"read_file": "读取文件", "write_file": "写入文件", "edit_file": "编辑文件",
                  "search_code": "搜索代码", "glob_files": "搜索文件", "execute_command": "执行命令"}
        operation = labels.get(self._tool, "调用工具")
        if self._external:
            operation = f"调用外部工具\n外部 Server> {self._field(self._external[0])} · 原始工具> {self._field(self._external[1])}"
        elif self._tool in FILE_TOOLS and self._targets:
            operation += " " + self._field(self._targets[0])
        scope = ("精确外部身份及完整参数" if self._external else "完整精确命令" if self._tool == "execute_command"
                 else "列出的真实文件（未来内容可变）" if self._tool in FILE_TOOLS else "完整 JSON 参数")
        lines = [f"操作> {operation}", f"范围> 本次仅当前调用；会话／永久绑定当前项目及{scope}。"]
        if self._external:
            lines.append(self.detail('arguments'))
        elif self._tool in {'write_file', 'edit_file'}:
            lines.append(self.detail('content'))
        elif self._tool in FILE_TOOLS | {'execute_command'}:
            lines.append(self.detail('targets'))
        else:
            lines.append(self.detail('arguments'))
        lines += [f"关联标识> {self._field(self._id)}", f"工具> {self._field(self._tool)} · 等待授权 · {self._field(self._mode)}",
                  f"原因> {self._field(self._reason)}", f"工作根目录> {self._field(self._root)}"]
        if self._external:
            lines += [f"稳定别名> {self._field(self._tool)}", f"安全连接描述> {self._field(self._connection) or '未提供'}",
                      "目标> 外部调用参数不作为经过本地验证的真实文件。"]
        elif self._tool in FILE_TOOLS:
            lines.append(f"真实目标数量> {len(self._targets)}（完整列表见目标详情）")
        lines += self._scope()
        return self._safe("\n".join(lines))

    def detail(self, section: str) -> str:
        """返回指定部分的全部安全文字，不读取目标文件。"""
        if section == "scope":
            return self._safe("\n".join(self._scope()))
        if section == "summary":
            return self.summary()
        if section == "all":
            return "\n\n".join([self.summary(), *(self.detail(part) for part in self.sections)])
        if section == "targets":
            if self._external:
                server, original = self._external
                text = "\n".join([f"外部 Server> {self._field(server)}", f"稳定别名> {self._field(self._tool)}",
                                  f"原始工具> {self._field(original)}", f"安全连接描述> {self._field(self._connection) or '未提供'}",
                                  "外部 path 等字段仅是调用参数，未作为本地真实文件核验。",
                                  *self._scope()])
            elif self._tool == "execute_command":
                text = "完整精确命令：\n" + str(self._arguments.get("command", ""))
            elif self._tool in FILE_TOOLS:
                text = f"全部真实目标（{len(self._targets)}）：\n" + ("\n".join(self._field(target) for target in self._targets) or "无文件目标")
            else:
                return terminal_text("完整 JSON 参数授权对象：\n" + self._arguments_json(), "", multiline=True)
        elif section == "arguments":
            return terminal_text("完整参数（本次有效参数快照）：\n" + self._arguments_json(), "", multiline=True)
        elif section == "content":
            if not self._external and self._tool == "write_file":
                text = "完整写入内容：\n" + str(self._arguments.get("content", ""))
            elif not self._external and self._tool == "edit_file":
                # 只比较当前调用已提供的原文与新文，不为预览读取文件。
                old = str(self._arguments.get("old_text", ""))
                new = str(self._arguments.get("new_text", ""))
                old_lines, new_lines = old.split("\n"), new.split("\n")
                diff = difflib.unified_diff(old_lines, new_lines, fromfile="old_text", tofile="new_text",
                                            n=max(len(old_lines), len(new_lines)), lineterm="")
                text = "完整编辑差异（仅当前调用提供的原文与新文）：\n" + "\n".join(diff)
                text += "\n原文末尾换行> " + ("有" if old.endswith("\n") else "无")
                text += "\n新文末尾换行> " + ("有" if new.endswith("\n") else "无")
            else:
                text = "此操作无本地写入内容或编辑差异；完整有效参数见 arguments。"
        else:
            raise ValueError(f"未知审批详情部分：{section}")
        return self._safe(text)

    def page(self, section: str, index: int, *, width: int, height: int) -> tuple[str, int, int]:
        """按实际字符显示宽度折行；页码从零开始，尺寸改变后重新布局。"""
        return self.page_text(self.detail(section), index, width=width, height=height)

    @staticmethod
    def page_text(text: str, index: int, *, width: int, height: int) -> tuple[str, int, int]:
        """分页已经过安全展示处理的文字，供审批及旁路结果共用。"""
        # 单个汉字通常占两列；仅一列时仍保留字形，不丢弃不可容纳的字符。
        width, height = max(2, width), max(1, height)
        rows: list[str] = []
        for line in text.split("\n"):
            row, used = "", 0
            for char in line.expandtabs(4):
                size = max(0, get_cwidth(char))
                if row and used + size > width:
                    rows.append(row)
                    row, used = "", 0
                row += char
                used += size
            rows.append(row)
        total = max(1, (len(rows) + height - 1) // height)
        index = min(max(0, index), total - 1)
        return "\n".join(rows[index * height:(index + 1) * height]), index, total
