"""用户短命令直接调用系统入口，不消耗提示词生成请求。"""

import asyncio
from dataclasses import asdict
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
    run_id = uuid4().hex
    budget = TaskBudget(session.agent.max_iterations, run_id)
    question = f'执行 Skill {name}。\n参数（原文）：{args}'
    session._task_budget, session._task_question = budget, question
    session.agent.current_user = None
    session._before_request()
    queue = asyncio.Queue()
    call = ToolCall(uuid4().hex, 'load_skill', json.dumps({'name': name, 'args': args}, ensure_ascii=False))
    def event(kind, **fields):
        return AgentEvent(kind, run_id=run_id, iteration=budget.used, mode=session.mode,
            permission_mode=session.permissions.mode, max_iterations=budget.limit, **fields)
    async def notify(notification):
        kind = notification['kind']
        fields = {'tool_call_id': call.id, 'tool_name': call.name}
        if kind == 'skill_event':
            fields['child_event'] = notification['event']
        elif kind == 'skill_loaded':
            fields['text'] = notification['text']
        elif kind.startswith('permission_'):
            fields.update(permission_request=notification.get('request'),
                          permission_decision=notification.get('decision', ''), warning=notification.get('warning', ''))
        queue.put_nowait(event(kind, **fields))
    user = Message('user', question)
    if session.agent.storage_blocked:
        raise OSError('存档已不可安全继续')
    if session.journal and skill.mode == 'isolated':
        session.journal.append('task_started', {'task_id': run_id, 'input': question, 'mode': session.mode, 'user_id': user.id})
    yield event('progress', phase='skill', text=f'加载 {name}')
    yield event('tool_call', tool_call_id=call.id, tool_name=call.name, call=call)
    operation = asyncio.create_task(session.executor.execute(call.name, call.arguments,
        allowed_tools=session.effective_tools, cancel_event=cancel, on_event=notify))
    try:
        while not operation.done() or not queue.empty():
            if not queue.empty():
                yield queue.get_nowait()
                continue
            waiting = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait((operation, waiting), return_when=asyncio.FIRST_COMPLETED)
                if waiting in done:
                    yield waiting.result()
            finally:
                if not waiting.done():
                    waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
        result = operation.result()
        yield event('tool_result', tool_call_id=call.id, tool_name=call.name, result=result)
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
            yield event('text_delta', text=summary)
        yield event('finished', reason=reason, text='Skill 已结束' if result.ok else 'Skill 未完成；已完成操作保留', usage=total)
    except asyncio.CancelledError:
        cancel.set()
        raise
    except OSError:
        session.agent.storage_blocked = True
        raise
    finally:
        if not operation.done():
            cancel.set()
        await protected(operation, cancel_event=cancel)
        session._task_budget = None
