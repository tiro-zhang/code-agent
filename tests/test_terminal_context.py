"""常驻上下文展示真实配置，并按可用宽高保留当前阶段。"""

from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.data_structures import Size
from prompt_toolkit.utils import get_cwidth

from mewcode.permissions.terminal import InputReader
from mewcode.terminal.controller import TerminalController


def test_context_uses_effective_session_and_sanitizes_model_and_root():
    terminal = TerminalController(InputReader(StringIO()), StringIO(), secret='secret',
                                  root='/old', on_interrupt=lambda: None)
    session = SimpleNamespace(tasks=None, mode='plan', permissions=SimpleNamespace(mode='strict'),
        agent=SimpleNamespace(max_iterations=20), config=SimpleNamespace(model='real-secret-model\x1b'),
        executor=SimpleNamespace(context=SimpleNamespace(root='/effective/secret/项目')))
    terminal.sync_session(session)
    terminal.set_phase('idle')
    text = terminal.context_text(width=110)
    assert 'real-[已隐藏]-model' in text and '/effective/' in text
    assert 'PLAN' in text and 'strict' in text and '空闲' in text
    assert 'secret' not in text and '\x1b' not in text
    assert terminal.root == '/effective/secret/项目'
    narrow = terminal.context_text(width=45)
    assert 'PLAN' in narrow and 'strict' in narrow and '空闲' in narrow
    assert len(narrow.splitlines()) <= 3
    assert all(get_cwidth(line) <= 45 for line in narrow.splitlines())


def test_context_shortens_middle_of_directory_preserving_project_name():
    terminal = TerminalController(InputReader(StringIO()), StringIO(), secret='',
        root='/long/' + 'parent/' * 50 + 'important-project', on_interrupt=lambda: None)
    terminal.model = 'actual-model'
    text = terminal.context_text(width=45)
    assert 'important-project' in text and '…' in text
