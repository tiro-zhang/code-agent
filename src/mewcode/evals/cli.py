"""显式启动真实测评，或只读取证据完成离线比较。"""
import argparse
import asyncio
from pathlib import Path
import signal
import sys

from .suite import EvaluationError


def parser():
    result = argparse.ArgumentParser(prog='mewcode-eval', description='核心 agent 日常回归与版本／模型／策略比较')
    commands = result.add_subparsers(dest='command',required=True)
    run = commands.add_parser('run',help='运行可信任务；将发起真实模型请求')
    run.add_argument('--config',required=True,help='模型 .env 文件，须显式指定')
    run.add_argument('--suite',required=True,help='可信 suite.yaml 或任务集目录')
    run.add_argument('--output',required=True,help='新的证据目录，不能已存在')
    run.add_argument('--label',default='',help='运行说明')
    run.add_argument('--case',action='append',default=[],help='只运行指定用例；可重复')
    run.add_argument('--repeat',type=int,default=1,help='每例串行尝试次数，默认 1')
    compare = commands.add_parser('compare',help='离线比较完整证据，不调用模型或工具')
    compare.add_argument('--baseline',required=True)
    compare.add_argument('--candidate',required=True)
    compare.add_argument('--kind',choices=('code','model','strategy'),required=True)
    compare.add_argument('--output',required=True,help='新的比较报告目录')
    return result


async def run(args, config, suite):
    from .runner import run_suite
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT,cancel.set)
    try:
        report = await run_suite(suite,config,args.output,repeat=args.repeat,selected=args.case,label=args.label,cancel_event=cancel)
    finally:
        loop.remove_signal_handler(signal.SIGINT)
    rate = f"{report['automatic']['passed']}/{report['automatic']['denominator']}" if report['automatic']['denominator'] else '不可计算（有效分母为 0）'
    print(f"自动通过 {rate}；证据：{args.output}")
    if cancel.is_set():
        return 130
    if report['counts']['harness_error']:
        return 2
    return 0 if report['automatic']['passed']==report['planned'] else 1


def bounded_loop(awaitable):
    """收尾状态未知也须结束命令，不无界等待拒绝取消的协程。"""
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(awaitable)
    finally:
        pending=asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.wait(pending,timeout=.25))
        for task in pending:
            if not task.done():
                # 已在结果中记录副作用未知；关闭循环不再调度该任务。
                task._log_destroy_pending=False
        loop.close()
        asyncio.set_event_loop(None)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command=='compare':
            from .artifacts import load_run, write_json
            from .compare import compare_runs, markdown
            from .report import read_reviews
            before, after = load_run(args.baseline), load_run(args.candidate)
            for root, evidence in ((args.baseline,before),(args.candidate,after)):
                evidence['reviews'] = read_reviews(root,evidence['manifest']['run_id'],evidence['results'])
            report = compare_runs(before,after,kind=args.kind)
            output = Path(args.output)
            if output.exists():
                raise EvaluationError('比较输出目录已存在')
            output.mkdir(parents=True,mode=0o700)
            write_json(output/'report.json',report)
            (output/'report.md').write_text(markdown(report))
            print(f"比较报告：{output}；控制条件兼容：{report['comparable']}")
            return 0 if report['comparable'] else 2
        from ..config import load_config, ConfigError
        from .suite import load_suite, preflight
        from .runner import validate_config
        config = load_config(args.config)
        validate_config(config)
        suite = load_suite(args.suite)
        preflight(suite,repeat=args.repeat,selected=args.case)
        return bounded_loop(run(args,config,suite))
    except KeyboardInterrupt:
        return 130
    except (EvaluationError, OSError, ValueError) as error:
        # 配置异常不包含密钥；不输出 traceback 或配置原文。
        print(f'测评失败：{error}',file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
