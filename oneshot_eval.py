"""Simpler alternative: hand the WHOLE raw CSV to the same model in ONE prompt ("clean it, analyze it, tell me what happened").
Scored on the same held-out files as Hallilha, so the value of the pipeline over a single prompt is measured, not assumed.

  python3 oneshot_eval.py --split test
Metrics: insight_hit / insight_lead (same definitions as eval.py), count_exact (does it know how many distinct records
there are after removing duplicates?), metric_err (relative error of one aggregate it has to compute), ungrounded-by-construction
(it has no tables, so every number is unverified).
"""
from __future__ import annotations

import argparse, json, re
from pathlib import Path

import numpy as np
import pandas as pd

from gen_data import DOMAINS, make_dataset
from llm import get_llm

DEV, TEST = [1, 2], [101, 102]
METRIC = {"sales": ("revenue", "sum"), "hr": ("satisfaction", "mean"), "support": ("csat", "mean")}
SYSTEM = """You are a senior data analyst. You receive a raw, messy CSV (duplicate rows, mixed date formats, currency text, spelling variants, placeholders, impossible values, typos).
Clean it mentally (remove exact duplicates, unify spellings and formats, ignore impossible values), analyze it, and report what changed over time and where, with concrete recommendations.
Reply with ONE JSON object: {"report": "<the analysis in English, headline first, then findings and recommendations>",
 "distinct_records": <integer: number of distinct records after removing exact duplicate rows>,
 "metric_value": <number: the value asked for in METRIC below, computed on the cleaned data>}"""


def truth_metric(ds):
    col, agg = METRIC[ds.domain]
    s = pd.to_numeric(ds.truth[col], errors="coerce")
    return float(s.sum() if agg == "sum" else s.mean())


def score(ds, out: dict) -> dict:
    text = str(out.get("report", "")).lower()
    hit = all(t.lower() in text for t in ds.insight_tokens)
    lead = all(t.lower() in text[:350] for t in ds.insight_tokens)
    try:
        cnt = int(out.get("distinct_records"))
    except (TypeError, ValueError):
        cnt = -1
    try:
        mv = float(out.get("metric_value"))
        err = abs(mv - truth_metric(ds)) / abs(truth_metric(ds))
    except (TypeError, ValueError):
        err = float("nan")
    return dict(domain=ds.domain, seed=ds.seed, insight_hit=hit, insight_lead=lead,
                count_exact=cnt == len(ds.truth), count_err=abs(cnt - len(ds.truth)), metric_err=err)


def parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test"], default="test")
    a = ap.parse_args()
    llm, rows = get_llm(), []
    for dom in DOMAINS:
        for seed in (DEV if a.split == "dev" else TEST):
            ds = make_dataset(dom, seed)
            col, agg = METRIC[dom]
            msg = f"METRIC: the {agg} of column '{col}' over the cleaned data (ignore missing values).\n\nCSV:\n{ds.csv()}"
            try:
                out = parse(llm.generate(SYSTEM, [{"role": "user", "content": msg}]))
                r = score(ds, out)
            except Exception as e:
                r = dict(domain=dom, seed=seed, error=str(e)[:200])
            rows.append(r); print(r, flush=True)
    ok = [r for r in rows if "error" not in r]
    summ = {"n": len(rows), "errors": len(rows) - len(ok)}
    if ok:
        df = pd.DataFrame(ok)
        summ.update({k: round(float(df[k].mean()), 4) for k in ["insight_hit", "insight_lead", "count_exact", "count_err", "metric_err"]})
    print("SUMMARY", summ)
    out = Path(__file__).parent / "results"; out.mkdir(exist_ok=True)
    (out / f"{a.split}_oneshot_llm.json").write_text(json.dumps(dict(rows=rows, summary=summ), indent=2))


if __name__ == "__main__":
    main()
