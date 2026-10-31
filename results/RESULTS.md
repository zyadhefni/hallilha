| Experiment | n | Fix rate | Damage | Dups left | Insight found | Insight in lead | Answered the new question | False alarm | Ungrounded nums | Flagged stages | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Hallilha (Gemini) | 6 | 100.0% | 0.00% | 0.00 | 100% | 83% | - | 0% | 0.00 | 0.50 | 13,755 |
|   - without change verification | 6 | 100.0% | 0.00% | 0.00 | 100% | 83% | - | 0% | 0.00 | 0.33 | 14,254 |
|   - without number grounding | 6 | 100.0% | 0.00% | 0.00 | 100% | 83% | - | 0% | 0.00 | 0.17 | 14,142 |
| Robustness: 2.5x noisier data | 6 | 100.0% | 0.00% | 0.00 | 83% | 67% | - | 0% | 0.00 | 1.33 | 14,052 |
| Robustness: Arabic column names | 6 | 100.0% | 0.00% | 0.00 | 83% | 83% | - | 0% | 0.00 | 0.67 | 15,474 |
| Robustness: 300 rows | 6 | 100.0% | 0.00% | 0.00 | 83% | 83% | - | 0% | 0.00 | 0.33 | 13,045 |
| Robustness: US dates + Arabic/European numbers | 6 | 100.0% | 0.00% | 0.00 | 83% | 83% | - | 0% | 0.00 | 0.83 | 15,502 |
| Control: no hidden problem (false alarms) | 6 | 100.0% | 0.00% | 0.00 | - | - | - | 17% | 0.00 | 0.33 | 15,590 |
| Adaptation: a different question on the same data | 6 | 100.0% | 0.00% | 0.00 | - | - | 100% | 0% | 0.00 | 0.50 | 13,542 |
| Stability: every file run 3 times | 18 | 100.0% | 0.00% | 0.00 | 100% | 78% | - | 0% | 0.00 | 0.22 | 14,889 |
| Baseline: rules only (no LLM) | 6 | 98.6% | 0.00% | 0.00 | 33% | 0% | - | 0% | 0.00 | 0.00 | 8,344 |

| Metric | Hallilha | One prompt, whole CSV |
|---|---|---|
| Planted problem found | 100% | 100% |
| ...named in headline / first action | 83% | 83% |
| Exact record count after de-duplication | 100% | 50% |
| Error of a computed aggregate (mean) | 0.00% | 3.34% |
