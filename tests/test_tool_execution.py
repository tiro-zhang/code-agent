"""通过真实进程验证 shell、超时、取消和搜索，不只检查异常名称。"""

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import pytest


class BlockingTool:
    name = "block"
    description = "用于验证终止"
    input_schema = {"type": "object"}

    def execute(self, arguments, context):
        time.sleep(20)


class FailingTool(BlockingTool):
    name = "fail"

    def execute(self, arguments, context):
        raise RuntimeError("api-secret must never appear")


@pytest.fixture
def runner(tmp_path):
    from mewcode.tools import default_registry
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    return ToolExecutor(default_registry(), ToolContext(tmp_path))


def test_shell_pipeline_redirection_exit_and_cwd(runner, tmp_path):
    result = runner.execute("execute_command", json.dumps({"command": "printf cat | tr a o > out; cat out; printf err >&2; exit 7"}))
    assert not result.ok and result.error["code"] == "command_failed"
    assert result.data == {"exit_code": 7, "stdout": "cot", "stderr": "err"}
    assert (tmp_path / "out").read_text() == "cot"
    runner.execute("execute_command", '{"command":"cd /; export MEW_TMP=changed"}')
    result = runner.execute("execute_command", '{"command":"pwd; printf ${MEW_TMP-unset}"}')
    assert result.data["stdout"] == str(tmp_path.resolve()) + "\nunset"


def test_large_command_output_is_bounded_and_does_not_deadlock(runner):
    command = "yes x | head -c 200000; yes e | head -c 200000 >&2"
    result = runner.execute("execute_command", json.dumps({"command": command}))
    assert result.ok and result.truncated
    assert len(result.data["stdout"].encode()) <= 32768
    assert len(result.data["stderr"].encode()) <= 32768


def test_timeout_kills_descendants_and_keeps_partial_output(runner, tmp_path):
    command = "printf started; (sleep 3; printf leaked > should-not-exist) & wait"
    result = runner.execute("execute_command", json.dumps({"command": command, "timeout_seconds": 1}))
    assert result.error["code"] == "timeout"
    assert result.data["stdout"] == "started"
    assert result.error["details"]["side_effects_may_have_occurred"]
    time.sleep(2.2)
    assert not (tmp_path / "should-not-exist").exists()


def test_non_command_tool_timeout_and_exception_are_contained(tmp_path):
    from mewcode.tools.base import ToolContext
    from mewcode.tools.executor import ToolExecutor
    from mewcode.tools.registry import ToolRegistry
    runner = ToolExecutor(ToolRegistry([BlockingTool(), FailingTool()]), ToolContext(tmp_path), timeout=1)
    assert runner.execute("block", "{}").error["code"] == "timeout"
    result = runner.execute("fail", "{}")
    assert result.error["code"] == "execution_error"
    assert "api-secret" not in result.to_json()


def test_cancel_returns_result_and_stops_process(runner, tmp_path):
    timer = threading.Timer(1, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        result = runner.execute("execute_command", '{"command":"sleep 3; touch cancelled-leak"}')
    finally:
        timer.cancel()
        timer.join()
    assert result.error["code"] == "cancelled"
    assert not (tmp_path / "cancelled-leak").exists()


def test_glob_and_search_honor_ignores_order_and_result_bounds(runner, tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "b.py").write_text("def 猫():\n    pass\n")
    (tmp_path / "a.py").write_text("def dog():\n    pass\n")
    (tmp_path / "ignored.py").write_text("def ignored(): pass")
    (tmp_path / ".hidden.py").write_text("def hidden(): pass")
    (tmp_path / "binary.py").write_bytes(b'def\x00abc')
    result = runner.execute("glob_files", '{"pattern":"**/*.py","max_results":2}')
    assert result.data["paths"] == ["a.py", "b.py"] and result.truncated
    result = runner.execute("search_code", '{"pattern":"^def","glob":"*.py"}')
    assert [(m["path"], m["line"]) for m in result.data["matches"]] == [("a.py", 1), ("b.py", 1)]
    assert "猫" in result.data["matches"][1]["text"]
    assert runner.execute("search_code", '{"pattern":"not_found"}').data["matches"] == []
    assert runner.execute("search_code", '{"pattern":"["}').error["code"] == "invalid_pattern"
    assert runner.execute("search_code", '{"pattern":"-x"}').ok
    assert runner.execute("glob_files", '{"pattern":"*.no"}').data["paths"] == []


def test_search_skips_outside_links_and_reports_missing_rg(runner, tmp_path, monkeypatch):
    outside = tmp_path.with_name(tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "external.py").write_text("secret-marker")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    (tmp_path / "linked.py").symlink_to(outside / "external.py")
    assert runner.execute("search_code", '{"pattern":"secret-marker"}').data["matches"] == []
    assert runner.execute("glob_files", '{"pattern":"*.py"}').data["paths"] == []
    monkeypatch.setenv("PATH", str(tmp_path))
    assert runner.execute("search_code", '{"pattern":"x"}').error["code"] == "dependency_missing"


def test_executor_rejects_invalid_input_before_process_side_effect(runner, tmp_path):
    result = runner.execute("execute_command", '{"command":"touch bad","timeout_seconds":0}')
    assert result.error["code"] == "invalid_arguments" and not (tmp_path / "bad").exists()
    assert runner.execute("unknown", "{}").error["code"] == "unknown_tool"


def test_search_truncates_large_lines_and_sorts_relative_paths(runner,tmp_path):
    (tmp_path/'a').mkdir()
    for name in ['a/z.py','a+.py']:
        (tmp_path/name).write_text('marker\n')
    glob=runner.execute('glob_files','{"pattern":"**/*.py"}')
    assert glob.data['paths']==sorted(glob.data['paths'])
    result=runner.execute('search_code','{"pattern":"marker","glob":"**/*.py"}')
    assert [m['path'] for m in result.data['matches']]==sorted(m['path'] for m in result.data['matches'])
    (tmp_path/'long.txt').write_text('猫'*100000)
    result=runner.execute('search_code','{"pattern":"猫","glob":"*.txt"}')
    assert result.ok and result.truncated
    assert sum(len(m['text'].encode()) for m in result.data['matches'])<=65536


def test_search_drops_matches_from_later_detected_binary_files(runner,tmp_path):
    (tmp_path/'binary').write_bytes(b'marker\n'+b'x\n'*100000+b'\0\n')
    (tmp_path/'text').write_text('marker\n')
    result=runner.execute('search_code','{"pattern":"marker","max_results":1}')
    assert [m['path'] for m in result.data['matches']]==['text']


@pytest.mark.parametrize('timeout', [False,True])
def test_invalid_utf8_expansion_marks_truncation_in_normal_and_partial_output(runner,timeout):
    import shlex
    script="import os,time; os.write(1,bytes([255])*20000)"+("; time.sleep(5)" if timeout else "")
    result=runner.execute('execute_command',json.dumps({'command':shlex.join([sys.executable,'-c',script]),'timeout_seconds':1 if timeout else 30}))
    assert result.truncated
    assert len(result.data['stdout'].encode())<=32768
    assert result.error['code']=='timeout' if timeout else result.ok
