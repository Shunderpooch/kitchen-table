"""Scheduled task: `scans inbox-tick` without a console window (run with pythonw).

pythonw has no console, so output goes to data/scans/inbox_task.log.
"""

import runpy
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOG = REPO / "data" / "scans" / "inbox_task.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
if LOG.exists() and LOG.stat().st_size > 1_000_000:
    LOG.replace(LOG.with_suffix(".old.log"))

with open(LOG, "a", encoding="utf-8") as log:
    sys.stdout = sys.stderr = log
    print(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}", flush=True)
    sys.path.insert(0, str(REPO / "backend"))
    sys.argv = ["src.main", "scans", "inbox-tick", *sys.argv[1:]]
    try:
        runpy.run_module("src.main", run_name="__main__", alter_sys=True)
    except SystemExit as exit_:
        if exit_.code not in (0, None):
            print(f"exit {exit_.code}")
