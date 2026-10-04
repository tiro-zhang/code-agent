"""自动笔记事实源、索引及并发边界。"""

import os
from dataclasses import replace

import pytest
import yaml

from mewcode.memory import MemoryStore
from mewcode.memory.store import Note, general_preference


def note_value(note_id="a" * 32, **changes):
    value = {
        "id": note_id, "category": "project_knowledge", "scope": "project",
        "summary": "项目使用 uv 启动", "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
        "sources": [{"session_id": "session-one", "task_id": "task-one", "message_id": "message-one"}],
        "content": "运行 uv run mewcode --config .env。",
    }
    value.update(changes)
    return value


def write_note(root, value):
    root.mkdir(parents=True, exist_ok=True)
    metadata = {key: value[key] for key in value if key != "content"}
    (root / (value["id"] + ".md")).write_text(
        "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n" + value["content"],
        encoding="utf-8",
    )


def test_refresh_rebuilds_stale_index_and_skips_broken_notes(tmp_path):
    store = MemoryStore(tmp_path)
    write_note(store.root, note_value())
    (store.root / "MEMORY.md").write_text("不存在的旧事实\n", encoding="utf-8")
    (store.root / ("b" * 32 + ".md")).write_text("坏 frontmatter", encoding="utf-8")
    snapshot = store.snapshot()
    assert len(snapshot.notes) == 1
    text = store.refresh()
    assert "项目使用 uv 启动" in text
    assert "a" * 32 + ".md" in text
    assert "不存在的旧事实" not in text
    assert store.diagnostics
    assert (store.root / "MEMORY.md").read_text(encoding="utf-8") == text


def test_private_permissions_and_local_ignore(tmp_path):
    store = MemoryStore(tmp_path)
    store.refresh()
    assert os.stat(store.root).st_mode & 0o777 == 0o700
    assert os.stat(store.root / "MEMORY.md").st_mode & 0o777 == 0o600
    assert "*" in (store.root / ".gitignore").read_text()


