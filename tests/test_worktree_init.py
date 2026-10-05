"""目录枚举预算在收集阶段生效。"""

import pytest


def test_file_enumeration_is_bounded_before_collecting_directory(tmp_path,monkeypatch):
    from mewcode.worktrees.initialize import enumerate_files
    import os
    for index in range(520): (tmp_path/str(index)).touch()
    def forbidden(*args): raise AssertionError('不能无界收集目录再检查预算')
    monkeypatch.setattr(os,'listdir',forbidden)
    with pytest.raises(ValueError): list(enumerate_files(tmp_path,limit=512))
