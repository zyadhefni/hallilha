# Iteration log: what we measured, what failed, what we changed

Method: every change was triggered by a *failure found by comparing the output with planted ground truth* (testfile/answer_key.json,
eval.py), then re-checked. Regression tests: `python3 -m pytest tests`, `LLM_BACKEND=fake python3 eval.py --split dev`.

| # | Observed failure (evidence) | Cause | Change | Check |
|---|---|---|---|---|
| 1 | Analysis stage crashed: "No module named numpy" | Sandbox subprocess ran in isolated mode (`-I`) and lost user site-packages | `-E` instead of `-I` (the AST whitelist and resource limits are the real isolation) | sandbox tests, full run |
| 2 | Region typos (24 values) were not unified; change rejected "too aggressive" | Guard rejected any merge below 40% of distinct values | Allow big merges when every target value already exists in the data; reject only invented targets | cleaning 15/15 on messy_sales.csv |
| 3 | Goal was "top performers" when no question was given | Prompt default | Default goal = find when/where the metric changed | Understand stage |
| 4 | Hidden problem (Abha Electronics) missed; the report blamed Jeddah/Riyadh Electronics | Analysis compared the last two months; drill-down ranked by totals, which change with order counts | Time split on the whole history; drill-down on region x category using per-order RATES | analysis table 4 |
| 5 | False "unverified number" warnings: "124,390.33" (cell was -124,390.33) and "2026," | Grounding compared signs and captured trailing punctuation | Compare absolute values; tighter number regex | grounding tests |
| 6 | Decision headline still about Jeddah/Riyadh; "high confidence" on noise | Totals confuse volume with behaviour | Volume/rate decomposition (volume_effect, rate_effect) in the drill-down; decision rules separate rate anomalies from volume swings | messy_sales.csv decision |
| 7 | Revenue of rows with a bad `units` value was dropped from revenue totals | Outlier rows excluded for every metric | Exclude a row only for the metric that is flagged | monthly totals = truth (6/6) |
| 8 | Decision invented a "B2B vs retail" explanation | Model speculation | Rule: explanations outside the data go only in `needs_human` | decision review |
| 9 | Arabic-Indic digits, European separators, `(120.00)` not read as numbers | Number parser | New parser + unit tests | tests/test_core.py |
| 10 | Chart titles clipped; the most important chart missing | Layout; model picked the first four analyses | Wrapped titles; the most extreme rate change must be charted | Visualize stage |
| 11 | Null control (no planted change): false alarm in 3 of 4 completed runs (a high-confidence recommendation was issued) | The prompts push to lead with the biggest rate change, even when nothing stands out | Decide prompt: null-result rule (no clear outlier means no clear change, only low-confidence monitoring actions) | `--variant null` re-run |

Held-out result after these changes (seeds 101, 102; development used other seeds): see `results/RESULTS.md`.
