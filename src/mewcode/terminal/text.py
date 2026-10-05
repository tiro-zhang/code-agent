"""所有终端展示共用的脱敏与用量文字。"""

from ..types import TokenUsage
import json
import re


def redact_json(text: str, secret: str) -> str:
    """在 JSON 字符串解码后脱敏，保留键值结构和重复字段。"""
    if not secret:
        return text
    def hide(match):
        try:
            value = json.loads(match[0])
        except ValueError:
            return match[0]
        return json.dumps(value.replace(secret, '[已隐藏]'), ensure_ascii=False)
    return re.sub(r'"(?:\\.|[^"\\])*"', hide, text)


class StreamText:
    """暂存可能构成密钥的尾缀，避免分片到达时泄露密钥前半段。"""

    def __init__(self, secret: str):
        self.secret, self.pending = secret, ''

    def feed(self, text: str, *, final: bool = False) -> str:
        value = self.pending + text
        self.pending = ''
        if self.secret:
            value = value.replace(self.secret, '[已隐藏]')
            if not final:
                for count in range(min(len(value), len(self.secret) - 1), 0, -1):
                    if value.endswith(self.secret[:count]):
                        self.pending, value = value[-count:], value[:-count]
                        break
        return terminal_text(value, '', multiline=True)


def terminal_text(text: str, secret: str, *, limit: int | None = None,
                  multiline: bool = False) -> str:
    """先脱敏再转义控制字符；正文可保留换行和制表符。"""
    text = text.replace(secret, "[已隐藏]") if secret else text
    escaped = "".join(char if char.isprintable() or (multiline and char in "\n\t")
                      else repr(char)[1:-1] for char in text)
    return escaped if limit is None or len(escaped) <= limit else escaped[:max(0, limit)] + "…"


def usage_text(usage: TokenUsage) -> str:
    """保留服务实际提供的输入、输出与缓存完整性标记。"""
    def count(field: str) -> str:
        value = getattr(usage, field)
        if value is None:
            return "未知"
        return str(value) + ("（部分）" if field in usage.incomplete_fields else "")

    incoming = f"总输入 {count('total_input_tokens')}"
    if usage.total_input_tokens is None:
        incoming += f"（基础输入 {count('input_tokens')}）"
    ratio = usage.cache_hit_rate
    rate = "未知" if ratio is None else f"{ratio:.1%}"
    base = "统计完整" if usage.complete else "统计不完整"
    cache = "缓存统计完整" if usage.cache_complete else "缓存统计不完整"
    return (f"{incoming}，输出 {count('output_tokens')} · {base} · "
            f"命中 {count('cache_read_tokens')}，未命中 {count('cache_miss_tokens')}，"
            f"写入 {count('cache_write_tokens')}，命中率 {rate} · {cache}")
