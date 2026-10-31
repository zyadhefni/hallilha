"""Deterministic dataset profiling. The LLM sees this compact profile instead of the raw data
(cheaper, and the facts in it are computed, not guessed)."""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

PLACEHOLDERS = {"n/a", "na", "null", "none", "nan", "-", "--", "?", "unknown", "missing",
                "not available", "#n/a", "nil", "tbd"}

_NUM = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
NUMERIC_RX = re.compile(rf"^\s*(?:[A-Za-z]{{1,3}}\s|[$€£﷼]\s?)?({_NUM})(?:\s?[A-Za-z%﷼]{{1,3}})?\s*$")
_DATE_HINT = re.compile(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|[A-Za-z]{3,9}\.?\s+\d{1,2}|\d{1,2}\s+[A-Za-z]{3,9}")
_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-]\d{2,4}\s*$")


# ------------------------------------------------------------------ small helpers
def is_placeholder(v) -> bool:
    return isinstance(v, str) and (v.strip().lower() in PLACEHOLDERS or v.strip() == "")


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬\u00a0\u202f", "01234567890123456789., " + " ")
_NUM_CORE = re.compile(r"^\s*(?:[A-Za-z]{1,3}(?:\.\s?|\s)|ر\.س\.?\s?|[$€£﷼]\s?)?([-+]?\d[\d.,\s]*\d|[-+]?\d)\s*(?:[A-Za-z%﷼]{1,3}|ر\.س)?\s*$")


def to_number(v) -> float:
    """'SAR 1,234.50' -> 1234.5 ; '١٢٣٫٥٠' -> 123.5 ; '1.234,50' -> 1234.5 ; '(120.00)' -> -120 ; other text -> nan."""
    if isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool):
        return float(v)
    if not isinstance(v, str):
        return float("nan")
    s = v.translate(_AR_DIGITS).strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    m = _NUM_CORE.match(s)
    if not m:
        return float("nan")
    core = re.sub(r"(?<=\d)\s(?=\d{3}\b)", "", m.group(1)).replace(" ", "")
    if re.search(r"\s", m.group(1)) and not re.fullmatch(r"[-+]?\d{1,3}(?:\s\d{3})+(?:[.,]\d+)?", m.group(1).strip()):
        return float("nan")
    has_c, has_d = "," in core, "." in core
    if has_c and has_d:
        dec = "," if core.rfind(",") > core.rfind(".") else "."
        core = core.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif has_c:
        if re.fullmatch(r"[-+]?\d{1,3}(?:,\d{3})+", core):
            core = core.replace(",", "")
        elif re.fullmatch(r"[-+]?\d+,\d{1,2}", core):
            core = core.replace(",", ".")
        else:
            return float("nan")
    elif has_d and core.count(".") > 1:
        if re.fullmatch(r"[-+]?\d{1,3}(?:\.\d{3})+", core):
            core = core.replace(".", "")
        else:
            return float("nan")
    try:
        x = float(core)
    except ValueError:
        return float("nan")
    return -x if neg else x


def _parse_dates_raw(s: pd.Series, dayfirst: bool) -> pd.Series:
    try:
        return pd.to_datetime(s, errors="coerce", format="mixed", dayfirst=dayfirst)
    except (TypeError, ValueError):
        from dateutil import parser as dp

        def one(v):
            try:
                return pd.Timestamp(dp.parse(str(v), dayfirst=dayfirst))
            except Exception:
                return pd.NaT

        return s.map(one)


