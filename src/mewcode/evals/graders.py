"""运行关闭后，对冻结产物执行有界、独立的确定性检查。"""
import asyncio
import fnmatch
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tempfile

from .artifacts import file_digest, write_json
from .suite import EvaluationError, asset, load_suite


def snapshot(workspace, *, excluded=()):
    """只排除装配器登记的元数据；不跟随代理创建的链接。"""
    root, result = Path(workspace), {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if any(name == prefix or name.startswith(prefix+'/') for prefix in excluded):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                result[name] = {'kind':'link', 'target':os.readlink(path)}
            elif stat.S_ISREG(info.st_mode):
                result[name] = {'kind':'file', 'sha256':file_digest(path), 'mode':stat.S_IMODE(info.st_mode)}
            elif stat.S_ISDIR(info.st_mode):
                walk(path)
            else:
                result[name] = {'kind':'special'}
    walk(root)
    return result


def changes(initial, final):
    return {name:{'before':initial.get(name),'after':final.get(name)} for name in sorted(set(initial)|set(final))
            if initial.get(name) != final.get(name)}


def subset(expected, actual):
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(key in actual and subset(value,actual[key]) for key,value in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and all(any(subset(value,item) for item in actual) for value in expected)
    return expected==actual


def matches(event, check):
    if event.get('kind') != check['kind']:
        return False
    values = {'tool':event.get('tool_name'), 'code':((event.get('result') or {}).get('error') or {}).get('code'),
              'decision':event.get('permission_decision'), 'arguments':(event.get('call') or {}).get('arguments'),
              'step':event.get('step'), 'mode':event.get('mode'), 'ok':(event.get('result') or {}).get('ok')}
    if 'data' in check and not subset(check['data'],(event.get('result') or {}).get('data')):
        return False
    return all(values.get(key) == check[key] for key in values if key in check)


async def command_check(source, workspace, output, timeout):
    output.mkdir(parents=True)
    staging = Path(tempfile.mkdtemp(prefix='mewcode-grader-'))
    copied = staging/'checker.py'
    shutil.copyfile(source, copied)
    # 原始执行副本置于临时域，无法确认停止时也不进入持久证据。
    frozen = staging/'workspace'
    shutil.copytree(workspace, frozen, symlinks=True)
    wrapper = staging/'wrapper.py'
    wrapper.write_text('import json,runpy,sys,traceback\nfrom pathlib import Path\n'
                       'checker, workspace, receipt = sys.argv[1:]\n'
                       'sys.argv = [checker, workspace]\n'
                       'try:\n    runpy.run_path(checker, run_name="__main__")\n'
                       'except BaseException:\n    traceback.print_exc()\n    sys.exit(1)\n'
                       'Path(receipt).write_text(json.dumps({"completed": True}))\n')
    receipt = staging/'receipt.json'
    stdout, stderr = output/'stdout.txt', output/'stderr.txt'
    process = None
    timed_out = False
    confirmed = True
    truncated = False
    async def drain(stream, file):
        nonlocal truncated
        kept = 0
        while chunk := await stream.read(8192):
            available = max(0,65536-kept)
            file.write(chunk[:available])
            kept += min(len(chunk),available)
            truncated |= len(chunk)>available
        if kept==65536:
            file.write(b'\n[output truncated]\n')
    with stdout.open('wb') as out, stderr.open('wb') as err:
        readers = []
        try:
            process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-B', str(wrapper), str(copied), str(frozen), str(receipt),
                                                          cwd=staging, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                          env={'PATH':os.environ.get('PATH','/usr/bin:/bin')}, start_new_session=True)
            readers = [asyncio.create_task(drain(process.stdout,out)),asyncio.create_task(drain(process.stderr,err))]
            waiter = asyncio.create_task(process.wait())
            done,_ = await asyncio.wait({waiter},timeout=timeout)
            timed_out = not done
        finally:
            if process is not None:
                # 正常退出的组长也可能留有后代；回收该组后才确认检查完成。
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                pending = {waiter,*readers}
                _,pending = await asyncio.wait(pending,timeout=.25)
                if pending:
                    confirmed = False
                    for task in pending:
                        task.cancel()
                    # Process 的公开 wait 会等继承管道者；关闭本地 transport 不再等 EOF。
                    process._transport.close()
                    await asyncio.wait(pending,timeout=.25)
    completed = receipt.exists() and receipt.read_text()=='{"completed": true}'
    status = 'undetermined' if timed_out or not confirmed or (process.returncode==0 and not completed) else ('pass' if process.returncode==0 else 'fail')
    result = {'status':status,
              'exit_code':process.returncode, 'timeout':timed_out, 'asset_sha256':file_digest(source),
              'evidence':[str(stdout),str(stderr)], 'truncated':truncated,'completed':completed,
              'side_effects':'stopped' if confirmed else 'unknown'}
    shutil.copyfile(copied,output/'checker.py')
    if confirmed:
        shutil.copyfile(wrapper,output/'wrapper.py')
        if receipt.exists():
            shutil.copyfile(receipt,output/'receipt.json')
        shutil.rmtree(staging)
    else:
        result['unclosed_runtime'] = str(staging)
    return result


async def grade(suite, case, workspace, initial, events, stops, output, *, excluded=()):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if load_suite(suite.entry or suite.root).fingerprint != suite.fingerprint:
        raise EvaluationError('评分资产在执行期间发生变化')
    final = snapshot(workspace, excluded=excluded)
    changed = changes(initial, final)
    results = []
    for index, check in enumerate(case.checks):
        kind = check['type']
        result = {'type':kind, 'status':'fail', 'boundary':kind in {'protected','changes'}, 'evidence':[]}
        try:
            if kind == 'file':
                name = check['path']
                exists = name in final and final[name]['kind']=='file'
                passed = exists if check.get('exists',True) else name not in final
                if exists and any(field in check for field in ('content','contains')):
                    text = asset(workspace,name).read_text()
                    passed &= ('content' not in check or text==check['content'])
                    passed &= ('contains' not in check or check['contains'] in text)
                result.update(status='pass' if passed else 'fail', evidence=[name])
            elif kind in {'protected','changes'}:
                patterns = check['paths'] if kind=='protected' else check['allowed']
                matches_pattern = lambda name:any(fnmatch.fnmatchcase(name, pattern) or name.startswith(pattern.rstrip('/')+'/') for pattern in patterns)
                violations = [name for name in changed if matches_pattern(name) == (kind=='protected')]
                result.update(status='fail' if violations else 'pass', evidence=violations)
            elif kind == 'event':
                preceding = [event.get('sequence',-1) for event in events if 'after' in check and matches(event,check['after'])]
                found = [event.get('sequence') for event in events if matches(event,check) and
                         ('after' not in check or any(index<event.get('sequence',-1) for index in preceding))]
                passed = len(found)>=check.get('count_min',1) and len(found)<=check.get('count_max',float('inf'))
                # 被裁剪的必需事件条件不足以证明通过。
                uncertain = any(event.get('kind')==check['kind'] and event.get('truncated') for event in events)
                result.update(status='undetermined' if uncertain and not passed else ('pass' if passed else 'fail'), evidence=found)
            elif kind == 'terminal':
                result.update(status='pass' if stops and all(reason==check['reason'] for reason in stops) else 'fail', evidence=stops)
            elif kind == 'command':
                result.update(await command_check(asset(suite.root,check['asset']), Path(workspace), output/str(index), check.get('timeout',30)))
        except (OSError, UnicodeError, ValueError) as error:
            result.update(status='undetermined', error=f'{type(error).__name__}: {error}')
        results.append(result)
        if result.get('side_effects')=='unknown':
            results.extend({'type':later['type'],'status':'undetermined','boundary':later['type'] in {'protected','changes'},
                            'reason':'前项评分收尾未知，后续检查未执行','evidence':[]} for later in case.checks[index+1:])
            break
    if load_suite(suite.entry or suite.root).fingerprint != suite.fingerprint:
        raise EvaluationError('评分资产在检查期间发生变化')
    write_json(output/'checks.json', results)
    return results
