"""Final report: one self-contained HTML file (charts embedded) with the decision, the evidence,
what was changed in the data, and the review trail (who approved what)."""
from __future__ import annotations

import base64
import html

CSS = """body{font:16px/1.55 system-ui,-apple-system,'Segoe UI',sans-serif;color:#0F1B2D;background:#F3F5F8;margin:0}
main{max-width:860px;margin:0 auto;padding:40px 20px 80px}h1{font-size:34px;line-height:1.1;letter-spacing:-.03em;margin:0 0 8px}
h2{font-size:20px;margin:40px 0 8px;padding-top:20px;border-top:1px solid #DCE2EA}h3{font-size:16px;margin:20px 0 4px}
.muted{color:#5A6678}.pill{display:inline-block;padding:1px 10px;border-radius:99px;background:#E9EEF8;font-size:13px}
.low{background:#FDE7E4}.medium{background:#FFF0C2}.high{background:#DDF3EA}
table{border-collapse:collapse;font-size:14px;margin:8px 0}td,th{border-bottom:1px solid #DCE2EA;padding:4px 12px 4px 0;text-align:left}
img{max-width:100%;border:1px solid #DCE2EA;border-radius:8px;background:#fff}pre{background:#fff;border:1px solid #DCE2EA;padding:10px;overflow:auto;font-size:13px;border-radius:8px}
li{margin:4px 0}.warn{background:#FFF0C2;padding:8px 12px;border-radius:8px}"""


def _e(x) -> str:
    return html.escape(str(x))


def _table(t: dict, limit: int = 15) -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in t["columns"])
    rows = "".join("<tr>" + "".join(f"<td>{_e('' if v is None else v)}</td>" for v in r) + "</tr>" for r in t["rows"][:limit])
    return f"<table><tr>{head}</tr>{rows}</table>"


def build_report_html(run) -> str:
    s = run.state
    u = s["stages"]["understand"]["report"]["understanding"]
    cl = s["stages"]["clean"]["report"]
    an = s["stages"]["analyze"]["report"]["analyses"]
    ch = s["stages"]["visualize"]["report"]["charts"]
    dec = s["stages"]["decide"]["report"]
    d = dec["decision"]
    o = [f"<main><p class='muted'>Analysis of {_e(s['filename'])} · {_e(s['created'][:10])}</p><h1>{_e(d['headline'])}</h1>",
         f"<p>{_e(d['summary'])}</p>", f"<p class='muted'><b>Goal:</b> {_e(u['goal'])}</p>"]
    if dec["ungrounded"]:
        o.append(f"<p class='warn'>Numbers not verified against the computed results: {_e(', '.join(dec['ungrounded']))}</p>")
    o.append("<h2>Findings</h2><ul>" + "".join(f"<li>{_e(f['claim'])} <span class='muted'>(analysis {', '.join(str(i + 1) for i in f['evidence'])})</span></li>" for f in d["findings"]) + "</ul>")
    o.append("<h2>Recommendations</h2><ul>" + "".join(
        f"<li><b>{_e(r['action'])}</b> <span class='pill {r['confidence']}'>{r['confidence']} confidence</span><br><span class='muted'>{_e(r['why'])}</span></li>"
        for r in d["recommendations"]) + "</ul>")
    if d["caveats"]:
        o.append("<h3>Caveats</h3><ul>" + "".join(f"<li>{_e(c)}</li>" for c in d["caveats"]) + "</ul>")
    if d["needs_human"]:
        o.append("<h3>Needs a human decision</h3><ul>" + "".join(f"<li>{_e(c)}</li>" for c in d["needs_human"]) + "</ul>")
    o.append("<h2>Charts</h2>")
    for c in ch:
        b64 = base64.b64encode((run.dir / "charts" / c["file"]).read_bytes()).decode()
        o.append(f"<h3>{_e(c['title'])}</h3><img alt='{_e(c['title'])}' src='data:image/png;base64,{b64}'><p class='muted'>{_e(c['takeaway'])}</p>")
    o.append("<h2>What was changed in the data</h2>")
    sm = cl["summary"]
    o.append(f"<p>{sm['rows_before']} rows in, {sm['rows_after']} rows out. {sm['applied']} changes applied, {sm['rejected']} rejected by verification.</p><ul>")
    for e in cl["log"]:
        if e["status"] == "applied":
            o.append(f"<li>{_e(e['description'])} <span class='pill {'low' if e['risk'] == 'high' else 'medium' if e['risk'] == 'medium' else ''}'>{e['risk']} risk</span></li>")
    o.append("</ul>")
    o.append("<h2>Analyses (appendix)</h2>")
    for a in an:
        if a["status"] == "ok":
            o.append(f"<h3>{a['index'] + 1}. {_e(a['title'])}</h3><p>{_e(a['finding'])}</p>{_table(a['table'])}<details><summary>Code</summary><pre>{_e(a['code'])}</pre></details>")
    o.append("<h2>Review trail</h2><table><tr><th>When</th><th>Stage</th><th>Action</th><th>Note</th></tr>")
    for ev in s["events"]:
        o.append(f"<tr><td>{_e(ev['at'])}</td><td>{_e(ev['stage'])}</td><td>{_e(ev['action'])}</td><td>{_e(ev['note'])}</td></tr>")
    m = s.get("meter", {})
    o.append(f"</table><p class='muted'>{m.get('calls', 0)} model calls · about {m.get('approx_tokens', 0):,} tokens.</p></main>")
    return f"<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Analysis report</title><style>{CSS}</style><body>{''.join(o)}</body></html>"


def write_report(run) -> None:
    (run.dir / "report.html").write_text(build_report_html(run), encoding="utf-8")
