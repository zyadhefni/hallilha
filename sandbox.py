"""Parent-side wrapper: runs analysis code in a separate process with a timeout."""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUNNER = Path(__file__).with_name("sandbox_runner.py")


def run_code(csv_path: str, date_cols: list[str], code: str, timeout: int = 40) -> dict:
    try:
        p = subprocess.run([sys.executable, "-E", str(RUNNER)],
                           input=json.dumps({"csv": str(csv_path), "date_cols": date_cols, "code": code}),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"TimeoutError: code ran longer than {timeout}s"}
    lines = [ln for ln in p.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {"ok": False, "error": "sandbox crashed: " + (p.stderr.strip().splitlines() or ["no output"])[-1][:200]}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": "sandbox returned invalid output"}


def run_many(csv_path: str, date_cols: list[str], codes: list[str], workers: int = 3) -> list[dict]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda c: run_code(csv_path, date_cols, c), codes))
