/* Hallilha (حلّلها): upload a CSV, watch an AI analyst work, review every stage. Vanilla JS, no build step. */
const $app = document.getElementById('app');
const $top = document.getElementById('topRight');
const $toast = document.getElementById('toast');

/* ---------- helpers ---------- */
const h = (tag, props = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v;
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return e;
};
let toastTimer;
function toast(msg, bad = false) {
  $toast.textContent = msg;
  $toast.className = 'on' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($toast.className = ''), bad ? 7000 : 2600);
}
async function api(path, method = 'GET', body) {
  const r = await fetch('/api' + path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.statusText);
  return j;
}
const act = async (fn, ok) => { try { const r = await fn(); if (ok) toast(ok); return r ?? true; } catch (e) { toast(e.message, true); return null; } };
const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US', { maximumFractionDigits: 2 }) : n ?? '');
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
const STAGE_INFO = {
  understand: ['Understand', 'Reads the file, profiles every column and works out what the data means.'],
  clean: ['Clean', 'Fixes problems in the data. Every change is verified by code and listed below, and you can switch any of them off.'],
  analyze: ['Analyze', 'Writes and runs analysis code to answer the goal. Every number comes from a computed table.'],
  visualize: ['Visualize', 'Chooses the charts that show the result most clearly.'],
  decide: ['Decide', 'Turns the evidence into findings and recommendations. Every number is checked against the results.'],
};
const STAGES = Object.keys(STAGE_INFO);

function table(t, { limit = 8 } = {}) {
  return h('div', { class: 'tablewrap' }, h('table', {},
    h('thead', {}, h('tr', {}, t.columns.map((c) => h('th', {}, c)))),
    h('tbody', {}, t.rows.slice(0, limit).map((r) => h('tr', {}, r.map((v) => h('td', { dir: 'auto' }, fmt(v))))))));
}
function stepper(s, view, onPick) {
  const ol = h('ol', { class: 'route draw', style: `--n:${STAGES.length}` });
  STAGES.forEach((k) => {
    const st = s.stages[k].status;
    const cur = k === s.current && s.status !== 'done';
    const cls = [st === 'approved' ? 'done' : '', cur ? 'cur' : '', cur && st === 'running' ? 'work' : '', cur && st === 'failed' ? 'fail' : '',
      st === 'approved' || cur ? 'clickable' : ''].join(' ');
    const tag = cur ? (st === 'running' ? 'Working' : st === 'review' ? 'Needs your review' : st === 'failed' ? 'Failed' : '') : '';
    ol.append(h('li', { class: cls, onclick: () => (st === 'approved' || cur) && onPick(k), 'aria-current': cur ? 'step' : false, title: st },
      h('span', { class: 'dot' }), STAGE_INFO[k][0] + (k === view && k !== s.current ? ' (viewing)' : ''), tag ? h('span', { class: 'tag' }, tag) : null));
  });
  return ol;
}

/* ---------- router ---------- */
let pollTimer;
async function route() {
  clearInterval(pollTimer);
  const [, page, arg] = (location.hash || '#/').split('/');
  $top.replaceChildren();
  try { page === 'run' && arg ? await runPage(arg) : await home(); }
  catch (e) { $app.replaceChildren(h('p', { class: 'muted' }, 'Could not load the page: ' + e.message)); }
}
window.addEventListener('hashchange', route);

