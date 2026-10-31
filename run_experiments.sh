#!/usr/bin/env bash
# Runs every experiment used in the submission (held-out seeds). Needs GEMINI_API_KEY. ~60-90 min, results/ gets one JSON each.
set -e
python3 eval.py --split test --variant base                      # headline result on unseen files
python3 eval.py --split test --variant base --no-verify          # ablation: no per-change verification
python3 eval.py --split test --variant base --no-grounding       # ablation: no number grounding
python3 eval.py --split test --variant noisy                     # robustness: 2.5x more planted problems
python3 eval.py --split test --variant arabic                    # robustness: Arabic column names
python3 eval.py --split test --variant small                     # robustness: only 300 rows
python3 eval.py --split test --variant locale                    # robustness: US dates, Arabic-Indic / European numbers
python3 eval.py --split test --variant requery                   # adaptation: a different question on the same data
python3 eval.py --split test --variant null                      # control: no hidden problem, must not raise a false alarm
python3 eval.py --split test --variant base --repeat 3 --tag _repeat3   # stability: same files, 3 runs each
python3 oneshot_eval.py --split test                              # simpler alternative: whole CSV in ONE prompt
LLM_BACKEND=fake python3 eval.py --split test --variant base     # baseline: rules only, no LLM
python3 results_table.py
