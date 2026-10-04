"""交互查看不创建存储、不修复文件，也不依赖派生索引。"""
import os
import pytest
from mewcode.memory.store import MemoryStore
from test_memory_store import note_value, write_note


def fingerprint(root):
    return {str(p.relative_to(root)): (p.lstat().st_mode, p.lstat().st_mtime_ns,
            p.read_bytes() if p.is_file() and not p.is_symlink() else None)
            for p in root.rglob('*')}


def test_readonly_missing_store_stays_missing(tmp_path):
    store = MemoryStore(tmp_path)
    assert store.query().notes == ()
    assert not list(tmp_path.iterdir())


def test_readonly_notes_ignore_broken_index_and_preserve_permissions(tmp_path):
    store = MemoryStore(tmp_path)
    write_note(store.root, note_value(content='完整正文\n' + '内容' * 1000))
    (store.root / 'MEMORY.md').write_bytes(b'\xffbroken')
    (store.root / ('b' * 32 + '.md')).write_text('损坏')
    (store.root / ('a' * 32 + '.md')).chmod(0o644)
    before = fingerprint(tmp_path)
    snapshot = store.query()
    assert len(snapshot.notes) == 1 and snapshot.notes[0].content.endswith('内容' * 1000)
    assert store.diagnostics and fingerprint(tmp_path) == before
    assert not (store.root / '.lock').exists()


@pytest.mark.parametrize('unsafe', ['directory', 'note', 'hardlink', 'identity', 'fifo'])
def test_readonly_rejects_unsafe_notes(tmp_path, unsafe):
    store = MemoryStore(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    write_note(other, note_value())
    target = other / ('a' * 32 + '.md')
    if unsafe == 'directory':
        (tmp_path / '.mewcode').symlink_to(other, target_is_directory=True)
        with pytest.raises((ValueError, OSError)):
            store.query()
        return
    store.root.mkdir(parents=True)
    path = store.root / ('a' * 32 + '.md')
    if unsafe == 'note': path.symlink_to(target)
    elif unsafe == 'hardlink': os.link(target, path)
    elif unsafe == 'identity':
        path.write_bytes(target.read_bytes().replace(('a' * 32).encode(), ('b' * 32).encode()))
    else: os.mkfifo(path)
    assert not store.query().notes and store.diagnostics
    assert target.read_text().endswith('运行 uv run mewcode --config .env。')
