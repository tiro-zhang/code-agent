"""共用匹配的完整值、类型和路径边界。"""

import pytest


@pytest.mark.parametrize("subject,pattern,path,want", [
    ("src/a.py", "src/**/*.py", True, True),
    ("src/deep/a.py", "src/*.py", True, False),
    ("src/deep/a.py", "src/**/*.py", True, True),
    ("a/b", "*", False, True),
    ("a/b", "*", True, False),
    ("src/A.py", "src/a.py", True, False),
    ("ab", "[ab]?", False, True),
])
def test_glob_boundaries(subject, pattern, path, want):
    from mewcode.matching import match_value
    assert match_value(subject, pattern, "glob", path=path) is want


@pytest.mark.parametrize("subject,value,want", [
    (True, 1, False), (1, 1.0, False), ("1", 1, False),
    ({"a": [True]}, {"a": [1]}, False),
    ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True),
    ([1, 2], [2, 1], False), (None, None, True),
])
def test_exact_json_types(subject, value, want):
    from mewcode.matching import match_value
    assert match_value(subject, value, "exact") is want


def test_regex_is_full_and_string_only():
    from mewcode.matching import match_value
    assert match_value("abc", "a.*", "regex")
    assert not match_value("abc", "b", "regex")
    assert not match_value(123, ".*", "regex")
    with pytest.raises(ValueError):
        match_value("a", "[", "regex")
