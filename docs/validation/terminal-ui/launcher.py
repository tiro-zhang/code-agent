"""使用现有授权配置和真实供应商；将可写状态限制在临时项目。"""
import os
from pathlib import Path
from mewcode.app import run

if __name__ == '__main__':
    root = Path(os.environ['MEWCODE_E2E_ROOT'])
    os.chdir(root)
    raise SystemExit(run(os.environ['MEWCODE_E2E_CONFIG'], user_root=root / 'user', memory_enabled=False))
