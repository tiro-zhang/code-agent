from pathlib import Path
import time
Path("started").write_text("started")
time.sleep(30)
Path("late").write_text("late")
