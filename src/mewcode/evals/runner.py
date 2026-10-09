"""保存所有串行尝试；生命周期之外不另建代理循环。"""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import time
import tempfile
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from ..agent import _total_usage
from ..types import TokenUsage
from ..web.security import Redactor
from .artifacts import TraceWriter, code_identity, encode_event, file_digest, write_json
from .graders import changes, grade, snapshot
from .report import markdown, summarize
from .runtime import ObservedProvider, make_session
from .suite import CORE_TOOLS, EvaluationError, digest, positive, preflight


def validate_config(config):
    for name in ('max_iterations','context_window','max_output_tokens'):
        positive(getattr(config,name), name, integer=True)
    if config.max_output_tokens >= config.context_window:
        raise EvaluationError('输出额度必须小于上下文窗口')
    if config.protocol not in {'openai','anthropic'} or not config.model or not config.api_key:
        raise EvaluationError('模型协议、标识或密钥无效')


def model_identity(config):
    endpoint = urlsplit(config.base_url)
    host = endpoint.hostname or ''
    if ':' in host:
        host = '['+host+']'
    if endpoint.port:
        host += ':'+str(endpoint.port)
    return {'protocol':config.protocol,'model':config.model,'service':urlunsplit((endpoint.scheme,host,endpoint.path,'','')),
            'thinking':config.thinking,'context_window':config.context_window,'max_output_tokens':config.max_output_tokens}


async def bounded(awaitable, seconds):
    """期限到期即返回未确认，避免 wait_for 等待不合作的取消者。"""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.cancel()
        # 消费最终异常，不把未知状态伪装成已收尾。
        task.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        return False, '关闭期限耗尽'
    try:
        await task
        return True, None
    except BaseException as error:
        return False, f'{type(error).__name__}: {error}'


def metrics(session, events, elapsed):
    budgets = [parent.budget for parent in session.tasks.parents.values()]
    records = [record for budget in budgets for record in budget.usage]
    records += [TokenUsage() for budget in budgets for _ in range(max(0,budget.used-len(budget.usage)))]
    usage = asdict(_total_usage(records))
    usage['incomplete_fields'] = sorted(usage['incomplete_fields'])
    return {**usage, 'requests':sum(budget.used for budget in budgets), 'elapsed':round(elapsed,6),
            'tools_proposed':sum(event['kind']=='tool_call' for event in events),
            'tools_started':sum(event['kind']=='tool_started' for event in events),
            'tool_errors':sum(event['kind']=='tool_result' and (event.get('result') or {}).get('ok') is False for event in events),
            'permission_denied':sum(event['kind']=='tool_result' and ((event.get('result') or {}).get('error') or {}).get('code')=='permission_denied' for event in events)}


def not_run(case, run_id, trial_id, reason):
    return {'format_version':1,'run_id':run_id,'case_id':case.id,'trial_id':trial_id,'status':'not_run',
            'started':False,'reason':reason,'metrics':{},'checks':[],'stops':[],'answers':[],
            'manual':list(case.manual),'references':[]}


