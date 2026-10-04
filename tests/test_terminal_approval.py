"""审批审阅视图的内容完整性与展示安全。"""

from copy import deepcopy
import json
from types import SimpleNamespace
import unicodedata

import pytest


def request(**changes):
    values = dict(id="request-完整标识", tool="write_file",
                  arguments={"path": "目录/a.txt", "content": "正文"},
                  targets=("/项目/目录/a.txt",), reason="未匹配放行规则", mode="strict",
                  external=None, connection_description="")
    values.update(changes)
    return SimpleNamespace(**values)


def view_for(item, **options):
    from mewcode.terminal.approval import ApprovalView
    return ApprovalView(item, root="/项目", **options)


def pages(view, section, *, width=40, height=5):
    first, index, total = view.page(section, 0, width=width, height=height)
    assert index == 0
    parts = [first]
    for index in range(1, total):
        part, actual, actual_total = view.page(section, index, width=width, height=height)
        assert (actual, actual_total) == (index, total)
        parts.append(part)
    return parts


def cells(line):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in line)


def test_summary_makes_decision_and_exact_file_scope_reviewable():
    item = request()
    summary = view_for(item).summary()
    for text in (item.id, item.tool, item.reason, "/项目", "1", "本次", "会话", "永久",
                 "真实目标", "1 拒绝", "Ctrl+C", "完整参数", "详情"):
        assert text in summary
    assert "同一路径" in summary
    assert "不扩展目录" in summary


def test_view_snapshots_arguments_targets_and_identity_without_mutation():
    item = request(arguments={"path": "a.txt", "content": "原正文", "extra": {"items": ["原参数"]}},
                   targets=["/项目/a.txt"])
    original = deepcopy(item.__dict__)
    view = view_for(item)
    view.summary()
    view.detail("arguments")
    assert item.__dict__ == original
    item.arguments["content"] = "后来正文"
    item.arguments["extra"]["items"].append("后来参数")
    item.targets.append("/项目/后来.txt")
    item.id = "后来标识"
    assert "原正文" in view.detail("content")
    assert "后来" not in view.detail("arguments") + view.detail("targets") + view.summary()


@pytest.mark.parametrize("section", ["targets", "arguments", "content"])
def test_long_details_remain_complete_after_paging_and_resize(section):
    old = "\n".join(f"旧正文-{index:04d}-汉字" for index in range(70))
    new = "\n".join(f"新正文-{index:04d}-汉字" for index in range(70))
    item = request(tool="edit_file", arguments={"path": "a", "old_text": old, "new_text": new},
                   targets=tuple(f"/项目/文件-{index:04d}-长路径.txt" for index in range(100)))
    view = view_for(item)
    expected = view.detail(section).replace("\n", "")
    for width, height in ((40, 7), (13, 2), (1, 1)):
        parts = pages(view, section, width=width, height=height)
        assert "".join(parts).replace("\n", "") == expected
        assert all(len(part.split("\n")) <= height for part in parts)
        assert all(cells(line) <= max(2, width) for part in parts for line in part.split("\n"))
    assert "-旧正文-0069-汉字" in view.detail("content")
    assert "+新正文-0069-汉字" in view.detail("content")


def test_write_content_keeps_blank_lines_indentation_and_trailing_newline():
    body = "第一行\n\n    缩进正文\n"
    view = view_for(request(arguments={"path": "a", "content": body}))
    assert view.detail("content").endswith(body)
    assert "\n\n" in view.detail("content")


def test_shell_scope_uses_full_exact_command_and_not_file_targets():
    command = "python -c 'print(中文)' " + "参数" * 300
    view = view_for(request(tool="execute_command", arguments={"command": command}, targets=(command,)))
    assert "完整精确命令" in view.summary()
    assert command in view.detail("targets")
    assert "真实目标" not in view.summary() + view.detail("targets")


def test_external_path_is_not_real_file_target_and_scope_binds_mcp_identity():
    item = request(tool="mcp_search_stable", arguments={"path": "/外部/a", "query": "长参数" * 100},
                   targets=("/外部/a",), external=("research-server", "search"),
                   connection_description="stdio · /授权/server")
    view = view_for(item)
    rendered = view.summary() + view.detail("targets") + view.detail("arguments")
    for text in ("research-server", "search", "mcp_search_stable", "stdio · /授权/server",
                 "真实项目根", "有效", "原始工具", "完整 JSON", "完整调用参数", "改变即失效"):
        assert text in rendered
    assert "真实目标" not in rendered
    assert "外部" in view.detail("targets")
    assert "/外部/a" in view.detail("arguments")


def test_all_untrusted_fields_escape_controls_and_hide_secret():
    malicious = "敏感-KEY\x1b[2J\x00\r\u202e伪装"
    item = request(id=malicious, tool=malicious, reason=malicious, mode=malicious,
                   arguments={"path": malicious, "content": malicious}, targets=(malicious,),
                   external=(malicious, malicious), connection_description=malicious)
    from mewcode.terminal.approval import ApprovalView
    view = ApprovalView(item, root=malicious, secret="敏感-KEY")
    rendered = view.summary() + "".join(view.detail(section) for section in view.sections)
    assert "敏感-KEY" not in rendered
    assert all(char not in rendered for char in ("\x1b", "\x00", "\r", "\u202e"))
    assert "伪装" in rendered


