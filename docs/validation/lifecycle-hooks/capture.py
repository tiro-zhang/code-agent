"""保存当前验收窗口，脱敏模型密钥并移除终端控制字符。"""

from pathlib import Path
import subprocess
import sys
import re

from mewcode.config import load_config
from mewcode.terminal.text import terminal_text


config = load_config(sys.argv[1])
target = Path(sys.argv[2])
result = subprocess.run(['tmux', 'capture-pane', '-p', '-t', 'mewcode-e2e', '-S', '-100'],
                        check=True, capture_output=True, text=True)
safe = terminal_text(result.stdout, config.api_key, multiline=True, limit=None)
safe = re.sub(r'(?ms)^[^\n]*· API 思考\n.*?(?=^会话>|^上下文>|\Z)', '[API 思考正文已省略]\n', safe)
target.write_text(safe)
print(target.name)
