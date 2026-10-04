"""在 SDK 启动的同一个进程中固定环境、记录回收身份并 exec 目标。"""

import json
import os
from pathlib import Path
import sys


if __name__ == "__main__":
    pid_path, env_keys, command, *arguments = sys.argv[1:]
    Path(pid_path).write_text(str(os.getpid()), encoding="ascii")
    environment = {key: os.environ[key] for key in json.loads(env_keys)}
    os.execve(command, [command, *arguments], environment)
