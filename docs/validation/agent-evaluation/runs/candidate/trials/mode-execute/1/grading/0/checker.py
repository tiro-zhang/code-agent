"""独立功能校准，不依赖 agent 工作目录中的 check.py。"""
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
clamp = runpy.run_path(str(root / "limits.py"))["clamp"]
for value, lower, upper, expected in [(5, 0, 10, 5), (-2, 0, 10, 0), (12, 0, 10, 10),
                                      (0, 0, 10, 0), (10, 0, 10, 10), (4, 4, 4, 4), (1.5, 0, 1, 1)]:
    assert clamp(value, lower, upper) == expected
print("independent clamp checks passed")
