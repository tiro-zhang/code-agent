"""本机单进程 Web 启动与有界退出；启动本身不创建运行会话。"""

import asyncio
from pathlib import Path
import socket
import sys

import uvicorn

from ..config import ConfigError, load_config
from ..runtime import RuntimeResources
from ..tools.base import ToolError
from .assets import validate_assets
from .manager import WebManager
from .security import LocalSecurity, Redactor


class LocalServer(uvicorn.Server):
    def __init__(self, config, manager):
        super().__init__(config)
        self.manager = manager

    def handle_exit(self, sig, frame):
        # 先终止事件长连接，否则 Uvicorn 会在进入 lifespan 前等待 SSE。
        self.manager.begin_shutdown()
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets=None):
        self.manager.begin_shutdown()
        try:
            await asyncio.wait_for(super().shutdown(sockets=sockets), timeout=30)
        except TimeoutError:
            sys.stderr.write('Web 收尾达到期限，未完成状态请在下次启动时核查。\n')


def run(config_path, *, permission_mode='default', port=8765):
    """先验证配置、命令和静态入口，再绑定唯一精确本机端口。"""
    config = None
    try:
        config = load_config(config_path)
        root = Path.cwd().resolve()
        RuntimeResources(config, root=root, permission_mode=permission_mode).validate()
        static_root = validate_assets()
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('本机端口必须在 1–65535 之间')
    except (ConfigError, ValueError, ToolError, OSError) as error:
        safe = Redactor(config.api_key if config is not None else '').text(error)
        sys.stderr.write('Web 启动失败：' + safe + '\n')
        return 2
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', port))
            listener.listen(128)
            listener.setblocking(False)
        except OSError:
            sys.stderr.write(f'Web 启动失败：本机端口 {port} 无法监听，请检查占用或权限。\n')
            return 2
        from .api import create_app
        manager = WebManager(config, root=root, config_path=Path(config_path).expanduser().resolve(),
                             permission_mode=permission_mode)
        security = LocalSecurity(port, server_instance_id=manager.server_instance_id)
        app = create_app(manager, security, static_root=static_root)
        server = LocalServer(uvicorn.Config(app, host='127.0.0.1', port=port,
            workers=1, access_log=False, log_level='warning', timeout_graceful_shutdown=30,
            proxy_headers=False, server_header=False), manager)
        sys.stdout.write(f'MewCode Web · 仅本机访问\n{security.url}\n')
        sys.stdout.flush()
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            return 0
        return 0 if server.started else 2
    finally:
        listener.close()
