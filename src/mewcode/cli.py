"""命令行入口。"""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MewCode 终端 AI 对话助手")
    parser.add_argument("--config", required=True, help="单组供应商配置的 .env 文件路径")
    parser.add_argument("--permission-mode", choices=("strict", "default", "bypass"), default="default",
                        help="权限模式，默认 default；bypass 仍遵守明确拒绝和硬限制")
    args = parser.parse_args(argv)

    from .app import run

    return run(args.config, permission_mode=args.permission_mode)
