"""独立执行复用权限及 MCP，只有最终摘要回到父消息。"""

import asyncio
from dataclasses import replace
from uuid import uuid4

from ..agent import Agent
from ..async_utils import protected
from ..prompts import PromptState
from ..sessions.codec import encode_message
from ..tools.base import ToolError, ToolResult
from ..tools.executor import ToolExecutor
from .audit import ChildJournal, ChildResultCache
from .history import background_history
from .runtime import SkillRuntime
from .service import SkillService


async def run_isolated(parent, skill, args, history_scope, *, cancel_event, on_event=None):
    budget = parent._task_budget
    if budget is None or budget.remaining <= 0:
        raise ToolError('skill_budget_exhausted', '任务请求预算不足，独立 Skill 未启动', not_started=True)
    if cancel_event.is_set():
        raise ToolError('cancelled', '独立 Skill 未启动', not_started=True)
    config = parent.config
    if skill.model and skill.model != getattr(config, 'model', None):
        try:
            config = config.for_skill(skill.model)
        except (ValueError, AttributeError):
            raise ToolError('skill_model_unavailable', f'未配置独立模型 {skill.model} 的窗口，未启动', not_started=True) from None
    # 展开失败必须发生在新客户端、审计意图和子请求之前。
    skill.render(args)
    run_id = uuid4().hex
    current = getattr(parent.agent, 'current_user', None)
    history = background_history(parent.history, history_scope, current_user_id=current.id if current else None)
    runtime = SkillRuntime(parent.skills.catalog, parent_tools=parent.effective_tools() - {'load_skill'})
    runtime.activate(skill.name, args, allow_isolated=True)
    prompt = PromptState(parent.executor.context.root, custom_instructions=parent.prompt_state.custom_instructions,
                         memory=parent.prompt_state.memory)
    prompt.environment = parent.prompt_state.environment
    prompt.enter_mode(parent.mode)
    executor = ToolExecutor(parent.executor.registry, parent.executor.context, timeout=parent.executor.timeout,
                            permissions=parent.permissions, mcp=parent.executor.mcp)
    allowed = lambda: runtime.allowed_tools(executor.registry.names(), mode=parent.mode)
    executor.system_handlers['load_skill'] = SkillService(runtime, allowed, timeout=executor.timeout)
    def prepare():
        prompt.active_skills, prompt.skill_index = runtime.render_active(), runtime.catalog.index_text()
        prompt.allowed_tools = allowed()
    journal = ChildJournal(parent.journal, budget.root_run_id, run_id, skill.name) if parent.journal else None
    if journal:
        runtime.commit = lambda records: journal.append('skills_changed', {'active_skills': records})
    provider = parent.provider if config is parent.config else parent.provider_factory(config)
    child = Agent(provider, executor, max_iterations=budget.limit, config=config, journal=journal,
                  prompt_state=prompt, before_request=prepare, allowed_tools=allowed)
    child.context.cache.close()
    child.context.cache = ChildResultCache(parent.context.cache, run_id)
    if journal:
        def checkpoint(messages, state):
            try:
                journal.append('history_checkpoint', {
                    'history': [encode_message(m) for m in messages], 'state': state,
                    'active_skills': runtime.descriptors()})
            except OSError:
                child.storage_blocked = True
                raise
        child.context.checkpoint = checkpoint
    question = (f'你已进入独立 Skill {skill.name} 的执行对话。其完整 SOP 已激活在最新上下文中，'
                '请直接按 SOP 完成任务，无需再次加载自身，也不能嵌套启动独立 Skill。\n'
                f'当前主任务目标：{parent._task_question}\n'
                f'Skill 参数（原文）：{args}\n最终回答作为主对话摘要，报告已完成、失败、未执行及实际证据。')
    stream = child.run(question, history=history, mode=parent.mode, cancel_event=cancel_event, budget=budget, run_id=run_id)
    finished = None
    try:
        async for event in stream:
            event = replace(event, skill_name=skill.name, parent_run_id=budget.root_run_id)
            if event.kind == 'finished':
                finished = event
            if on_event:
                await on_event({'kind': 'skill_event', 'event': event})
    except asyncio.CancelledError:
        cancel_event.set()
        # 转发事件受背压阻塞时也要让子循环完成成对提交和审计收尾。
        async def drain():
            nonlocal finished
            async for event in stream:
                if event.kind == 'finished':
                    finished = event
        await protected(drain(), cancel_event=cancel_event)
    finally:
        await protected(stream.aclose(), cancel_event=cancel_event)
        parent.context.summary_files.update(child.context.cache.paths)
        child.context.cache.close()
        if provider is not parent.provider:
            await protected(provider.aclose(), cancel_event=cancel_event)
        if child.storage_blocked:
            parent.agent.storage_blocked = True
    task = child.last_task or {}
    reason = finished.reason if finished else 'cancelled' if cancel_event.is_set() else 'stream_error'
    summary = task['final_message'].content if task.get('final_message') else (finished.text if finished else '独立执行未完整结束')
    evidence = [{'source': m.id, 'call_id': m.tool_call_id, 'ok': m.tool_result.ok,
                 'error': m.tool_result.error, 'cache_path': m.cache_path} for m in task.get('tools', ())]
    data = {'name': skill.name, 'child_run_id': run_id, 'parent_task_id': budget.root_run_id,
            'source': str(skill.path), 'reason': reason, 'summary': summary, 'evidence': evidence,
            'cache_paths': sorted(child.context.cache.paths)}
    if child.storage_blocked:
        raise OSError('子运行审计写入失败，停止主子后续工作')
    if reason == 'model_done':
        return ToolResult.success(data)
    return ToolResult.failure('cancelled' if reason == 'cancelled' else 'skill_incomplete',
                              '独立 Skill 未完成；已完成操作保留，请核查证据', data=data,
                              details={'reason': reason, 'side_effects_may_have_occurred': bool(evidence)})
