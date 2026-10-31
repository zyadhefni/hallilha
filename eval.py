"""Score the agent against planted ground truth.

  LLM_BACKEND=fake python3 eval.py --split dev            # rules-only baseline (no API key)
  python3 eval.py --split test                             # real model (Gemini), needs GEMINI_API_KEY
  python3 eval.py --split test --no-verify                 # ablation: no per-change verification
  python3 eval.py --split test --no-grounding              # ablation: no number checking

Metrics per dataset: fix rate on injected cells, damage rate on clean cells, duplicates left,
insight hit (planted segment named in the decision), ungrounded numbers, human stops, model calls/tokens.
"""
from __future__ import annotations

import argparse, json, re, tempfile, time
from pathlib import Path

import numpy as np
import pandas as pd

from gen_data import DOMAINS, make_dataset
from llm import get_llm
from pipeline import Engine, Options
from oneshot_eval import METRIC, truth_metric

DEV, TEST = [1, 2], [101, 102]
# requirement-change test: the user asks something else than the planted problem; the decision must address THAT question
REQUERY = {"sales": ("How do Online and Store channels compare on revenue?", ["online", "store"]),
           "hr": ("Is salary related to who resigns?", ["salary"]),
           "support": ("Which priority level is resolved the slowest?", ["priority"])}


