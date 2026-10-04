"""所有终端展示共用的脱敏与用量文字。"""

from ..types import TokenUsage


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
