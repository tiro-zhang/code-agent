"""父拥有缓存归档与受限会话引用登记。"""
import pytest

from mewcode.context.spill import ResultCache
from mewcode.tools.base import ToolResult
from mewcode.types import Message
from mewcode.sessions.projection import validate_payload,build_projection


def test_archive_preserves_source_identity_full_body_and_truncation(tmp_path):
    from mewcode.worktrees.evidence import archive_cache
    child=tmp_path/'child';child.mkdir()
    source=ResultCache(child,session_id='agent_child',persistent=True)
    parent=ResultCache(tmp_path,session_id='parent')
    message=Message('tool',tool_call_id='call',tool_result=ToolResult.success({'text':'文'*14000},truncated=True))
    try:
        original=source.save(message,'read_file')
        index=source.write_index([original])
        mapping=archive_cache(source,parent,'agent_child')
        archived=mapping[str(child/original)]
        assert parent.restore(archived)==message.tool_result
        assert parent.describe(archived)['source']==message.id
        assert parent.describe(archived)['provenance']=={'task_id':'agent_child','source_root':str(child),'source_path':original}
        assert parent.restore(mapping[str(child/index)]).data['files']==[archived]
        with pytest.raises(OSError): parent.restore(str(child/original))
    finally:
        source.persistent=False;source.close();parent.close()


def mapping_event(root):
    workspace=str(root/'child')
    return {'parent_task_id':'parent_task','run_id':'agent_child','stage':'archived',
            'workspace_root':workspace,'worktree':{'state':'retained'},
            'cache_mappings':{workspace+'/.mewcode/context/agent_child/result.jsonl':'.mewcode/context/parent/archived.jsonl'}}


def test_projection_registers_only_parent_archive_and_preserves_child_source(tmp_path):
    payload=mapping_event(tmp_path)
    child={'parent_task_id':'parent_task','run_id':'agent_child','skill':'agent:worker','kind':'task_started',
           'payload':{'input':'目标','state':{'cache_paths':['.mewcode/context/agent_child/result.jsonl']}},
           'cache_identity':'agent_child','workspace_root':payload['workspace_root']}
    validate_payload('child_event',child,'parent',2)
    validate_payload('worktree_event',payload,'parent',3)
    records=[{'seq':1,'kind':'session_created','timestamp':'2026-10-05T00:00:00+00:00','payload':{}},
             {'seq':2,'kind':'child_event','timestamp':'2026-10-05T00:00:00+00:00','payload':child},
             {'seq':3,'kind':'worktree_event','timestamp':'2026-10-05T00:00:00+00:00','payload':payload}]
    projection=build_projection(records,[])
    assert projection.cache_paths=={'.mewcode/context/parent/archived.jsonl'}
    assert projection.worktrees['agent_child']==payload
    assert child['payload']['state']['cache_paths']==['.mewcode/context/agent_child/result.jsonl']
    assert projection.history==[]


@pytest.mark.parametrize('source,destination',[
    ('/etc/secret','.mewcode/context/parent/result.jsonl'),
    ('child/../../etc/secret','.mewcode/context/parent/result.jsonl'),
    ('child/.mewcode/context/other/result.jsonl','.mewcode/context/parent/result.jsonl'),
    ('child/.mewcode/context/agent_child/result.jsonl','/tmp/unrestricted'),
    ('child/.mewcode/context/agent_child/result.jsonl','.mewcode/context/other/result.jsonl'),
    ('child/.mewcode/context/agent_child/../../escape.jsonl','.mewcode/context/parent/result.jsonl')])
def test_mapping_rejects_external_traversal_and_identity_forgery(tmp_path,source,destination):
    payload=mapping_event(tmp_path)
    source=source if source.startswith('/') else str(tmp_path/source)
    payload['cache_mappings']={source:destination}
    with pytest.raises(ValueError): validate_payload('worktree_event',payload,'parent',3)


@pytest.mark.parametrize('changes',[
    {'cache_identity':True},{'run_id':None},{'workspace_root':'relative'},
    {'cache_identity':'other'},{'workspace_root':None}])
def test_isolated_child_audit_rejects_invalid_identity_shapes(tmp_path,changes):
    child={'parent_task_id':'parent_task','run_id':'agent_child','skill':'agent:worker','kind':'task_started',
           'payload':{'input':'目标'},'cache_identity':'agent_child','workspace_root':str(tmp_path/'child')}
    child.update(changes)
    with pytest.raises(ValueError): validate_payload('child_event',child,'parent',2)


