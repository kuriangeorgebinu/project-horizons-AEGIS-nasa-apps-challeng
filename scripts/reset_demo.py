"""
Clean, repeatable demo start. Run before a rehearsal or recording:

    python reset_demo.py

If the backend is running it resets everything through POST /api/demo/reset (database, simulator, agents'
memory, phone-alarm log). If it isn't running, it clears the database directly; the simulator starts clean
on the next backend start anyway. The dashboard shortcut Shift+R does the same.
"""
import json
import sys
import urllib.request
from pathlib import Path

# Run as `python scripts/reset_demo.py`: the repo root must be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

URL = "http://127.0.0.1:8000/api/demo/reset"

try:
    with urllib.request.urlopen(urllib.request.Request(URL, method="POST"), timeout=15) as r:
        out = json.loads(r.read())
    print("Demo reset through the running backend:", out.get("removed"))
except Exception:
    from app import database as db
    print("Backend not running: cleared the database directly:", db.reset_demo_data())
