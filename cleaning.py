"""Executes cleaning operations and verifies them.

The LLM only *proposes* operations (see schemas.CleanOp). Each operation is run by deterministic code,
checked on its own, and logged with before/after examples so a human can review exactly what changed.
"""
from __future__ import annotations

import difflib

import numpy as np
import pandas as pd
from pydantic import TypeAdapter

from dataprofile import PLACEHOLDERS, _norm_key, date_order_evidence, parse_dates, to_number
from schemas import CleanOp

_ADAPTER = TypeAdapter(CleanOp)


class Rejected(Exception):
    """The operation is unsafe or does not fit the data: not applied, shown to the human."""


class NoOp(Exception):
    """Nothing to change."""


def parse_ops(raw_ops: list[dict]) -> tuple[list, list[dict]]:
    ops, invalid = [], []
    for d in raw_ops:
        try:
            ops.append(_ADAPTER.validate_python(d))
        except Exception as e:  # malformed op: keep going with the rest
            invalid.append({"op": d, "error": str(e).splitlines()[0][:200]})
    return ops, invalid


def _cols(op) -> list[str]:
    if hasattr(op, "columns"):
        return list(op.columns)
    if hasattr(op, "column"):
        return [op.column]
    return []


def _need(df: pd.DataFrame, col: str) -> str:
    if col not in df.columns:
        raise Rejected(f"column '{col}' does not exist")
    return col


def _range_txt(lo, hi):
    if lo is not None and hi is not None:
        return f"{lo:g} to {hi:g}"
    return f"at least {lo:g}" if lo is not None else f"at most {hi:g}"


def _dissimilar(a: str, b: str) -> bool:
    ka, kb = _norm_key(a), _norm_key(b)
    return ka != kb and difflib.SequenceMatcher(None, ka, kb).ratio() < 0.85


def _is_text(s: pd.Series) -> bool:
    return not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s)
                or pd.api.types.is_bool_dtype(s))


def _ex(before, after, n=3, same=False):
    """Up to n {before, after} examples where values differ."""
    out = []
    for b, a in zip(before, after):
        if (pd.isna(b) and pd.isna(a)) or (not same and str(b) == str(a)):
            continue
        out.append({"before": "(empty)" if pd.isna(b) else str(b), "after": "(empty)" if pd.isna(a) else str(a)})
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ executors
def _drop_dups(df, op, verify):
    before = len(df)
    new = df.drop_duplicates()
    n = before - len(new)
    if not n:
        raise NoOp()
    dropped = df[df.duplicated()].head(3)
    ex = [{"before": f"row {i} repeats an earlier row", "after": "removed"} for i in dropped.index]
    return new, dict(description=f"Removed {n} exact duplicate rows", rows_affected=n, examples=ex, risk="low")


def _strip(df, op, verify):
    new, total, ex = df.copy(), 0, []
    for c in op.columns:
        _need(df, c)
        if not _is_text(df[c]):
            continue
        s = df[c]
        t = s.where(s.isna(), s.astype(str).str.strip().str.replace(r"\s+", " ", regex=True))
        ch = (s.astype(str) != t.astype(str)) & s.notna()
        total += int(ch.sum())
        if ch.any():
            ex += _ex(s[ch].head(3), t[ch].head(3), 3 - len(ex)) if len(ex) < 3 else []
        new[c] = t
    if not total:
        raise NoOp()
    return new, dict(description=f"Trimmed extra spaces in {', '.join(op.columns)}", rows_affected=total, examples=ex, risk="low")


def _std_missing(df, op, verify):
    new, total, ex = df.copy(), 0, []
    toks = {t.strip().lower() for t in op.tokens} | {""}
    odd = [t for t in op.tokens if t.strip().lower() not in PLACEHOLDERS]
    for c in op.columns:
        _need(df, c)
        s = df[c]
        if not _is_text(s):
            continue
        mask = s.notna() & s.astype(str).str.strip().str.lower().isin(toks)
        total += int(mask.sum())
        if mask.any() and len(ex) < 3:
            ex += _ex(s[mask].head(3), [np.nan] * 3, 3 - len(ex), same=True)
        new[c] = s.mask(mask, np.nan)
    if not total:
        raise NoOp()
    risk = "medium" if odd else "low"
    note = f"Unusual tokens treated as missing: {odd}" if odd else None
    return new, dict(description=f"Turned placeholder values ({', '.join(map(repr, op.tokens))}) into proper missing values in {', '.join(op.columns)}",
                     rows_affected=total, examples=ex, risk=risk, note=note)


