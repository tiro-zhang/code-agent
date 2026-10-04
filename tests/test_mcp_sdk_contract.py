"""锁定实际发布 SDK 的协议及副作用合同。"""

import asyncio
import json
from pathlib import Path
import sys

import pytest
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.client.streamable_http import streamable_http_client
import mcp_types as types

from mewcode.mcp.http import LifecycleHTTPClient, RequestLifetime

from conftest import async_test
from mcp_fixture import http_peer


FIXTURE = Path(__file__).with_name("mcp_fixture.py")


async def exercise(client, version):
    assert client.protocol_version == version
    first = await client.session.list_tools()
    second = await client.session.list_tools(params=types.PaginatedRequestParams(cursor=first.next_cursor))
    assert [t.name for t in first.tools + second.tools] == ["echo", "second"]
    slow = asyncio.create_task(client.session.call_tool("echo", {"text": "慢", "delay": .08}))
    fast = asyncio.create_task(client.session.call_tool("echo", {"text": "快"}))
    assert (await fast).content[0].text == "快"
    assert (await slow).content[0].text == "慢"
    pending = asyncio.create_task(client.session.call_tool("echo", {"delay": 1}))
    await asyncio.sleep(.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (await client.session.call_tool("echo", {"text": "取消后"})).content[0].text == "取消后"


@pytest.mark.parametrize("era,version", [("legacy", "2025-11-25"), ("modern", "2026-07-28")])
@async_test
async def test_stdio_protocol_pairing_cancel_and_reuse(tmp_path, era, version):
    log = tmp_path / "messages.jsonl"
    parameters = StdioServerParameters(command=sys.executable, args=[str(FIXTURE), "--era", era, "--log", str(log)])
    async with Client(parameters, mode="auto", cache=None) as client:
        await exercise(client, version)
    messages = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(m["method"] == "PROCESS_START" for m in messages) == 1
    assert sum(m["method"] == "initialize" for m in messages) == (era == "legacy")
    assert sum(m["method"] == "notifications/initialized" for m in messages) == (era == "legacy")
    assert sum(m["method"] == "tools/call" for m in messages) == 4


@pytest.mark.parametrize("era,version", [("legacy", "2025-11-25"), ("modern", "2026-07-28")])
@async_test
async def test_http_protocol_pairing_cancel_and_reuse(era, version):
    with http_peer(era) as (peer, url):
        async with Client(url, mode="auto", cache=None) as client:
            await exercise(client, version)
        assert sum(m["method"] == "tools/call" for m in peer.messages) == 4
        assert sum(m["method"] == "HTTP_DELETE" for m in peer.messages) == (era == "legacy")


@async_test
async def test_lowlevel_does_not_retry_header_mismatch_or_input_required():
    with http_peer() as (peer, url):
        async with Client(url, cache=None) as client:
            await client.session.list_tools()
            with pytest.raises(MCPError) as failure:
                await client.session.call_tool("echo", {"error": "header"}, allow_input_required=True)
            assert failure.value.code == types.HEADER_MISMATCH
            result = await client.session.call_tool("echo", {"input": True}, allow_input_required=True)
            assert isinstance(result, types.InputRequiredResult)
        assert sum(m["method"] == "tools/call" for m in peer.messages) == 2


@async_test
async def test_sse_recovery_get_never_reposts_call():
    with http_peer() as (peer, url):
        async with Client(url, cache=None) as client:
            await client.session.list_tools()
            result = await asyncio.wait_for(client.session.call_tool("echo", {"recover": True}), 8)
            assert result.content[0].text == "fixture-ok"
        assert sum(m["method"] == "tools/call" for m in peer.messages) == 1
        assert any(m["method"] == "HTTP_GET" and m["token"] for m in peer.messages)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("era", ["modern", "legacy"])
@async_test
async def test_recovery_stops_on_deadline_or_cancel_and_connection_survives(cancel, era):
    with http_peer(era) as (peer, url):
        peer.recovery_empty = True
        async with LifecycleHTTPClient() as http, Client(streamable_http_client(url, http_client=http), cache=None) as client:
            await client.session.list_tools()
            lifetime = RequestLifetime()
            async def call():
                with lifetime.bind():
                    try:
                        return await client.session.call_tool("echo", {"recover": True})
                    finally:
                        lifetime.close()
            operation = asyncio.create_task(call())
            if cancel:
                await asyncio.sleep(.1)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(operation, .1)
            before = sum(m["method"] == "HTTP_GET" for m in peer.messages)
            await asyncio.sleep(.12)
            after = sum(m["method"] == "HTTP_GET" for m in peer.messages)
            assert before == after, "已取消请求的响应恢复仍在运行"
            assert (await client.session.call_tool("echo", {"text": "仍可用"})).content[0].text == "仍可用"
        assert sum(m["method"] == "tools/call" for m in peer.messages) == 2
