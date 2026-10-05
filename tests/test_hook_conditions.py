"""扁平条件、缺失字段及实际路径语义。"""

import pytest


def event(**tool):
    from mewcode.hooks.events import HookEvent
    return HookEvent.create("tool.before", session_id="s", tool=tool)


def test_missing_and_wrong_type_never_match_negation():
    from mewcode.hooks.conditions import parse_condition
    rule = parse_condition({"any": [{"field": "tool.arguments.path", "match": "glob",
                                     "value": "src/*", "negate": True}]}, "tool.before")
    assert not rule.matches(event(name="execute_command", arguments={}))
    assert not rule.matches(event(name="read_file", arguments={"path": 10}))
    assert rule.matches(event(name="read_file", arguments={"path": "docs/a"}))


def test_all_any_and_path_vs_mcp_string():
    from mewcode.hooks.conditions import parse_condition
    atoms = [{"field": "tool.name", "match": "exact", "value": "read_file"},
             {"field": "tool.arguments.path", "match": "glob", "value": "src/*.py"}]
    assert not parse_condition({"all": atoms}, "tool.before").matches(
        event(name="read_file", arguments={"path": "src/deep/a.py"}))
    assert parse_condition({"any": atoms}, "tool.before").matches(
        event(name="read_file", arguments={"path": "src/deep/a.py"}))
    assert parse_condition({"all": atoms[1:]}, "tool.before").matches(
        event(name="mcp__test__read", arguments={"path": "src/deep/a.py"}))


@pytest.mark.parametrize("condition,event_name", [
    ({"all": []}, "tool.before"), ({"all": [], "any": []}, "tool.before"),
    ({"all": [{"any": []}]}, "tool.before"),
    ({"all": [{"field": "tool.name", "match": "exact", "value": "x"}]}, "session.start"),
    ({"all": [{"field": "tool.result.ok", "match": "exact", "value": True}]}, "tool.before"),
    ({"all": [{"field": "tool.arguments.0", "match": "exact", "value": 1}]}, "tool.before"),
    ({"all": [{"field": "tool.name", "match": "regex", "value": "["}]}, "tool.before"),
])
def test_invalid_conditions(condition, event_name):
    from mewcode.hooks.conditions import parse_condition
    with pytest.raises(ValueError):
        parse_condition(condition, event_name)
