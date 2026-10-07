"""增量样式保留原文，只有渲染器可以产生格式。"""

from io import StringIO
from mewcode.app import _Renderer
from mewcode.types import AgentEvent


def test_markdown_styles_preserve_heading_list_code_diff_and_unclosed_fence():
    from mewcode.terminal.markdown import MarkdownStyle
    style = MarkdownStyle()
    text = '# 标题\n- 列表\n```diff\n-旧\n+新\n'
    fragments = style.render(text)
    assert ''.join(text for _, text in fragments) == text
    assert any('bold' in token for token, _ in fragments)
    assert any('ansigreen' in token for token, _ in fragments)
    assert any('ansired' in token for token, _ in fragments)
    partial = style.render('未关闭的代码', commit=False)
    assert partial[0][0] and partial[0][1] == '未关闭的代码'
    style.render('```\n')
    assert style.render('普通文字')[0][0] == ''


def test_split_secrets_controls_and_raw_markdown_remain_safe_in_plain():
    output = StringIO()
    renderer = _Renderer(output, 'private-key')
    for text in ['# 标题\n```python\n', 'private-', 'key\x1b]0;title\x07\n', 'print(1)\n```']:
        renderer.show(AgentEvent('text_delta', text=text))
        assert 'private-' not in output.getvalue()
    renderer.line('结束')
    result = output.getvalue()
    assert result.count('print(1)') == 1 and '[已隐藏]' in result
    assert '\x1b' not in result and '\x07' not in result
    assert '```python' in result and '\\x1b]0;title\\x07' in result


from mewcode.terminal.markdown import MarkdownStyle
from prompt_toolkit.utils import get_cwidth


def visible(fragments):
    return ''.join(text for _, text in fragments)


def test_complete_inline_formatting_removes_delimiters_with_distinct_styles():
    fragments = MarkdownStyle().render('**粗体** 和 __重点__ 与 `file_name.py`\n')
    assert visible(fragments) == '粗体 和 重点 与 file_name.py\n'
    assert any('bold' in token and text == '粗体' for token, text in fragments)
    assert any('bold' in token and text == '重点' for token, text in fragments)
    assert any('italic' in token and text == 'file_name.py' for token, text in fragments)


def test_inline_preview_is_raw_and_does_not_commit_partial_delimiters():
    style = MarkdownStyle()
    for text in ['*', '**粗', '**粗体*']:
        assert visible(style.render(text, commit=False)) == text
    assert visible(style.render('**粗体**\n')) == '粗体\n'
    assert visible(style.render('**未闭合\n')) == '**未闭合\n'
    assert visible(style.render('`未闭合\n')) == '`未闭合\n'


def test_inline_escaping_identifiers_and_code_are_not_reinterpreted():
    text = r'\**普通** foo__bar__baz snake_name `**原样** a\b | c` ``a`b``'
    fragments = MarkdownStyle().render(text)
    assert visible(fragments) == r'**普通** foo__bar__baz snake_name **原样** a\b | c a`b'
    assert not any('bold' in token for token, _ in fragments)


def test_fenced_code_preserves_every_character_and_flush_resets_style():
    style = MarkdownStyle()
    style.render('```python\n')
    code = '    **literal** = `value`\n\n\tprint("x|y")\n'
    assert visible(style.render(code)) == code
    assert all('bold' not in token for token, _ in style.render('**仍是代码**\n'))
    assert visible(style.flush()) == ''
    assert style.render('系统提示\n') == [('', '系统提示\n')]


def test_table_candidate_is_immediately_visible_and_flushes_once():
    style = MarkdownStyle()
    header = '| 名称 | 路径 |\n'
    assert style.render(header, width=40) == []
    assert visible(style.render('', commit=False, width=40)) == header
    assert visible(style.render('| --', commit=False, width=40)) == header + '| --'
    assert visible(style.flush(width=40)) == header
    assert style.flush() == []


def test_invalid_table_candidate_and_malformed_row_fall_back_to_raw():
    style = MarkdownStyle()
    header = '| 名称 | 路径 |\n'
    assert style.render(header) == []
    assert visible(style.render('并非分隔行\n')) == header + '并非分隔行\n'
    style.render(header)
    style.render('| --- | --- |\n')
    broken = '| 只有一列 |\n'
    assert visible(style.render(broken)) == broken
    assert visible(style.render('普通正文\n')) == '普通正文\n'


def test_table_handles_escaped_and_code_pipes_without_losing_column_links():
    style = MarkdownStyle()
    style.render('| 名称 | 表达式 |\n', width=12)
    style.render('| --- | --- |\n', width=12)
    fragments = style.render('| a\\|b | `x|y` |\n', width=12)
    assert visible(fragments) == '名称：a|b\n表达式：x|y\n'
    assert any('italic' in token and text == 'x|y' for token, text in fragments)


