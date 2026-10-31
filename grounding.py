"""Number grounding: every number the agent states in its findings must be traceable to a computed result.

This is the anti-hallucination check. It is deliberately about numbers (the thing that gets decisions wrong),
not wording. Years and small counts (<= 10) are ignored; dates are stripped before extraction.
"""
from __future__ import annotations

import itertools
import re

import numpy as np

_DATE_RX = re.compile(r"\d{4}-\d{2}(?:-\d{2})?|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,4}\b", re.I)
_NUM_RX = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)\s?(%|[kKmMbB]\b)?")


def extract_numbers(text: str) -> list[tuple[str, float, int, str]]:
    """-> [(raw, value, decimals, suffix)] for numbers that need grounding."""
    text = _DATE_RX.sub(" ", text or "")
    out = []
    for m in _NUM_RX.finditer(text):
        raw, suf = m.group(1), (m.group(2) or "").lower()
        clean = raw.replace(",", "")
        try:
            v = float(clean)
        except ValueError:
            continue
        dec = len(clean.split(".")[1]) if "." in clean else 0
        if not suf and dec == 0 and ("," not in raw) and (abs(v) <= 10 or 1900 <= abs(v) <= 2100):
            continue
        out.append((raw + (m.group(2) or ""), v, dec, suf))
    return out


def build_pool(tables: list[dict], extra: list[float] | None = None) -> np.ndarray:
    """All numbers a statement may legitimately cite: table cells plus simple derivations
    (sums, shares, differences, ratios and % changes between rows of the same column)."""
    pool: list[float] = list(extra or [])
    for t in tables:
        if not t:
            continue
        pool.append(float(t.get("n_rows_total", len(t["rows"]))))
        for j, _ in enumerate(t["columns"]):
            vals = [r[j] for r in t["rows"] if isinstance(r[j], (int, float)) and not isinstance(r[j], bool)]
            if not vals:
                continue
            pool += vals
            s = float(sum(vals))
            pool += [s, s / len(vals), max(vals), min(vals), max(vals) - min(vals)]
            if s:
                pool += [v / s * 100 for v in vals] + [v / s for v in vals]
            for a, b in itertools.islice(itertools.permutations(vals[:14], 2), 400):
                pool.append(a - b)
                if b:
                    pool += [(a / b - 1) * 100, a / b, a / b * 100]
        # share of a count column over the table's own total rows
    vals = [round(float(x), 6) for x in pool if np.isfinite(x)]
    return np.array(sorted(set(vals) | {abs(v) for v in vals}))  # text may say 'fell by 5' for a cell of -5


def is_grounded(value: float, dec: int, suf: str, pool: np.ndarray) -> bool:
    if pool.size == 0:
        return False
    cands = [(value, 10 ** -dec)]
    if suf == "k":
        cands = [(value * 1e3, 10 ** -dec * 1e3)]
    elif suf == "m":
        cands = [(value * 1e6, 10 ** -dec * 1e6)]
    elif suf == "b":
        cands = [(value * 1e9, 10 ** -dec * 1e9)]
    elif suf == "%":
        cands = [(value, 10 ** -dec), (value / 100, 10 ** -dec / 100)]
    for v, unit in cands:
        tol = max(0.5 * unit, 1e-9) * 1.001 + abs(v) * 0.002
        i = np.searchsorted(pool, v - tol)
        if i < pool.size and pool[i] <= v + tol:
            return True
    return False


def ungrounded(texts: list[str], pool: np.ndarray) -> list[str]:
    bad: list[str] = []
    for t in texts:
        for raw, v, dec, suf in extract_numbers(t):
            if not is_grounded(v, dec, suf, pool) and raw not in bad:
                bad.append(raw)
    return bad