def test_limited_task_report_keeps_archived_reference_without_duplicate_body():
    import json
    from mewcode.tasks.manager import TaskOutcome,TaskRecord
    path='.mewcode/context/parent/archived.jsonl'
    evidence={'source':'result','call_id':'read','tool_name':'read_file','cache_path':path,
              'result':ToolResult.success({'content':'文'*25000},truncated=True).to_dict()}
    record=TaskRecord('agent_child','parent_task','defined','execute',0)
    record.outcome=TaskOutcome('model_done','实际结果',(evidence,),worktree={'archive':'complete','state':'removed'})
    report=record.report(limit=65536)
    assert not report['truncated']
    parsed=json.loads(report['content'])
    assert parsed['evidence'][0]['cache_path']==path
    assert parsed['evidence'][0]['result']['truncated'] is True
    assert len(report['content'])<5000
    assert record.outcome.evidence[0]['result']['data']['content']=='文'*25000


def test_early_returned_event_does_not_hide_missing_parent_history(tmp_path):
    payload=mapping_event(tmp_path);payload['stage']='returned'
    child={'parent_task_id':'parent_task','run_id':'agent_child','skill':'agent:worker','kind':'task_finished',
           'payload':{'reason':'model_done'},'cache_identity':'agent_child','workspace_root':payload['workspace_root']}
    records=[{'seq':1,'kind':'session_created','timestamp':'2026-10-05T00:00:00+00:00','payload':{}},
             {'seq':2,'kind':'child_event','timestamp':'2026-10-05T00:00:00+00:00','payload':child},
             {'seq':3,'kind':'worktree_event','timestamp':'2026-10-05T00:00:00+00:00','payload':payload}]
    warnings=[];build_projection(records,warnings)
    assert warnings


def test_long_final_text_keeps_archive_index_through_report_and_spill():
    from mewcode.tasks.manager import TaskOutcome,TaskRecord
    from mewcode.context.spill import _reference
    path='.mewcode/context/parent/archived.jsonl'
    item={'source':'result','call_id':'read','tool_name':'read_file','cache_path':path,
          'result':ToolResult.success({'content':'actual'}).to_dict()}
    record=TaskRecord('agent_child','parent_task','defined','execute',0)
    record.outcome=TaskOutcome('model_done','文'*25000,(item,),worktree={'archive':'complete','state':'removed'})
    report=record.report(limit=65536)
    assert report['cache_paths']==[path]
    summary=_reference(Message('tool',tool_result=ToolResult.success(report)),'.mewcode/context/parent/report.jsonl','agent')
    assert summary.tool_result.data['state']['cache_paths']==[path]


@pytest.mark.parametrize('missing',['workspace_root','run_id'])
def test_child_audit_missing_required_fields_rejected(tmp_path,missing):
    payload={'parent_task_id':'parent_task','run_id':'agent_child','skill':'agent:worker','kind':'task_started',
             'payload':{'input':'目标'},'cache_identity':'agent_child','workspace_root':str(tmp_path/'child')}
    del payload[missing]
    with pytest.raises(ValueError): validate_payload('child_event',payload,'parent',2)


def test_return_confirmation_requires_existing_main_commit(tmp_path):
    payload=mapping_event(tmp_path);payload.update(stage='returned',history_commit_seq=3)
    records=[{'seq':1,'kind':'session_created','timestamp':'2026-10-05T00:00:00+00:00','payload':{}},
             {'seq':2,'kind':'child_event','timestamp':'2026-10-05T00:00:00+00:00',
              'payload':{'run_id':'agent_child','skill':'agent:worker','kind':'task_finished','payload':{'reason':'model_done'}}},
             {'seq':3,'kind':'history_commit','timestamp':'2026-10-05T00:00:00+00:00','payload':{'messages':[]}},
             {'seq':4,'kind':'worktree_event','timestamp':'2026-10-05T00:00:00+00:00','payload':payload}]
    warnings=[];build_projection(records,warnings)
    assert not warnings
    records[2]['kind']='task_finished'
    warnings=[];build_projection(records,warnings)
    assert warnings
    with pytest.raises(ValueError): validate_payload('worktree_event',dict(payload,history_commit_seq=True),'parent',4)
