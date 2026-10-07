"""将已有工具快照转换为可读正文；不加载文件或外部引用。"""

import difflib
import json
from typing import Any

from .text import redact_json, terminal_text


def _safe_value(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return terminal_text(value, secret, multiline=True)
    if isinstance(value, dict):
        return {terminal_text(str(key), secret): _safe_value(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item, secret) for item in value]
    return value


def _snapshot(value: Any, secret: str) -> tuple[Any, str, bool]:
    if value is None:
        return None, '', True
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return None, terminal_text(redact_json(value, secret), secret, multiline=True), False
    else:
        decoded = value
    try:
        safe = _safe_value(decoded, secret)
        raw = terminal_text(redact_json(value, secret), secret, multiline=True) if isinstance(value, str) else json.dumps(safe, ensure_ascii=False, indent=2)
    except (ValueError, TypeError, RecursionError):
        return None, terminal_text(redact_json(value, secret), secret, multiline=True) if isinstance(value, str) else '结构快照无法安全解析', False
    return safe, raw, True


def _readable(value: Any, depth: int = 0) -> str:
    """结构数据保持字段与文本块换行，外部路径仅作为普通字段。"""
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.extend([f'{key}：', _readable(item, depth + 1)])
            else:
                lines.append(f'{key}：{_readable(item, depth + 1)}')
        return '\n'.join(lines) or '（空对象）'
    if isinstance(value, list):
        return '\n'.join(_readable(item, depth + 1) for item in value) or '（空列表）'
    if value is None:
        return '（空结果）'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


def format_result(name: str, arguments: Any, result: Any, *, raw: bool = False,
                  secret: str = '', arguments_truncated: bool = False,
                  result_truncated: bool = False, arguments_evicted: bool = False,
                  result_evicted: bool = False) -> str:
    """只格式化保留快照，缺失与截断均明确标记；原始入口同样安全。"""
    args, args_raw, args_valid = _snapshot(arguments, secret)
    response, result_raw, result_valid = _snapshot(result, secret)
    lines = []
    if arguments_evicted:
        lines.append('参数正文已移出（容量限制），不能恢复')
    elif arguments_truncated:
        lines.append('参数快照不完整：已截断（容量限制）')
    if result_evicted:
        lines.append('结果正文已移出（容量限制），不能恢复')
    elif result_truncated or (isinstance(response, dict) and response.get('truncated')):
        lines.append('结果已截断，仅搜索和展示保留片段')
    if raw:
        lines.extend(['参数 · 原始 JSON', args_raw or '未采集', '结果 · 原始 JSON', result_raw or '未采集'])
        if not args_valid or not result_valid:
            lines.append('快照无法解析；保留原始片段，不补全 JSON')
        return '\n'.join(lines)

    args = args if isinstance(args, dict) else {}
    lines.extend(['参数', args_raw or '未采集'])
    if not args_valid:
        lines.append('参数快照不完整，无法解析 JSON')
    if result is None:
        lines.append('结果未采集' if not result_evicted else '结果仅保留关联状态')
        return '\n'.join(lines)
    if not result_valid:
        lines.extend(['结果快照不完整，无法解析 JSON', result_raw])
        return '\n'.join(lines)
    data = response.get('data') if isinstance(response, dict) and 'data' in response else response
    fields = data if isinstance(data, dict) else {}
    if name == 'read_file':
        lines.append(f"路径：{fields.get('path', args.get('path', '未提供'))}")
        if 'start_line' in fields or 'end_line' in fields:
            lines.append(f"已有行范围：{fields.get('start_line', '?')}–{fields.get('end_line', '?')}")
        lines.extend(['文件内容', str(fields.get('content', '内容未采集'))])
    elif name == 'execute_command':
        lines.append(f"命令：{args.get('command', '未提供')}")
        lines.append(f"退出码：{fields.get('exit_code', '未采集')}")
        lines.extend(['stdout', str(fields.get('stdout', '未采集')), 'stderr', str(fields.get('stderr', '未采集'))])
    elif name == 'write_file':
        lines.append(f"路径：{args.get('path', '未提供')}")
        lines.extend(['写入内容快照（执行状态另见实际结果）', str(args.get('content', '内容快照不完整／未采集'))])
    elif name == 'edit_file':
        lines.append(f"路径：{args.get('path', '未提供')}")
        old, new = args.get('old_text'), args.get('new_text')
        if arguments_truncated or arguments_evicted or not isinstance(old, str) or not isinstance(new, str):
            lines.append('编辑参数快照不完整，无法展示可靠局部差异')
        else:
            lines.append('局部 diff（仅依据调用参数 old_text／new_text）')
            lines.extend(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                             fromfile='原文片段', tofile='新文片段', lineterm=''))
            if old and not old.endswith('\n'):
                lines.append('\\ 原文末尾无换行')
            if new and not new.endswith('\n'):
                lines.append('\\ 新文末尾无换行')
    lines.extend(['实际结果', _readable(response)])
    return '\n'.join(lines)