def test_index_bounds_preserve_unselected_notes_and_priority(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(220):
        write_note(store.root, note_value(f"{index:032x}", category="reference", summary="参考资料" + "界" * 195))
    write_note(store.root, note_value("f" * 32, category="user_preference", summary="偏好应被优先保留"))
    text = store.refresh()
    assert len(text.splitlines()) <= 200
    assert len(text.encode("utf-8")) <= 25000
    assert "偏好应被优先保留" in text
    assert len(list(store.root.glob("*.md"))) == 222


@pytest.mark.parametrize("target", ["directory", "note", "index", "lock"])
def test_symlinks_cannot_escape_store(tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    untouched = outside / "sentinel"
    untouched.write_text("保留")
    if target == "directory":
        (tmp_path / ".mewcode").symlink_to(outside, target_is_directory=True)
    else:
        root = tmp_path / ".mewcode" / "memory"
        root.mkdir(parents=True)
        name = {"note": "a" * 32 + ".md", "index": "MEMORY.md", "lock": ".lock"}[target]
        (root / name).symlink_to(untouched)
    try:
        store = MemoryStore(tmp_path)
        store.refresh()
    except (OSError, ValueError):
        pass
    assert untouched.read_text() == "保留"


def test_invalid_frontmatter_does_not_become_knowledge(tmp_path):
    store = MemoryStore(tmp_path)
    for index, changes in enumerate([
        {"summary": "多行\n摘要"}, {"summary": "a" * 201}, {"sources": []},
        {"category": "unknown"}, {"scope": "user"}, {"content": "界" * 6000},
        {"created_at": "yesterday"},
    ]):
        write_note(store.root, note_value(f"{index:032x}", **changes))
    assert not store.snapshot().notes
    assert "项目使用 uv 启动" not in store.refresh()


def test_unchanged_snapshot_can_commit_and_manual_changes_conflict(tmp_path):
    store = MemoryStore(tmp_path)
    write_note(store.root, note_value())
    snapshot = store.snapshot()
    updated = Note.from_dict(note_value(summary="已验证的更新"), scope="project")
    changed, messages = store.commit([(updated, ())], version=snapshot.version)
    assert changed == 1
    assert "已验证的更新" in store.refresh()
    snapshot = store.snapshot()
    write_note(store.root, note_value(summary="用户手工修正"))
    changed, messages = store.commit([(updated, ())], version=snapshot.version)
    assert changed == 0
    assert messages
    assert "用户手工修正" in store.refresh()


def test_failed_merge_target_does_not_delete_old_notes(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    first, second = note_value(), note_value("b" * 32)
    write_note(store.root, first)
    write_note(store.root, second)
    snapshot = store.snapshot()
    merged = Note.from_dict(note_value("c" * 32, summary="合并后的知识"), scope="project")
    atomic = store._atomic

    def fail_target(path, data):
        if path.name == "c" * 32 + ".md":
            raise OSError("模拟磁盘故障")
        return atomic(path, data)

    monkeypatch.setattr(store, "_atomic", fail_target)
    changed, messages = store.commit([(merged, (first["id"], second["id"]))], version=snapshot.version)
    assert changed == 0
    assert messages
    assert {note.id for note in store.snapshot().notes} == {first["id"], second["id"]}


def test_failed_index_keeps_committed_note_and_rebuilds_next_refresh(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path)
    store.refresh()
    snapshot = store.snapshot()
    old_index = (store.root / "MEMORY.md").read_bytes()
    new = Note.from_dict(note_value(), scope="project")
    atomic = store._atomic

    def fail_index(path, data):
        if path.name == "MEMORY.md":
            raise OSError("模拟索引故障")
        return atomic(path, data)

    monkeypatch.setattr(store, "_atomic", fail_index)
    changed, messages = store.commit([(new, ())], version=snapshot.version)
    assert changed == 1
    assert messages
    assert (store.root / "MEMORY.md").read_bytes() == old_index
    assert (store.root / (new.id + ".md")).exists()
    monkeypatch.setattr(store, "_atomic", atomic)
    assert "项目使用 uv 启动" in store.refresh()


def test_commit_revalidates_note_and_merge_id_before_writing(tmp_path):
    store = MemoryStore(tmp_path)
    snapshot = store.snapshot()
    note = Note.from_dict(note_value(), scope="project")
    changed, messages = store.commit([(replace(note, summary="非法\n多行"), ())], version=snapshot.version)
    assert changed == 0
    assert messages
    assert not store.snapshot().notes
    snapshot = store.snapshot()
    changed, messages = store.commit([(note, ("../../escape",))], version=snapshot.version)
    assert changed == 0
    assert messages
    assert not store.snapshot().notes


def test_other_process_holds_exclusive_store_lock(tmp_path):
    import subprocess
    import sys

    store = MemoryStore(tmp_path)
    store.refresh()
    script = "import fcntl,sys; f=open(sys.argv[1],'r+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.readline()"
    process = subprocess.Popen([sys.executable, "-c", script, str(store.root / ".lock")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(BlockingIOError):
            store.snapshot()
    finally:
        process.communicate("release\n", timeout=5)
    assert not store.snapshot().notes


def test_refresh_repairs_store_ignore_rules(tmp_path):
    store = MemoryStore(tmp_path)
    store.refresh()
    (store.root / ".gitignore").write_text("!*.md\n")
    store.refresh()
    assert (store.root / ".gitignore").read_text() == "*\n!.gitignore\n"


def test_exact_note_size_limit_survives_readback(tmp_path):
    store = MemoryStore(tmp_path)
    baseline = Note.from_dict(note_value(content="x"), scope="project")
    available = 16000 - len(baseline.encode()) + 1
    note = Note.from_dict(note_value(content="x" * available), scope="project")
    snapshot = store.snapshot()
    changed, messages = store.commit([(note, ())], version=snapshot.version)
    assert changed == 1
    assert len(store.snapshot().notes) == 1
    assert (store.root / (note.id + ".md")).stat().st_size == 16000


def test_hardlink_does_not_change_external_file_permissions(tmp_path):
    store = MemoryStore(tmp_path)
    store.refresh()
    outside = tmp_path / "outside.md"
    outside.write_text("外部文件")
    outside.chmod(0o644)
    os.link(outside, store.root / ("a" * 32 + ".md"))
    assert not store.snapshot().notes
    assert outside.stat().st_mode & 0o777 == 0o644


def test_line_limit_and_newest_then_stable_identity_selection(tmp_path):
    store = MemoryStore(tmp_path)
    for index in range(205):
        write_note(store.root, note_value(f"{index:032x}", summary=f"事实{index}"))
    write_note(store.root, note_value("f" * 32, summary="最新事实", updated_at="2026-10-03T00:00:00+00:00"))
    text = store.refresh()
    assert len(text.splitlines()) == 200
    assert "最新事实" in text.splitlines()[1]
    assert "事实0 " in text.splitlines()[2]
    assert "事实204 " not in text
    assert len(store.snapshot().notes) == 206


@pytest.mark.parametrize("quote", [
    "这段测试总是失败，请帮我排查", "接口始终报错", "程序一直没有结果",
    "The tests always fail, please investigate", "所有项目测试总是失败，请帮我排查",
    "以后所有项目测试都会失败",
    "所有项目都使用同一服务，因此故障同时出现", "所有项目必须安装这个依赖才能启动",
    "在所有项目里，请帮我排查故障", "所有项目测试总是失败，我希望排查原因",
])
def test_frequency_or_universal_state_does_not_imply_preference(quote):
    assert not general_preference(quote)


@pytest.mark.parametrize("quote", [
    "以后所有项目的回答都尽量简短", "以后所有项目都保持统一的回复格式",
    "请始终用中文回复", "Please always answer in Chinese",
])
def test_explicit_continuing_general_request_remains_a_preference(quote):
    assert general_preference(quote)


@pytest.mark.parametrize("quote", [
    "在所有项目里，我都偏好简短中文、最多三个要点，请记住。",
    "在所有项目里，我希望用中文",
])
def test_scope_introduction_can_precede_explicit_self_preference_across_comma(quote):
    assert general_preference(quote)
    value = note_value(scope="user", category="user_preference", summary="通用回复偏好",
                       sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "user-one", "quote": quote}])
    assert Note.from_dict(value, scope="user").sources[0]["quote"] == quote


def test_saved_real_general_preference_survives_store_restart(tmp_path):
    quote = "在所有项目里，我都偏好简短中文、最多三个要点，请记住。"
    store = MemoryStore(tmp_path / "user", scope="user")
    write_note(store.root, note_value(scope="user", category="user_preference", summary="回复使用简短中文，最多三个要点",
               sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "user-one", "quote": quote}]))
    assert "回复使用简短中文，最多三个要点" in store.refresh()
    restored = MemoryStore(tmp_path / "user", scope="user")
    assert "回复使用简短中文，最多三个要点" in restored.refresh()
    assert len(restored.snapshot().notes) == 1
