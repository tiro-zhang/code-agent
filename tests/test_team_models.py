"""团队记录的严格归属及版本验证。"""
import pytest
from mewcode.teams.models import Member, Run, TeamValidationError, new_id


def test_member_roundtrip_and_unknown_fields():
    member = Member(member_id='member-' + 'a' * 32, name='alice', role='developer', workspace_root='/tmp/project')
    assert Member.from_dict(member.to_dict()) == member
    with pytest.raises(TeamValidationError):
        Member.from_dict({**member.to_dict(), 'credentials': 'secret'})


@pytest.mark.parametrize('value', [True, -1, '10', 0])
def test_run_budget_rejects_wrong_type(value):
    with pytest.raises(TeamValidationError):
        Run(run_id='run-' + 'a' * 32, member_id='member-' + 'b' * 32,
            goal_id='goal-' + 'c' * 32, task_id='task-' + 'd' * 32,
            claim_id='claim-' + 'e' * 32, budget_limit=value)


def test_identity_kind_is_not_interchangeable():
    with pytest.raises(TeamValidationError):
        Member(member_id='task-' + 'a' * 32, name='alice', role='developer', workspace_root='/tmp/project')


def test_model_rejects_boolean_protocol_version_and_invalid_state():
    from mewcode.teams.models import Message, IntegrationOperation
    with pytest.raises(TeamValidationError):
        Message(new_id('message'), new_id('team'), new_id('member'), new_id('member'), 'body', 'summary', protocol_version=True)
    with pytest.raises(TeamValidationError):
        IntegrationOperation(new_id('operation'), new_id('team'), new_id('goal'), 'bad', [], state='invented')


@pytest.mark.parametrize('bad', [None, [], {'bad': 'not a member'}])
def test_nested_team_corruption_reports_validation_error(bad):
    from mewcode.teams.models import Team
    member = Member(new_id('member'), 'lead', 'lead', '/tmp/project')
    team = Team(new_id('team'), 'example', '/tmp/project/.git', '/tmp/project', member.member_id,
                'a' * 40, 'main', members={member.member_id: member})
    data = team.to_dict()
    data['members'] = bad
    with pytest.raises(TeamValidationError):
        Team.from_dict(data)


def test_incompatible_or_missing_record_version_is_rejected():
    member = Member(new_id('member'), 'lead', 'lead', '/tmp/project')
    data = member.to_dict()
    del data['version']
    with pytest.raises(TeamValidationError):
        Member.from_dict(data)


def test_message_specific_fields_and_run_budget_are_strict():
    from mewcode.teams.models import Message
    with pytest.raises(TeamValidationError):
        Message(new_id('message'), new_id('team'), new_id('member'), new_id('member'), 'text', 'summary', fields={'approved': True})
    with pytest.raises(TeamValidationError):
        Run(new_id('run'), new_id('member'), new_id('goal'), new_id('task'), new_id('claim'), budget_limit=1, budget_used=2)


def test_goal_and_team_reject_mutable_or_invalid_commit_identity():
    from mewcode.teams.models import Goal, Team
    with pytest.raises(TeamValidationError):
        Goal(new_id('goal'), 'goal', 'HEAD', 'main', 'team-branch')
    member = Member(new_id('member'), 'lead', 'lead', '/tmp/project')
    with pytest.raises(TeamValidationError):
        Team(new_id('team'), 'example', '/tmp/project/.git', '/tmp/project', member.member_id,
             'HEAD', 'main', members={member.member_id: member})


def test_frozen_definition_fingerprint_is_roundtrippable_and_strict():
    member = Member(new_id('member'), 'alice', 'developer', '/tmp/project', definition_fingerprint='a' * 64)
    assert Member.from_dict(member.to_dict()).definition_fingerprint == 'a' * 64
    with pytest.raises(TeamValidationError):
        Member(new_id('member'), 'alice', 'developer', '/tmp/project', definition_fingerprint='wrong')
