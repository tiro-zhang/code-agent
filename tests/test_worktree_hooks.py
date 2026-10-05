"""子 Hook 的目录与真实后台句柄归属。"""
import asyncio

from conftest import async_test
from test_hook_runtime import make_runtime, rule


@async_test
async def test_child_hook_command_runs_in_child_root(tmp_path):
    child=tmp_path/'child';child.mkdir()
    runtime=make_runtime(tmp_path,[rule(0,'command',command='pwd > actual')])
    view=runtime.scope('child',root=child,permissions=runtime.runner.permissions.fork(root=child))
    try:
        await view.emit('turn.end')
        await view.close()
        assert (child/'actual').read_text().strip()==str(child)
        assert not (tmp_path/'actual').exists() and not runtime.closed
    finally:
        await runtime.close()


@async_test
async def test_scope_close_waits_own_background_without_closing_sibling(tmp_path):
    runtime=make_runtime(tmp_path,[rule(0,'command',command='sleep .1; touch finished',background=True)])
    view=runtime.scope('child')
    try:
        await view.emit('turn.end')
        await view.close()
        assert (tmp_path/'finished').exists()
        assert runtime.background_count==0 and not runtime.closed
        await runtime.scope('sibling').emit('turn.end')
        await runtime.drain_background()
        assert runtime.background_count==0
    finally:
        await runtime.close()


@async_test
async def test_cancel_scope_drains_active_and_pending_owned_hooks(tmp_path):
    runtime=make_runtime(tmp_path,[rule(i,'command',command='sleep 30; touch too-late',background=True) for i in range(2)],max_running=1)
    view=runtime.scope('child')
    try:
        await view.emit('turn.end')
        cancel=asyncio.Event();cancel.set()
        await asyncio.wait_for(view.close(cancel_event=cancel),3)
        assert runtime.background_count==0 and not (tmp_path/'too-late').exists()
        await view.emit('turn.end')
        assert runtime.background_count==0
    finally:
        await runtime.close()
