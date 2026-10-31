"""Step-by-step accuracy of Hallilha's cleaned file vs the answer key.
   python3 compare.py cleaned_messy_sales.csv [report.html]
"""
import json, re, sys
import pandas as pd, numpy as np

key = json.load(open("answer_key.json")); truth = pd.read_csv("truth_sales_clean.csv")
c = pd.read_csv(sys.argv[1], keep_default_na=False, na_values=[""], low_memory=False)
rows = []
def add(step, got, want, ok): rows.append((step, got, want, "PASS" if ok else "FAIL"))

add("1. duplicates removed (rows)", len(c), key["problems"]["file"]["true_rows_after_dedup"], len(c) == 600)
add("   order_id still duplicated", int(c.order_id.duplicated().sum()), 0, c.order_id.duplicated().sum() == 0)
d = pd.to_datetime(c["order_date"], errors="coerce")
add("2. dates parsed (non-null)", int(d.notna().sum()), len(c), d.notna().all())
tm = truth.set_index("order_id"); cm = c.drop_duplicates("order_id").set_index("order_id")
td = pd.to_datetime(tm["order_date"]); common = cm.index.intersection(tm.index)
dd = pd.to_datetime(cm.loc[common, "order_date"], errors="coerce")
add("   dates equal to truth (day/month not swapped)", f"{(dd == td.loc[common]).mean():.1%}", "100%", (dd == td.loc[common]).all())
rv = pd.to_numeric(c["revenue"], errors="coerce")
add("3. revenue numeric", int(rv.notna().sum()), len(c), rv.notna().all())
cr = pd.to_numeric(cm.loc[common, "revenue"], errors="coerce")
add("   revenue equal to truth", f"{(abs(cr - tm.loc[common,'revenue']) < 0.01).mean():.1%}", "100%", (abs(cr - tm.loc[common,'revenue']) < 0.01).all())
add("4. region distinct values", c["region"].nunique(), 5, c["region"].nunique() == 5)
reg_ok = (cm.loc[common, "region"].astype(str).str.strip().str.lower() == tm.loc[common, "region"].str.lower())
add("   region equal to truth", f"{reg_ok.mean():.1%}", "100%", reg_ok.all())
add("5. category with extra spaces", int(c["category"].astype(str).map(lambda v: v != v.strip()).sum()), 0, True)
add("6. placeholders left (-, N/A)", int(c[["channel", "units"]].isin(["-", "N/A"]).sum().sum()), 0, c[["channel", "units"]].isin(["-", "N/A"]).sum().sum() == 0)
u = pd.to_numeric(c["units"], errors="coerce")
add("7. negative units left", int((u < 0).sum()), 0, (u < 0).sum() == 0)
extra = [x for x in c.columns if x.endswith("_is_outlier")]
add("8. x100 typos (3 planted) flagged/fixed", int(sum((c[x].astype(str).str.lower() == "true").sum() for x in extra)) if extra else "no flag column",
    "≥3 flagged, or units ≤ 30 only", (u.dropna() <= 30).all() or (bool(extra) and sum((c[x].astype(str).str.lower() == "true").sum() for x in extra) >= 3))
add("9. total revenue vs truth", f"{rv.sum():,.2f}", f"{key['truth']['total_revenue_true']:,.2f}", abs(rv.sum() - key["truth"]["total_revenue_true"]) < 1)
m = rv.groupby(d.dt.to_period("M").astype(str)).sum().round(2)
mt = key["truth"]["revenue_by_month_true"]
add("   monthly revenue matches truth", f"{sum(abs(m.get(k,0)-v)<1 for k,v in mt.items())}/6 months", "6/6", all(abs(m.get(k, 0) - v) < 1 for k, v in mt.items()))
if len(sys.argv) > 2:
    t = open(sys.argv[2], encoding="utf-8", errors="ignore").read().lower()
    hit = all(x.lower() in t for x in key["insight_tokens"])
    add("10. found the hidden problem (" + " + ".join(key["insight_tokens"]) + ")", "yes" if hit else "no", "yes", hit)
w = max(len(r[0]) for r in rows)
for s, g, wnt, v in rows: print(f"{s:<{w}}  got={str(g):<22} want={str(wnt):<22} {v}")
p = sum(r[3] == "PASS" for r in rows); print(f"\n{p}/{len(rows)} checks passed")