_ISO_START = re.compile(r"^\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


def parse_dates(s: pd.Series, dayfirst: bool = False) -> pd.Series:
    """Parse mixed-format date text. Year-first values (2026-03-04) are unambiguous and never day-first."""
    s = s.astype("object").where(s.notna(), None)
    iso = s.map(lambda v: bool(_ISO_START.match(v)) if isinstance(v, str) else False)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if iso.any():
        out[iso] = _parse_dates_raw(s[iso], False).astype("datetime64[ns]")
    rest = ~iso & s.notna()
    if rest.any():
        out[rest] = _parse_dates_raw(s[rest], dayfirst).astype("datetime64[ns]")
    return out


def date_order_evidence(s: pd.Series) -> dict:
    """How many dd/mm/yyyy-style values prove day-first (first part > 12) or month-first (second part > 12)."""
    day_first = month_first = 0
    for v in s.dropna().astype(str):
        m = _DMY.match(v)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            day_first += a > 12
            month_first += b > 12
    return {"dayfirst_evidence": int(day_first), "monthfirst_evidence": int(month_first)}


def _num(x, nd=4):
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return None
    return round(float(x), nd)


def _numeric_stats(v: pd.Series) -> dict:
    v = v.dropna().astype(float)
    if v.empty:
        return {}
    q1, q3 = v.quantile(0.25), v.quantile(0.75)
    iqr = q3 - q1
    out = int(((v < q1 - 3 * iqr) | (v > q3 + 3 * iqr)).sum()) if iqr > 0 else 0
    return {"min": _num(v.min()), "max": _num(v.max()), "mean": _num(v.mean()), "median": _num(v.median()),
            "negatives": int((v < 0).sum()), "outliers_iqr3": out}


def _norm_key(x: str) -> str:
    return re.sub(r"[^a-z0-9]", "", x.lower())


# ------------------------------------------------------------------ column profile
def profile_col(s: pd.Series, n_rows: int) -> dict:
    info: dict = {"name": str(s.name), "dtype": str(s.dtype), "missing": int(s.isna().sum())}
    if pd.api.types.is_bool_dtype(s):
        info["kind"] = "boolean"
        return info
    if pd.api.types.is_datetime64_any_dtype(s):
        info["kind"] = "datetime"
        nn = s.dropna()
        if len(nn):
            info["min"], info["max"] = str(nn.min().date()), str(nn.max().date())
        return info
    if pd.api.types.is_numeric_dtype(s):
        info["kind"] = "numeric"
        info["n_unique"] = int(s.nunique())
        info["unique_ratio"] = _num(s.nunique() / max(1, s.notna().sum()), 3)
        info.update(_numeric_stats(s))
        return info

    # ---- text-like column
    vals = s.dropna().astype(str)
    stripped = vals.str.strip()
    ph_mask = stripped.str.lower().isin(PLACEHOLDERS) | (stripped == "")
    if ph_mask.any():
        info["placeholder_missing"] = {k: int(c) for k, c in Counter(stripped[ph_mask]).most_common(5)}
    real = stripped[~ph_mask]
    collapsed = vals.str.strip().str.replace(r"\s+", " ", regex=True)
    info["whitespace_issues"] = int((vals != collapsed).sum())
    info["samples"] = [str(x) for x in pd.unique(vals)[:5]]
    info["n_unique"] = int(real.nunique())
    if real.empty:
        info["kind"] = "empty"
        return info
    info["unique_ratio"] = _num(real.nunique() / len(real), 3)

    nums = real.map(to_number)
    if nums.notna().mean() >= 0.8:
        info["kind"] = "numeric_like_text"
        info["unparseable_values"] = [str(x) for x in real[nums.isna()].unique()[:5]]
        info.update(_numeric_stats(nums))
        return info

    hint = real.map(lambda x: bool(_DATE_HINT.search(x)))
    if hint.mean() >= 0.8:
        parsed = parse_dates(real)
        if parsed.notna().mean() >= 0.8:
            info["kind"] = "date_like_text"
            pats = Counter(re.sub(r"[A-Za-z]+", "A", re.sub(r"\d", "9", x)) for x in real)
            info["date_formats"] = {k: int(c) for k, c in pats.most_common(5)}
            info.update(date_order_evidence(real))
            return info

    if real.nunique() <= 50 or real.nunique() / len(real) <= 0.5:
        info["kind"] = "categorical"
        vc = real.value_counts()
        info["top_values"] = [[str(k), int(c)] for k, c in vc.head(40).items()]
        groups: dict[str, list] = {}
        for k, c in vc.items():
            groups.setdefault(_norm_key(str(k)), []).append((str(k), int(c)))
        info["variant_groups"] = [{"values": [x[0] for x in g], "counts": [x[1] for x in g]}
                                  for g in groups.values() if len(g) > 1][:8]
        return info

    info["kind"] = "id_like" if info["unique_ratio"] > 0.9 else "text"
    return info


def profile_df(df: pd.DataFrame, max_cols: int = 60) -> dict:
    n = len(df)
    return {
        "n_rows": int(n), "n_cols": int(df.shape[1]),
        "exact_duplicate_rows": int(df.duplicated().sum()),
        "columns": [profile_col(df[c], n) for c in list(df.columns)[:max_cols]],
    }


# ------------------------------------------------------------------ issues
def detected_issues(prof: dict) -> list[dict]:
    """Human-readable issues. `fixable` issues are ones the cleaning DSL can resolve (and trigger a 2nd pass)."""
    out: list[dict] = []
    n = max(1, prof["n_rows"])
    if prof["exact_duplicate_rows"]:
        out.append({"type": "duplicates", "column": None, "count": prof["exact_duplicate_rows"], "fixable": True,
                    "detail": f"{prof['exact_duplicate_rows']} exact duplicate rows"})
    for c in prof["columns"]:
        name, kind = c["name"], c.get("kind")
        if c.get("placeholder_missing"):
            cnt = sum(c["placeholder_missing"].values())
            toks = ", ".join(repr(t) for t in c["placeholder_missing"])
            out.append({"type": "placeholders", "column": name, "count": cnt, "fixable": True,
                        "detail": f"{cnt} placeholder values ({toks}) that mean 'missing'"})
        if c.get("whitespace_issues"):
            out.append({"type": "whitespace", "column": name, "count": c["whitespace_issues"], "fixable": True,
                        "detail": f"{c['whitespace_issues']} values with extra spaces"})
        if kind == "numeric_like_text":
            out.append({"type": "numeric_as_text", "column": name, "count": prof["n_rows"] - c["missing"], "fixable": True,
                        "detail": "numbers stored as text (currency symbols or thousands separators)"})
        if kind == "date_like_text":
            fm = c.get("date_formats", {})
            out.append({"type": "dates_as_text", "column": name, "count": sum(fm.values()), "fixable": True,
                        "detail": f"dates stored as text in {len(fm)} different format(s)"})
        if kind == "categorical" and c.get("variant_groups"):
            out.append({"type": "category_variants", "column": name, "count": len(c["variant_groups"]), "fixable": True,
                        "detail": f"{len(c['variant_groups'])} groups of spelling/case variants"})
        if kind in ("numeric", "numeric_like_text"):
            if c.get("negatives"):
                out.append({"type": "negatives", "column": name, "count": c["negatives"], "fixable": False,
                            "detail": f"{c['negatives']} negative values (check if valid)"})
            if c.get("outliers_iqr3"):
                out.append({"type": "outliers", "column": name, "count": c["outliers_iqr3"], "fixable": False,
                            "detail": f"{c['outliers_iqr3']} extreme values (beyond 3x IQR)"})
        real_missing = c["missing"]
        if real_missing and real_missing / n >= 0.02:
            out.append({"type": "missing", "column": name, "count": real_missing, "fixable": False,
                        "detail": f"{real_missing} missing values ({real_missing / n:.1%})"})
    return out
