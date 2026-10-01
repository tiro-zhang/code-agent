"""验证每轮预算、实际文件副作用和失败后的完整历史。"""

from pathlib import Path
import pytest
from mewcode.session import ChatSession
from mewcode.tools import default_registry
from mewcode.tools.base import ToolContext, ToolResult
from mewcode.tools.executor import ToolExecutor
from mewcode.types import ContextLimitError, Message, ProviderError, StreamEvent, ToolCall


class ScriptedProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def stream(self, messages, **options):
        self.requests.append((list(messages), options))
        for event in next(self.responses):
            if isinstance(event, BaseException):
                raise event
            yield event


def answer(text='答复'):
    return [StreamEvent('text_delta',text),StreamEvent('completed')]


def call(*calls):
    return [StreamEvent('completed',message=Message('assistant',tool_calls=tuple(calls)))]


class CountingExecutor(ToolExecutor):
    def __init__(self, root):
        super().__init__(default_registry(),ToolContext(root))
        self.count=0

    def execute(self, name, raw):
        self.count+=1
        return super().execute(name,raw)


@pytest.fixture
def executor(tmp_path):
    return CountingExecutor(tmp_path)


def test_single_call_performs_real_create_and_final_request_disables_tools(executor,tmp_path):
    provider=ScriptedProvider(call(ToolCall('a','write_file','{"path":"x","content":"猫"}')),answer(),answer('下一轮'))
    session=ChatSession(provider,executor=executor)
    received=list(session.ask('创建文件'))
    assert [e.kind for e in received]==['tool_started','tool_result','text_delta','completed']
    assert (tmp_path/'x').read_text()=='猫' and executor.count==1
    assert len(provider.requests)==2
    assert provider.requests[0][1]['tool_choice']=='auto' and provider.requests[1][1]['tool_choice']=='none'
    assert len(provider.requests[0][1]['tools'])==6
    sent=provider.requests[1][0]
    assert [m.role for m in sent]==['user','assistant','tool']
    assert sent[-1].tool_call_id=='a' and sent[-1].tool_result.ok
    list(session.ask('刚才做了什么'))
    assert provider.requests[-1][0][:-1]==session.history[:-2]
    assert executor.count==1


@pytest.mark.parametrize('raw,expected', [('{"path":','invalid_arguments'),('{"path":"missing"}','file_not_found')])
def test_tool_failure_is_sent_back_once_without_retry(executor,raw,expected):
    provider=ScriptedProvider(call(ToolCall('a','read_file',raw)),answer('操作失败'))
    session=ChatSession(provider,executor=executor)
    list(session.ask('读文件'))
    assert len(provider.requests)==2 and executor.count==1
    assert session.history[2].tool_result.error['code']==expected


def test_all_multiple_calls_rejected_with_one_result_per_id(executor,tmp_path):
    provider=ScriptedProvider(call(*[ToolCall(id,'write_file','{"path":"x","content":"bad"}') for id in ['a','b']]),answer())
    session=ChatSession(provider,executor=executor)
    list(session.ask('多工具'))
    assert executor.count==0 and not (tmp_path/'x').exists() and len(provider.requests)==2
    assert [m.tool_call_id for m in session.history[2:4]]==['a','b']
    assert all(m.tool_result.error['code']=='too_many_tool_calls' for m in session.history[2:4])


@pytest.mark.parametrize('failure', [ProviderError('网络断开'),KeyboardInterrupt()])
def test_final_failure_keeps_real_side_effect_and_discards_partial_answer(executor,tmp_path,failure):
    provider=ScriptedProvider(call(ToolCall('a','write_file','{"path":"x","content":"done"}')),
                              [StreamEvent('text_delta','残缺'),failure],answer('文件已创建'))
    session=ChatSession(provider,executor=executor)
    with pytest.raises(type(failure)):
        list(session.ask('创建'))
    assert (tmp_path/'x').read_text()=='done' and len(session.history)==3
    assert session.history[-1].tool_result.ok
    list(session.ask('继续'))
    assert len(provider.requests[-1][0])==4 and '残缺' not in repr(provider.requests[-1][0])
    assert executor.count==1


def test_second_tool_response_is_rejected_without_execution_or_third_request(executor,tmp_path):
    provider=ScriptedProvider(call(ToolCall('a','read_file','{"path":"missing"}')),
                              call(ToolCall('b','write_file','{"path":"bad","content":"bad"}')))
    session=ChatSession(provider,executor=executor)
    with pytest.raises(ProviderError,match='工具'):
        list(session.ask('问'))
    assert executor.count==1 and len(provider.requests)==2 and len(session.history)==3
    assert not (tmp_path/'bad').exists()


def test_first_stream_interrupted_does_not_execute_or_commit(executor):
    provider=ScriptedProvider([StreamEvent('text_delta','部分'),KeyboardInterrupt()])
    session=ChatSession(provider,executor=executor)
    with pytest.raises(KeyboardInterrupt): list(session.ask('问'))
    assert session.history==[] and executor.count==0


def test_cancelled_tool_keeps_pair_and_skips_final_model(executor,monkeypatch):
    provider=ScriptedProvider(call(ToolCall('a','execute_command','{"command":"sleep 5"}')))
    monkeypatch.setattr(executor,'execute',lambda *args: ToolResult.failure('cancelled','已取消；请检查状态'))
    session=ChatSession(provider,executor=executor)
    received=list(session.ask('执行'))
    assert len(provider.requests)==1 and len(session.history)==3
    assert session.history[-1].tool_result.error['code']=='cancelled'
    assert received[-1].kind=='tool_result'


def test_context_limit_after_tool_result_drops_older_turns_only(executor,tmp_path):
    overflow=ContextLimitError('服务 上下文长度已超出模型限制')
    provider=ScriptedProvider(answer('旧答'),
                              call(ToolCall('a','write_file','{"path":"x","content":"猫"}')),
                              [overflow],answer('完成'))
    session=ChatSession(provider,executor=executor)
    list(session.ask('旧问题'))
    received=list(session.ask('执行'))
    assert [e.kind for e in received]==['tool_started','tool_result','history_trimmed','text_delta','completed']
    # 重试请求保留当前轮的 user、工具调用与结果配对，只丢弃了较早轮次。
    assert [m.role for m in provider.requests[-1][0]]==['user','assistant','tool']
    assert (tmp_path/'x').read_text()=='猫' and session.history[-1]==Message('assistant','完成')


def test_cancel_during_cleanup_keeps_created_file_history_and_skips_final(executor,tmp_path,monkeypatch):
    import mewcode.tools.executor as execution
    import os
    import signal
    import threading
    original=execution._cleanup
    def interrupt_cleanup(process,grouped):
        timer=threading.Timer(.05,lambda:os.kill(os.getpid(),signal.SIGINT))
        timer.start()
        try: return original(process,grouped)
        finally: timer.join()
    monkeypatch.setattr(execution,'_cleanup',interrupt_cleanup)
    provider=ScriptedProvider(call(ToolCall('a','write_file','{"path":"x","content":"done"}')),answer('不应请求'))
    session=ChatSession(provider,executor=executor)
    list(session.ask('创建'))
    assert (tmp_path/'x').read_text()=='done'
    assert len(provider.requests)==1 and len(session.history)==3
    assert session.history[-1].tool_result.error['code']=='cancelled'
    assert session.history[-1].tool_result.data['bytes_written']==4
