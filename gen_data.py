"""Synthetic datasets with KNOWN problems and a KNOWN hidden insight, so the agent can be scored.

Every dataset has: a clean ground-truth table, a dirty version with planted problems (duplicates, mixed date
formats, currency text, placeholder tokens, spelling/case variants, impossible values, extreme typos), and one
planted business insight (a segment whose metric changes) whose name the final decision should mention.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DOMAINS = ["sales", "hr", "support"]
LABELS = {"sales": "Retail sales", "hr": "Employee attrition", "support": "Support tickets"}
QUESTIONS = {"sales": "Why did revenue drop recently, and what should we do?",
             "hr": "Where are we losing employees and why?",
             "support": "Which team has a service problem?"}


@dataclass
class Dataset:
    domain: str
    seed: int
    key: str
    truth: pd.DataFrame
    dirty: pd.DataFrame
    insight_tokens: list[str]
    insight_text: str
    injected: dict = field(default_factory=dict)       # col -> set of truth row positions with a planted problem
    unrecoverable: dict = field(default_factory=dict)  # col -> positions whose true value cannot be recovered
    planted: dict = field(default_factory=dict)        # issue name -> count
    date_cols: list = field(default_factory=list)
    numeric_cols: list = field(default_factory=list)

    def csv(self) -> str:
        return self.dirty.to_csv(index=False)


class _Inj:
    def __init__(self, truth: pd.DataFrame, rng, noise: float = 1.0, locale: bool = False):
        self.rng, self.truth, self.noise, self.locale = rng, truth, noise, locale
        self.dirty = truth.astype(object).copy()
        self.injected, self.unrec, self.planted = {}, {}, {}

    def _pick(self, col, rate):
        n = len(self.truth)
        return set(self.rng.choice(n, size=max(1, int(n * min(rate * self.noise, 0.4))), replace=False).tolist())

    def _mark(self, col, pos, name, unrec=False):
        self.injected.setdefault(col, set()).update(pos)
        if unrec:
            self.unrec.setdefault(col, set()).update(pos)
        self.planted[name] = self.planted.get(name, 0) + len(pos)

    def date_formats(self, col):
        fmts = ["%Y-%m-%d", "%m/%d/%Y" if self.locale else "%d/%m/%Y", "%b %d, %Y"]   # locale: US month-first dates
        w = self.rng.choice(3, size=len(self.truth), p=[0.4, 0.4, 0.2])
        d = pd.to_datetime(self.truth[col])
        self.dirty[col] = [x.strftime(fmts[k]) for x, k in zip(d, w)]
        self._mark(col, set(np.where(w > 0)[0].tolist()), "mixed_date_formats")

    def currency(self, col, prefix="SAR ", rate=0.7):
        pos = self._pick(col, rate)
        for i in pos:
            x = float(self.truth.at[i, col])
            if self.locale:   # mixed regional number formats: Arabic-Indic digits, European separators
                r = self.rng.random()
                txt = (f"{x:,.2f}".translate(str.maketrans("0123456789,.", "٠١٢٣٤٥٦٧٨٩٬٫")) if r < 0.4
                       else f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") if r < 0.7 else f"{x:,.2f}")
                self.dirty.at[i, col] = f"{prefix}{txt}" if r >= 0.7 or r < 0.4 else f"{txt} SAR"
            else:
                self.dirty.at[i, col] = f"{prefix}{x:,.2f}"
        self._mark(col, pos, "currency_text")

    def variants(self, col, rate=0.08, typos=None):
        pos = sorted(self._pick(col, rate))
        for i in pos:
            v = str(self.truth.at[i, col])
            r = self.rng.random()
            self.dirty.at[i, col] = (v.lower() if r < 0.35 else v.upper() if r < 0.6 else f" {v} " if r < 0.8
                                     else (typos or {}).get(v, v.lower()))
        self._mark(col, set(pos), "category_variants")

    def whitespace(self, col, rate=0.05):
        pos = self._pick(col, rate)
        for i in pos:
            self.dirty.at[i, col] = f"{self.truth.at[i, col]}  "
        self._mark(col, pos, "extra_whitespace")

    def placeholders(self, col, token, rate=0.02):
        pos = self._pick(col, rate)
        for i in pos:
            self.dirty.at[i, col] = token
            self.truth.at[i, col] = np.nan
        self._mark(col, pos, "placeholder_tokens")

    def blanks(self, col, rate=0.02):
        pos = self._pick(col, rate)
        for i in pos:
            self.dirty.at[i, col] = np.nan
            self.truth.at[i, col] = np.nan
        self._mark(col, pos, "blank_values")

    def negatives(self, col, rate=0.01):
        pos = self._pick(col, rate)
        for i in pos:
            self.dirty.at[i, col] = -abs(float(self.truth.at[i, col]) or 1)
            self.truth.at[i, col] = np.nan
        self._mark(col, pos, "impossible_negatives")

    def outliers(self, col, mult=100, rate=0.006):
        pos = self._pick(col, rate)
        for i in pos:
            self.dirty.at[i, col] = float(self.truth.at[i, col]) * mult
        self._mark(col, pos, "extreme_typos", unrec=True)

    def duplicates(self, rate=0.03):
        n = len(self.dirty)
        dup = self.dirty.iloc[self.rng.choice(n, size=int(n * min(rate * self.noise, 0.4)), replace=False)]
        self.planted["duplicate_rows"] = len(dup)
        self.dirty = pd.concat([self.dirty, dup]).sample(frac=1, random_state=int(self.rng.integers(1e6))).reset_index(drop=True)


def _dates(rng, n, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    return s + pd.to_timedelta(rng.integers(0, (e - s).days + 1, n), unit="D")


def make_dataset(domain: str, seed: int, n: int = 900, noise: float = 1.0, arabic: bool = False,
                 locale: bool = False, insight: bool = True) -> Dataset:
    rng = np.random.default_rng(seed)
    if domain == "sales":
        regions = ["Riyadh", "Jeddah", "Dammam", "Makkah", "Abha"]
        cats = ["Electronics", "Grocery", "Fashion", "Home"]
        base = {"Electronics": 900, "Grocery": 60, "Fashion": 250, "Home": 400}
        R, C = str(rng.choice(regions)), str(rng.choice(cats))
        t = pd.DataFrame({"order_id": [f"ORD-{i:05d}" for i in range(n)],
                          "order_date": _dates(rng, n, "2026-01-01", "2026-06-30"),
                          "region": rng.choice(regions, n), "category": rng.choice(cats, n),
                          "units": rng.poisson(5, n) + 1, "channel": rng.choice(["Online", "Store"], n)})
        t["unit_price"] = [round(base[c] * rng.uniform(0.9, 1.1), 2) for c in t["category"]]
        hit = (t["region"] == R) & (t["category"] == C) & (t["order_date"] >= "2026-04-01") & insight
        t.loc[hit, "units"] = np.maximum(1, (t.loc[hit, "units"] * 0.3).round()).astype(int)
        t["revenue"] = (t["units"] * t["unit_price"]).round(2)
        t = t[["order_id", "order_date", "region", "category", "channel", "units", "unit_price", "revenue"]]
        key, tokens = "order_id", ([str(R), str(C)] if insight else [])
        text = f"From April 2026, {C} sales in {R} dropped sharply (units fell by about 70%)."
        inj = _Inj(t, rng, noise, locale)
        inj.date_formats("order_date"); inj.currency("revenue")
        inj.variants("region", typos={"Riyadh": "Riyad", "Jeddah": "Jedda", "Dammam": "Damam", "Makkah": "Makka", "Abha": "Abaha"})
        inj.whitespace("category"); inj.placeholders("channel", "-"); inj.placeholders("units", "N/A")
        inj.blanks("unit_price"); inj.negatives("units"); inj.outliers("units")
        dates, nums = ["order_date"], ["units", "unit_price", "revenue"]
    elif domain == "hr":
        depts = ["Engineering", "Sales", "Finance", "Operations", "Marketing"]
        D = str(rng.choice(depts)) if insight else "__none__"
        t = pd.DataFrame({"emp_id": [f"E{i:04d}" for i in range(n)], "department": rng.choice(depts, n),
                          "hire_date": _dates(rng, n, "2018-01-01", "2025-12-31")})
        t["monthly_salary"] = (rng.normal(14000, 3000, n)).round(2).clip(6000)
        t["overtime_hours"] = np.where(t["department"] == D, rng.normal(26, 5, n), rng.normal(8, 4, n)).clip(0).round(1)
        t["satisfaction"] = np.where(t["department"] == D, rng.normal(4.8, 1.2, n), rng.normal(7.2, 1.3, n)).clip(1, 10).round(1)
        p = np.where(t["department"] == D, 0.38, 0.08)
        t["resigned"] = np.where(rng.random(n) < p, "Yes", "No")
        key, tokens = "emp_id", ([str(D)] if insight else [])
        text = f"{D} has far more resignations (about 38% vs 8%), with much higher overtime and lower satisfaction."
        inj = _Inj(t, rng, noise, locale)
        inj.date_formats("hire_date"); inj.currency("monthly_salary")
        inj.variants("department", typos={"Engineering": "Enginering", "Sales": "Sale", "Finance": "Finanse", "Operations": "Operation", "Marketing": "Marketng"})
        inj.variants("resigned", rate=0.06); inj.placeholders("satisfaction", "N/A"); inj.negatives("overtime_hours"); inj.outliers("monthly_salary", mult=10)
        dates, nums = ["hire_date"], ["monthly_salary", "overtime_hours", "satisfaction"]
    else:
        teams = ["Billing", "Technical", "Accounts", "Shipping", "Returns"]
        T = str(rng.choice(teams)) if insight else "__none__"
        t = pd.DataFrame({"ticket_id": [f"T-{i:05d}" for i in range(n)], "opened_at": _dates(rng, n, "2026-01-01", "2026-06-30"),
                          "team": rng.choice(teams, n), "priority": rng.choice(["Low", "Medium", "High"], n, p=[0.4, 0.4, 0.2])})
        hours = rng.gamma(4, 3, n)
        late = (t["team"] == T) & (t["opened_at"] >= "2026-03-15")
        hours = np.where(late, hours * 2.4, hours)
        t["resolution_hours"] = hours.round(1)
        t["csat"] = np.where(late, rng.normal(2.6, 0.8, n), rng.normal(4.1, 0.7, n)).clip(1, 5).round(1)
        key, tokens = "ticket_id", ([str(T)] if insight else [])
        text = f"Since mid-March 2026 the {T} team takes about 2.4x longer to resolve tickets and has much lower satisfaction."
        inj = _Inj(t, rng, noise, locale)
        inj.date_formats("opened_at")
        inj.variants("team", typos={"Billing": "Biling", "Technical": "Tecnical", "Accounts": "Acounts", "Shipping": "Shiping", "Returns": "Retuns"})
        inj.variants("priority", rate=0.06); inj.placeholders("csat", "N/A"); inj.negatives("resolution_hours"); inj.outliers("resolution_hours", mult=50)
        dates, nums = ["opened_at"], ["resolution_hours", "csat"]
    inj.duplicates()
    ds = Dataset(domain, seed, key, inj.truth, inj.dirty, tokens, text, inj.injected, inj.unrec, inj.planted, dates, nums)
    return _arabic_headers(ds) if arabic else ds


AR = {"order_id": "رقم_الطلب", "order_date": "تاريخ_الطلب", "region": "المنطقة", "category": "الفئة", "channel": "القناة",
      "units": "الوحدات", "unit_price": "سعر_الوحدة", "revenue": "الإيراد", "emp_id": "رقم_الموظف", "department": "القسم",
      "hire_date": "تاريخ_التعيين", "monthly_salary": "الراتب_الشهري", "overtime_hours": "ساعات_إضافية",
      "satisfaction": "الرضا", "resigned": "استقال", "ticket_id": "رقم_التذكرة", "opened_at": "تاريخ_الفتح", "team": "الفريق",
      "priority": "الأولوية", "resolution_hours": "ساعات_الحل", "csat": "رضا_العميل"}


def _arabic_headers(ds: Dataset) -> Dataset:
    """Same data, Arabic column names: tests robustness to a different input convention."""
    ds.truth = ds.truth.rename(columns=AR)
    ds.dirty = ds.dirty.rename(columns=AR)
    ds.key = AR[ds.key]
    ds.date_cols = [AR[c] for c in ds.date_cols]
    ds.numeric_cols = [AR[c] for c in ds.numeric_cols]
    ds.injected = {AR[c]: v for c, v in ds.injected.items()}
    ds.unrecoverable = {AR[c]: v for c, v in ds.unrecoverable.items()}
    return ds