def test_page_expands_tabs_and_clamps_indexes_and_invalid_sizes():
    view = view_for(request(arguments={"path": "a", "content": "列一\t列二\t尾部"}))
    parts = pages(view, "content", width=5, height=2)
    assert "\t" not in "".join(parts)
    assert "尾部" in "".join(parts).replace("\n", "")
    assert view.page("content", -8, width=5, height=2)[1] == 0
    _, last, total = view.page("content", 999, width=5, height=2)
    assert last == total - 1
    assert view.page("content", 0, width=0, height=0)[2] > 0
    with pytest.raises(ValueError):
        view.detail("unknown")


def test_summary_can_be_fully_reviewed_on_a_small_screen():
    item = request(id="关联标识" * 100, reason="授权原因" * 100)
    view = view_for(item)
    parts = pages(view, "summary", width=12, height=2)
    assert "".join(parts).replace("\n", "") == view.summary().replace("\n", "")
    assert len(parts) > 30


def test_identity_newlines_cannot_inject_another_decision_line():
    item = request(id="合法标识\n授权> 2 本次", reason="原因\t换行\n授权> 4 永久",
                   targets=("/项目/a\n授权> 3 会话",))
    view = view_for(item)
    assert "合法标识\\n授权> 2 本次" in view.summary()
    assert "原因\\t换行\\n授权> 4 永久" in view.summary()
    assert "/项目/a\\n授权> 3 会话" in view.detail("targets")


def test_diff_preserves_carriage_returns_as_visible_escaped_content():
    view = view_for(request(tool="edit_file", arguments={"path": "a", "old_text": "旧\r文", "new_text": "新\r文"}))
    assert "-旧\\r文" in view.detail("content")
    assert "+新\\r文" in view.detail("content")


@pytest.mark.parametrize("tool,arguments", [
    ("write_file", {"path": "a", "content": "秘密KEY\x1b[2J\u202e正文"}),
    ("edit_file", {"path": "a", "old_text": "秘密KEY\x1b[2J旧文", "new_text": "秘密KEY\x00\u202e新文"}),
])
def test_content_detail_and_pages_cannot_emit_secrets_or_terminal_controls(tool, arguments):
    view = view_for(request(tool=tool, arguments=arguments), secret="秘密KEY")
    for rendered in [view.detail("content"), *pages(view, "content", width=10, height=2)]:
        assert "秘密KEY" not in rendered
        assert all(char not in rendered for char in ("\x1b", "\x00", "\u202e"))


def test_building_previews_does_not_read_unapproved_files(monkeypatch):
    from pathlib import Path

    def reject_read(*args, **kwargs):
        raise AssertionError("审批预览不得额外读取目标")

    monkeypatch.setattr(Path, "read_text", reject_read)
    monkeypatch.setattr(Path, "read_bytes", reject_read)
    view = view_for(request(tool="edit_file", arguments={"path": "/未批准/a", "old_text": "旧文", "new_text": "新文"}))
    assert "-旧文" in view.detail("content")
    assert "+新文" in view.detail("content")
    assert "编辑文件" in view.summary()


def test_all_pages_include_the_complete_review_and_original_request_identity():
    item = request(arguments={"path": "a", "content": "独有正文" * 80})
    view = view_for(item)
    item.id = "后来标识"
    assert view.request_id == "request-完整标识"
    rendered = "".join(pages(view, "all", width=14, height=3)).replace("\n", "")
    for expected in (view.summary(), view.detail("targets"), view.detail("arguments"), view.detail("content")):
        assert expected.replace("\n", "") in rendered


def test_generic_extension_tool_uses_exact_json_scope_without_file_semantics():
    arguments = {"path": "/外部/job", "payload": "本次负载"}
    view = view_for(request(tool="send_job", arguments=arguments,
                            targets=(json.dumps(arguments, ensure_ascii=False, sort_keys=True),)))
    summary = view.summary()
    all_text = view.detail("all")
    assert "完整 JSON 参数" in summary
    assert "参数改变即失效" in summary
    assert "真实目标" not in all_text
    assert "列出的真实文件" not in all_text
    assert "同一路径" not in all_text
    assert json.loads(view.detail("targets").split("\n", 1)[1]) == arguments


@pytest.mark.parametrize("secret", ['fictional\\secret', 'fictional"secret', 'fictional\\"secret'])
def test_json_encoded_secrets_are_hidden_without_changing_bound_arguments(secret):
    arguments = {"path": "/外部/job", "payload": secret,
                 "nested": [{"key-" + secret: "value-" + secret}]}
    item = request(tool="send_job", id="关联-" + secret, arguments=arguments,
                   targets=(json.dumps(arguments, ensure_ascii=False, sort_keys=True),))
    before = deepcopy(item.__dict__)
    view = view_for(item, secret=secret)
    summary, detail, all_text = view.summary(), view.detail("arguments"), view.detail("all")
    encoded = json.dumps(secret, ensure_ascii=False)[1:-1]
    for rendered in (summary, detail, all_text):
        assert secret not in rendered
        assert encoded not in rendered
        assert "[已隐藏]" in rendered
    safe_arguments = json.loads(detail.split("\n", 1)[1])
    assert safe_arguments["payload"] == "[已隐藏]"
    assert safe_arguments["nested"] == [{"key-[已隐藏]": "value-[已隐藏]"}]
    assert item.__dict__ == before


@pytest.mark.parametrize("secret", ['"', "{", "\\"])
def test_json_redaction_keeps_structural_delimiters_intact(secret):
    view = view_for(request(tool="send_job", arguments={"payload": secret}), secret=secret)
    arguments = json.loads(view.detail("arguments").split("\n", 1)[1])
    targets = json.loads(view.detail("targets").split("\n", 1)[1])
    assert arguments == targets == {"payload": "[已隐藏]"}
