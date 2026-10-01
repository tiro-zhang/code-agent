"""以有界块排空子进程输出，避免管道死锁。"""

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import selectors
import subprocess


@contextmanager
def child_process(argv: list[str], cwd: Path):
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def output_chunks(process: subprocess.Popen) -> Iterator[tuple[str, bytes]]:
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            for key, _ in selector.select():
                chunk = os.read(key.fd, 8192)
                if chunk:
                    yield key.data, chunk
                else:
                    selector.unregister(key.fileobj)


def output_text(data: bytes, limit: int) -> tuple[str, bool]:
    """替换非法字节可能膨胀；返回文本及这次转换是否发生截断。"""
    encoded = data.decode("utf-8", errors="replace").encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(encoded) > limit
