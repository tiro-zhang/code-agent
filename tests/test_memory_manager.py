"""后台维护使用固定证据、独立计量和有界请求。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from conftest import ScriptedProvider, async_test
from mewcode.memory import MemoryManager
from mewcode.types import Message, ProviderError, ProviderEvent, TokenUsage, ToolCall
from test_memory_store import note_value, write_note


def evidence(user="这个项目使用 uv", task_id="task-one", **changes):
    value = {"session_id": "session-one", "task_id": task_id,
             "user_message": Message("user", user, id="user-one"),
             "final_message": Message("assistant", "已确认项目使用 uv", id="final-one"),
             "tools": (), "mode": "execute"}
    value.update(changes)
    return value


def operation(data=None, **changes):
    data = data or evidence()
    value = {"action": "add", "scope": "project", "category": "project_knowledge",
             "summary": "项目使用 uv", "content": "项目使用 uv 安装和运行。",
             "sources": [{"session_id": data["session_id"], "task_id": data["task_id"],
                          "message_id": data["user_message"].id}]}
    value.update(changes)
    return value


def response(operations):
    return [ProviderEvent("completed", message=Message("assistant", json.dumps({"operations": operations}, ensure_ascii=False))),
            ProviderEvent("usage", usage=TokenUsage(input_tokens=120, output_tokens=35, total_input_tokens=120, complete=True))]


@async_test
async def test_accepted_enqueue_immediately_reports_queued_without_waiting_for_worker(tmp_path):
    notifications = []
    provider = ScriptedProvider([response([])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    assert manager.enqueue(evidence())
    assert notifications
    queued = notifications[-1]
    assert queued["kind"] == "memory_update"
    assert queued["status"] == "queued"
    assert queued["purpose"] == "memory"
    assert queued["session_id"] == "session-one"
    assert queued["task_id"] == "task-one"
    assert "排队" in queued["text"]
    assert not provider.requests
    await manager.aclose()


@async_test
async def test_queue_freezes_evidence_and_records_separate_usage(tmp_path):
    notifications = []
    data = evidence()
    provider = ScriptedProvider([response([operation(data)])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    assert manager.enqueue(data)
    data["user_message"] = Message("user", "之后的新任务")
    await manager.aclose()
    text = manager.refresh()
    assert "项目使用 uv" in text
    assert "之后的新任务" not in provider.requests[0][0][0].content
    assert provider.requests[0][1]["tools"] == ()
    assert provider.requests[0][1]["tool_choice"] == "none"
    usages = [item for item in notifications if item["kind"] == "usage"]
    assert len(usages) == 1
    assert usages[0]["purpose"] == "memory"
    assert usages[0]["usage"].input_tokens == 120
    assert not manager.enqueue(evidence(task_id="late"))


@async_test
async def test_scope_validation_keeps_valid_project_candidate(tmp_path):
    data = evidence()
    provider = ScriptedProvider([response([
        operation(data, scope="user", category="user_preference", sources=[{
            "session_id": "session-one", "task_id": "task-one", "message_id": "user-one", "quote": "这个项目使用 uv"}]),
        operation(data),
    ])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    manager.enqueue(data)
    await manager.aclose()
    assert "项目使用 uv" in manager.refresh()
    assert not manager.user.snapshot().notes
    assert len(manager.project.snapshot().notes) == 1


@async_test
async def test_user_preference_requires_current_real_quote(tmp_path):
    data = evidence("以后所有项目的回答都尽量简短")
    good = operation(data, scope="user", category="user_preference", summary="回答尽量简短",
                     sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "user-one",
                               "quote": data["user_message"].content}])
    bad = operation(data, scope="user", category="user_preference", summary="始终使用 uv",
                    sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "final-one",
                              "quote": "所有项目始终使用 uv"}])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=ScriptedProvider([response([good, bad])]))
    manager.enqueue(data)
    await manager.aclose()
    notes = manager.user.snapshot().notes
    assert len(notes) == 1
    assert notes[0].summary == "回答尽量简短"


@async_test
async def test_no_request_when_complete_user_evidence_exceeds_budget(tmp_path):
    provider = ScriptedProvider([])
    config = SimpleNamespace(protocol="openai", context_window=18000, max_output_tokens=4000, api_key="secret")
    notifications = []
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, config=config, notify=notifications.append)
    manager.enqueue(evidence("完整用户要求" * 5000))
    await manager.aclose()
    assert not provider.requests
    assert any(item.get("status") == "skipped" for item in notifications)
    assert not manager.project.snapshot().notes


@async_test
async def test_background_queue_is_serial_and_close_cancels_stream(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hanging():
        entered.set()
        await release.wait()
        yield ProviderEvent("completed", message=Message("assistant", '{"operations":[]}'))

    provider = ScriptedProvider([hanging, response([operation(evidence(task_id="task-two"))])])
    notifications = []
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    assert manager.enqueue(evidence())
    assert manager.enqueue(evidence(task_id="task-two"))
    await entered.wait()
    assert len(provider.requests) == 1
    await manager.aclose(wait_seconds=0.01)
    assert provider.closed_streams == 1
    assert not provider.closed
    assert not manager.project.snapshot().notes
    assert any(item.get("status") == "cancelled" for item in notifications)


@async_test
async def test_malformed_toolcall_and_untrusted_source_never_write(tmp_path):
    bad = operation(sources=[{"session_id": "forged", "task_id": "task-one", "message_id": "user-one"}])
    provider = ScriptedProvider([
        [ProviderEvent("completed", message=Message("assistant", "[]"))],
        [ProviderEvent("completed", message=Message("assistant", "", (ToolCall("call", "write", "{}"),)))],
        response([bad]),
    ])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    for index in range(3):
        manager.enqueue(evidence(task_id=f"task-{index}"))
    await manager.aclose()
    assert not manager.project.snapshot().notes
    assert provider.closed_streams == 3


@pytest.mark.parametrize("changes", [
    {"reason": "cancelled"}, {"reason": "stream_error"}, {"reason": "max_iterations"},
    {"reason": "context_blocked"}, {"final_message": Message("assistant", " ")},
    {"final_message": Message("assistant", "", (ToolCall("call", "read", "{}"),))},
])
@async_test
async def test_non_natural_completion_is_not_enqueued(tmp_path, changes):
    provider = ScriptedProvider([])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    assert not manager.enqueue(evidence(**changes))
    await manager.aclose()
    assert not provider.requests


@async_test
async def test_two_indexes_share_one_injection_limit_and_refresh_version(tmp_path):
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=ScriptedProvider([]))
    for index in range(110):
        write_note(manager.project.root, note_value(f"{index:032x}", summary="项目知识" + "界" * 190))
        write_note(manager.user.root, note_value(f"{index + 110:032x}", scope="user", category="user_preference",
                   summary="用户偏好" + "界" * 190, sources=[{"session_id": "old-session", "task_id": "old-task",
                   "message_id": "old-user", "quote": "以后所有项目都保持统一的回复格式"}]))
    text = manager.refresh()
    assert len(text.splitlines()) <= 200
    assert len(text.encode()) <= 25000
    assert "用户偏好" in text
    assert "项目知识" not in text
    old_version = manager.version
    write_note(manager.project.root, note_value("e" * 32, category="correction", summary="项目纠正优先进入背景",
                                               updated_at="2026-10-03T00:00:00+00:00"))
    assert "项目纠正优先进入背景" in manager.refresh()
    assert manager.version != old_version
    assert manager.text.index("项目自动记忆") < manager.text.index("用户通用偏好")


@async_test
async def test_update_and_merge_preserve_old_sources(tmp_path):
    data = evidence()
    first = note_value()
    second = note_value("b" * 32, sources=[{"session_id": "old-session", "task_id": "old-task", "message_id": "old-message"}])
    old_ids = [first["id"], second["id"]]
    provider = ScriptedProvider([response([operation(data, action="merge", ids=old_ids, summary="项目统一使用 uv")])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    write_note(manager.project.root, first)
    write_note(manager.project.root, second)
    manager.enqueue(data)
    await manager.aclose()
    notes = manager.project.snapshot().notes
    assert len(notes) == 1
    assert notes[0].id not in old_ids
    assert {source["message_id"] for source in notes[0].sources} == {"message-one", "old-message", "user-one"}
    assert not any((manager.project.root / (identity + ".md")).exists() for identity in old_ids)


@async_test
async def test_summary_only_note_cannot_be_updated(tmp_path):
    data = evidence("项目问题" * 2000)
    value = operation(data, action="update", id="a" * 32, summary="不应覆盖的摘要")
    config = SimpleNamespace(protocol="openai", context_window=22000, max_output_tokens=8192, api_key="secret")
    provider = ScriptedProvider([response([value, operation(data, summary="合法的保守新增")])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, config=config)
    write_note(manager.project.root, note_value(content="界" * 3800))
    manager.enqueue(data)
    await manager.aclose()
    request = json.loads(provider.requests[0][0][0].content)
    assert request["summary_notes"]
    assert not request["full_notes"]
    notes = manager.project.snapshot().notes
    assert {note.summary for note in notes} == {"项目使用 uv 启动", "合法的保守新增"}


@async_test
async def test_manual_note_edit_during_model_request_wins(tmp_path):
    data = evidence()
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait_then_update():
        entered.set()
        await release.wait()
        for event in response([operation(data, action="update", id="a" * 32, summary="模型旧候选")]):
            yield event

    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=ScriptedProvider([wait_then_update]))
    write_note(manager.project.root, note_value())
    manager.enqueue(data)
    await entered.wait()
    write_note(manager.project.root, note_value(summary="用户在请求期间的新修正"))
    release.set()
    await manager.aclose()
    assert "用户在请求期间的新修正" in manager.refresh()
    assert "模型旧候选" not in manager.text


@async_test
async def test_model_paths_credentials_and_failed_tool_sources_are_rejected(tmp_path):
    tool = Message("tool", "写入失败", tool_call_id="call", id="tool-one")
    data = evidence(tools=(tool,))
    config = SimpleNamespace(protocol="openai", context_window=128000, max_output_tokens=8192, api_key="live-secret-123")
    bad_path = operation(data, path="../../outside.md")
    credential = operation(data, content="配置 api_key=live-secret-123")
    failed_tool = operation(data, sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "tool-one"}])
    provider = ScriptedProvider([response([bad_path, credential, failed_tool])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, config=config)
    manager.enqueue(data)
    await manager.aclose()
    assert not manager.project.snapshot().notes


@async_test
async def test_plan_evidence_does_not_claim_to_be_execute(tmp_path):
    data = evidence(mode="plan")
    provider = ScriptedProvider([response([])])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    manager.enqueue(data)
    await manager.aclose()
    request = json.loads(provider.requests[0][0][0].content)
    assert request["task"]["mode"] == "plan"
    assert "尚未执行的计划" in provider.requests[0][1]["system_prompt"]


@async_test
async def test_unavailable_user_store_does_not_prevent_project_update(tmp_path):
    data = evidence()
    provider = ScriptedProvider([response([operation(data)])])
    outside = tmp_path / "outside"
    outside.mkdir()
    user = tmp_path / "user"
    user.symlink_to(outside, target_is_directory=True)
    manager = MemoryManager(tmp_path, user_root=user, provider=provider)
    manager.enqueue(data)
    await manager.aclose()
    assert len(manager.project.snapshot().notes) == 1
    assert not list(outside.iterdir())


@async_test
async def test_request_deadline_closes_stream_without_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("mewcode.memory.manager.REQUEST_TIMEOUT", 0.01)
    entered = asyncio.Event()

    async def hanging():
        entered.set()
        await asyncio.Event().wait()
        yield ProviderEvent("completed", message=Message("assistant", '{"operations":[]}'))

    notifications = []
    provider = ScriptedProvider([hanging])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    manager.enqueue(evidence())
    await entered.wait()
    await manager.aclose()
    assert len(provider.requests) == 1
    assert provider.closed_streams == 1
    assert not manager.project.snapshot().notes
    assert any(item.get("status") == "failed" for item in notifications)


@async_test
async def test_usage_survives_provider_failure_and_internal_thinking_is_not_saved(tmp_path):
    notifications = []
    provider = ScriptedProvider([[ProviderEvent("thinking_delta", text="私有推理内容"),
        ProviderEvent("usage", usage=TokenUsage(input_tokens=99, complete=False)), ProviderError("供应商请求失败")]])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    manager.enqueue(evidence())
    await manager.aclose()
    assert not manager.project.snapshot().notes
    usages = [item for item in notifications if item["kind"] == "usage"]
    assert usages[0]["usage"].input_tokens == 99
    assert not usages[0]["usage"].complete
    assert all("私有推理内容" not in item.get("text", "") for item in notifications)


@async_test
async def test_repeated_close_cancellation_waits_for_provider_cleanup(tmp_path):
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_close():
        try:
            entered.set()
            await asyncio.Event().wait()
            yield ProviderEvent("completed", message=Message("assistant", '{"operations":[]}'))
        finally:
            closing.set()
            await release.wait()

    provider = ScriptedProvider([slow_close])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider)
    manager.enqueue(evidence())
    await entered.wait()
    close_task = asyncio.create_task(manager.aclose(wait_seconds=0.01))
    await closing.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    assert not close_task.done()
    assert provider.closed_streams == 0
    release.set()
    await close_task
    assert provider.closed_streams == 1


@async_test
async def test_failure_frequency_quote_cannot_promote_model_candidate_to_user(tmp_path):
    data = evidence("这段测试总是失败，请帮我排查")
    candidate = operation(data, scope="user", category="user_preference", summary="所有项目统一使用测试方案",
                          sources=[{"session_id": "session-one", "task_id": "task-one", "message_id": "user-one",
                                    "quote": data["user_message"].content}])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=ScriptedProvider([response([candidate])]))
    manager.enqueue(data)
    await manager.aclose()
    assert not manager.user.snapshot().notes


@async_test
async def test_shutdown_after_request_timeout_cannot_interrupt_provider_finally(tmp_path, monkeypatch):
    monkeypatch.setattr("mewcode.memory.manager.REQUEST_TIMEOUT", 0.01)
    closing, release = asyncio.Event(), asyncio.Event()
    state = {"completed": False, "interrupted": False}

    async def delayed_cleanup():
        try:
            await asyncio.Event().wait()
            yield ProviderEvent("completed", message=Message("assistant", '{"operations":[]}'))
        finally:
            closing.set()
            try:
                await release.wait()
                state["completed"] = True
            except asyncio.CancelledError:
                state["interrupted"] = True
                raise

    notifications = []
    provider = ScriptedProvider([delayed_cleanup])
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notifications.append)
    manager.enqueue(evidence())
    await closing.wait()
    close_task = asyncio.create_task(manager.aclose(wait_seconds=0.01))
    await asyncio.sleep(0.03)
    assert not close_task.done()
    assert not any(item["kind"] == "usage" for item in notifications)
    release.set()
    await close_task
    assert state["completed"]
    assert not state["interrupted"]
    assert provider.closed_streams == 1
    assert not manager.project.snapshot().notes


@async_test
async def test_each_job_reports_running_and_usage_precedes_final_result(tmp_path):
    data = evidence()
    provider = ScriptedProvider([response([operation(data)])])
    observations = []

    def notify(event):
        observations.append((event, provider.closed_streams))

    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=provider, notify=notify)
    manager.enqueue(data)
    await manager.aclose()
    task_events = [(event, closed) for event, closed in observations if event.get("task_id") == "task-one"]
    assert [event.get("status") for event, _ in task_events[:2]] == ["queued", "running"]
    usage_index = next(index for index, (event, _) in enumerate(task_events) if event["kind"] == "usage")
    final_index = next(index for index, (event, _) in enumerate(task_events) if event.get("status") == "updated")
    assert usage_index < final_index
    assert task_events[usage_index][1] == 1
    assert task_events[final_index][1] == 1


@async_test
async def test_shutdown_marks_each_queued_task_cancelled(tmp_path):
    entered = asyncio.Event()

    async def hanging():
        entered.set()
        await asyncio.Event().wait()
        yield ProviderEvent("completed", message=Message("assistant", '{"operations":[]}'))

    notifications = []
    manager = MemoryManager(tmp_path, user_root=tmp_path / "user", provider=ScriptedProvider([hanging]), notify=notifications.append)
    manager.enqueue(evidence(task_id="first"))
    manager.enqueue(evidence(task_id="queued"))
    await entered.wait()
    await manager.aclose(wait_seconds=0.01)
    states = {item["task_id"]: item.get("status") for item in notifications
              if item["kind"] == "memory_update" and item.get("task_id")}
    assert states == {"first": "cancelled", "queued": "cancelled"}
