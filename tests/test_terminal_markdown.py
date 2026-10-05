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
