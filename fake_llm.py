"""Rule-based stand-in for the LLM (LLM_BACKEND=fake).

Two uses: (1) offline tests and demos without an API key; (2) the "rules only" baseline in eval.py.
It reads the same profile the real model sees and answers with fixed heuristics. It cannot fix typos,
spot the changing segment, or write context-aware advice, which is exactly what the real model is for.
"""
from __future__ import annotations

import json
import re

from llm import LLM

_METRIC_NAME = re.compile(r"(revenue|sales|amount|total|salary|cost|hours|score|units|price|csat)", re.I)
_NONNEG_NAME = re.compile(r"(units|quantity|qty|count|age|hours|revenue|salary|amount|cost|days|price)", re.I)


def _payload(messages) -> dict:
    return json.loads(messages[0]["content"].split("INPUT:\n", 1)[1])


class HeuristicLLM(LLM):
    name = "heuristic (no LLM)"

    def generate(self, system: str, messages: list[dict]) -> str:
        stage = system.splitlines()[0].replace("STAGE:", "").strip()
        p = _payload(messages)
        return json.dumps(getattr(self, f"_{stage}")(p), ensure_ascii=False)

    # ------------------------------------------------------------ understand
    def _understand(self, p):
        cols = p["profile"]["columns"]
        roles = []
        for c in cols:
            k = c.get("kind")
            role = ("date" if k in ("datetime", "date_like_text") else
                    "id" if k == "id_like" or c["name"].lower().endswith("id") else
                    "metric" if k in ("numeric", "numeric_like_text") else
                    "category" if k == "categorical" else "other")
            roles.append({"name": c["name"], "meaning": f"{role} column", "role": role})
        metrics = [r["name"] for r in roles if r["role"] == "metric"]
        main = next((m for m in metrics if _METRIC_NAME.search(m)), metrics[0] if metrics else None)
        goal = p.get("question") or (f"Find what drives {main} and where it changed" if main else "Describe the dataset")
        return {"goal": goal, "dataset_description": f"{p['profile']['n_rows']} rows, {p['profile']['n_cols']} columns",
                "main_metric": main, "columns": roles, "suspected_issues": [], "assumptions": ["Values are taken as given (no unit or currency conversion)."]}

    # ------------------------------------------------------------ clean
    def _clean(self, p):
        if "already_applied" in p:  # the rules model makes a single pass
            return {"ops": [], "notes": ""}
        prof, ops = p["profile"], []
        cols = prof["columns"]
        if prof["exact_duplicate_rows"]:
            ops.append({"op": "drop_exact_duplicates", "reason": "exact duplicate rows in profile"})
        ws = [c["name"] for c in cols if c.get("whitespace_issues")]
        if ws:
            ops.append({"op": "strip_whitespace", "columns": ws, "reason": "extra spaces"})
        for c in cols:
            if c.get("placeholder_missing"):
                ops.append({"op": "standardize_missing", "columns": [c["name"]], "tokens": list(c["placeholder_missing"]), "reason": "placeholder tokens"})
        for c in cols:
            if c.get("kind") == "numeric_like_text":
                ops.append({"op": "parse_numeric", "column": c["name"], "reason": "numbers stored as text"})
            if c.get("kind") == "date_like_text":
                ops.append({"op": "parse_date", "column": c["name"], "dayfirst": c.get("dayfirst_evidence", 0) > c.get("monthfirst_evidence", 0), "reason": "dates stored as text"})
        for c in cols:
            if c.get("kind") == "categorical" and c.get("variant_groups"):
                m = {}
                for g in c["variant_groups"]:
                    best = g["values"][max(range(len(g["counts"])), key=lambda i: g["counts"][i])]
                    m.update({v: best for v in g["values"] if v != best})
                ops.append({"op": "normalize_categories", "column": c["name"], "mapping": m, "reason": "case/spelling variants"})
        for c in cols:
            if c.get("kind") in ("numeric", "numeric_like_text") and c.get("negatives") and _NONNEG_NAME.search(c["name"]):
                ops.append({"op": "set_invalid_to_missing", "column": c["name"], "min_value": 0, "reason": "negative values"})
        for c in cols:
            if c.get("kind") in ("numeric", "numeric_like_text") and c.get("outliers_iqr3"):
                ops.append({"op": "flag_outliers", "column": c["name"], "k": 3, "reason": "extreme values"})
        return {"ops": ops, "notes": "rule-based plan"}

    # ------------------------------------------------------------ analyze
    def _analyze(self, p):
        cols = p["profile"]["columns"]
        main = (p.get("understanding") or {}).get("main_metric")
        num = [c["name"] for c in cols if c.get("kind") == "numeric" and not c["name"].lower().endswith("id") and not c["name"].endswith("_is_outlier")]
        metric = main if main in num else (num[0] if num else None)
        cats = [c["name"] for c in cols if c.get("kind") == "categorical" and c.get("n_unique", 99) <= 20]
        dates = [c["name"] for c in cols if c.get("kind") == "datetime"]
        binary = next((c for c in cols if c.get("kind") == "categorical" and {str(v[0]).lower() for v in c.get("top_values", [])} == {"yes", "no"}), None)
        out = []
        if metric:
            out.append({"title": f"Overview of {metric}", "question": "How is the main metric distributed?",
                        "code": f"result = df['{metric}'].describe().round(2).reset_index()"})
            for c in cats[:3]:
                out.append({"title": f"{metric} by {c}", "question": f"Which {c} values differ most?",
                            "code": f"result = df.groupby('{c}')['{metric}'].agg(n='count', total='sum', average='mean').round(2).sort_values('total', ascending=False).reset_index()"})
            if dates:
                d = dates[0]
                out.append({"title": f"{metric} by month", "question": "How does the metric move over time?",
                            "code": f"result = df.groupby(df['{d}'].dt.to_period('M').astype(str))['{metric}'].agg(n='count', total='sum', average='mean').round(2).reset_index()"})
        if binary and cats:
            b = binary["name"]
            c = next((x for x in cats if x != b), None)
            if c:
                out.append({"title": f"Share of '{b}' = Yes by {c}", "question": "Where is the rate highest?",
                            "code": f"result = df.groupby('{c}')['{b}'].agg(n='count', yes_rate_pct=lambda s: round((s == 'Yes').mean() * 100, 1)).sort_values('yes_rate_pct', ascending=False).reset_index()"})
        if not out:
            out.append({"title": "Row count", "question": "How big is the data?", "code": "result = {'rows': len(df)}"})
        return {"analyses": out[:6]}

    def _repair(self, p):
        return {"fixed": []}

    def _interpret(self, p):
        f = []
        for a in p["analyses"]:
            t = a["table"]
            if not t["rows"]:
                f.append({"index": a["index"], "finding": "The table is empty."})
                continue
            row = ", ".join(f"{c} = {v}" for c, v in zip(t["columns"], t["rows"][0]))
            f.append({"index": a["index"], "finding": f"Top row: {row} (table has {t['n_rows_total']} rows)."})
        return {"findings": f}

    def _visualize(self, p):
        charts = []
        for a in p["tables"][:3]:
            cols, rows = a["columns"], a["rows"]
            if len(cols) < 2 or not rows:
                continue
            ycol = next((c for j, c in enumerate(cols[1:], 1) if isinstance(rows[0][j], (int, float))), None)
            if not ycol:
                continue
            line = bool(re.match(r"^\d{4}-\d{2}$", str(rows[0][0])))
            charts.append({"analysis_index": a["index"], "kind": "line" if line else "bar", "x": cols[0], "y": [ycol],
                           "title": a["title"], "takeaway": f"First row: {cols[0]} = {rows[0][0]}."})
        return {"charts": charts}

    def _decide(self, p):
        fs = [a for a in p["analyses"] if a.get("finding")]
        findings = [{"claim": a["finding"], "evidence": [a["index"]]} for a in fs[:3]]
        recs = [{"action": f"Review the top segment in '{a['title']}' with the team", "why": a["finding"],
                 "confidence": "low", "evidence": [a["index"]]} for a in fs[1:3]]
        return {"headline": fs[0]["finding"] if fs else "No analysis result available",
                "summary": "Rule-based summary: the largest groups per analysis are listed. No change over time was investigated.",
                "findings": findings, "recommendations": recs,
                "caveats": ["Generated by fixed rules without a language model."], "needs_human": []}
