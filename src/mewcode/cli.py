"""命令行入口。"""

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MewCode 终端 AI 对话助手")
    parser.add_argument("--config", required=True, help="单组供应商配置的 .env 文件路径")
    args = parser.parse_args(argv)

    from .app import run

    return run(args.config)
