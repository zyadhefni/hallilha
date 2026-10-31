"""Web server: JSON API over the pipeline + the static website.

  python3 server.py                        # http://127.0.0.1:8000
  LLM_BACKEND=fake python3 server.py       # offline demo with the rule-based stand-in model
"""
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gen_data import DOMAINS, LABELS, QUESTIONS, make_dataset
from llm import get_llm
from pipeline import STAGES, TITLES, Engine, Options

STATIC = Path(__file__).parent / "static"
MAX_BYTES = 5_000_000

app = FastAPI(title="Hallilha")
_engine: Engine | None = None
_lock = threading.Lock()


def engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            try:
                _engine = Engine(get_llm(), Options())
            except Exception as e:
                raise HTTPException(503, f"LLM not available: {e}")
        return _engine


def _run(rid: str):
    run = engine().get(rid)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


class NewRun(BaseModel):
    filename: str = "data.csv"
    csv_text: str
    question: str = ""
    autopilot: bool = False


class SampleRun(BaseModel):
    domain: str
    autopilot: bool = False
    question: str = ""


class Note(BaseModel):
    note: str = ""


class Toggle(BaseModel):
    disabled: list[int] = []


class Auto(BaseModel):
    on: bool


@app.get("/api/info")
def info():
    return {"stages": [{"key": s, "title": TITLES[s]} for s in STAGES],
            "samples": [{"domain": d, "label": LABELS[d], "question": QUESTIONS[d]} for d in DOMAINS]}


@app.get("/api/runs")
def list_runs():
    return engine().list_runs()


@app.post("/api/runs")
def create_run(body: NewRun):
    if len(body.csv_text.encode("utf-8")) > MAX_BYTES:
        raise HTTPException(413, "File too large (limit 5 MB)")
    if body.csv_text.count("\n") < 2:
        raise HTTPException(400, "The file needs a header row and at least one data row")
    eng = engine()
    run = eng.new_run(body.csv_text, body.filename[:120], body.question[:500], body.autopilot)
    try:
        run.df_raw()
    except Exception as e:
        raise HTTPException(400, f"Could not read the file as CSV: {e}")
    eng.start(run)
    return {"id": run.id}


@app.post("/api/runs/sample")
def create_sample(body: SampleRun):
    if body.domain not in DOMAINS:
        raise HTTPException(400, "Unknown sample")
    ds = make_dataset(body.domain, seed=7)
    eng = engine()
    run = eng.new_run(ds.csv(), f"sample_{body.domain}.csv", body.question or QUESTIONS[body.domain], body.autopilot)
    eng.start(run)
    return {"id": run.id}


@app.get("/api/runs/{rid}")
def get_run(rid: str):
    run = _run(rid)
    with run.lock:
        return run.state


@app.post("/api/runs/{rid}/approve")
def approve(rid: str):
    run = _run(rid)
    try:
        engine().approve(run)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/runs/{rid}/revise")
def revise(rid: str, body: Note):
    run = _run(rid)
    try:
        engine().revise(run, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/runs/{rid}/clean/toggle")
def clean_toggle(rid: str, body: Toggle):
    run = _run(rid)
    try:
        engine().override_clean(run, body.disabled)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/runs/{rid}/autopilot")
def autopilot(rid: str, body: Auto):
    run = _run(rid)
    with run.lock:
        run.state["autopilot"] = body.on
        run.save()
        st = run.stage(run.state["current"])
        go = body.on and st["status"] == "review" and not st["flags"]
    if go:
        engine().approve(run, auto=True)
    return {"ok": True}


@app.get("/api/runs/{rid}/chart/{name}")
def chart(rid: str, name: str):
    run = _run(rid)
    p = run.dir / "charts" / Path(name).name
    if not p.exists():
        raise HTTPException(404, "Chart not found")
    return FileResponse(p, media_type="image/png")


@app.get("/api/runs/{rid}/clean.csv")
def clean_csv(rid: str):
    run = _run(rid)
    if not run.clean_csv.exists():
        raise HTTPException(404, "Not cleaned yet")
    return FileResponse(run.clean_csv, media_type="text/csv", filename=f"cleaned_{run.state['filename']}")


@app.get("/api/runs/{rid}/report.html")
def report(rid: str):
    run = _run(rid)
    p = run.dir / "report.html"
    if not p.exists():
        raise HTTPException(404, "The report is created after the last review")
    return FileResponse(p, media_type="text/html")


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="site")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port)