/* ---------- home ---------- */
async function home() {
  const [info, runs] = await Promise.all([api('/info'), api('/runs')]);
  let csv = null, fname = '', busy = false;
  const q = h('textarea', { class: 'field', placeholder: 'Optional: what do you want to know? e.g. Why did revenue drop?', dir: 'auto', 'aria-label': 'Question' });
  const auto = h('input', { type: 'checkbox' });
  const fileIn = h('input', { type: 'file', accept: '.csv,text/csv,.txt', hidden: true });
  const drop = h('div', { class: 'drop', tabindex: 0, role: 'button', 'aria-label': 'Choose a CSV file' }, h('b', {}, 'Drop a CSV file here'), 'or click to choose one (max 5 MB)');
  const go = h('button', { class: 'btn primary', disabled: true }, 'Start analysis');
  const setFile = async (f) => {
    if (!f) return;
    if (f.size > 5e6) return toast('The file is larger than 5 MB', true);
    csv = await f.text(); fname = f.name;
    drop.className = 'drop has'; drop.replaceChildren(h('b', {}, fname), `${(f.size / 1024).toFixed(0)} KB ready`);
    go.disabled = false;
  };
  drop.onclick = () => fileIn.click();
  drop.onkeydown = (e) => (e.key === 'Enter' || e.key === ' ') && fileIn.click();
  fileIn.onchange = () => setFile(fileIn.files[0]);
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => setFile(e.dataTransfer.files[0]));
  const start = async (call) => {
    if (busy) return; busy = true;
    const r = await act(call);
    busy = false;
    if (r && r.id) location.hash = `#/run/${r.id}`;
  };
  go.onclick = () => start(() => api('/runs', 'POST', { filename: fname, csv_text: csv, question: q.value, autopilot: auto.checked }));

  const samples = info.samples.map((sm) => h('button', { class: 'sample', onclick: () => start(() => api('/runs/sample', 'POST', { domain: sm.domain, question: q.value || sm.question, autopilot: auto.checked })) },
    h('div', {}, h('div', { class: 'nm' }, sm.label), h('div', { class: 'q' }, sm.question)), h('span', { class: 'go' }, 'Analyze this data')));

  const demo = h('ol', { class: 'route draw', style: `--n:${STAGES.length}` });
  STAGES.forEach((k, i) => demo.append(h('li', { class: i < 2 ? 'done' : i === 2 ? 'cur' : '' }, h('span', { class: 'dot' }), STAGE_INFO[k][0], i === 2 ? h('span', { class: 'tag' }, 'Needs your review') : null)));

  $app.replaceChildren(h('div', { class: 'hero' },
    h('div', {},
      h('h1', {}, 'Hand over the spreadsheet. Review the work.'),
      h('p', { class: 'lead' }, 'An AI analyst that understands your data, cleans it, analyzes it and recommends what to do. It does all the work; you review each stage before it moves on.'),
      h('div', { class: 'route-wrap' }, demo),
      h('div', { class: 'samples' }, h('h2', {}, 'No data at hand? Try a sample with hidden problems'), samples)),
    h('div', {},
      h('div', { class: 'start' },
        h('h2', {}, 'Start a new analysis'), drop, fileIn, q,
        h('label', { class: 'chk' }, auto, h('span', {}, 'Autopilot', h('small', {}, 'Continue by itself when every check passes. It still stops when something needs your eyes.'))),
        go),
      runs.length ? h('div', { class: 'recent' }, h('h2', {}, 'Recent analyses'), runs.slice(0, 5).map((r) => h('a', { href: `#/run/${r.id}` }, h('span', {}, r.filename), h('span', { class: 'chip ' + (r.status === 'done' ? 'ok' : r.status === 'failed' ? 'late' : 'here') }, r.status === 'done' ? 'done' : r.status === 'review' ? 'needs review' : r.status)))) : null)));
}