async def run_trial(suite, case, config, directory, run_id, trial_id, *, provider=None,
                    session_factory=None, cancel_event=None, close_timeout=5):
    positive(close_timeout,'收尾期限')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    runtime_root = Path(tempfile.mkdtemp(prefix='mewcode-eval-runtime-'))
    workspace, user_root = runtime_root/'workspace', runtime_root/'user'
    redactor = Redactor(config.api_key)
    result = {**not_run(case,run_id,trial_id,''),'status':'harness_error','started':True,'side_effects':'none',
              'case_fingerprint':case.fingerprint,'excluded':['.mewcode/context'],'timings':{}}
    session, observed, trace = None, None, None
    events, stops, answers, plan_violations = [], [], [], []
    cancel = asyncio.Event()
    outer = cancel_event if cancel_event is not None else asyncio.Event()
    started = time.monotonic()
    initial, drive, watcher = None, None, None
    execution_error, cancellation, closed = None, None, True
    prompt_identity = None
    try:
        preflight(suite)
        validate_config(config)
        shutil.copytree(case.fixture, workspace)
        user_root.mkdir(mode=0o700)
        if provider is None:
            from ..providers import make_provider
            provider = make_provider(config)
        observed = ObservedProvider(provider, workspace, user_root)
        session = (session_factory or make_session)(observed,config,case,workspace,user_root)
        result['excluded'] = [session.context.cache.relative.as_posix(),'.mewcode/context/.gitignore']
        initial = snapshot(workspace, excluded=result['excluded'])
        write_json(directory/'initial.json', initial)
        from ..prompts import build_system_prompt
        # 使用实际装配的提示模板，动态请求中的指纹另存，不让重试次数改变控制身份。
        prompt_identity = {'system':digest(build_system_prompt()),
                           'instructions':digest(session.prompt_state.custom_instructions),
                           'tools':digest([asdict(tool) for tool in session.executor.registry.definitions()])}
        trace = TraceWriter(directory/'trace.jsonl')
        prepared={'kind':'runtime_prepared','workspace':str(workspace),'user_root':str(user_root),'sequence':0,'elapsed':0}
        events.append(prepared)
        trace.append(prepared)
        async def consume():
            for step_index, step in enumerate(case.steps):
                if cancel.is_set():
                    break
                if 'mode' in step:
                    before = sum(parent.budget.used for parent in session.tasks.parents.values())
                    files_before = snapshot(workspace,excluded=result['excluded'])
                    starts_before = sum(event['kind']=='tool_started' for event in events)
                    await session.set_mode(step['mode'], cancel_event=cancel)
                    after = sum(parent.budget.used for parent in session.tasks.parents.values())
                    record = {'kind':'mode_control','step':step_index,'mode':step['mode'],
                              'requests_before':before,'requests_after':after,'sequence':len(events)}
                    events.append(record)
                    trace.append(record)
                    if before!=after or starts_before!=sum(event['kind']=='tool_started' for event in events) or files_before!=snapshot(workspace,excluded=result['excluded']):
                        raise RuntimeError('模式切换产生了额外请求或副作用')
                    continue
                found = False
                files_before = snapshot(workspace,excluded=result['excluded'])
                async for event in session.ask(step['message'],cancel_event=cancel):
                    value = encode_event(event,redactor)
                    if value is not None:
                        value.update(step=step_index, sequence=len(events))
                        value['elapsed'] = round(time.monotonic()-started,6)
                        events.append(value)
                        trace.append(value)
                    if event.kind=='task_finished':
                        found = True
                        stops.append(event.reason)
                        parent = session.tasks.parents[event.run_id]
                        answer = parent.final_message.content if parent.final_message else ''
                        safe = redactor.text(answer)
                        answers.append({'step':step_index,'text':safe[:65536],'redacted':safe!=answer,'truncated':len(safe)>65536})
                if session.mode=='plan':
                    plan_violations.extend(changes(files_before,snapshot(workspace,excluded=result['excluded'])))
                if not found and not cancel.is_set():
                    raise RuntimeError('用户任务未发布父任务终态')
                if stops and stops[-1]!='model_done':
                    break
        drive = asyncio.create_task(consume())
        watcher = asyncio.create_task(outer.wait())
        done, _ = await asyncio.wait({drive,watcher},timeout=case.timeout,return_when=asyncio.FIRST_COMPLETED)
        if drive not in done:
            cancellation = 'user' if outer.is_set() else 'deadline'
            cancel.set()
            settled, error = await bounded(asyncio.shield(drive),close_timeout)
            if not settled:
                closed = False
                result['close_error'] = error
        else:
            try:
                await drive
            except Exception as error:
                if isinstance(error,EvaluationError):
                    result.update(error=redactor.text(str(error)),reason=redactor.text(str(error)),exception_phase='evidence')
                else:
                    execution_error = redactor.text(f'{type(error).__name__}: {error}')
                    result['exception_phase'] = 'core_execution'
    except Exception as error:
        result['error'] = redactor.text(f'{type(error).__name__}: {error}')
        result['reason'] = result['error']
        result['exception_phase'] = 'preparation' if session is None else 'evidence'
    finally:
        cancel.set()
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher,return_exceptions=True)
        if drive is not None and not drive.done():
            closed = False
            drive.cancel()
            drive.add_done_callback(lambda future: future.exception() if not future.cancelled() else None)
        run_elapsed = time.monotonic()-started
        close_started = time.monotonic()
        for owner in (session, observed if observed is not None else provider):
            if owner is not None:
                confirmed, error = await bounded(owner.aclose(),close_timeout)
                if not confirmed:
                    closed = False
                    result['close_error'] = redactor.text(error)
        result['timings'].update(run=round(run_elapsed,6),close=round(time.monotonic()-close_started,6))
        if trace is not None:
            trace.close()
    result.update(stops=stops,answers=answers)
    if session is not None:
        result['metrics'] = metrics(session,events,run_elapsed)
    if observed is not None:
        result['observed'] = {'system_prompt_hashes':sorted(observed.prompts),'tool_hashes':sorted(observed.tools),
                              'identity':prompt_identity}
        result['timings']['model_requests'] = observed.requests
    starts, permissions = {}, {}
    tool_times, permission_times = [], []
    for event in events:
        key = (event.get('run_id'),event.get('tool_call_id'))
        if event['kind']=='tool_started':
            starts[key] = event
        elif event['kind']=='tool_result' and key in starts:
            first = starts.pop(key)
            tool_times.append({'tool':event.get('tool_name'),'elapsed':round(event['elapsed']-first['elapsed'],6)})
        if event['kind']=='permission_requested':
            permissions[key] = event
        elif event['kind']=='permission_resolved' and key in permissions:
            first = permissions.pop(key)
            permission_times.append(round(event['elapsed']-first['elapsed'],6))
    result['timings'].update(tools=tool_times,permissions=permission_times)
    if not closed:
        result.update(status='harness_error',side_effects='unknown',reason='无法确认运行已停止；未冻结或评分')
        result['unclosed_runtime'] = str(runtime_root)
    elif initial is not None and 'error' not in result:
        grade_started = time.monotonic()
        try:
            final = snapshot(workspace,excluded=result['excluded'])
            write_json(directory/'final.json',final)
            write_json(directory/'changes.json',changes(initial,final))
            shutil.copytree(workspace,directory/'snapshot',symlinks=True)
            checks = await grade(suite,case,directory/'snapshot',initial,events,stops,directory/'grading',excluded=result['excluded'])
            checks.append({'type':'expected_stops','status':'pass' if stops and all(stop in case.expected_stops for stop in stops) else 'fail',
                           'boundary':False,'evidence':stops})
            checks.append({'type':'plan_boundary','status':'fail' if plan_violations else 'pass','boundary':True,'evidence':plan_violations})
            # 评分证据路径相对试跑目录，重新汇总时不依赖临时绝对路径。
            for check in checks:
                if check['type']=='command':
                    check['evidence'] = [Path(value).relative_to(directory).as_posix() for value in check['evidence']]
            result['checks'] = checks
            if any(check['status']=='undetermined' for check in checks):
                result.update(status='harness_error',reason='独立检查未完成')
            elif cancellation:
                result.update(status='cancelled',reason=cancellation)
            elif 'stream_error' in stops:
                result.update(status='provider_failed',reason='模型请求或协议流失败')
            elif execution_error:
                result.update(status='agent_failed',reason=execution_error)
            else:
                result.update(status='passed' if all(check['status']=='pass' for check in checks) else 'agent_failed',
                              reason='自动检查通过' if all(check['status']=='pass' for check in checks) else '自动检查或终态失败')
            result['side_effects'] = 'unknown' if any(check.get('side_effects')=='unknown' for check in checks) else 'stopped'
        except Exception as error:
            result.update(status='harness_error',reason=redactor.text(f'冻结或评分失败：{type(error).__name__}: {error}'))
        result['timings']['grade'] = round(time.monotonic()-grade_started,6)
    if closed:
        if workspace.exists():
            shutil.move(str(workspace),directory/'workspace')
        shutil.rmtree(runtime_root)
    # 评分已基于真实产物完成；可分享副本随后脱敏，保留原始内容指纹。
    result['redacted_files'] = []
    for path in directory.rglob('*'):
        if path.is_file() and not path.is_symlink():
            raw = path.read_bytes()
            safe = raw.replace(config.api_key.encode(), '[已隐藏]'.encode())
            if safe != raw:
                path.write_bytes(safe)
                result['redacted_files'].append(path.relative_to(directory).as_posix())
                result['redacted'] = True
    result = redactor.value(result)
    proofs = [path for path in directory.rglob('*') if path.is_file() and not path.is_symlink()
              and path.parts[len(directory.parts)] not in {'workspace','user'} and path.name!='result.json']
    result['references'] = [{'path':path.relative_to(directory).as_posix(),'sha256':file_digest(path)} for path in sorted(proofs)]
    write_json(directory/'result.json',result)
    return result


