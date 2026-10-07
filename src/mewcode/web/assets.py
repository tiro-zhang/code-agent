"""校验随包分发的前端清单；开发源码存在时拒绝陈旧构建。"""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


_EXCLUDED = {'node_modules', 'test-results', 'playwright-report', '.git'}
_BUILD_HINT = '请在 frontend 运行 npm ci && npm run build'


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def _files(root, *, exclusions=(), skip=()):
    result = {}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            if path.name in exclusions:
                continue
            if path.is_symlink():
                raise ValueError('静态资源或构建源码包含符号链接')
            if path.is_dir():
                visit(path)
            elif path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative not in skip:
                    result[relative] = _digest(path)
            else:
                raise ValueError('静态资源或构建源码包含非普通文件')
    visit(root)
    return dict(sorted(result.items()))


def _mapping(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('构建清单的文件表无效')
    for name, digest in value.items():
        if (not isinstance(name, str) or not name or '\\' in name or '\0' in name
                or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or str(PurePosixPath(name)) != name
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('构建清单包含非法路径或摘要')
    return dict(sorted(value.items()))


def _aggregate(sources):
    return hashlib.sha256(''.join(f'{path}\0{digest}\n' for path, digest in sorted(sources.items())).encode('utf-8')).hexdigest()


def validate_assets(static_root=None, *, frontend_root=None):
    """安装包只验证产物；源码目录存在时再核验锁文件与完整源码集合。"""
    static = Path(static_root) if static_root is not None else Path(__file__).parent / 'static'
    if static.is_symlink() or not static.is_dir():
        raise ValueError('缺少静态资源目录；' + _BUILD_HINT)
    static = static.resolve()
    manifest_path = static / 'manifest.json'
    try:
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('构建清单不是合法的有界普通文件')
        manifest = json.loads(manifest_path.read_text('utf-8'))
        if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
                or manifest.get('algorithm') != 'sha256'):
            raise ValueError('构建清单版本无效')
        sources, artifacts = _mapping(manifest.get('sources')), _mapping(manifest.get('artifacts'))
        if 'index.html' not in artifacts:
            raise ValueError('构建清单缺少静态入口')
        if _files(static, skip={'manifest.json'}) != artifacts:
            raise ValueError('静态产物缺失、损坏或与构建清单不一致')
        if (manifest.get('source_sha256') != _aggregate(sources)
                or manifest.get('lock_sha256') != sources.get('package-lock.json')):
            raise ValueError('构建清单的源码或锁文件摘要不一致')
        frontend = (Path(frontend_root) if frontend_root is not None else
                    Path(__file__).resolve().parents[3] / 'frontend')
        if frontend.exists():
            if frontend.is_symlink() or not frontend.is_dir():
                raise ValueError('构建源码目录无效')
            current = _files(frontend, exclusions=_EXCLUDED)
            if current != sources:
                raise ValueError('前端源码或锁文件已变化，静态构建已陈旧')
    except (OSError, ValueError, TypeError, RecursionError) as error:
        if isinstance(error, ValueError) and '构建' in str(error):
            raise ValueError(str(error) + '；' + _BUILD_HINT) from None
        raise ValueError('静态资源或构建清单缺失、损坏；' + _BUILD_HINT) from None
    return static
