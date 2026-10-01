"""真实文件操作的边界与失败时不改写保证。"""

import json
import os
from pathlib import Path
import pytest


@pytest.fixture
def invoke(tmp_path):
    from mewcode.tools.base import ToolContext
    from mewcode.tools.files import ReadFile, WriteFile, EditFile
    from mewcode.tools.registry import ToolRegistry
    registry = ToolRegistry([ReadFile(), WriteFile(), EditFile()])
    return lambda name, **args: registry.invoke(name, json.dumps(args), ToolContext(tmp_path))


def test_write_creates_parents_and_never_overwrites(invoke, tmp_path):
    result = invoke("write_file", path="sub/a.py", content="猫\r\n")
    assert result.ok and result.data["bytes_written"] == 5
    target = tmp_path / "sub/a.py"
    assert target.read_bytes() == "猫\r\n".encode()
    result = invoke("write_file", path="sub/a.py", content="改写")
    assert result.error["code"] == "file_exists"
    assert target.read_bytes() == "猫\r\n".encode()
    (tmp_path / "broken").symlink_to("missing")
    assert invoke("write_file", path="broken", content="x").error["code"] == "file_exists"
    assert invoke("write_file", path="sub", content="x").error["code"] == "file_exists"


def test_paths_reject_traversal_prefix_and_outside_symlinks(invoke, tmp_path):
    outside = tmp_path.with_name(tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "secret").write_text("outside")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    for path in [str(outside / "secret"), "../" + outside.name + "/secret", "escape/secret"]:
        assert invoke("read_file", path=path).error["code"] == "path_outside_workspace"
    assert invoke("write_file", path="escape/new", content="no").error["code"] == "path_outside_workspace"
    assert not (outside / "new").exists()
    (tmp_path / "local").write_text("inside")
    assert invoke("read_file", path=str(tmp_path / "local")).data["content"] == "inside"


def test_read_ranges_empty_encoding_and_special_files(invoke, tmp_path):
    (tmp_path / "text").write_bytes("一\r\n二\n三".encode())
    result = invoke("read_file", path="text", start_line=2, max_lines=1)
    assert result.data["content"] == "二\n"
    assert (result.data["start_line"], result.data["end_line"]) == (2, 2)
    assert result.truncated
    assert invoke("read_file", path="text", start_line=20).data["content"] == ""
    (tmp_path / "empty").touch()
    assert invoke("read_file", path="empty").data["content"] == ""
    assert invoke("read_file", path="missing").error["code"] == "file_not_found"
    assert invoke("read_file", path=".").error["code"] == "not_regular_file"
    for data in [b'\xff', b'a\x00b']:
        (tmp_path / "binary").write_bytes(data)
        assert invoke("read_file", path="binary").error["code"] == "unsupported_encoding"
    os.mkfifo(tmp_path / "fifo")
    assert invoke("read_file", path="fifo").error["code"] == "not_regular_file"


def test_read_long_utf8_line_is_bounded(invoke, tmp_path):
    (tmp_path / "long").write_text("猫" * 100000)
    result = invoke("read_file", path="long")
    assert result.ok and result.truncated
    assert len(result.data["content"].encode()) <= 65536
    assert set(result.data["content"]) == {"猫"}


@pytest.mark.parametrize("old,code,count", [("zero", "text_not_found", 0), ("aa", "multiple_matches", 2), ("", "invalid_arguments", None)])
def test_edit_rejects_zero_multiple_overlapping_and_empty(invoke, tmp_path, old, code, count):
    target = tmp_path / "text"
    target.write_bytes(b'aaa\r\n')
    result = invoke("edit_file", path="text", old_text=old, new_text="x")
    assert result.error["code"] == code
    if count is not None:
        assert result.error["details"]["matches"] == count
    assert target.read_bytes() == b'aaa\r\n'


def test_edit_preserves_other_bytes_and_permissions(invoke, tmp_path):
    target = tmp_path / "text"
    target.write_bytes("一\r\n唯一\r\n尾".encode())
    target.chmod(0o751)
    result = invoke("edit_file", path="text", old_text="唯一", new_text="")
    assert result.ok and result.data["replacements"] == 1
    assert target.read_bytes() == "一\r\n\r\n尾".encode()
    assert target.stat().st_mode & 0o777 == 0o751


def test_file_input_limits_do_not_partially_write(invoke, tmp_path):
    result = invoke("write_file", path="large", content="x" * (1048576 + 1))
    assert result.error["code"] == "input_too_large" and not (tmp_path / "large").exists()
    (tmp_path / "large").write_bytes(b'x' * (1048576 + 1))
    result = invoke("edit_file", path="large", old_text="xx", new_text="a")
    assert result.error["code"] == "input_too_large"
    assert (tmp_path / "large").stat().st_size == 1048577


def test_create_race_keeps_competing_file(invoke, tmp_path, monkeypatch):
    from mewcode.tools import files
    original = os.link
    def race(source, target, **kwargs):
        Path(target).write_text("other writer")
        return original(source, target, **kwargs)
    monkeypatch.setattr(files.os, "link", race)
    assert invoke("write_file", path="race", content="mine").error["code"] == "file_exists"
    assert (tmp_path / "race").read_text() == "other writer"


def test_edit_detects_change_before_commit(invoke, tmp_path, monkeypatch):
    from mewcode.tools import files
    target = tmp_path / "race"
    target.write_text("original")
    original = files.stage_file
    def race(*args, **kwargs):
        temporary = original(*args, **kwargs)
        target.write_text("external change")
        return temporary
    monkeypatch.setattr(files, "stage_file", race)
    assert invoke("edit_file", path="race", old_text="original", new_text="mine").error["code"] == "file_changed"
    assert target.read_text() == "external change"
