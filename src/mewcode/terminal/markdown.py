"""保留原文的轻量增量样式，不解释 HTML、链接或终端转义。"""

import re


class MarkdownStyle:
    def __init__(self):
        self.fence = ''
        self.language = ''

    def render(self, text, *, commit=True):
        """完整行提交围栏状态，未完成行只预览；复制得到原始代码。"""
        fence, language = self.fence, self.language
        fragments = []
        for line in text.splitlines(keepends=True):
            content = line.removeprefix('MewCode> ').lstrip()
            marker = re.match(r'(`{3,}|~{3,})([^\n]*)', content)
            style = ''
            if marker:
                style = 'ansibrightblack'
                if not fence:
                    fence, language = marker[1], marker[2].strip().lower()
                elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                    fence, language = '', ''
            elif fence:
                style = 'ansicyan'
                if language in {'diff', 'patch'}:
                    if line.startswith('+'):
                        style = 'ansigreen'
                    elif line.startswith('-'):
                        style = 'ansired'
                    elif line.startswith('@@'):
                        style = 'bold ansicyan'
            elif re.match(r'#{1,6}\s', content):
                style = 'bold ansicyan'
            elif re.match(r'(?:[-*+] |\d+[.)] )', content):
                style = 'ansiblue'
            fragments.append((style, line))
        if commit:
            self.fence, self.language = fence, language
        return fragments
