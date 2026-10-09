import json,runpy,sys,traceback
from pathlib import Path
checker, workspace, receipt = sys.argv[1:]
sys.argv = [checker, workspace]
try:
    runpy.run_path(checker, run_name="__main__")
except BaseException:
    traceback.print_exc()
    sys.exit(1)
Path(receipt).write_text(json.dumps({"completed": True}))