async def run_suite(suite, config, output, *, repeat=1, selected=(), label='', provider_factory=None, cancel_event=None,close_timeout=5):
    validate_config(config)
    positive(close_timeout,'收尾期限')
    preflight(suite,repeat=repeat,selected=selected)
    output = Path(output).resolve()
    if output.exists():
        raise EvaluationError('输出目录已存在；请为本次运行选择新目录')
    if suite.root.is_relative_to(output) or any(output.is_relative_to(case.fixture) or case.fixture.is_relative_to(output) for case in suite.cases):
        raise EvaluationError('输出目录与任务资产必须隔离')
    output.mkdir(parents=True,mode=0o700)
    identity = code_identity()
    cases = [case for case in suite.cases if not selected or case.id in selected]
    run_id = uuid4().hex
    plan = [(case,str(number)) for case in cases for number in range(1,repeat+1)]
    manifest = {'format_version':1,'run_id':run_id,'complete':False,'label':label,
                'created_at':datetime.now(timezone.utc).isoformat(),'suite_id':suite.id,
                'execution':'test-double' if provider_factory else 'real', 'repeat':repeat,
                'source':identity, 'scope':sorted(CORE_TOOLS),'ignored_config_fields':['skill_models','agent_models','agent_plugin_dirs','agent_background_tools','team_backend','team_max_running','team_max_queued','team_coordinator_enabled'],
                'controls':{'suite':suite.fingerprint,'selected':[case.id for case in cases], 'scope':sorted(CORE_TOOLS),
                            'evaluator':digest({name:file_digest(Path(__file__).parent/name) for name in ('suite.py','runtime.py','runner.py','graders.py')}),
                            'request_budget':config.max_iterations,'tool_timeout':30,
                            'cases':{case.id:{'fingerprint':case.fingerprint,'timeout':case.timeout,'permissions':digest({'rules':case.rules,'approvals':case.approvals,'mode':case.permission_mode})} for case in cases}},
                'variants':{'source':identity['digest'],'model':model_identity(config),'environment':identity['environment']},
                'trials':[{'case_id':case.id,'trial_id':trial,'result':f'trials/{case.id}/{trial}/result.json'} for case,trial in plan]}
    manifest['controls']['close_timeout'] = close_timeout
    manifest['config_sha256'] = digest({'model':model_identity(config),'max_iterations':config.max_iterations})
    manifest = Redactor(config.api_key).value(manifest)
    write_json(output/'manifest.json',manifest)
    cancel = cancel_event if cancel_event is not None else asyncio.Event()
    results, halt = [], None
    for (case, trial), entry in zip(plan,manifest['trials']):
        directory = output/Path(entry['result']).parent
        if cancel.is_set() or halt:
            result = not_run(case,run_id,trial,halt or 'user')
        else:
            try:
                provider = provider_factory(config) if provider_factory else None
                result = await run_trial(suite,case,config,directory,run_id,trial,provider=provider,cancel_event=cancel,close_timeout=close_timeout)
            except Exception as error:
                result = {**not_run(case,run_id,trial,''),'status':'harness_error','started':True,
                          'reason':Redactor(config.api_key).text(f'试跑装配或证据失败：{type(error).__name__}: {error}'),'side_effects':'unknown'}
            if result.get('side_effects')=='unknown':
                halt = '前次运行收尾或证据状态未知'
        # 结果中的引用统一为运行目录相对路径。
        for reference in result['references']:
            reference['path'] = (Path(entry['result']).parent/reference['path']).as_posix()
        write_json(output/entry['result'],result)
        entry['sha256'] = file_digest(output/entry['result'])
        results.append(result)
        write_json(output/'manifest.json',manifest)
    observed = [row.get('observed',{}).get('identity') for row in results if row.get('observed',{}).get('identity')]
    manifest['variants'].update(prompt=digest(sorted({item['system'] for item in observed})),
                                tools=digest(sorted({item['tools'] for item in observed})))
    manifest['complete'] = True
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    report = summarize(results)
    report.update(format_version=1,run_id=run_id,execution=manifest['execution'],cases={case.id:summarize([row for row in results if row['case_id']==case.id]) for case in cases})
    write_json(output/'report.json',report)
    (output/'report.md').write_text(markdown(report,results))
    write_json(output/'manifest.json',manifest)
    return report
