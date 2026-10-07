"""命令行入口。"""

import argparse
import asyncio
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MewCode 终端 AI 对话助手")
    parser.add_argument("--config", required=True, help="单组供应商配置的 .env 文件路径")
    parser.add_argument("--permission-mode", choices=("strict", "default", "bypass"), default="default",
                        help="权限模式，默认 default；bypass 仍遵守明确拒绝和硬限制")
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument('--resume', metavar='ID|latest', help='显式恢复当前项目的非活动会话')
    sessions.add_argument('--list-sessions', action='store_true', help='扫描存档，不启动模型或 MCP')
    sessions.add_argument('--team', metavar='NAME', help='显式恢复指定团队，与普通会话恢复互斥')
    sessions.add_argument('--team-worker', metavar='STARTUP', help=argparse.SUPPRESS)
    sessions.add_argument('--web', action='store_true', help='启动本机浏览器工作台，与终端恢复入口互斥')
    parser.add_argument('--port', type=int, help='Web 本机监听端口，默认 8765；仅 --web 可用')
    args = parser.parse_args(argv)
    if args.port is not None and not args.web:
        parser.error('--port 仅能与 --web 一起使用')
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port 必须在 1–65535 之间')
    if args.web:
        from .web.server import run
        return run(args.config, permission_mode=args.permission_mode, port=args.port or 8765)

    if args.team_worker:
        from .teams.worker import worker
        return asyncio.run(worker(Path(args.team_worker), Path(args.config)))

    from .app import run

    options = {'permission_mode': args.permission_mode}
    if args.resume:
        options['resume'] = args.resume
    if args.list_sessions:
        options['list_sessions'] = True
    if args.team:
        options['team'] = args.team
    return run(args.config, **options)
