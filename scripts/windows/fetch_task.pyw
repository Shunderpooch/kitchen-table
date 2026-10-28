"""Scheduled task: `fetch run` once a day without a console window (run with pythonw).

Does nothing until `fetch connect` has stored an access URL. Output goes to
data/fetch/fetch_task.log.
"""

import runpy
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if not (REPO / "data" / "secrets" / "simplefin_access_url").exists():
    sys.exit(0)
LOG = REPO / "data" / "fetch" / "fetch_task.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
if LOG.exists() and LOG.stat().st_size > 1_000_000:
    LOG.replace(LOG.with_suffix(".old.log"))

with open(LOG, "a", encoding="utf-8") as log:
    sys.stdout = sys.stderr = log
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    sys.path.insert(0, str(REPO / "backend"))
    sys.argv = ["src.main", "fetch", "run", *sys.argv[1:]]
    try:
        runpy.run_module("src.main", run_name="__main__", alter_sys=True)
    except SystemExit as exit_:
        if exit_.code not in (0, None):
            print(f"exit {exit_.code}")