def _parse_numeric(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    if pd.api.types.is_numeric_dtype(s):
        raise NoOp()
    nn = int(s.notna().sum())
    num = s.map(to_number).astype(float)
    ok = int(num.notna().sum())
    rate = ok / nn if nn else 1.0
    if verify and rate < 0.9:
        raise Rejected(f"only {rate:.0%} of '{c}' values look like numbers, so converting would lose data")
    unparsed = nn - ok
    new = df.copy()
    new[c] = num
    return new, dict(description=f"Converted '{c}' from text to numbers (removed currency symbols and thousands separators)",
                     rows_affected=ok, examples=_ex(s[num.notna()].head(30), num[num.notna()].head(30), 3),
                     risk="medium" if unparsed else "low",
                     note=f"{unparsed} value(s) could not be read as numbers and became missing" if unparsed else None)


def _parse_date(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    if pd.api.types.is_datetime64_any_dtype(s):
        raise NoOp()
    nn = int(s.notna().sum())
    ev = date_order_evidence(s)
    dayfirst, note = op.dayfirst, None
    if ev["dayfirst_evidence"] and not ev["monthfirst_evidence"] and not dayfirst:
        dayfirst, note = True, "Used day-first order because some dates have a day above 12 in the first position"
    elif ev["monthfirst_evidence"] and not ev["dayfirst_evidence"] and dayfirst:
        dayfirst, note = False, "Used month-first order because some dates have a day above 12 in the second position"
    chosen = parse_dates(s, dayfirst=dayfirst)
    rate = chosen.notna().sum() / nn if nn else 1.0
    if rate < 0.98:  # try the other day/month order and keep the better parse
        other = parse_dates(s, dayfirst=not dayfirst)
        r2 = other.notna().sum() / nn if nn else 1.0
        if r2 > rate:
            chosen, rate, dayfirst = other, r2, not dayfirst
            note = f"Day/month order switched to {'day-first' if dayfirst else 'month-first'} because it parsed more values"
    if verify and rate < 0.9:
        raise Rejected(f"only {rate:.0%} of '{c}' could be read as dates")
    ambiguous = ev["dayfirst_evidence"] == 0 and ev["monthfirst_evidence"] == 0 and nn > 0
    if ambiguous:
        note = ((note + ". ") if note else "") + "Day/month order is ambiguous in this data; please confirm"
    unparsed = nn - int(chosen.notna().sum())
    new = df.copy()
    new[c] = chosen
    return new, dict(description=f"Converted '{c}' from text to real dates (ISO format)",
                     rows_affected=int(chosen.notna().sum()),
                     examples=_ex(s[chosen.notna()].head(40), chosen[chosen.notna()].head(40).dt.strftime("%Y-%m-%d"), 3),
                     risk="medium" if (unparsed or ambiguous) else "low",
                     note=note or (f"{unparsed} value(s) could not be read as dates and became missing" if unparsed else None))


def _norm_cats(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    if not _is_text(s):
        raise Rejected(f"'{c}' is not a text column")
    t = s.copy()
    if op.case:
        stripped = t.astype(str).str.strip()
        t = t.where(t.isna(), getattr(stripped.str, op.case)())
    lower_map = {k.strip().lower(): v.strip() for k, v in op.mapping.items() if v and v.strip()}
    present = {str(v).strip().lower() for v in s.dropna().unique()}
    unknown = [k for k in lower_map if k not in present and (not op.case or k not in {str(v).strip().lower() for v in t.dropna().unique()})]
    t = t.map(lambda v: lower_map.get(v.strip().lower(), v) if isinstance(v, str) else v)
    changed = (s.astype(str) != t.astype(str)) & s.notna()
    n = int(changed.sum())
    if not n:
        raise NoOp()
    before_u, after_u = s.dropna().nunique(), t.dropna().nunique()
    if verify and after_u < max(1, 0.4 * before_u) and before_u > 3:
        # A big merge is fine when every target is a value that already exists in the data (typo variants -> the real
        # value). Reject only when targets are invented or the merge collapses values that look unrelated.
        targets = {v.strip().lower() for v in lower_map.values()}
        invented = [x for x in targets if x not in present]
        if invented:
            raise Rejected(f"would merge {before_u} distinct values into {after_u} using values that do not exist in the data ({', '.join(sorted(invented)[:3])}); too aggressive")
    merges = {}
    for v in s[changed].dropna().unique():
        merges.setdefault(str(t[s == v].iloc[0]), set()).add(str(v))
    semantic = [f"{', '.join(sorted(srcs))} -> {dst}" for dst, srcs in merges.items()
                if any(_dissimilar(x, dst) for x in srcs)]
    note = None
    if semantic:
        note = "Merged values that are not just spelling/case variants, please check: " + "; ".join(semantic[:5])
    elif unknown:
        note = f"Ignored mapping entries not found in the data: {unknown[:3]}"
    new = df.copy()
    new[c] = t
    return new, dict(description=f"Unified spelling/case in '{c}' ({before_u} distinct values -> {after_u})",
                     rows_affected=n, examples=_ex(s[changed].head(40), t[changed].head(40), 3),
                     risk="medium" if semantic else "low", note=note)


def _impute(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    miss = int(s.isna().sum())
    if not miss:
        raise NoOp()
    share = miss / max(1, len(s))
    if verify and share > 0.5:
        raise Rejected(f"{share:.0%} of '{c}' is missing; imputing would mostly invent data")
    if op.strategy in ("median", "mean"):
        if not pd.api.types.is_numeric_dtype(s):
            raise Rejected(f"'{c}' is not numeric yet (convert it first)")
        fill = s.median() if op.strategy == "median" else s.mean()
    elif op.strategy == "mode":
        m = s.mode(dropna=True)
        if m.empty:
            raise Rejected("no most-common value to use")
        fill = m.iloc[0]
    else:
        if op.value is None:
            raise Rejected("constant strategy needs a value")
        fill = float(op.value) if pd.api.types.is_numeric_dtype(s) else op.value
    new = df.copy()
    new[c] = s.fillna(fill)
    return new, dict(description=f"Filled {miss} missing values in '{c}' with the {op.strategy}" + (f" ({fill:.4g})" if isinstance(fill, (int, float, np.floating)) else f" ({fill})"),
                     rows_affected=miss, examples=[{"before": "(empty)", "after": str(fill)}],
                     risk="high" if share > 0.3 else "medium",
                     note="Imputation changes the data distribution; the report treats these values as estimates")


def _drop_rows(df, op, verify):
    for c in op.columns:
        _need(df, c)
    mask = df[list(op.columns)].isna().any(axis=1)
    n = int(mask.sum())
    if not n:
        raise NoOp()
    share = n / max(1, len(df))
    if verify and share > 0.05:
        raise Rejected(f"would drop {share:.0%} of all rows; too many")
    return df[~mask], dict(description=f"Dropped {n} rows with missing {', '.join(op.columns)}", rows_affected=n,
                           examples=[{"before": f"row {i}", "after": "removed"} for i in df.index[mask][:3]],
                           risk="medium" if share > 0.01 else "low")


def _flag_outliers(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    if not pd.api.types.is_numeric_dtype(s):
        raise Rejected(f"'{c}' is not numeric yet (convert it first)")
    v = s.dropna()
    q1, q3 = v.quantile(0.25), v.quantile(0.75)
    iqr = q3 - q1
    if iqr <= 0:
        raise Rejected(f"'{c}' has no spread, outlier rule does not apply")
    lo, hi = q1 - op.k * iqr, q3 + op.k * iqr
    mask = (s < lo) | (s > hi)
    n = int(mask.sum())
    if not n:
        raise NoOp()
    name = f"{c}_is_outlier"
    if name in df.columns:
        raise Rejected(f"column '{name}' already exists")
    new = df.copy()
    new[name] = mask.fillna(False)
    return new, dict(description=f"Flagged {n} extreme values in '{c}' (outside {lo:.4g} to {hi:.4g}) in a new column; values were NOT changed",
                     rows_affected=n, examples=[{"before": f"{c} = {x:.6g}", "after": "flagged"} for x in s[mask].head(3)],
                     risk="low", note="Extreme values stay in the data; analysis code should decide whether to exclude them")


def _set_invalid(df, op, verify):
    c = _need(df, op.column)
    s = df[c]
    if not pd.api.types.is_numeric_dtype(s):
        raise Rejected(f"'{c}' is not numeric yet (convert it first)")
    if op.min_value is None and op.max_value is None:
        raise Rejected("needs min_value and/or max_value")
    mask = pd.Series(False, index=s.index)
    if op.min_value is not None:
        mask |= s < op.min_value
    if op.max_value is not None:
        mask |= s > op.max_value
    n = int(mask.sum())
    if not n:
        raise NoOp()
    share = n / max(1, len(s))
    if verify and share > 0.05:
        raise Rejected(f"{share:.0%} of '{c}' is outside the allowed range; the range is probably wrong")
    new = df.copy()
    new[c] = s.mask(mask, np.nan)
    return new, dict(description=f"Set {n} impossible values in '{c}' to missing (allowed: {_range_txt(op.min_value, op.max_value)})",
                     rows_affected=n, examples=[{"before": f"{x:.6g}", "after": "(empty)"} for x in s[mask].head(3)],
                     risk="medium" if share > 0.01 else "low")


def _drop_cols(df, op, verify):
    for c in op.columns:
        _need(df, c)
        if verify and df[c].notna().any():
            raise Rejected(f"'{c}' contains data; only completely empty columns may be dropped")
    return df.drop(columns=list(op.columns)), dict(description=f"Dropped empty column(s) {', '.join(op.columns)}",
                                                    rows_affected=0, examples=[], risk="low")


EXEC = {
    "drop_exact_duplicates": _drop_dups, "strip_whitespace": _strip, "standardize_missing": _std_missing,
    "parse_numeric": _parse_numeric, "parse_date": _parse_date, "normalize_categories": _norm_cats,
    "impute": _impute, "drop_rows_missing": _drop_rows, "flag_outliers": _flag_outliers,
    "set_invalid_to_missing": _set_invalid, "drop_columns": _drop_cols,
}


# ------------------------------------------------------------------ plan execution + global checks
def apply_plan(df_raw: pd.DataFrame, ops: list, disabled=frozenset(), verify: bool = True):
    """Run ops in order on a copy. Returns (clean_df, log, checks, summary)."""
    df = df_raw.copy()
    log, targeted, dups = [], set(), 0
    for i, op in enumerate(ops):
        entry = {"id": i, "op": op.op, "reason": op.reason, "columns": _cols(op), "params": op.model_dump()}
        if i in disabled:
            entry["status"] = "disabled"
            log.append(entry)
            continue
        try:
            new_df, info = EXEC[op.op](df, op, verify)
        except NoOp:
            entry["status"] = "skipped"
            log.append(entry)
            continue
        except Rejected as e:
            entry.update(status="rejected", reject_reason=str(e))
            log.append(entry)
            continue
        except Exception as e:  # defensive: a buggy op must never break the run
            entry.update(status="rejected", reject_reason=f"could not run: {type(e).__name__}: {e}")
            log.append(entry)
            continue
        entry.update(status="applied", **info)
        log.append(entry)
        df = new_df
        targeted |= set(_cols(op))
        if op.op == "flag_outliers":
            targeted.add(f"{op.column}_is_outlier")
        if op.op == "drop_exact_duplicates":
            dups += info["rows_affected"]

    checks = []
    removed_other = len(df_raw) - len(df) - dups
    checks.append({"name": "No unexpected row loss", "passed": removed_other <= 0.03 * max(1, len(df_raw)),
                   "detail": f"{len(df_raw)} -> {len(df)} rows ({dups} were exact duplicates)"})
    lost = [c for c in df_raw.columns if c not in df.columns]
    checks.append({"name": "No column lost", "passed": not lost, "detail": "all original columns kept" if not lost else f"lost: {lost}"})
    common = df.index.intersection(df_raw.index)
    changed_untouched = []
    for c in df_raw.columns:
        if c in targeted or c not in df.columns:
            continue
        if not df_raw.loc[common, c].equals(df.loc[common, c]):
            changed_untouched.append(c)
    checks.append({"name": "Untouched columns unchanged", "passed": not changed_untouched,
                   "detail": "only targeted columns were modified" if not changed_untouched else f"unexpected changes in {changed_untouched}"})
    miss_b = int(df_raw.isna().sum().sum())
    miss_a = int(df[[c for c in df_raw.columns if c in df.columns]].isna().sum().sum())
    summary = {"rows_before": len(df_raw), "rows_after": len(df), "missing_before": miss_b, "missing_after": miss_a,
               "columns_added": [c for c in df.columns if c not in df_raw.columns],
               "applied": sum(e["status"] == "applied" for e in log),
               "rejected": sum(e["status"] == "rejected" for e in log),
               "imputed_columns": [e["columns"][0] for e in log if e["status"] == "applied" and e["op"] == "impute"],
               "date_columns": [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]}
    return df, log, checks, summary
