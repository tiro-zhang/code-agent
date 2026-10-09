"""受信任务资产的严格加载与模型调用前预检。"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import math
import yaml

from ..permissions.config import _StrictLoader, _document

CORE_TOOLS = frozenset({'read_file', 'write_file', 'edit_file', 'execute_command', 'glob_files', 'search_code'})


class EvaluationError(ValueError):
    """测评输入或证据无效，不应产生模型请求。"""


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def relative(value):
    if not isinstance(value, str) or not value or '\x00' in value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise EvaluationError('路径必须是范围内的相对路径')
    return value


def asset(root, value):
    path = Path(root) / relative(value)
    current = path
    while current != Path(root):
        if current.is_symlink():
            raise EvaluationError(f'资产禁止链接：{value}')
        current = current.parent
    if not path.exists():
        raise EvaluationError(f'资产不存在：{value}')
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise EvaluationError('资产路径逃逸')
    return path


def tree_hash(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        if path.is_symlink():
            raise EvaluationError(f'初始文件禁止链接：{path.name}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise EvaluationError('资产必须是普通文件或目录')
    return result


def mapping(value, allowed, required=()):
    if not isinstance(value, dict) or set(value)-set(allowed) or set(required)-set(value):
        raise EvaluationError('字段缺失、类型无效或含未知字段')


def texts(value):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise EvaluationError('需要非空文本组成的列表')


def positive(value, label, integer=False):
    if type(value) not in ((int,) if integer else (int, float)) or value <= 0 or not math.isfinite(value):
        raise EvaluationError(f'{label} 必须是有限正数')


CHECK_FIELDS = {
    'file': {'type', 'path', 'exists', 'content', 'contains'},
    'protected': {'type', 'paths'}, 'changes': {'type', 'allowed'},
    'command': {'type', 'asset', 'timeout'},
    'event': {'type', 'kind', 'tool', 'code', 'decision', 'arguments', 'data', 'ok', 'mode', 'after', 'count_min', 'count_max', 'step'},
    'terminal': {'type', 'reason'},
}


@dataclass(frozen=True)
class Case:
    id: str
    fixture: Path
    steps: tuple
    checks: tuple
    approvals: tuple = ()
    rules: tuple = ()
    permission_mode: str = 'default'
    timeout: float = 600
    manual: tuple = ()
    tags: tuple = ()
    expected_stops: tuple = ('model_done',)
    fingerprint: str = ''


@dataclass(frozen=True)
class Suite:
    id: str
    root: Path
    cases: tuple[Case, ...]
    fingerprint: str
    entry: Path | None = None


def load_suite(path):
    path = Path(path)
    if path.is_dir():
        path /= 'suite.yaml'
    try:
        if path.is_symlink():
            raise EvaluationError('任务入口禁止链接')
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise EvaluationError('任务入口超过 1 MiB')
        doc = yaml.load(raw, Loader=_StrictLoader)
        mapping(doc, {'version','id','cases'}, {'version','id','cases'})
        if type(doc['version']) is not int or doc['version'] != 1:
            raise EvaluationError('任务格式仅支持 version: 1')
        if not isinstance(doc['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', doc['id']):
            raise EvaluationError('任务集标识无效')
        if not isinstance(doc['cases'], list) or not doc['cases']:
            raise EvaluationError('任务集不能为空')
        root, cases, identities = path.resolve().parent, [], set()
        for entry in doc['cases']:
            mapping(entry, {'id','fixture','steps','checks','approvals','rules','permission_mode','timeout','manual','tags','expected_stops'}, {'id','fixture','steps','checks'})
            identity = entry['id']
            if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', identity):
                raise EvaluationError('用例标识无效')
            if identity in identities:
                raise EvaluationError(f'用例标识重复：{identity}')
            identities.add(identity)
            fixture = asset(root, entry['fixture'])
            if not fixture.is_dir():
                raise EvaluationError(f'{identity} 初始文件必须是目录')
            files = tree_hash(fixture)
            if any(name.startswith(('.mewcode/hooks', '.mewcode/skills', '.mewcode/agents', '.mewcode/permissions.local', '.mewcode/context', '.mewcode/worktree-state')) for name in files):
                raise EvaluationError(f'{identity} 初始文件含范围外运行入口或状态')
            policy = fixture/'.mewcode/permissions.yaml'
            if policy.exists():
                _document(policy.read_bytes(),policy,False)
            steps = entry['steps']
            if not isinstance(steps, list) or not steps or not any(isinstance(s, dict) and 'message' in s for s in steps):
                raise EvaluationError('用例需要用户请求步骤')
            for step in steps:
                mapping(step, {'mode','message'})
                if len(step) != 1:
                    raise EvaluationError('步骤只能声明一个操作')
                if 'mode' in step and step['mode'] not in {'plan','execute'}:
                    raise EvaluationError('模式无效')
                if 'message' in step and (not isinstance(step['message'], str) or not step['message'].strip()):
                    raise EvaluationError('用户请求不能为空')
            checks = entry['checks']
            if not isinstance(checks, list) or not checks:
                raise EvaluationError('用例必须声明检查')
            assets = {}
            for check in checks:
                if not isinstance(check, dict) or check.get('type') not in CHECK_FIELDS:
                    raise EvaluationError('检查类型无效')
                kind = check['type']
                required = {'file':'path','protected':'paths','changes':'allowed','command':'asset','event':'kind','terminal':'reason'}[kind]
                mapping(check, CHECK_FIELDS[kind], {'type', required})
                if kind == 'file':
                    relative(check['path'])
                    if 'exists' in check and type(check['exists']) is not bool:
                        raise EvaluationError('exists 需要布尔值')
                    for field in ('content','contains'):
                        if field in check and not isinstance(check[field], str):
                            raise EvaluationError('文件断言需要文本')
                if kind in {'protected','changes'}:
                    field = 'paths' if kind == 'protected' else 'allowed'
                    texts(check[field])
                    for name in check[field]:
                        relative(name)
                if kind == 'command':
                    source = asset(root, check['asset'])
                    if not source.is_file() or source.suffix != '.py':
                        raise EvaluationError('评分命令资产必须是受信 Python 普通文件')
                    assets[check['asset']] = hashlib.sha256(source.read_bytes()).hexdigest()
                    positive(check.get('timeout', 30), '评分期限')
                if kind == 'event':
                    if not isinstance(check['kind'], str):
                        raise EvaluationError('事件类型需要文本')
                    for field in ('count_min','count_max','step'):
                        if field in check and (type(check[field]) is not int or check[field] < 0):
                            raise EvaluationError('事件数量和步骤需要非负整数')
                    if 'arguments' in check and not isinstance(check['arguments'], dict):
                        raise EvaluationError('事件参数需要映射')
                    if 'data' in check and not isinstance(check['data'], dict):
                        raise EvaluationError('结果条件需要映射')
                    if 'ok' in check and type(check['ok']) is not bool:
                        raise EvaluationError('结果条件 ok 需要布尔值')
                    if 'mode' in check and check['mode'] not in {'plan','execute'}:
                        raise EvaluationError('事件模式无效')
                    if 'after' in check:
                        mapping(check['after'], {'kind','tool','code','step'}, {'kind'})
                        if not isinstance(check['after']['kind'],str):
                            raise EvaluationError('事件前置条件类型无效')
                    for field in ('tool','code','decision'):
                        if field in check and not isinstance(check[field], str):
                            raise EvaluationError('事件条件需要文本')
                if kind == 'terminal' and not isinstance(check['reason'], str):
                    raise EvaluationError('终态条件需要文本')
            approvals = entry.get('approvals', [])
            if not isinstance(approvals, list):
                raise EvaluationError('审批需要列表')
            for approval in approvals:
                mapping(approval, {'tool','path','arguments','decision'}, {'tool','decision'})
                if approval['tool'] not in CORE_TOOLS or approval['decision'] not in {'once','session','deny'}:
                    raise EvaluationError('审批工具或决定无效')
                if ('path' in approval) == ('arguments' in approval):
                    raise EvaluationError('审批必须声明路径或完整参数')
                if 'path' in approval:
                    if approval['tool'] not in {'read_file','write_file','edit_file'}:
                        raise EvaluationError('路径审批只用于单文件工具')
                    relative(approval['path'])
                if 'arguments' in approval and not isinstance(approval['arguments'], dict):
                    raise EvaluationError('审批参数必须是映射')
            mode = entry.get('permission_mode','default')
            if mode not in {'default','strict'}:
                raise EvaluationError('测评仅使用 default 或 strict 权限模式')
            rules = entry.get('rules', [])
            if rules and policy.exists():
                raise EvaluationError('权限规则与初始权限文件不能同时声明')
            _document(yaml.safe_dump({'version':1,'rules':rules}).encode(), root/'suite.yaml', False)
            for field in ('manual','tags','expected_stops'):
                texts(entry.get(field, ['model_done'] if field == 'expected_stops' else []))
            timeout = entry.get('timeout', 600)
            positive(timeout, '试跑期限')
            cases.append(Case(identity, fixture, tuple(steps), tuple(checks), tuple(approvals), tuple(rules), mode, timeout,
                              tuple(entry.get('manual', [])), tuple(entry.get('tags', [])), tuple(entry.get('expected_stops', ['model_done'])),
                              digest({'definition':entry,'fixtures':files,'graders':assets})))
        return Suite(doc['id'], root, tuple(cases), digest([case.fingerprint for case in cases]), path.resolve())
    except EvaluationError:
        raise
    except (OSError, ValueError, TypeError, yaml.YAMLError, RecursionError) as error:
        raise EvaluationError(f'任务集校验失败：{error}') from None


def preflight(suite, *, repeat=1, selected=()):
    positive(repeat, '重复次数', integer=True)
    if not shutil.which('rg'):
        raise EvaluationError('依赖 rg 不存在')
    if len(set(selected)) != len(selected) or set(selected)-{c.id for c in suite.cases}:
        raise EvaluationError('选例重复或引用未知用例')
    # 再校验资产，避免启动时使用加载之后已变更的内容。
    current = load_suite(suite.entry or suite.root)
    if current.fingerprint != suite.fingerprint:
        raise EvaluationError('任务资产在校验后发生变化')
