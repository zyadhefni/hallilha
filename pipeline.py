"""The autonomous analyst: five stages, each followed by a human review gate.

  understand -> clean -> analyze -> visualize -> decide

The model plans and writes; code executes and verifies. After every stage the run stops at
`review`: the human sees exactly what the agent did, approves, or sends it back with a note.
With autopilot on, a stage that passed every check and raised no flags is approved automatically.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

import charts as charts_mod
import prompts as P
from cleaning import apply_plan, parse_ops
from dataprofile import detected_issues, profile_df
from grounding import build_pool, ungrounded
from sandbox import run_many
from schemas import (AnalysisPlan, AnalysisRepair, ChartPlan, CleanPlanRaw, Decision, Interpretation,
                     Understanding)

STAGES = ["understand", "clean", "analyze", "visualize", "decide"]
TITLES = {"understand": "Understand", "clean": "Clean", "analyze": "Analyze", "visualize": "Visualize", "decide": "Decide"}
ROOT = Path(__file__).parent / "runs"


@dataclass
class Options:
    verify: bool = True        # per-operation verification of cleaning
    grounding: bool = True     # number-grounding checks + repair
    max_clean_passes: int = 2


@dataclass
class Meter:
    calls: int = 0
    chars_in: int = 0
    chars_out: int = 0

    def tokens(self) -> int:
        return (self.chars_in + self.chars_out) // 4


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("no JSON object found")
    return json.loads(t[i:j + 1])


def call_json(llm, system: str, payload: dict, model_cls, meter: Meter, retries: int = 2):
    """One validated LLM call: invalid JSON or schema violations are fed back to the model and retried."""
    messages = [{"role": "user", "content": "INPUT:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)}]
    last = None
    for _ in range(retries + 1):
        raw = llm.generate(system, messages)
        meter.calls += 1
        meter.chars_in += len(system) + sum(len(m["content"]) for m in messages)
        meter.chars_out += len(raw or "")
        try:
            return model_cls.model_validate(parse_json(raw))
        except Exception as e:
            last = e
            messages += [{"role": "assistant", "content": raw or "(empty)"},
                         {"role": "user", "content": f"INVALID OUTPUT ({str(e)[:300]}). Reply with ONE valid JSON object only."}]
    raise RuntimeError(f"The model kept returning invalid output: {str(last)[:200]}")


# ====================================================================== run storage
class Run:
    def __init__(self, rid: str, root: Path = ROOT):
        self.id = rid
        self.dir = Path(root) / rid
        self.lock = threading.RLock()
        self.meter = Meter()
        self.state: dict = {}

    @classmethod
    def create(cls, csv_text: str, filename: str, question: str, autopilot: bool, root: Path = ROOT) -> "Run":
        r = cls(uuid.uuid4().hex[:10], root)
        r.dir.mkdir(parents=True, exist_ok=True)
        (r.dir / "raw.csv").write_text(csv_text, encoding="utf-8")
        r.state = {"id": r.id, "filename": filename, "question": question.strip(), "autopilot": autopilot,
                   "created": now(), "status": "running", "current": "understand", "events": [], "meter": {},
                   "stages": {s: {"status": "pending", "report": None, "flags": [], "feedback": [], "attempts": 0, "error": None}
                              for s in STAGES}}
        r.save()
        return r

    @classmethod
    def load(cls, rid: str, root: Path = ROOT) -> "Run":
        r = cls(rid, root)
        r.state = json.loads((r.dir / "state.json").read_text(encoding="utf-8"))
        m = r.state.get("meter", {})
        r.meter = Meter(m.get("calls", 0), m.get("chars_in", 0), m.get("chars_out", 0))
        return r

    def save(self):
        with self.lock:
            self.state["meter"] = {"calls": self.meter.calls, "chars_in": self.meter.chars_in,
                                   "chars_out": self.meter.chars_out, "approx_tokens": self.meter.tokens()}
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, default=str), encoding="utf-8")
            tmp.replace(self.dir / "state.json")

    def stage(self, name: str) -> dict:
        return self.state["stages"][name]

    def event(self, stage: str, action: str, note: str = ""):
        self.state["events"].append({"at": now(), "stage": stage, "action": action, "note": note})

    # ---- data access
    @property
    def raw_csv(self) -> Path:
        return self.dir / "raw.csv"

    @property
    def clean_csv(self) -> Path:
        return self.dir / "clean.csv"

    def df_raw(self) -> pd.DataFrame:
        return pd.read_csv(self.raw_csv, keep_default_na=False, na_values=[""], low_memory=False)

    def date_cols(self) -> list[str]:
        rep = self.stage("clean").get("report") or {}
        return (rep.get("summary") or {}).get("date_columns", [])

    def df_clean(self) -> pd.DataFrame:
        df = pd.read_csv(self.clean_csv, keep_default_na=False, na_values=[""], low_memory=False)
        for c in self.date_cols():
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df


def _rows(df: pd.DataFrame, n: int = 8) -> list[list]:
    d = df.head(n)
    return [[None if pd.isna(v) else (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else (bool(v) if isinstance(v, bool) else v if isinstance(v, (int, float)) else str(v)))
             for v in r] for r in d.itertuples(index=False, name=None)]


def _table_brief(t: dict, n: int = 15) -> dict:
    return {"columns": t["columns"], "rows": t["rows"][:n], "n_rows_total": t["n_rows_total"]}


# ====================================================================== stages
def stage_understand(eng, run: Run, fb: list[str]):
    df = run.df_raw()
    if df.empty or df.shape[1] == 0:
        raise RuntimeError("The file has no rows or no columns")
    prof = profile_df(df)
    u = call_json(eng.llm, P.UNDERSTAND, {"question": run.state["question"], "profile": prof, "feedback": fb}, Understanding, run.meter)
    names = set(df.columns)
    u.columns = [c for c in u.columns if c.name in names]
    if u.main_metric not in names:
        u.main_metric = None
    report = {"n_rows": prof["n_rows"], "n_cols": prof["n_cols"], "understanding": u.model_dump(),
              "detected_issues": detected_issues(prof),
              "columns": [{k: c.get(k) for k in ("name", "kind", "missing", "n_unique", "samples")} for c in prof["columns"]],
              "preview": {"columns": list(df.columns), "rows": _rows(df)}}
    return report, []


def _clean_report(run: Run, df_raw, ops, disabled, invalid, passes, residual):
    df, log, checks, summary = apply_plan(df_raw, ops, disabled=disabled, verify=run_opt(run).verify)
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]) and (out[c].dropna() == out[c].dropna().dt.normalize()).all():
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    out.to_csv(run.clean_csv, index=False)
    flags = []
    if any(not c["passed"] for c in checks):
        flags.append("A verification check failed")
    for e in log:
        if e["status"] == "rejected":
            flags.append(f"Rejected change: {e['op']} {e['columns']}")
        elif e["status"] == "applied" and e["risk"] != "low":
            flags.append(f"{e['risk'].capitalize()}-risk change: {e['description']}")
    if invalid:
        flags.append(f"{len(invalid)} malformed operation(s) ignored")
    if residual:
        flags.append(f"{len(residual)} fixable issue(s) remain")
    report = {"ops": [o.model_dump() for o in ops], "disabled": sorted(disabled), "log": log, "checks": checks,
              "summary": summary, "invalid_ops": invalid, "passes": passes, "residual": residual,
              "preview": {"columns": list(out.columns), "rows": _rows(out)}}
    return report, flags


_OPTIONS: dict[str, Options] = {}


def run_opt(run: Run) -> Options:
    return _OPTIONS.get(run.id, Options())


def stage_clean(eng, run: Run, fb: list[str]):
    opt = run_opt(run)
    df_raw = run.df_raw()
    prof_raw = profile_df(df_raw)
    u = run.stage("understand")["report"]["understanding"]
    ops_all, invalid_all, residual, log = [], [], [], []
    df_cur, passes = None, 0
    for pass_no in range(1, opt.max_clean_passes + 1):
        passes = pass_no
        if pass_no == 1:
            payload = {"profile": prof_raw, "understanding": u, "feedback": fb}
        else:
            payload = {"profile": profile_df(df_cur), "understanding": u, "residual_issues": residual,
                       "already_applied": [{"op": e["op"], "columns": e["columns"], "status": e["status"]} for e in log]}
        plan = call_json(eng.llm, P.CLEAN, payload, CleanPlanRaw, run.meter)
        new_ops, invalid = parse_ops(plan.ops)
        ops_all += new_ops
        invalid_all += invalid
        df_cur, log, _, _ = apply_plan(df_raw, ops_all, verify=opt.verify)
        residual = [i for i in detected_issues(profile_df(df_cur)) if i["fixable"]]
        if not residual or not new_ops:
            break
    return _clean_report(run, df_raw, ops_all, set(), invalid_all, passes, residual)


def stage_analyze(eng, run: Run, fb: list[str]):
    opt = run_opt(run)
    df = run.df_clean()
    prof = profile_df(df)
    u = run.stage("understand")["report"]["understanding"]
    dates = run.date_cols()
    sample = {"columns": list(df.columns), "rows": _rows(df, 5)}
    plan = call_json(eng.llm, P.ANALYZE, {"goal": u["goal"], "main_metric": u["main_metric"], "assumptions": u["assumptions"],
                                          "profile": prof, "sample_rows": sample, "feedback": fb}, AnalysisPlan, run.meter)
    items = [a for a in plan.analyses if a.code.strip()][:6]
    if not items:
        raise RuntimeError("The model returned no analyses")
    results = run_many(str(run.clean_csv), dates, [a.code for a in items])
    analyses = [{"index": i, "title": a.title, "question": a.question, "code": a.code, "status": "ok" if r["ok"] else "failed",
                 "error": None if r["ok"] else r["error"], "table": r.get("table"), "finding": "", "ungrounded": []}
                for i, (a, r) in enumerate(zip(items, results))]
    failed = [a for a in analyses if a["status"] == "failed"]
    if failed:  # one self-repair round
        rep = call_json(eng.llm, P.REPAIR, {"columns": [{"name": c, "dtype": str(df[c].dtype)} for c in df.columns],
                                            "failed": [{"index": a["index"], "title": a["title"], "code": a["code"], "error": a["error"]} for a in failed]},
                        AnalysisRepair, run.meter)
        fixes = {f.index: f.code for f in rep.fixed}
        todo = [a for a in failed if a["index"] in fixes]
        for a, r in zip(todo, run_many(str(run.clean_csv), dates, [fixes[a["index"]] for a in todo])):
            a["code"] = fixes[a["index"]]
            if r["ok"]:
                a.update(status="ok", error=None, table=r["table"], repaired=True)
            else:
                a["error"] = r["error"]
    ok = [a for a in analyses if a["status"] == "ok"]
    if not ok:
        raise RuntimeError("None of the analyses could be computed: " + "; ".join(str(a["error"]) for a in analyses)[:300])
    interp = call_json(eng.llm, P.INTERPRET, {"goal": u["goal"], "analyses": [
        {"index": a["index"], "title": a["title"], "question": a["question"], "table": _table_brief(a["table"], 20)} for a in ok]},
        Interpretation, run.meter)
    by = {f.index: f.finding for f in interp.findings}
    flags = []
    for a in ok:
        a["finding"] = by.get(a["index"], "")
        if opt.grounding:
            pool = build_pool([a["table"]], extra=[prof["n_rows"], prof["n_cols"]])
            a["ungrounded"] = ungrounded([a["finding"]], pool)
            if a["ungrounded"]:
                flags.append(f"Finding {a['index'] + 1} cites numbers not found in its table: {', '.join(a['ungrounded'])}")
    for a in analyses:
        if a["status"] == "failed":
            flags.append(f"Analysis '{a['title']}' failed: {a['error']}")
    return {"analyses": analyses, "n_ok": len(ok), "n_failed": len(analyses) - len(ok)}, flags


def _auto_charts(ok: list[dict]):
    specs = []
    for a in ok[:3]:
        t = a["table"]
        if len(t["columns"]) < 2 or not t["rows"]:
            continue
        y = next((c for j, c in enumerate(t["columns"][1:], 1) if isinstance(t["rows"][0][j], (int, float))), None)
        if y:
            line = bool(re.match(r"^\d{4}-\d{2}", str(t["rows"][0][0])))
            specs.append({"analysis_index": a["index"], "kind": "line" if line else "bar", "x": t["columns"][0], "y": [y],
                          "title": a["title"], "takeaway": ""})
    return specs


def stage_visualize(eng, run: Run, fb: list[str]):
    from schemas import ChartSpec
    ok = [a for a in run.stage("analyze")["report"]["analyses"] if a["status"] == "ok"]
    by_idx = {a["index"]: a for a in ok}
    plan = call_json(eng.llm, P.VISUALIZE, {"tables": [{"index": a["index"], "title": a["title"], **_table_brief(a["table"])} for a in ok],
                                            "feedback": fb}, ChartPlan, run.meter)
    specs, dropped = [], []
    for s in plan.charts[:4]:
        a = by_idx.get(s.analysis_index)
        reason = "no such analysis" if a is None else charts_mod.validate_spec(s, a["table"])
        (dropped.append({"title": s.title, "reason": reason}) if reason else specs.append(s))
    if not specs:  # deterministic fallback so the stage always yields something readable
        specs = [ChartSpec(**d) for d in _auto_charts(ok)]
        if specs:
            dropped.append({"title": "(all)", "reason": "model charts were unusable; drew default charts instead"})
    cdir = run.dir / "charts"
    out = []
    for k, s in enumerate(specs):
        charts_mod.render(s, by_idx[s.analysis_index]["table"], cdir / f"chart_{k}.png")
        out.append({"n": k, "file": f"chart_{k}.png", "title": s.title, "takeaway": s.takeaway, "kind": s.kind,
                    "analysis_index": s.analysis_index, "x": s.x, "y": s.y, "ungrounded": []})
    flags = [f"Chart dropped: {d['title']} ({d['reason']})" for d in dropped]
    if run_opt(run).grounding:
        for c in out:
            pool = build_pool([by_idx[c["analysis_index"]]["table"]])
            c["ungrounded"] = ungrounded([c["takeaway"]], pool)
            if c["ungrounded"]:
                flags.append(f"Chart '{c['title']}' takeaway cites numbers not in its table: {', '.join(c['ungrounded'])}")
    if not out:
        raise RuntimeError("No chart could be drawn from the analysis results")
    return {"charts": out, "dropped": dropped}, flags


def decision_texts(d: Decision) -> list[str]:
    return ([d.headline, d.summary] + [f.claim for f in d.findings] +
            [r.action + " " + r.why for r in d.recommendations])


def stage_decide(eng, run: Run, fb: list[str]):
    opt = run_opt(run)
    u = run.stage("understand")["report"]["understanding"]
    cl = run.stage("clean")["report"]
    ok = [a for a in run.stage("analyze")["report"]["analyses"] if a["status"] == "ok"]
    chart_notes = [{"title": c["title"], "takeaway": c["takeaway"]} for c in run.stage("visualize")["report"]["charts"]]
    cleaning = {"rows_before": cl["summary"]["rows_before"], "rows_after": cl["summary"]["rows_after"],
                "changes": [e["description"] for e in cl["log"] if e["status"] == "applied"],
                "imputed_columns": cl["summary"]["imputed_columns"], "assumptions": u["assumptions"]}
    payload = {"goal": u["goal"], "cleaning": cleaning, "charts": chart_notes, "feedback": fb,
               "analyses": [{"index": a["index"], "title": a["title"], "finding": a["finding"], **_table_brief(a["table"])} for a in ok]}
    d = call_json(eng.llm, P.DECIDE, payload, Decision, run.meter)
    pool = build_pool([a["table"] for a in ok],
                      extra=[cleaning["rows_before"], cleaning["rows_after"], cleaning["rows_before"] - cleaning["rows_after"]])
    bad = ungrounded(decision_texts(d), pool) if opt.grounding else []
    repaired = False
    if bad:
        d = call_json(eng.llm, P.DECIDE, {**payload, "fix": {"ungrounded_numbers": bad}, "previous_answer": d.model_dump()}, Decision, run.meter)
        bad, repaired = ungrounded(decision_texts(d), pool), True
    valid = {a["index"] for a in ok}
    for f in d.findings:
        f.evidence = [i for i in f.evidence if i in valid]
    for r in d.recommendations:
        r.evidence = [i for i in r.evidence if i in valid]
    flags = [f"Unverified numbers remain: {', '.join(bad)}"] if bad else []
    return {"decision": d.model_dump(), "ungrounded": bad, "repaired": repaired}, flags


STAGE_FUNCS = {"understand": stage_understand, "clean": stage_clean, "analyze": stage_analyze,
               "visualize": stage_visualize, "decide": stage_decide}


# ====================================================================== engine (threads + review gates)
class Engine:
    def __init__(self, llm, options: Options | None = None, root: Path = ROOT):
        self.llm = llm
        self.opt = options or Options()
        self.root = Path(root)
        self.runs: dict[str, Run] = {}

    # ---- run management
    def new_run(self, csv_text: str, filename: str, question: str, autopilot: bool = False) -> Run:
        run = Run.create(csv_text, filename, question, autopilot, self.root)
        _OPTIONS[run.id] = self.opt
        self.runs[run.id] = run
        return run

    def get(self, rid: str) -> Run | None:
        if rid in self.runs:
            return self.runs[rid]
        if (self.root / rid / "state.json").exists():
            run = Run.load(rid, self.root)
            _OPTIONS[rid] = self.opt
            self.runs[rid] = run
            return run
        return None

    def list_runs(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*/state.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
                out.append({k: s[k] for k in ("id", "filename", "created", "status", "current")})
            except Exception:
                pass
        return out

    # ---- execution
    def start(self, run: Run):
        self._launch(run, "understand")

    def _launch(self, run: Run, stage: str):
        with run.lock:
            st = run.stage(stage)
            st.update(status="running", error=None)
            run.state.update(status="running", current=stage)
            run.save()
        threading.Thread(target=self._exec, args=(run, stage), daemon=True).start()

    def _exec(self, run: Run, stage: str):
        try:
            report, flags = STAGE_FUNCS[stage](self, run, list(run.stage(stage)["feedback"]))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            with run.lock:
                run.stage(stage).update(status="failed", error=f"{type(e).__name__}: {e}"[:600])
                run.state["status"] = "failed"
                run.event(stage, "failed", str(e)[:200])
                run.save()
            return
        with run.lock:
            st = run.stage(stage)
            st.update(status="review", report=report, flags=flags, attempts=st["attempts"] + 1)
            run.state["status"] = "review"
            run.save()
            auto = run.state["autopilot"] and not flags
        if auto:
            self.approve(run, auto=True)

    # ---- human gate
    def approve(self, run: Run, auto: bool = False):
        with run.lock:
            stage = run.state["current"]
            st = run.stage(stage)
            if st["status"] != "review":
                raise ValueError("Nothing to approve right now")
            st["status"] = "approved"
            run.event(stage, "auto-approved (all checks passed)" if auto else "approved by human")
            i = STAGES.index(stage)
            if i == len(STAGES) - 1:
                run.state["status"] = "done"
                run.save()
                from report import write_report
                write_report(run)
                return
            run.save()
        self._launch(run, STAGES[i + 1])

    def revise(self, run: Run, note: str):
        with run.lock:
            stage = run.state["current"]
            st = run.stage(stage)
            if st["status"] not in ("review", "failed"):
                raise ValueError("Nothing to send back right now")
            if note.strip():
                st["feedback"].append(note.strip())
            run.event(stage, "sent back", note.strip()[:300])
            run.save()
        self._launch(run, stage)

    def override_clean(self, run: Run, disabled: list[int]):
        """Human switches individual cleaning changes off. Deterministic re-run, no LLM call."""
        with run.lock:
            st = run.stage("clean")
            if run.state["current"] != "clean" or st["status"] != "review":
                raise ValueError("Cleaning can only be edited while it is waiting for review")
            rep = st["report"]
            ops, _ = parse_ops(rep["ops"])
            new_rep, flags = _clean_report(run, run.df_raw(), ops, set(disabled), rep["invalid_ops"], rep["passes"], rep["residual"])
            st.update(report=new_rep, flags=flags)
            run.event("clean", "changes toggled", f"switched off: {sorted(disabled)}" if disabled else "all changes on")
            run.save()

    # ---- synchronous mode (used by eval): approves every stage, counts the stops a human would have had
    def run_sync(self, run: Run) -> dict:
        stops = 0
        for stage in STAGES:
            run.state["current"] = stage
            report, flags = STAGE_FUNCS[stage](self, run, [])
            st = run.stage(stage)
            st.update(status="approved", report=report, flags=flags, attempts=1)
            stops += bool(flags)
        run.state.update(status="done", human_stops=stops)
        run.save()
        from report import write_report
        write_report(run)
        return run.state
