"""按功能接受合法实现，不要求照抄参考代码。"""
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
slugify = runpy.run_path(str(root / "slug.py"))["slugify"]
for text, expected in [(" Hello   World ", "hello-world"), ("A\tB\nC", "a-b-c"),
                       ("", ""), ("   ", ""), ("中文 名称", "中文-名称")]:
    assert slugify(text) == expected
print("independent slug checks passed")
