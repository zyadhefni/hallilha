"""Builds results/RESULTS.md (table for the slides) from results/test_*.json."""
import json
from pathlib import Path

R = Path(__file__).parent / "results"
names = {"test_base_llm": "Hallilha (Gemini)", "test_base_noveryllm": "  - without change verification",
         "test_base_nogrllm": "  - without number grounding", "test_noisy_llm": "Robustness: 2.5x noisier data",
         "test_arabic_llm": "Robustness: Arabic column names", "test_small_llm": "Robustness: 300 rows",
         "test_locale_llm": "Robustness: US dates + Arabic/European numbers", "test_null_llm": "Control: no hidden problem (false alarms)",
         "test_requery_llm": "Adaptation: a different question on the same data",
         "test_base_rep3_repeat3": "Stability: every file run 3 times",
         "test_base_fake": "Baseline: rules only (no LLM)"}
cols = [("fix_rate", "Fix rate", "{:.1%}"), ("damage_rate", "Damage", "{:.2%}"), ("dup_left", "Dups left", "{:.2f}"),
        ("insight_hit", "Insight found", "{:.0%}"), ("insight_lead", "Insight in lead", "{:.0%}"), ("answered", "Answered the new question", "{:.0%}"), ("false_alarm", "False alarm", "{:.0%}"), ("ungrounded", "Ungrounded nums", "{:.2f}"),
        ("human_stops", "Flagged stages", "{:.2f}"), ("tokens", "Tokens", "{:,.0f}")]
lines = ["| Experiment | n | " + " | ".join(c[1] for c in cols) + " |", "|---|---|" + "---|" * len(cols)]
for key, label in names.items():
    f = R / f"{key}.json"
    if not f.exists():
        continue
    s = json.loads(f.read_text())["summary"]
    lines.append(f"| {label} | {s['n']} | " + " | ".join(fmt.format(s[k]) if k in s and s[k] == s[k] else "-" for k, _, fmt in cols) + " |")
(R / "RESULTS.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))

# --- Hallilha vs the simpler alternative (whole CSV in one prompt), same files and same metrics
one, dp = R / "test_oneshot_llm.json", R / "test_base_llm.json"
if one.exists() and dp.exists():
    a, b = json.loads(dp.read_text())["summary"], json.loads(one.read_text())["summary"]
    f = lambda s, k, fmt: fmt.format(s[k]) if k in s and s[k] == s[k] else "-"
    rows = [("Planted problem found", "insight_hit", "{:.0%}"), ("...named in headline / first action", "insight_lead", "{:.0%}"),
            ("Exact record count after de-duplication", "count_exact", "{:.0%}"), ("Error of a computed aggregate (mean)", "metric_err", "{:.2%}")]
    out = ["", "| Metric | Hallilha | One prompt, whole CSV |", "|---|---|---|"]
    out += [f"| {n} | {f(a, k, fm)} | {f(b, k, fm)} |" for n, k, fm in rows]
    (R / "RESULTS.md").write_text((R / "RESULTS.md").read_text() + "\n".join(out) + "\n")
    print("\n".join(out))