def _norm(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return str(v).strip().lower()


def score(ds, run, requery: bool = False) -> dict:
    clean = run.df_clean()
    key = ds.key
    tr = ds.truth.reset_index(drop=True)
    dup_left = int(clean[key].duplicated().sum())
    c = clean.drop_duplicates(key).set_index(key)
    t = tr.set_index(key)
    fixed = total = damaged = clean_cells = 0
    for col in t.columns:
        if col not in c.columns:
            continue
        inj = {tr.index[i] for i in ds.injected.get(col, set())}
        unrec = {tr.index[i] for i in ds.unrecoverable.get(col, set())}
        for pos, kv in enumerate(t.index):
            if kv not in c.index:
                continue
            tv, cv = t.at[kv, col], c.at[kv, col]
            if col in ds.date_cols:
                tv, cv = pd.to_datetime(tv, errors="coerce"), pd.to_datetime(cv, errors="coerce")
                tv = None if pd.isna(tv) else str(tv.date()); cv = None if pd.isna(cv) else str(cv.date())
            ok = _norm(tv) == _norm(cv)
            if pos in inj and pos not in unrec:
                total += 1; fixed += ok
            elif pos not in inj:
                clean_cells += 1; damaged += (not ok)
    dec = run.state["stages"]["decide"]["report"] or {}
    d = dec.get("decision", {})
    text = json.dumps(d, ensure_ascii=False).lower()
    if requery:
        ds.insight_tokens = REQUERY[ds.domain][1]
    hit = all(tok.lower() in text for tok in ds.insight_tokens)
    lead = " ".join([d.get("headline", "")] + [(f.get("claim", "") if isinstance(f, dict) else "") for f in d.get("findings", [])[:1]]
                    + [(x.get("action", "") if isinstance(x, dict) else "") for x in d.get("recommendations", [])[:1]]).lower()
    false_alarm = (not ds.insight_tokens) and any((x.get("confidence") == "high") for x in d.get("recommendations", []) if isinstance(x, dict))
    lead_hit = all(tok.lower() in lead for tok in ds.insight_tokens)   # strict: named in headline / first finding / first action
    answered = hit if requery else float('nan')
    if requery:
        hit = lead_hit = float('nan')
    if not ds.insight_tokens:   # null control: there is nothing to find, so 'found' is not defined
        hit = lead_hit = float('nan')
    stops = sum(bool(run.state["stages"][s]["flags"]) for s in run.state["stages"])
    m = run.state["meter"]
    col, agg = METRIC[ds.domain]
    try:   # the same aggregate the one-shot baseline must produce, computed here from the cleaned file
        s = pd.to_numeric(clean[col], errors="coerce")
        mv = float(s.sum() if agg == "sum" else s.mean())
        metric_err = abs(mv - truth_metric(ds)) / abs(truth_metric(ds))
    except Exception:
        metric_err = float("nan")
    return dict(domain=ds.domain, seed=ds.seed, fix_rate=fixed / max(total, 1), damage_rate=damaged / max(clean_cells, 1),
                dup_left=dup_left, count_exact=len(clean) == len(tr), metric_err=metric_err, insight_hit=hit, insight_lead=lead_hit, answered=answered, false_alarm=false_alarm, ungrounded=len(dec.get("ungrounded", [])), human_stops=stops,
                calls=m.get("calls", 0), tokens=m.get("approx_tokens", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--no-grounding", action="store_true")
    ap.add_argument("--variant", choices=["base", "noisy", "arabic", "small", "locale", "null", "requery"], default="base",
                    help="base | noisy (2.5x problems) | arabic (Arabic headers) | small (300 rows) | locale (US dates, Arabic-Indic/European numbers) | null (NO hidden problem: must not raise a false alarm) | requery (a DIFFERENT question than the planted problem: must answer what was asked)")
    ap.add_argument("--repeat", type=int, default=1, help="run every file k times to measure run-to-run stability")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    opt = Options(verify=not a.no_verify, grounding=not a.no_grounding)
    llm = get_llm()
    rows = []
    for dom in DOMAINS:
        for seed, rep in [(sd, r) for sd in (DEV if a.split == "dev" else TEST) for r in range(a.repeat)]:
            ds = make_dataset(dom, seed, n=300 if a.variant == "small" else 900, noise=2.5 if a.variant == "noisy" else 1.0, arabic=a.variant == "arabic", locale=a.variant == "locale", insight=a.variant != "null")
            eng = Engine(llm, opt, root=Path(tempfile.mkdtemp()))
            run = eng.new_run(ds.csv(), f"{dom}_{seed}.csv", REQUERY[dom][0] if a.variant == "requery" else "", False)
            t0 = time.time()
            try:
                eng.run_sync(run)
                r = score(ds, run, a.variant == "requery")
            except Exception as e:
                r = dict(domain=dom, seed=seed, error=str(e)[:200])
            r["secs"] = round(time.time() - t0, 1)
            r["rep"] = rep
            rows.append(r); print(r, flush=True)
    ok = [r for r in rows if "error" not in r]
    summ = {"n": len(rows), "errors": len(rows) - len(ok)}
    if ok:
        df = pd.DataFrame(ok)
        summ.update({k: round(float(df[k].mean()), 4) for k in ["fix_rate", "damage_rate", "dup_left", "count_exact", "metric_err", "insight_hit", "insight_lead", "answered", "false_alarm", "ungrounded", "human_stops", "calls", "tokens"]})
    if a.repeat > 1 and ok:   # share of files whose outcome is identical in every repeat
        g = pd.DataFrame(ok).groupby(["domain", "seed"])
        summ["stability"] = round(float(((g["insight_hit"].nunique() == 1) & (g["fix_rate"].apply(lambda x: x.max() - x.min() < 0.01))).mean()), 4)
        summ["fix_rate_min"] = round(float(pd.DataFrame(ok)["fix_rate"].min()), 4)
    print("SUMMARY", summ)
    out = Path(__file__).parent / "results"; out.mkdir(exist_ok=True)
    name = f"{a.split}_{a.variant}_{'novery' if a.no_verify else ''}{'rep' + str(a.repeat) if a.repeat > 1 else ''}{'nogr' if a.no_grounding else ''}{a.tag or ('fake' if __import__('os').environ.get('LLM_BACKEND') == 'fake' else 'llm')}.json"
    (out / name).write_text(json.dumps(dict(options=vars(a), rows=rows, summary=summ), indent=2))
    print("saved", out / name)


if __name__ == "__main__":
    main()
