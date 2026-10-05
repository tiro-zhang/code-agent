"""有界且不可变的工作树初始化清单。"""

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from ..tools.base import ToolError
from .paths import read_file


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError('配置字段必须为唯一字符串')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


@dataclass(frozen=True)
class PathItem:
    path: str
    required: bool = False


@dataclass(frozen=True)
class WorktreeConfig:
    copy_files: tuple[PathItem, ...] = (PathItem('.env'),)
    link_directories: tuple[PathItem, ...] = (PathItem('.venv'), PathItem('node_modules'))
    copy_ignored_files: tuple[PathItem, ...] = ()
    regenerable_paths: tuple[str, ...] = ('.mewcode/context/**', '.pytest_cache/**', '**/__pycache__/*.pyc')
    hooks_path: str | None = None
    ttl_days: int = 30
    interval_seconds: int = 1800


def relative_path(value, *, pattern=False):
    """相对规则不含穿越段；通配符也不能覆盖任意目录。"""
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError('清单路径为空或超长')
    parts = value.split('/')
    chars = r'[A-Za-z0-9_.*/?\[\]-]+' if pattern else r'[A-Za-z0-9_./-]+'
    if len(parts) > 16 or re.fullmatch(chars, value) is None or any(p in {'', '.', '..'} for p in parts):
        raise ValueError('清单必须使用安全项目相对路径')
    if pattern and (value in {'*', '**'} or parts[-1] == '**'):
        raise ValueError('不能将任意全目录作为规则')
    protected = ('.mewcode/context', '.mewcode/sessions', '.mewcode/memory', '.mewcode/permissions.local.yaml', '.mewcode/worktrees', '.mewcode/worktree-state', '.git')
    if not pattern and any(value == p or value.startswith(p + '/') for p in protected):
        raise ValueError('运行存储及批准记录不能通过初始化搬迁')
    return value


def regenerable_pattern(value):
    """再生产物必须限定文件名，只有公开的专属缓存规则可覆盖目录。"""
    if value in WorktreeConfig().regenerable_paths:
        return value
    relative_path(value, pattern=True)
    parts = value.split('/')
    literal = re.sub(r'\[[^]]*\]', '', parts[-1]).replace('*', '').replace('?', '')
    if not literal.strip('.') or (len(parts) > 1 and re.search(r'[*?\[\]]', parts[0])):
        raise ValueError('可再生规则不能覆盖任意全目录')
    return value


def _items(data, field, default, *, pattern=False):
    values = data.get(field, default)
    if values is default:
        return default
    if not isinstance(values, list) or len(values) > 512:
        raise ValueError('清单必须为有界列表')
    result = []
    key = 'pattern' if pattern else 'path'
    for item in values:
        if not isinstance(item, dict) or set(item) - {key, 'required'}:
            raise ValueError('清单项字段无效')
        path = relative_path(item.get(key), pattern=pattern)
        required = item.get('required', False)
        if type(required) is not bool:
            raise ValueError('required 必须为布尔值')
        result.append(PathItem(path, required))
    if len({item.path for item in result}) != len(result):
        raise ValueError('清单路径重复')
    return tuple(result)


def load_config(root: Path) -> WorktreeConfig:
    path = Path(root) / '.mewcode/worktrees.yaml'
    defaults = WorktreeConfig()
    try:
        try:
            raw = read_file(path, limit=65536)
        except FileNotFoundError:
            return defaults
        data = yaml.load(raw.decode('utf-8'), Loader=StrictLoader)
        fields = {'version', 'copy-files', 'link-directories', 'copy-ignored-files', 'regenerable-paths', 'hooks-path', 'cleanup'}
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError('配置必须为映射且不含未知字段')
        version = data.get('version', 1)
        if type(version) is not int or version != 1:
            raise ValueError('仅支持 version 1')
        cleanup = data.get('cleanup', {})
        if not isinstance(cleanup, dict) or set(cleanup) - {'ttl-days', 'interval-seconds'}:
            raise ValueError('cleanup 字段无效')
        ttl, interval = cleanup.get('ttl-days', 30), cleanup.get('interval-seconds', 1800)
        if type(ttl) is not int or type(interval) is not int or min(ttl, interval) <= 0:
            raise ValueError('清理周期必须为正整数')
        regen = defaults.regenerable_paths
        if 'regenerable-paths' in data:
            values = data['regenerable-paths']
            if not isinstance(values, list) or len(values) > 512:
                raise ValueError('可再生规则必须为有界列表')
            regen = tuple(regenerable_pattern(p) for p in values)
        hooks = relative_path(data['hooks-path']) if 'hooks-path' in data else None
        return WorktreeConfig(_items(data, 'copy-files', defaults.copy_files),
                              _items(data, 'link-directories', defaults.link_directories),
                              _items(data, 'copy-ignored-files', (), pattern=True), regen, hooks, ttl, interval)
    except (OSError, ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise ToolError('worktree_config_error', '工作树初始化配置无效或无法安全读取', not_started=True) from None