/* ---------- run page ---------- */
async function runPage(id) {
  let s = await api('/runs/' + id);
  const ui = { view: null, disabled: null, note: '', noteOpen: false, cleanKey: '' };
  const root = h('div');
  $app.replaceChildren(root);
  $top.append(h('a', { class: 'btn ghost small', href: '#/' }, 'New analysis'));
  let sig = '';

  const doApprove = () => act(async () => { await api(`/runs/${id}/approve`, 'POST'); ui.view = null; ui.noteOpen = false; ui.note = ''; await refresh(); }, null);
  const doRevise = () => act(async () => { await api(`/runs/${id}/revise`, 'POST', { note: ui.note }); ui.noteOpen = false; ui.note = ''; await refresh(); }, 'Sent back to the analyst');
  async function refresh() { s = await api('/runs/' + id); draw(true); }

  function draw(force) {
    const next = JSON.stringify(s);
    if (!force && next === sig) return;
    sig = next;
    const y = window.scrollY;
    const view = ui.view || s.current;
    const st = s.stages[view];
    const isCur = view === s.current && s.status !== 'done';
    const kids = [];

    kids.push(h('div', { class: 'runhead' }, h('p', { class: 'file' }, s.filename + (s.question ? ' · ' + s.question : '')),
      stepper(s, view, (k) => { ui.view = k === s.current ? null : k; draw(true); })));

    if (s.status === 'done') {
      kids.push(h('div', { class: 'callout good' }, h('b', {}, 'Analysis complete'), 'You approved every stage. The report includes the findings, charts, what changed in the data, and your review trail.',
        h('div', { class: 'row' },
          h('a', { class: 'btn primary small', href: `/api/runs/${id}/report.html`, target: '_blank' }, 'Open the report'),
          h('a', { class: 'btn ghost small', href: `/api/runs/${id}/clean.csv` }, 'Download cleaned CSV'))));
    }

    kids.push(h('div', { class: 'stagehead' }, h('h1', {}, STAGE_INFO[view][0]), h('p', {}, STAGE_INFO[view][1])));

    if (st.status === 'running' || st.status === 'pending') {
      kids.push(h('div', { class: 'working' }, h('span', { class: 'dots' }, h('i'), h('i'), h('i')), ` The analyst is working. Model calls so far: ${s.meter.calls || 0}.`));
    } else if (st.status === 'failed') {
      kids.push(h('div', { class: 'callout bad' }, h('b', {}, 'This stage failed'), st.error || 'Unknown error',
        h('div', { class: 'row' }, h('button', { class: 'btn small', onclick: () => act(async () => { await api(`/runs/${id}/revise`, 'POST', { note: '' }); await refresh(); }, 'Trying again') }, 'Try again'))));
    } else if (st.report) {
      if (st.flags.length && isCur) {
        kids.push(h('div', { class: 'callout' }, h('b', {}, `${plural(st.flags.length, 'thing')} need your attention`), h('ul', {}, st.flags.map((f) => h('li', { dir: 'auto' }, f)))));
      }
      kids.push(...({ understand: vUnderstand, clean: vClean, analyze: vAnalyze, visualize: vVisualize, decide: vDecide })[view](st.report, isCur));
    }

    const evs = s.events;
    kids.push(h('details', { class: 'trail' }, h('summary', {}, `Review trail and cost (${evs.length} events, ${s.meter.calls || 0} model calls, about ${(s.meter.approx_tokens || 0).toLocaleString()} tokens)`),
      evs.length ? h('table', {}, h('thead', {}, h('tr', {}, ['When', 'Stage', 'Action', 'Note'].map((x) => h('th', {}, x)))),
        h('tbody', {}, evs.map((e) => h('tr', {}, h('td', {}, e.at.replace('T', ' ')), h('td', {}, e.stage), h('td', {}, e.action), h('td', { dir: 'auto' }, e.note))))) : h('p', {}, 'Nothing yet.')));

    /* review bar */
    if (isCur && st.status === 'review') {
      const last = view === STAGES[STAGES.length - 1];
      const noteBox = h('textarea', { class: 'field', placeholder: 'Tell the analyst what to change, e.g. "Revenue is in USD, not SAR" or "Do not merge Riyad into Riyadh"', dir: 'auto', 'aria-label': 'Note for the analyst', oninput: (e) => (ui.note = e.target.value) }, ui.note);
      kids.push(h('div', { class: 'sticky' },
        ui.noteOpen ? h('div', {}, noteBox) : null,
        h('div', { class: 'bar' },
          h('span', { class: 'txt' }, h('b', {}, `Review the ${STAGE_INFO[view][0].toLowerCase()} work`), s.autopilot ? ' · Autopilot is on and stopped here because of the flags above' : ''),
          h('label', { class: 'chk' }, h('input', { type: 'checkbox', checked: s.autopilot, onchange: (e) => act(async () => { await api(`/runs/${id}/autopilot`, 'POST', { on: e.target.checked }); await refresh(); }) }), 'Autopilot'),
          ui.noteOpen ? h('button', { class: 'btn ghost', onclick: () => { ui.noteOpen = false; draw(true); } }, 'Cancel') : h('button', { class: 'btn ghost', onclick: () => { ui.noteOpen = true; draw(true); } }, 'Send back with a note'),
          ui.noteOpen ? h('button', { class: 'btn', onclick: doRevise }, 'Send back') : h('button', { class: 'btn primary', onclick: doApprove }, last ? 'Approve and finish' : 'Approve and continue'))));
    }
    root.replaceChildren(...kids.filter((k) => k != null && k !== false));
    window.scrollTo(0, y);
  }

  /* --- stage views --- */
  function vUnderstand(r) {
    const u = r.understanding;
    return [
      h('div', { class: 'sec' }, h('h2', {}, 'Goal of the analysis'), h('p', { class: 'goal', dir: 'auto' }, u.goal), h('p', { class: 'muted', dir: 'auto', style: 'margin-top:10px' }, u.dataset_description)),
      u.assumptions.length ? h('div', { class: 'sec' }, h('h2', {}, 'Assumptions to confirm'), h('div', { class: 'list' }, u.assumptions.map((a) => h('div', { class: 'it', dir: 'auto' }, a)))) : null,
      h('div', { class: 'sec' }, h('h2', {}, `Problems found in the raw data (${r.detected_issues.length})`),
        r.detected_issues.length ? r.detected_issues.map((i) => h('div', { class: 'issue' }, h('span', { class: 'chip ' + (i.fixable ? 'here' : '') }, i.type.replace(/_/g, ' ')), h('span', {}, (i.column ? `${i.column}: ` : '') + i.detail)))
          : h('p', { class: 'muted' }, 'No obvious problems detected.')),
      h('div', { class: 'sec' }, h('h2', {}, `Columns (${r.n_cols}) · ${fmt(r.n_rows)} rows`),
        h('div', { class: 'tablewrap' }, h('table', {}, h('thead', {}, h('tr', {}, ['Column', 'Role', 'Meaning', 'Type found', 'Missing'].map((x) => h('th', {}, x)))),
          h('tbody', {}, r.columns.map((c) => { const m = u.columns.find((x) => x.name === c.name) || {}; return h('tr', {}, h('td', {}, h('b', {}, c.name)), h('td', {}, m.role || ''), h('td', { dir: 'auto' }, m.meaning || ''), h('td', {}, (c.kind || '').replace(/_/g, ' ')), h('td', {}, c.missing)); }))))),
      h('details', { class: 'sec' }, h('summary', {}, 'Preview of the raw file'), table(r.preview)),
    ];
  }

  function vClean(r, isCur) {
    const sm = r.summary;
    if (ui.cleanKey !== id + JSON.stringify(r.disabled)) { ui.cleanKey = id + JSON.stringify(r.disabled); ui.disabled = new Set(r.disabled); }
    const dirty = JSON.stringify([...ui.disabled].sort()) !== JSON.stringify(r.disabled);
    const rows = r.log.filter((e) => e.status !== 'skipped');
    return [
      h('div', { class: 'kv' },
        h('div', {}, h('b', {}, `${fmt(sm.rows_before)} → ${fmt(sm.rows_after)}`), h('span', {}, 'rows')),
        h('div', {}, h('b', {}, `${fmt(sm.missing_before)} → ${fmt(sm.missing_after)}`), h('span', {}, 'missing cells')),
        h('div', {}, h('b', {}, sm.applied), h('span', {}, 'changes applied')),
        h('div', {}, h('b', {}, sm.rejected), h('span', {}, 'rejected by verification'))),
      h('div', { class: 'sec' }, h('h2', {}, 'Verification'), r.checks.map((c) => h('div', { class: 'check ' + (c.passed ? 'ok' : 'no') }, h('i', {}, c.passed ? '✓' : '✗'), h('span', {}, `${c.name}: ${c.detail}`)))),
      h('div', { class: 'sec' }, h('h2', {}, `Changes the analyst made (${rows.length})`),
        isCur ? h('p', { class: 'muted', style: 'margin-bottom:6px;font-size:14px' }, 'Untick a change to undo it. No model call is needed; the cleaning is simply re-run.') : null,
        rows.map((e) => h('div', { class: 'change' + (ui.disabled.has(e.id) ? ' off' : '') + (e.status === 'rejected' ? ' rej' : '') },
          e.status === 'rejected' ? h('span', {}) : h('input', { type: 'checkbox', checked: !ui.disabled.has(e.id), disabled: !isCur, 'aria-label': 'Keep this change',
            onchange: (ev) => { ev.target.checked ? ui.disabled.delete(e.id) : ui.disabled.add(e.id); sig = ''; draw(true); } }),
          h('div', {},
            h('div', { class: 't', dir: 'auto' }, e.status === 'rejected' ? `Not applied: ${e.op.replace(/_/g, ' ')} on ${e.columns.join(', ')}` : e.description, ' ',
              e.status === 'applied' ? h('span', { class: 'chip ' + (e.risk === 'low' ? 'ok' : e.risk === 'medium' ? 'here' : 'late') }, `${e.risk} risk`) : null,
              e.status === 'applied' && e.rows_affected ? h('span', { class: 'chip', style: 'margin-inline-start:6px' }, `${fmt(e.rows_affected)} cells`) : null),
            e.status === 'rejected' ? h('div', { class: 'note', style: 'color:#9E1F14' }, e.reject_reason) : null,
            e.reason ? h('div', { class: 'why', dir: 'auto' }, 'Why: ' + e.reason) : null,
            e.examples && e.examples.length ? h('div', { class: 'ex' }, e.examples.map((x) => h('div', {}, h('code', {}, x.before), h('span', { class: 'arrow' }, '→'), h('code', {}, x.after)))) : null,
            e.note ? h('div', { class: 'note' }, e.note) : null)))),
      isCur && dirty ? h('div', { class: 'callout' }, h('b', {}, 'You changed the selection'), h('div', { class: 'row' },
        h('button', { class: 'btn small primary', onclick: () => act(async () => { await api(`/runs/${id}/clean/toggle`, 'POST', { disabled: [...ui.disabled] }); await refresh(); }, 'Cleaning re-run with your selection') }, 'Apply my selection'),
        h('button', { class: 'btn small ghost', onclick: () => { ui.disabled = new Set(r.disabled); sig = ''; draw(true); } }, 'Reset'))) : null,
      r.residual.length ? h('div', { class: 'sec' }, h('h2', {}, 'Problems that remain'), r.residual.map((i) => h('div', { class: 'issue' }, h('span', { class: 'chip late' }, i.type.replace(/_/g, ' ')), h('span', {}, (i.column ? `${i.column}: ` : '') + i.detail)))) : null,
      h('details', { class: 'sec' }, h('summary', {}, 'Preview of the cleaned data'), table(r.preview)),
    ];
  }

  function vAnalyze(r) {
    return [h('div', { class: 'cards' }, r.analyses.map((a) => h('div', { class: 'an' },
      h('h3', { dir: 'auto' }, `${a.index + 1}. ${a.title}`), h('div', { class: 'qn', dir: 'auto' }, a.question),
      a.status === 'ok' ? [
        h('p', { class: 'fd', dir: 'auto' }, a.finding, a.ungrounded.length ? h('span', { class: 'chip late', style: 'margin-inline-start:8px' }, `unverified: ${a.ungrounded.join(', ')}`) : null),
        table(a.table, { limit: 40 }), h('details', {}, h('summary', {}, 'Show the code the analyst wrote'), h('pre', {}, a.code))]
        : h('div', { class: 'callout bad', style: 'margin-top:8px' }, h('b', {}, 'Failed'), a.error, h('details', {}, h('summary', {}, 'Code'), h('pre', {}, a.code))))))];
  }

  function vVisualize(r) {
    return [h('div', { class: 'charts' }, r.charts.map((c) => h('figure', {}, h('img', { src: `/api/runs/${id}/chart/${c.file}?v=${s.meter.calls}`, alt: c.title }),
      h('figcaption', { dir: 'auto' }, c.takeaway || c.title)))),
      r.dropped.length ? h('p', { class: 'muted', style: 'margin-top:18px;font-size:14px' }, 'Skipped: ' + r.dropped.map((d) => `${d.title} (${d.reason})`).join('; ')) : null];
  }

  function vDecide(r) {
    const d = r.decision;
    const conf = { low: 'late', medium: 'here', high: 'ok' };
    return [
      h('div', { class: 'sec' }, h('h2', { class: 'headline', dir: 'auto' }, d.headline), h('p', { class: 'summary', dir: 'auto' }, d.summary),
        r.ungrounded.length ? h('div', { class: 'callout', style: 'margin-top:16px' }, h('b', {}, 'Unverified numbers'), `These numbers could not be traced to the computed results: ${r.ungrounded.join(', ')}`) : null,
        r.repaired && !r.ungrounded.length ? h('p', { class: 'muted', style: 'margin-top:10px;font-size:14px' }, 'The analyst first cited numbers that were not in the results, was told, and rewrote the answer.') : null),
      h('div', { class: 'sec' }, h('h2', {}, 'What the data shows'), h('div', { class: 'list' }, d.findings.map((f) => h('div', { class: 'it' }, h('span', { dir: 'auto' }, f.claim), f.evidence.length ? h('div', { class: 'sub' }, 'Based on analysis ' + f.evidence.map((i) => i + 1).join(', ')) : null)))),
      h('div', { class: 'sec' }, h('h2', {}, 'Recommended actions'), h('div', { class: 'list' }, d.recommendations.map((x) => h('div', { class: 'it' },
        h('b', { dir: 'auto' }, x.action), ' ', h('span', { class: 'chip ' + conf[x.confidence] }, `${x.confidence} confidence`), h('div', { class: 'sub', dir: 'auto' }, x.why))))),
      d.caveats.length ? h('div', { class: 'sec' }, h('h2', {}, 'Caveats'), h('div', { class: 'list' }, d.caveats.map((c) => h('div', { class: 'it', dir: 'auto' }, c)))) : null,
      d.needs_human.length ? h('div', { class: 'sec' }, h('h2', {}, 'Decisions only a person can make'), h('div', { class: 'list' }, d.needs_human.map((c) => h('div', { class: 'it', dir: 'auto' }, c)))) : null,
    ];
  }

  draw(true);
  pollTimer = setInterval(async () => {
    try { s = await api('/runs/' + id); draw(false); } catch { /* server busy */ }
  }, 1500);
}

route();
