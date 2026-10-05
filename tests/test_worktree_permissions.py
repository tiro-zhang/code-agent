"""父规则上限与新实际工作根的批准身份。"""

import pytest

from mewcode.permissions.runtime import PermissionManager
from mewcode.tools.base import ToolError


def roots(tmp_path):
    parent=tmp_path/'parent';child=tmp_path/'child'
    for root in (parent,child):
        (root/'src').mkdir(parents=True)
        (root/'src/a.py').write_text('x')
    return parent,child


def policy(root,text):
    path=root/'.mewcode/permissions.yaml'
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize('kind',['path','command'])
def test_parent_approval_is_not_rebound_to_new_root(tmp_path,kind):
    parent,child=roots(tmp_path)
    p=PermissionManager(parent,user_path=tmp_path/'user.yaml')
    tool='write_file' if kind=='path' else 'execute_command'
    target=str(parent/'src/a.py') if kind=='path' else 'printf same'
    p.grants.remember(tool,kind,[target],'session')
    c=p.fork(root=child)
    arguments={'path':'src/a.py','content':'new'} if kind=='path' else {'command':'printf same'}
    with pytest.raises(ToolError) as error: c.authorize_noninteractive(tool,arguments)
    assert error.value.code=='approval_required'
    assert not c.grants.session and p.grants.session


def test_parent_live_deny_and_ask_cannot_be_weakened(tmp_path):
    parent,child=roots(tmp_path)
    policy(parent,'rules:\n  - {effect: allow, rule: "read_file(src/**)", match: glob}\n')
    policy(child,'rules:\n  - {effect: allow, rule: "read_file(src/**)", match: glob}\n')
    p=PermissionManager(parent,user_path=tmp_path/'user.yaml')
    c=p.fork(root=child)
    assert c.authorize_noninteractive('read_file',{'path':'src/a.py'}).targets==(str(child/'src/a.py'),)
    policy(parent,'rules:\n  - {effect: ask, rule: "read_file(src/**)", match: glob}\n')
    with pytest.raises(ToolError) as error: c.authorize_noninteractive('read_file',{'path':'src/a.py'})
    assert error.value.code=='approval_required'
    policy(parent,'rules:\n  - {effect: deny, rule: "read_file(src/**)", match: glob}\n')
    with pytest.raises(ToolError) as error: c.authorize_noninteractive('read_file',{'path':'src/a.py'})
    assert error.value.code=='permission_denied'


def test_child_rule_cannot_remove_default_parent_approval_requirement(tmp_path):
    parent,child=roots(tmp_path)
    policy(child,'rules:\n  - {effect: allow, rule: "read_file(src/**)", match: glob}\n')
    p=PermissionManager(parent,user_path=tmp_path/'user.yaml')
    c=p.fork(root=child)
    with pytest.raises(ToolError) as error: c.authorize_noninteractive('read_file',{'path':'src/a.py'})
    assert error.value.code=='approval_required'
    c.grants.remember('read_file','path',[str(child/'src/a.py')],'session')
    assert c.authorize_noninteractive('read_file',{'path':'src/a.py'}).targets==(str(child/'src/a.py'),)


def test_parent_mapping_fails_closed_on_alias_escape(tmp_path):
    parent,child=roots(tmp_path)
    p=PermissionManager(parent,mode='bypass',user_path=tmp_path/'user.yaml')
    c=p.fork(root=child)
    outside=tmp_path/'outside';outside.mkdir()
    (parent/'src/a.py').unlink();(parent/'src').rename(parent/'original')
    (parent/'src').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ToolError): c.authorize_noninteractive('read_file',{'path':'src/a.py'})


def test_application_worktree_state_is_not_a_model_write_target(tmp_path):
    root=tmp_path
    (root/'.mewcode/worktree-state').mkdir(parents=True)
    p=PermissionManager(root,mode='bypass',user_path=tmp_path/'user.yaml')
    with pytest.raises(ToolError) as error:
        p.authorize_noninteractive('write_file',{'path':'.mewcode/worktree-state/record.json','content':'fake'})
    assert error.value.code=='permission_denied'


@pytest.mark.parametrize('duration',['session','permanent'])
@pytest.mark.parametrize('kind',['path','command','mcp'])
def test_all_approval_kinds_keep_original_root_identity(tmp_path,duration,kind):
    from test_mcp_permissions import TOOL
    from mewcode.mcp.permissions import grant_value
    parent,child=roots(tmp_path)
    p=PermissionManager(parent,user_path=tmp_path/'user.yaml')
    p.bind_mcp_tools([TOOL])
    if kind=='path':
        tool='read_file';target=str(parent/'src/a.py');arguments={'path':'src/a.py'}
    elif kind=='command':
        tool='execute_command';target='pwd';arguments={'command':'pwd'}
    else:
        tool=TOOL.name;arguments={'value':1};target=grant_value(TOOL,arguments)
    p.grants.remember(tool,kind,[target],duration)
    c=p.fork(root=child)
    with pytest.raises(ToolError) as error:c.authorize_noninteractive(tool,arguments)
    assert error.value.code=='approval_required'
    assert c.mcp_tools[TOOL.name] is TOOL


def test_parent_config_error_fails_closed_in_child(tmp_path):
    parent,child=roots(tmp_path)
    p=PermissionManager(parent,mode='bypass',user_path=tmp_path/'user.yaml')
    c=p.fork(root=child)
    policy(parent,'rules: invalid')
    with pytest.raises(ToolError) as error:c.authorize_noninteractive('read_file',{'path':'src/a.py'})
    assert error.value.code=='permission_config_error'
