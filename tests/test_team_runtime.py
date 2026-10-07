"""稳定成员空闲后的磁盘上下文接续。"""
import asyncio

from conftest import ScriptedProvider, async_test
from test_agent_loop import answer
from test_team_service import make_session


def response(text):
    return answer(text)


async def wait_idle(session, identity, previous_requests=0):
    runtime = session.teams.runners[identity]
    async with asyncio.timeout(5):
        while len(runtime.session.provider.requests) <= previous_requests or runtime.busy:
            if runtime.task.done():
                raise AssertionError((runtime.task.exception(),runtime.session.warnings))
            await asyncio.sleep(.01)
    return runtime


@async_test
async def test_member_receives_direct_message_and_keeps_history(tmp_path):
    session = make_session(tmp_path)
    providers = []
    def factory(config):
        provider = ScriptedProvider([response('第一次完成'),response('继续完成')])
        providers.append(provider)
        return provider
    session.provider_factory = factory
    await session.teams.control({'action':'create','name':'alpha'})
    await session.teams.control({'action':'goal','description':'检查文件'})
    member = await session.teams.member({'action':'spawn','name':'alice','role':'general','backend':'inprocess'})
    identity = member['member_id']
    await session.teams.message({'action':'send','recipient':'alice','body':'先检查 README'})
    runtime = await wait_idle(session,identity)
    runtime.session.history.clear()
    await session.teams.message({'action':'send','recipient':'alice','body':'继续检查'})
    await wait_idle(session,identity,1)
    assert len(providers[0].requests) == 2
    assert any('第一次完成' in message.content for message in providers[0].requests[1][0])
    assert runtime.session.executor.context.root != session.executor.context.root
    assert runtime.session.journal.path.is_relative_to(session.user_root)
    await session.aclose()
    assert providers[0].closed
    team = session.teams.store.load('alpha')
    assert team.status == 'paused' and team.members[identity].state == 'stopped'


@async_test
async def test_explicit_resume_reuses_member_and_disk_context(tmp_path):
    session=make_session(tmp_path)
    session.provider_factory=lambda config:ScriptedProvider([answer('上次成果')])
    await session.teams.control({'action':'create','name':'alpha'})
    await session.teams.control({'action':'goal','description':'可接续目标'})
    member=await session.teams.member({'action':'spawn','name':'alice','role':'general','backend':'inprocess'})
    identity=member['member_id']
    await session.teams.message({'action':'send','recipient':'alice','body':'第一轮'})
    await wait_idle(session,identity)
    await session.aclose()
    fresh=make_session(tmp_path)
    provider=ScriptedProvider([answer('续接成果')])
    fresh.provider_factory=lambda config:provider
    await fresh.teams.control({'action':'resume','name':'alpha'})
    assert not fresh.teams.runners and not provider.requests
    await fresh.teams.message({'action':'send','recipient':'alice','body':'明确继续'})
    await wait_idle(fresh,identity)
    assert '上次成果' in repr(provider.requests[0][0])
    assert fresh.teams.store.load('alpha').members[identity].workspace_root==member['workspace_root']
    await fresh.aclose()
