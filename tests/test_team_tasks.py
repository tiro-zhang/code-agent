"""任务原子领取、DAG、接纳与旧领取结果保留。"""
import asyncio
import pytest
from mewcode.teams.models import Member, new_id
from mewcode.teams.store import TeamStore, TeamStoreError
from mewcode.teams.tasks import TaskBoard, TaskError
from test_team_store import repository


async def setup(tmp_path):
    store = TeamStore(tmp_path / 'teams')
    team = await store.create('example', repository(tmp_path))
    goal = await store.start_goal('example', '实现接口')
    members = [Member(new_id('member'), name, 'developer', team.workspace_root) for name in ('alice', 'bob')]
    for member in members:
        await store.register_member('example', member)
    return store, team, goal, members, TaskBoard(store, 'example')


def test_revision_dependency_and_atomic_claim(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        a = await board.create(team.lead_id, goal.goal_id, 'A', code=False)
        b = await board.create(team.lead_id, goal.goal_id, 'B', depends_on=[a.task_id])
        with pytest.raises(TaskError):
            await board.claim(members[0].member_id, b.task_id)
        with pytest.raises(TaskError, match='环'):
            await board.update(team.lead_id, a.task_id, a.revision, depends_on=[b.task_id])
        claims = await asyncio.gather(*(board.claim(m.member_id, a.task_id) for m in members), return_exceptions=True)
        assert sum(not isinstance(value, Exception) for value in claims) == 1
        current = await board.get(team.lead_id, a.task_id)
        with pytest.raises(TaskError):
            await board.update(team.lead_id, a.task_id, 0, title='stale')
        with pytest.raises(TaskError):
            await board.delete(team.lead_id, a.task_id, current.revision)
        await store.pause('example')
    asyncio.run(scenario())


def test_result_acceptance_and_late_claim_audit(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '分析', code=False)
        task = await board.claim(members[0].member_id, task.task_id)
        old_claim = task.claim_id
        with pytest.raises(TaskError):
            await board.reassign(team.lead_id, task.task_id, members[1].member_id, old_run_stopped=False)
        task = await board.reassign(team.lead_id, task.task_id, members[1].member_id, old_run_stopped=True)
        late = await board.submit(members[0].member_id, task.task_id, old_claim,
                                  {'summary': '旧成果', 'verified': True, 'evidence': ['实测']})
        assert late.owner_id == members[1].member_id and late.history[-1]['late'] is True
        task = await board.submit(members[1].member_id, task.task_id, task.claim_id,
                                  {'summary': '分析结论', 'verified': False, 'evidence': ['失败']})
        with pytest.raises(TaskError):
            await board.accept(team.lead_id, task.task_id, task.revision, reason='拒绝失败验证')
        with pytest.raises(TaskError):
            await board.accept(members[1].member_id, task.task_id, task.revision, reason='自验收')
        task = await board.submit(members[1].member_id, task.task_id, task.claim_id,
                                  {'summary': '分析结论', 'verified': True, 'evidence': ['真实证据']})
        task = await board.accept(team.lead_id, task.task_id, task.revision, reason='证据完整')
        assert task.state == 'completed'
        await store.pause('example')
    asyncio.run(scenario())


def test_code_commit_and_sync_gate(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '代码')
        task = await board.claim(members[0].member_id, task.task_id)
        with pytest.raises(TaskError):
            await board.submit(members[0].member_id, task.task_id, task.claim_id,
                               {'summary': '未提交', 'verified': True, 'evidence': ['通过']})
        task = await board.start(members[0].member_id, task.task_id, task.claim_id, synced_commit=team.baseline_commit)
        assert task.state == 'running'
        await store.pause('example')
    asyncio.run(scenario())


def test_sync_and_submission_verify_actual_git_identity(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '代码')
        task = await board.claim(members[0].member_id, task.task_id)
        with pytest.raises(TaskError):
            await board.start(members[0].member_id, task.task_id, task.claim_id, synced_commit='a' * 40)
        with pytest.raises(TaskError):
            await board.submit(members[0].member_id, task.task_id, task.claim_id,
                               {'summary': '伪造提交', 'verified': True, 'evidence': ['通过'], 'branch': 'main', 'commit': 'a' * 40})
        await store.pause('example')
    asyncio.run(scenario())


def test_code_dependency_requires_integrated_commit_and_actual_sync(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        a = await board.create(team.lead_id, goal.goal_id, 'A')
        b = await board.create(team.lead_id, goal.goal_id, 'B', depends_on=[a.task_id])
        a = await board.claim(members[0].member_id, a.task_id)
        a = await board.submit(members[0].member_id, a.task_id, a.claim_id,
                               {'summary': '真实提交', 'verified': True, 'evidence': ['通过'], 'branch': 'main', 'commit': team.baseline_commit})
        a = await board.accept(team.lead_id, a.task_id, a.revision, reason='真实检查')
        with pytest.raises(TaskError):
            await board.claim(members[1].member_id, b.task_id)
        a = await board.integrate(team.lead_id, a.task_id, a.revision, commit=team.baseline_commit)
        b = await board.claim(members[1].member_id, b.task_id)
        b = await board.start(members[1].member_id, b.task_id, b.claim_id, synced_commit=team.baseline_commit)
        assert b.state == 'running'
        await store.pause('example')
    asyncio.run(scenario())


def test_sync_failure_preserves_claim_and_blocks_task(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '代码')
        task = await board.claim(members[0].member_id, task.task_id)
        with pytest.raises(TaskError):
            await board.start(members[0].member_id, task.task_id, task.claim_id, synced_commit='a' * 40)
        current = await board.get(team.lead_id, task.task_id)
        assert current.state == 'blocked' and current.claim_id == task.claim_id and current.reason
        await store.pause('example')
    asyncio.run(scenario())


def test_budget_persists_across_runs_and_cannot_refresh(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '预算', code=False, budget_limit=2)
        task = await board.claim(members[0].member_id, task.task_id)
        await board.use_budget(members[0].member_id, task.task_id, task.claim_id)
        task = await TaskBoard(store, 'example').use_budget(members[0].member_id, task.task_id, task.claim_id)
        assert task.budget_used == 2
        with pytest.raises(TaskError):
            await board.use_budget(members[0].member_id, task.task_id, task.claim_id)
        assert (await board.get(team.lead_id, task.task_id)).budget_used == 2
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('dependencies', ['missing', 'duplicate', 'self', 'cross_goal'])
def test_invalid_dependencies_do_not_publish(tmp_path, dependencies):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        a = await board.create(team.lead_id, goal.goal_id, 'A')
        if dependencies == 'missing':
            depends = [new_id('task')]
        elif dependencies == 'duplicate':
            depends = [a.task_id, a.task_id]
        elif dependencies == 'self':
            depends = [a.task_id]
        else:
            await store.update_team('example', lambda team: setattr(team.goals[goal.goal_id], 'state', 'cancelled'))
            second = await store.start_goal('example', '另一个目标')
            with pytest.raises(TaskError):
                await board.create(team.lead_id, second.goal_id, 'B', depends_on=[a.task_id])
            await store.pause('example')
            return
        with pytest.raises(TaskError):
            await board.update(team.lead_id, a.task_id, a.revision, depends_on=depends)
        assert (await board.get(team.lead_id, a.task_id)).depends_on == []
        await store.pause('example')
    asyncio.run(scenario())


def test_concurrent_dag_edits_never_publish_cycle(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        a = await board.create(team.lead_id, goal.goal_id, 'A')
        b = await board.create(team.lead_id, goal.goal_id, 'B')
        results = await asyncio.gather(board.update(members[0].member_id, a.task_id, a.revision, depends_on=[b.task_id]),
                                       board.update(members[1].member_id, b.task_id, b.revision, depends_on=[a.task_id]), return_exceptions=True)
        assert sum(not isinstance(item, Exception) for item in results) == 1
        assert sum(len(task.depends_on) for task in await board.list(team.lead_id)) == 1
        await store.pause('example')
    asyncio.run(scenario())


def _claim_task_process(storage_root, actor_id, task_id, barrier, connection):
    async def claim():
        board = TaskBoard(TeamStore(storage_root), 'example')
        try:
            task = await board.claim(actor_id, task_id)
            connection.send(('claimed', task.claim_id))
        except TaskError:
            connection.send(('rejected', None))
    barrier.wait(5)
    asyncio.run(claim())


def test_real_process_double_claim_has_one_winner(tmp_path):
    import multiprocessing
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        task = await board.create(team.lead_id, goal.goal_id, '抢任务', code=False)
        context = multiprocessing.get_context('spawn')
        barrier = context.Barrier(2)
        pairs = [context.Pipe() for _ in members]
        processes = [context.Process(target=_claim_task_process,
            args=(str(store.root), member.member_id, task.task_id, barrier, pair[1]))
            for member, pair in zip(members, pairs)]
        for process in processes:
            process.start()
        try:
            results = []
            for parent, child in pairs:
                assert parent.poll(10)
                results.append(parent.recv())
            assert [value[0] for value in results].count('claimed') == 1
        finally:
            for process in processes:
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join()
        current = await board.get(team.lead_id, task.task_id)
        assert current.owner_id in {member.member_id for member in members}
        await store.pause('example')
    asyncio.run(scenario())


def test_deep_valid_dag_within_published_capacity(tmp_path):
    async def scenario():
        from mewcode.teams.models import Task
        store, team, goal, members, board = await setup(tmp_path)
        tasks = []
        for index in range(1100):
            task = Task('task-' + f'{1100-index:032x}', team.team_id, goal.goal_id, str(index),
                        depends_on=[tasks[-1].task_id] if tasks else [])
            tasks.append(task)
        store.write_json('example', 'tasks.json', {'version': 1, 'team_id': team.team_id,
                         'tasks': {task.task_id: task.to_dict() for task in reversed(tasks)}})
        assert len(await board.list(team.lead_id)) == 1100
        await store.pause('example')
    asyncio.run(scenario())


def test_code_result_cannot_claim_another_registered_branch(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        await store.update_team('example', lambda team: setattr(team.members[members[0].member_id], 'branch', 'owned'))
        task = await board.create(team.lead_id, goal.goal_id, '代码')
        task = await board.claim(members[0].member_id, task.task_id)
        with pytest.raises(TaskError):
            await board.submit(members[0].member_id, task.task_id, task.claim_id,
                {'summary': '错误分支', 'verified': True, 'evidence': ['evidence'], 'branch': 'main', 'commit': team.baseline_commit})
        await store.pause('example')
    asyncio.run(scenario())


def test_submitted_task_can_pay_for_final_model_summary_without_new_claim(tmp_path):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        member = members[0].member_id
        task = await board.create(team.lead_id, goal.goal_id, '提交后总结', code=False, budget_limit=2)
        task = await board.claim(member, task.task_id)
        task = await board.use_budget(member, task.task_id, task.claim_id)
        task = await board.submit(member, task.task_id, task.claim_id,
            {'summary': '分析完成', 'verified': True, 'evidence': ['读取证据']})
        task = await board.use_budget(member, task.task_id, task.claim_id)
        assert task.state == 'submitted' and task.budget_used == 2
        with pytest.raises(TaskError):
            await board.use_budget(member, task.task_id, task.claim_id)
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['submitted', 'accepted', 'integrated', 'completed'])
def test_successful_task_can_pay_for_readonly_followup_without_budget_refresh(tmp_path, state):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        member = members[0].member_id
        task = await board.create(team.lead_id, goal.goal_id, '成果后的有限通信', code=True, budget_limit=2)
        task = await board.claim(member, task.task_id)
        task = await board.use_budget(member, task.task_id, task.claim_id)
        task = await board.submit(member, task.task_id, task.claim_id,
            {'summary': '代码成果', 'verified': True, 'evidence': ['真实基线'], 'branch': 'main', 'commit': team.baseline_commit})
        if state in {'accepted', 'integrated', 'completed'}:
            task = await board.accept(team.lead_id, task.task_id, task.revision, reason='独立验证')
        if state in {'integrated', 'completed'}:
            task = await board.integrate(team.lead_id, task.task_id, task.revision, commit=team.baseline_commit)
        if state == 'completed':
            task = await board.complete(team.lead_id, task.task_id, task.revision)
        task = await board.use_budget(member, task.task_id, task.claim_id)
        assert task.state == state and task.budget_used == 2
        with pytest.raises(TaskError):
            await board.use_budget(member, task.task_id, task.claim_id)
        assert (await board.get(team.lead_id, task.task_id)).budget_used == 2
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['blocked', 'failed', 'needs_review'])
def test_unsuccessful_task_cannot_refresh_budget_for_followup(tmp_path, state):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        member = members[0].member_id
        task = await board.create(team.lead_id, goal.goal_id, '受阻通信不得续额度', code=False)
        task = await board.claim(member, task.task_id)
        task = await board.use_budget(member, task.task_id, task.claim_id)
        task = await board.report(member, task.task_id, task.claim_id, state=state, reason='仍然受阻')
        with pytest.raises(TaskError):
            await board.use_budget(member, task.task_id, task.claim_id)
        assert (await board.get(team.lead_id, task.task_id)).budget_used == 1
        await store.pause('example')
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['accepted', 'integrated', 'completed'])
def test_late_member_failure_cannot_downgrade_lead_accepted_outcome(tmp_path, state):
    async def scenario():
        store, team, goal, members, board = await setup(tmp_path)
        member = members[0].member_id
        task = await board.create(team.lead_id, goal.goal_id, '验收后晚到失败', code=True, budget_limit=2)
        task = await board.claim(member, task.task_id)
        task = await board.use_budget(member, task.task_id, task.claim_id)
        task = await board.submit(member, task.task_id, task.claim_id,
            {'summary': '被 Lead 验收的成果', 'verified': True, 'evidence': ['真实提交'], 'branch': 'main', 'commit': team.baseline_commit})
        task = await board.accept(team.lead_id, task.task_id, task.revision, reason='真实独立验收')
        if state in {'integrated', 'completed'}:
            task = await board.integrate(team.lead_id, task.task_id, task.revision, commit=team.baseline_commit)
        if state == 'completed':
            task = await board.complete(team.lead_id, task.task_id, task.revision)
        original = task.to_dict()
        result = await board.report(member, task.task_id, task.claim_id,
            state='blocked', reason='最后一次只读通信额度耗尽', budget_used=2)
        assert result.state == state
        assert result.result == original['result']
        assert result.integrated_commit == original['integrated_commit']
        assert result.budget_used == original['budget_used'] == 1
        assert result.reason == original['reason']
        assert result.history[-1]['late'] is True
        assert result.history[-1]['reported_state'] == 'blocked'
        assert result.history[-1]['budget_used'] == 2
        assert result.history[-1]['claim_id'] == task.claim_id
        # 持久保存失败须报告错误，不能把审计成功伪造为内存追加。
        original_write = store.write_json
        def fail(*args, **kwargs):
            raise OSError('模拟审计落盘失败')
        store.write_json = fail
        with pytest.raises(OSError):
            await board.report(member, task.task_id, task.claim_id, state='failed', reason='迟到报告')
        store.write_json = original_write
        saved = await board.get(team.lead_id, task.task_id)
        assert saved.state == state and len(saved.history) == len(result.history)
        await store.pause('example')
    asyncio.run(scenario())
