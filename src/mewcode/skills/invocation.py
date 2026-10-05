"""用户短命令直接调用系统入口，不消耗提示词生成请求。"""

import asyncio
from dataclasses import asdict, replace
import json
from uuid import uuid4

from ..agent import _total_usage
from ..async_utils import protected
from ..sessions.codec import encode_message
from ..types import AgentEvent, Message, ToolCall
from .budget import TaskBudget


async def run_skill(session, name, args='', *, cancel_event=None):
    session.validate_skills()
    skill = session.skills.catalog.get(name)
    cancel = cancel_event if cancel_event is not None else asyncio.Event()
    budget = session._task_budget or TaskBudget(session.agent.max_iterations, uuid4().hex)
    run_id = budget.root_run_id
    question = f'执行 Skill {name}。\n参数（原文）：{args}'
    session._task_budget, session._task_question = budget, question
    session.agent.current_user = None
    session._before_request()
    call = ToolCall(uuid4().hex, 'load_skill', json.dumps({'name': name, 'args': args}, ensure_ascii=False))
    def event(kind, **fields):
        return AgentEvent(kind, run_id=run_id, iteration=budget.used, mode=session.mode,
            permission_mode=session.permissions.mode, max_iterations=budget.limit, **fields)
    user = Message('user', question)
    if session.agent.storage_blocked:
        raise OSError('存档已不可安全继续')
    if session.journal and skill.mode == 'isolated':
        session.journal.append('task_started', {'task_id': run_id, 'input': question, 'mode': session.mode, 'user_id': user.id})
    yield event('progress', phase='skill', text=f'加载 {name}')
    from ..tools.scheduler import ToolScheduler
    scheduler = ToolScheduler(session.executor, hooks=session.hooks,
        hook_fields=lambda: session.agent.hook_fields(budget, run_id))
    source = scheduler.run((call,), allowed_tools=session.effective_tools, run_id=run_id,
                           iteration=budget.used, mode=session.mode, cancel_event=cancel)
    try:
        async for item in source:
            yield replace(item, iteration=budget.used, max_iterations=budget.limit)
        if scheduler.storage_error is not None:
            raise scheduler.storage_error
        result = scheduler.results[0]
        if skill.mode == 'shared' and result.ok and not cancel.is_set():
            async for item in session.ask(question, cancel_event=cancel):
                yield item
            return
        reason = ('cancelled' if cancel.is_set() else 'model_done' if result.ok else
                  (result.data or {}).get('reason', 'stream_error'))
        data = result.data or {}
        summary = (f'Skill {name} · 子运行 {data.get("child_run_id", "未启动")} · {reason}\n'
                   f'{data.get("summary", result.error["message"] if result.error else "加载完成")}\n'
                   f'证据：{json.dumps(data.get("evidence", []), ensure_ascii=False)}')
        answer = Message('assistant', summary)
        total = _total_usage(budget.usage)
        if skill.mode == 'isolated':
            if session.journal:
                session.journal.append('history_commit', {'interaction_id': None,
                    'messages': [encode_message(user), encode_message(answer)], 'child_run_id': data.get('child_run_id')})
            session.history.extend((user, answer))
            session.agent.last_task = {'session_id': session.session_id, 'task_id': run_id,
                'user_message': user, 'final_message': answer if reason == 'model_done' else None,
                'tools': (), 'mode': session.mode, 'reason': reason}
            if session.journal:
                usage = asdict(total)
                usage['incomplete_fields'] = sorted(total.incomplete_fields)
                session.journal.append('task_finished', {'task_id': run_id, 'reason': reason,
                    'mode': session.mode, 'state': session.context.state(), 'usage': usage})
            if reason == 'model_done' and session.memory and session.memory.enqueue(session.agent.last_task):
                session.memory_tasks[run_id] = 'queued'
            yield event('text_delta', text=summary, replay_of=data.get('child_run_id', ''))
        yield event('finished', reason=reason, text='Skill 已结束' if result.ok else 'Skill 未完成；已完成操作保留', usage=total)
    except asyncio.CancelledError:
        cancel.set()
        raise
    except OSError:
        session.agent.storage_blocked = True
        raise
    finally:
        await protected(source.aclose(), cancel_event=cancel)
        session._task_budget = None
