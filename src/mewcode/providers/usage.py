"""协议字段归一化：只采纳服务实际报告的非负整数。"""

from ..types import TokenUsage


def reported(source, name):
    fields = getattr(source, 'model_fields_set', None)
    if fields is not None and name not in fields:
        return None
    return getattr(source, name, None)


def update_counts(counts: dict[str, int], source, names) -> None:
    for name in names:
        value = reported(source, name)
        if type(value) is int and value >= 0:
            counts[name] = value


def _usage(base, output, total, hit, miss, write, *, complete=False):
    valid = (total is not None and hit is not None and 0 <= hit <= total
             and (miss is None or hit + miss == total)
             and (write is None or (miss is not None and write <= miss)))
    return TokenUsage(input_tokens=base, output_tokens=output, complete=complete,
                      total_input_tokens=total, cache_read_tokens=hit, cache_miss_tokens=miss,
                      cache_write_tokens=write, cache_complete=valid)


def anthropic_usage(counts: dict[str, int], source, *, service: str) -> TokenUsage:
    update_counts(counts, source, ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                  'cache_creation_input_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'))
    base, output = counts.get('input_tokens'), counts.get('output_tokens')
    hit, write = counts.get('cache_read_input_tokens'), counts.get('cache_creation_input_tokens')
    total = miss = None
    explicit_hit, explicit_miss = counts.get('prompt_cache_hit_tokens'), counts.get('prompt_cache_miss_tokens')
    if service == 'deepseek':
        # 实测兼容 usage：input_tokens 是未命中，read 是命中。
        # creation=0 无法证明自动缓存的实际写入量，保守标为未知。
        hit = explicit_hit if explicit_hit is not None else hit
        miss = explicit_miss if explicit_miss is not None else base
        total = miss + hit if miss is not None and hit is not None else None
        write = None
    elif service == 'claude':
        miss = base + write if base is not None and write is not None else None
        total = miss + hit if miss is not None and hit is not None else None
    else:
        hit = explicit_hit if explicit_hit is not None else hit
        miss = explicit_miss
        if explicit_hit is not None and explicit_miss is not None:
            total, write = explicit_hit + explicit_miss, None
    return _usage(base, output, total, hit, miss, write)


def openai_usage(counts: dict[str, int], source) -> TokenUsage:
    update_counts(counts, source, ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'))
    details = reported(source, 'prompt_tokens_details')
    if details is not None:
        update_counts(counts, details, ('cached_tokens',))
    base, output = counts.get('prompt_tokens'), counts.get('completion_tokens')
    total = base
    explicit_hit, explicit_miss = counts.get('prompt_cache_hit_tokens'), counts.get('prompt_cache_miss_tokens')
    hit = explicit_hit if explicit_hit is not None else counts.get('cached_tokens')
    miss = explicit_miss
    if total is None and hit is not None and miss is not None:
        total = hit + miss
    if miss is None and total is not None and hit is not None and hit <= total:
        miss = total - hit
    if hit is None and total is not None and miss is not None and miss <= total:
        hit = total - miss
    return _usage(base, output, total, hit, miss, None, complete=base is not None and output is not None)
