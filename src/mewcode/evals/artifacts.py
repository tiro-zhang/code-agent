"""私有、原子且可核验的测评证据。"""
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

from .suite import EvaluationError, asset, digest

STATUSES = {'passed','agent_failed','provider_failed','harness_error','cancelled','not_run'}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.eval-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数值')))
    except (OSError, ValueError) as error:
        raise EvaluationError(f'证据无法读取：{Path(path).name}：{error}') from None


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode_event(event, redactor):
    if event.kind == 'thinking_delta':
        return None
    names = ('kind','run_id','parent_run_id','iteration','mode','permission_mode','tool_name','tool_call_id','phase','purpose','reason','text','permission_decision','warning')
    raw = {name:getattr(event, name) for name in names}
    if event.kind in {'text_delta','display_line'}:
        # 跨片段秘密不能逐片段安全脱敏；正文从完整答复或工具结果保存。
        raw.update(text='[流式正文省略，见完整答复或工具结果]',body_omitted=True)
    if event.call:
        try:
            arguments = json.loads(event.call.arguments)
        except ValueError:
            arguments = event.call.arguments
        raw['call'] = {'id':event.call.id, 'name':event.call.name, 'arguments':arguments}
        raw['tool_name'] = event.call.name
    if event.result:
        raw['result'] = event.result.to_dict()
    if event.message:
        raw['message'] = {'role':event.message.role,'content':event.message.content}
    if event.permission_request:
        request = event.permission_request
        raw['approval'] = {'id':request.id,'tool':request.tool,'arguments':request.arguments,'targets':request.targets}
    if event.usage:
        raw['usage'] = asdict(event.usage)
        raw['usage']['incomplete_fields'] = sorted(event.usage.incomplete_fields)
    safe = redactor.value(raw)
    safe['redacted'] = safe != raw
    encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 65536:
        # 大字段标记裁剪；调用名、阶段及结构身份仍可定位。
        for field in ('result','call','text','message','approval'):
            if field in safe:
                safe[field] = {'truncated':True} if field != 'text' else '[内容已裁剪]'
        safe['truncated'] = True
    return safe


class TraceWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.file = self.path.open('x', encoding='utf-8')
        os.chmod(self.path, 0o600)

    def append(self, value):
        value = {'format_version':1,**value}
        try:
            self.file.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
            self.file.flush()
        except OSError:
            raise EvaluationError('轨迹证据写入失败') from None

    def close(self):
        if not self.file.closed:
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()


def code_identity():
    package = Path(__file__).resolve().parents[1]
    files = {p.relative_to(package).as_posix():file_digest(p) for p in sorted(package.rglob('*'))
             if p.is_file() and p.suffix in {'.py','.md'} and '__pycache__' not in p.parts}
    def git(*args):
        result = subprocess.run(['git','-C',str(package),*args], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    packages = {}
    for name in ('anthropic','openai','mcp','pyyaml','jsonschema'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    dirty = git('status','--porcelain','--',str(package))
    return {'digest':digest(files),'commit':git('rev-parse','HEAD'),
            'dirty':bool(dirty) if dirty is not None else None,
            'environment':{'python':platform.python_version(),'platform':platform.system(),'dependencies':packages}}


def load_run(directory):
    root = Path(directory).resolve()
    manifest = read_json(asset(root, 'manifest.json'))
    if not isinstance(manifest, dict) or manifest.get('format_version') != 1 or manifest.get('complete') is not True:
        raise EvaluationError('结果不是已完整提交的 version 1 运行')
    if not isinstance(manifest.get('trials'), list):
        raise EvaluationError('试跑清单无效')
    if (not manifest['trials'] or not isinstance(manifest.get('run_id'),str) or not manifest['run_id'] or
            not isinstance(manifest.get('controls'),dict) or not isinstance(manifest.get('variants'),dict) or
            manifest.get('execution') not in {'real','test-double'}):
        raise EvaluationError('运行身份、控制条件或来源不完整')
    results, identities = [], set()
    for entry in manifest['trials']:
        try:
            path = asset(root, entry['result'])
            if entry.get('sha256') != file_digest(path):
                raise EvaluationError('结果内容指纹不匹配')
            row = read_json(path)
            if not isinstance(row,dict):
                raise EvaluationError('试跑结果必须是对象')
            identity = (row['case_id'],row['trial_id'])
            if (row.get('format_version') != 1 or row.get('run_id') != manifest.get('run_id') or
                    identity != (entry['case_id'],entry['trial_id']) or identity in identities or
                    row.get('status') not in STATUSES):
                raise EvaluationError('试跑身份或状态无效')
            if (not isinstance(row.get('metrics'),dict) or not isinstance(row.get('checks'),list) or
                    not isinstance(row.get('manual'),list) or not isinstance(row.get('references'),list) or
                    type(row.get('started')) is not bool):
                raise EvaluationError('试跑指标或检查结构无效')
            for name, value in row['metrics'].items():
                if name in {'incomplete_fields'}:
                    if not isinstance(value,list) or any(not isinstance(field,str) for field in value):
                        raise EvaluationError('指标完整性字段无效')
                elif name in {'complete','cache_complete'}:
                    if type(value) is not bool:
                        raise EvaluationError('指标完整性标记无效')
                elif value is not None and (type(value) not in (int,float) or value<0):
                    raise EvaluationError('指标数值无效')
            if any(not isinstance(check,dict) or check.get('status') not in {'pass','fail','undetermined'} or
                   type(check.get('boundary')) is not bool for check in row['checks']):
                raise EvaluationError('检查决定无效')
            if row['status']=='passed' and (not row['checks'] or any(check['status']!='pass' for check in row['checks'])):
                raise EvaluationError('自动通过缺少完整检查证据')
            for reference in row.get('references', []):
                proof = asset(root, reference['path'])
                if reference.get('sha256') != file_digest(proof):
                    raise EvaluationError('证据引用内容已改变')
            identities.add(identity)
            results.append(row)
        except (KeyError, TypeError) as error:
            raise EvaluationError('试跑结果结构无效') from error
    return {'manifest':manifest,'results':results}
