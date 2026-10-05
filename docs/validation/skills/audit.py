"""从本次真实请求与 Journal 提取脱敏验收证据，不改会话。"""

import json
from pathlib import Path
import re
import sys

from mewcode.sessions.projection import build_projection


def audit(root, output):
    rows = [json.loads(line) for line in (root / 'requests.jsonl').read_text().splitlines()]
    requests = []
    for index, row in enumerate(rows, 1):
        runtime = [c['text'] for c in row['contexts'] if c['kind'] == 'runtime'][-1]
        background = '\n'.join(c['text'] for c in row['contexts'] if c['kind'] == 'skill_background')
        active = re.findall(r'^### Skill: (.+)$', runtime, re.M)
        assert runtime.index('## 已激活') < runtime.index('## 可发现')
        requests.append({'request': index, 'model': row['model'], 'tools': row['tools'],
            'active': active, 'full_reminder': '## 环境信息' in runtime,
            'guide_v1': 'SOP_GUIDE_V1' in runtime, 'guide_v2': 'SOP_GUIDE_V2' in runtime,
            'rejected_body': 'SOP_GUIDE_REJECTED' in runtime,
            'literal_args': '原始  参数42' in runtime,
            'background_chars': len(background),
            'background_marker_17': 'HISTORY_MARKER_17' in background,
            'background_marker_29': 'HISTORY_MARKER_29' in background})
    assert not requests[2]['active'] and not requests[2]['guide_v1']
    assert requests[3]['guide_v1'] and not requests[3]['full_reminder']
    assert any(set(r['active']) == {'guide', 'narrow'} and set(r['tools']) == {'read_file', 'load_skill'} for r in requests)
    assert any(r['active'] == ['pack'] and r['tools'] == ['load_skill'] for r in requests)
    assert any(r['guide_v2'] and r['literal_args'] for r in requests)
    assert not any(r['rejected_body'] for r in requests)
    for r in requests:
        if r['active'] == ['historyzero']:
            assert not r['background_chars']
        elif r['active'] == ['historyone']:
            assert r['background_marker_29'] and not r['background_marker_17']
        elif r['active'] == ['historyall']:
            assert r['background_marker_17'] and r['background_marker_29']
    journals = sorted((root / '.mewcode/sessions').glob('*.jsonl'))
    path = next(p for p in journals if 'child_event' in p.read_text())
    records = [json.loads(line) for line in path.read_text().splitlines()]
    reset = next(i for i, r in enumerate(records) if r['kind'] == 'history_checkpoint' and r['payload'].get('reset'))
    warnings = []
    before = build_projection(records[:reset], warnings)
    after = build_projection(records[:reset + 1], warnings)
    assert not warnings and before.history and not after.history and not after.active_skills
    assert [a['name'] for a in before.active_skills] == ['parent']
    finished = {r['payload']['task_id']: r['payload'] for r in records if r['kind'] == 'task_finished'}
    children = []
    child_calls = set()
    for record in records:
        if record['kind'] != 'child_event':
            continue
        outer = record['payload']
        payload = outer['payload']
        if outer['kind'] == 'tool_result':
            child_calls.add(payload['message']['tool_call_id'])
        if outer['kind'] == 'task_finished':
            parent = finished[outer['parent_task_id']]
            assert payload['usage'] == parent['usage']
            children.append({'skill': outer['skill'], 'run_id': outer['run_id'],
                'parent_task_id': outer['parent_task_id'], 'reason': payload['reason'],
                'total_input_tokens': payload['usage']['total_input_tokens'],
                'output_tokens': payload['usage']['output_tokens'], 'usage_counted_once': True})
    assert not any(m.tool_call_id in child_calls for m in before.history if m.role == 'tool')
    assert (root / 'started').read_text() == 'started'
    assert not (root / 'leaked').exists() and not (root / 'forbidden.txt').exists()
    result = {'session_id': path.stem, 'requests': requests, 'children': children,
              'reset': {'before_messages': len(before.history), 'after_messages': len(after.history),
                        'after_active': after.active_skills},
              'artifacts': {'started': True, 'leaked': False, 'forbidden.txt': False,
                            '__pycache__': (root / '__pycache__').exists()}}
    (output / 'request-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'核验通过：{len(rows)} 个真实请求、{len(children)} 个独立运行；输入范围、用量、主历史隔离和 reset 均符合预期。')


if __name__ == '__main__':
    audit(Path(sys.argv[1]), Path(__file__).parent)