def test_wide_table_wraps_chinese_and_long_paths_without_truncation():
    style = MarkdownStyle()
    style.render('| 中文 | 路径 |\n', width=25)
    header = style.render('| --- | --- |\n', width=25)
    row = style.render('| 中文内容中文内容 | /very/long/path/to/file.py |\n', width=25)
    assert all(get_cwidth(line) <= 25 for line in visible(header + row).splitlines())
    lines = visible(row).splitlines()
    assert len(lines) > 1
    left, right = zip(*(line.split(' │ ') for line in lines))
    assert ''.join(part.strip() for part in left) == '中文内容中文内容'
    assert ''.join(part.strip() for part in right) == '/very/long/path/to/file.py'
    assert '…' not in visible(row)


def test_table_resize_changes_only_subsequent_rows_to_labelled_fields():
    style = MarkdownStyle()
    style.render('| 名称 | 路径 |\n', width=40)
    first = style.render('| --- | --- |\n| 第一项 | /a |\n', width=40)
    assert ' │ ' in visible(first)
    second = style.render('| 第二项 | /b |\n', width=8)
    assert visible(second) == '名称：第二项\n路径：/b\n'
    assert '第一项' not in visible(second)
    third = style.render('| 第三项 | /c |\n', width=40)
    assert ' │ ' in visible(third)
    assert '第二项' not in visible(third)


def test_oversized_table_candidate_is_emitted_as_raw_without_retention():
    style = MarkdownStyle()
    text = '| 中文' + '字' * 23000 + ' | 路径 |\n'
    assert visible(style.render(text)) == text
    assert style.render('', commit=False) == []
    assert style.flush() == []


def test_table_releases_state_at_regular_prose_and_keeps_prefix():
    style = MarkdownStyle()
    style.render('MewCode> | 名称 | 数值 |\n', width=30)
    confirmed = style.render('| --- | --- |\n', width=30)
    assert visible(confirmed).startswith('MewCode> ')
    style.render('| 项目 | 1 |\n', width=30)
    assert visible(style.render('结束。\n')) == '结束。\n'
    assert visible(style.render('后续 **正文**\n')) == '后续 正文\n'


def test_oversized_candidate_preserves_inline_delimiters_in_raw_fallback():
    style = MarkdownStyle()
    text = '| **原样** | ' + 'x' * (64 * 1024) + ' |\n'
    assert visible(style.render(text)) == text
    assert style.flush() == []


def test_invalid_table_separator_keeps_header_and_separator_in_order():
    style = MarkdownStyle()
    header = '| 名称 | 数值 |\n'
    separator = '| --- |\n'
    style.render(header)
    assert visible(style.render(separator)) == header + separator
    assert style.flush() == []


def test_table_candidate_flush_does_not_parse_unconfirmed_header_inline():
    style = MarkdownStyle()
    header = '| **名称** | `路径` |\n'
    style.render(header)
    assert visible(style.flush()) == header


def test_very_long_fence_does_not_accept_shorter_closing_marker():
    style = MarkdownStyle()
    marker = '`' * (64 * 1024 + 1)
    style.render(marker + '\n')
    style.render('`' * (64 * 1024) + '\n')
    assert any('italic' in token for token, _ in style.render('仍是代码\n'))
    style.flush()
    assert style.render('普通正文\n') == [('', '普通正文\n')]


def test_single_column_table_wraps_and_retains_all_content():
    style = MarkdownStyle()
    assert style.render('| 名称 |\n', width=12) == []
    header = style.render('| --- |\n', width=12)
    assert '名称' in visible(header)
    row = style.render('| 很长的中文名称很长的中文名称 |\n', width=12)
    assert ''.join(line.strip() for line in visible(row).splitlines()) == '很长的中文名称很长的中文名称'
    assert all(get_cwidth(line) <= 12 for line in visible(row).splitlines())


def test_role_prefix_does_not_push_first_table_row_beyond_current_width():
    style = MarkdownStyle()
    style.render('MewCode> | 名称 | 数值 |\n', width=20)
    header = style.render('| --- | --- |\n', width=20)
    assert visible(header).startswith('MewCode> ')
    assert all(get_cwidth(line) <= 20 for line in visible(header).splitlines())


def test_many_unclosed_inline_markers_remain_complete_raw_text():
    text = '**x ' * 200 + '__y ' * 200 + '`结束\n'
    assert visible(MarkdownStyle().render(text)) == text


def test_bold_closing_marker_inside_inline_code_does_not_end_bold():
    fragments = MarkdownStyle().render('**a `**` b**\n')
    assert visible(fragments) == 'a ** b\n'
    assert any('bold' in token and 'italic' in token and text == '**' for token, text in fragments)
