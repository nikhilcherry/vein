"""Render a recording as one self-contained HTML page.

No CDN, no build step, no dependencies: the data is embedded as JSON and the
page is a few hundred lines of vanilla CSS and JS. Open it straight from disk
or attach it to a pull request.
"""

from __future__ import annotations

import html
import json

from . import static_scan
from .store import Run

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa; --panel: #ffffff; --line: #e6e4df; --ink: #1c1b19;
  --muted: #6b6862; --accent: #b4541e; --accent-soft: #f0d9cb;
  --dead: #9a9792; --shadow: 0 1px 2px rgba(0,0,0,.05);
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16150f; --panel: #1e1d16; --line: #322f26; --ink: #ece8dd;
    --muted: #96907f; --accent: #e08a4e; --accent-soft: #3a2a1c;
    --dead: #6a6558; --shadow: none;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
a { color: var(--accent); }
.wrap { max-width: 1180px; margin: 0 auto; padding: 32px 20px 80px; }
header h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -.02em; }
header h1 span { color: var(--accent); }
.cmd {
  font-family: var(--mono); font-size: 13px; color: var(--muted);
  word-break: break-all; margin: 2px 0;
}
.stats { display: flex; flex-wrap: wrap; gap: 10px; margin: 22px 0 26px; }
.stat {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; min-width: 108px; box-shadow: var(--shadow);
}
.stat b { display: block; font-size: 21px; letter-spacing: -.02em; }
.stat span { font-size: 11px; text-transform: uppercase; letter-spacing: .07em;
  color: var(--muted); }
.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  margin-bottom: 14px; }
input[type=search], select {
  font: inherit; padding: 7px 11px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--panel); color: var(--ink);
}
input[type=search] { flex: 1 1 240px; }
button.toggle {
  font: inherit; padding: 7px 12px; border: 1px solid var(--line);
  border-radius: 8px; background: var(--panel); color: var(--muted);
  cursor: pointer;
}
button.toggle[aria-pressed=true] {
  background: var(--accent-soft); color: var(--ink); border-color: var(--accent);
}
.layout { display: grid; grid-template-columns: 1fr; gap: 18px; }
@media (min-width: 900px) { .layout { grid-template-columns: 1fr 330px; } }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  box-shadow: var(--shadow); overflow: hidden;
}
.file { border-bottom: 1px solid var(--line); }
.file:last-child { border-bottom: 0; }
.file > summary {
  cursor: pointer; padding: 11px 14px; display: flex; gap: 10px;
  align-items: baseline; font-family: var(--mono); font-size: 13px;
}
.file > summary::-webkit-details-marker { display: none; }
.file > summary::before { content: "\\25b8"; color: var(--muted); }
.file[open] > summary::before { content: "\\25be"; }
.file .path { flex: 1; word-break: break-all; }
.file .meta { color: var(--muted); font-size: 12px; white-space: nowrap; }
table { width: 100%; border-collapse: collapse; }
tbody tr { border-top: 1px solid var(--line); cursor: pointer; }
tbody tr:hover, tbody tr.sel { background: var(--accent-soft); }
td { padding: 6px 14px; font-size: 13px; vertical-align: middle; }
td.name { font-family: var(--mono); word-break: break-all; }
td.num { text-align: right; color: var(--muted);
  font-variant-numeric: tabular-nums; white-space: nowrap; width: 1%; }
tr.dead td.name { color: var(--dead); }
.tag {
  font-size: 10px; text-transform: uppercase; letter-spacing: .06em;
  border: 1px solid var(--line); border-radius: 999px; padding: 1px 6px;
  color: var(--muted); margin-left: 6px; vertical-align: 1px;
  font-style: normal;
}
.meter { display: block; height: 4px; background: var(--line);
  border-radius: 2px; overflow: hidden; min-width: 60px; }
.meter i { display: block; height: 100%; background: var(--accent); }
aside { position: sticky; top: 18px; align-self: start; }
aside .card { padding: 16px; }
aside h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); margin: 0 0 10px; }
aside .who { font-family: var(--mono); font-size: 13px; word-break: break-all; }
aside .sub { color: var(--muted); font-size: 12px; margin: 4px 0 14px;
  font-family: var(--mono); }
aside ul { list-style: none; margin: 0 0 14px; padding: 0; }
aside li { font-family: var(--mono); font-size: 12px; padding: 3px 0;
  display: flex; gap: 8px; }
aside li span { color: var(--muted); margin-left: auto; }
aside li a { text-decoration: none; }
aside li a:hover { text-decoration: underline; }
.empty { color: var(--muted); font-size: 13px; padding: 18px 14px; }
footer { margin-top: 34px; color: var(--muted); font-size: 12px; }
"""

JS = """
const F = DATA.functions, E = DATA.edges;
const callers = {}, callees = {};
for (const [a, b, n] of E) {
  (callees[a] = callees[a] || []).push([b, n]);
  (callers[b] = callers[b] || []).push([a, n]);
}
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function dur(ns) {
  if (!ns) return "0";
  if (ns < 1e3) return ns.toFixed(0) + "ns";
  if (ns < 1e6) return (ns / 1e3).toFixed(1) + "\\u00b5s";
  if (ns < 1e9) return (ns / 1e6).toFixed(1) + "ms";
  return (ns / 1e9).toFixed(2) + "s";
}
function num(n) {
  if (n < 1e3) return String(n);
  if (n < 1e6) return (n / 1e3).toFixed(1) + "k";
  return (n / 1e6).toFixed(1) + "M";
}

const state = { q: "", sort: "self", deadOnly: false, sel: null };
const peak = Math.max(1, ...F.map(f => f.self_ns));

function metric(f) {
  return state.sort === "calls" ? f.calls
       : state.sort === "cum" ? f.cum_ns
       : state.sort === "name" ? 0 : f.self_ns;
}

function visible() {
  const q = state.q.toLowerCase();
  return F.filter(f =>
    (!state.deadOnly || f.dead) &&
    (!q || (f.file + ":" + f.qualname).toLowerCase().indexOf(q) >= 0));
}

function render() {
  const rows = visible();
  const byFile = new Map();
  for (const f of rows) {
    if (!byFile.has(f.file)) byFile.set(f.file, []);
    byFile.get(f.file).push(f);
  }
  const files = [...byFile.entries()].sort((a, b) =>
    state.sort === "name"
      ? a[0].localeCompare(b[0])
      : b[1].reduce((s, f) => s + metric(f), 0) -
        a[1].reduce((s, f) => s + metric(f), 0));

  const out = [];
  for (const [file, funcs] of files) {
    funcs.sort((a, b) =>
      state.sort === "name" ? a.line - b.line : metric(b) - metric(a));
    const live = funcs.filter(f => !f.dead).length;
    const self = funcs.reduce((s, f) => s + f.self_ns, 0);
    out.push('<details class="file" open><summary>' +
      '<span class="path">' + esc(file) + '</span>' +
      '<span class="meta">' + live + '/' + funcs.length + ' ran &middot; ' +
      dur(self) + '</span></summary><table><tbody>' +
      funcs.map(f =>
        '<tr data-id="' + f.id + '" class="' + (f.dead ? "dead" : "") +
        (state.sel === f.id ? " sel" : "") + '">' +
        '<td class="name">' + esc(f.qualname) +
        (f.dead ? '<em class="tag">never ran</em>' : "") + '</td>' +
        '<td class="num">' + (f.dead ? "&mdash;" : num(f.calls)) + '</td>' +
        '<td class="num">' + (f.dead ? "&mdash;" : dur(f.self_ns)) + '</td>' +
        '<td class="num" style="width:80px"><span class="meter"><i style="width:' +
        (f.self_ns / peak * 100).toFixed(1) + '%"></i></span></td></tr>').join("") +
      '</tbody></table></details>');
  }
  $("#files").innerHTML = out.join("") ||
    '<p class="empty">Nothing matches that filter.</p>';
  $("#shown").textContent = rows.length;
  for (const tr of document.querySelectorAll("tbody tr")) {
    tr.onclick = () => select(Number(tr.dataset.id));
  }
}

function link(id, n) {
  const f = F[id];
  if (!f) return '<li>&laquo;entry&raquo;<span>' + num(n) + '</span></li>';
  return '<li><a href="#" data-id="' + id + '">' + esc(f.qualname) +
    '</a><span>' + num(n) + '</span></li>';
}

function select(id) {
  state.sel = id;
  const f = F[id];
  const ins = (callers[id] || []).slice().sort((a, b) => b[1] - a[1]);
  const outs = (callees[id] || []).slice().sort((a, b) => b[1] - a[1]);
  $("#panel").innerHTML =
    '<h2>Function</h2><div class="who">' + esc(f.qualname) + '</div>' +
    '<div class="sub">' + esc(f.file) + ':' + f.line + '</div>' +
    (f.dead
      ? '<p class="empty" style="padding:0 0 12px">Never executed in this run.</p>'
      : '<h2>Cost</h2><ul><li>calls<span>' + num(f.calls) +
        '</span></li><li>self<span>' + dur(f.self_ns) +
        '</span></li><li>cumulative<span>' + dur(f.cum_ns) + '</span></li></ul>') +
    '<h2>Called by (' + ins.length + ')</h2><ul>' +
    (ins.map(e => link(e[0], e[1])).join("") || '<li class="empty">nothing</li>') +
    '</ul><h2>Calls (' + outs.length + ')</h2><ul>' +
    (outs.map(e => link(e[0], e[1])).join("") || '<li class="empty">nothing</li>') +
    '</ul>';
  for (const a of $("#panel").querySelectorAll("a[data-id]")) {
    a.onclick = (e) => { e.preventDefault(); select(Number(a.dataset.id)); };
  }
  render();
}

$("#q").oninput = (e) => { state.q = e.target.value; render(); };
$("#sort").onchange = (e) => { state.sort = e.target.value; render(); };
$("#dead").onclick = (e) => {
  state.deadOnly = !state.deadOnly;
  e.target.setAttribute("aria-pressed", String(state.deadOnly));
  render();
};
render();
"""


def _num(n: int) -> str:
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1_000:.1f}k"
    return f"{n / 1_000_000:.1f}M"


def build_payload(run: Run, definitions, include_dead: bool):
    """Executed functions, plus never-executed definitions, plus edges."""
    functions = []
    executed = set()
    for i, func in enumerate(run.functions):
        executed.add(func.key)
        functions.append(
            {
                "id": i,
                "file": func.file,
                "line": func.line,
                "qualname": func.qualname,
                "calls": func.calls,
                "self_ns": func.self_ns,
                "cum_ns": func.cum_ns,
                "dead": False,
            }
        )
    if include_dead:
        for definition in definitions:
            if definition.key in executed:
                continue
            if static_scan.is_probably_registered(definition):
                continue
            functions.append(
                {
                    "id": len(functions),
                    "file": definition.file,
                    "line": definition.line,
                    "qualname": definition.qualname,
                    "calls": 0,
                    "self_ns": 0,
                    "cum_ns": 0,
                    "dead": True,
                }
            )
    edges = [[a, b, c] for (a, b), c in run.edges.items()]
    return functions, edges


def render_report(run: Run, definitions=None, include_dead: bool = True) -> str:
    definitions = definitions or []
    functions, edges = build_payload(run, definitions, include_dead)
    dead = sum(1 for f in functions if f["dead"])
    files = len({f["file"] for f in functions})
    data = json.dumps({"functions": functions, "edges": edges}, separators=(",", ":"))

    stats = [(str(len(run.functions)), "functions ran")]
    if include_dead:
        stats.append((str(dead), "never ran"))
    stats += [
        (str(files), "files"),
        (_num(run.total_calls()), "calls"),
        (f"{run.wall_s:.2f}s", "wall clock"),
        (str(run.processes), "processes"),
    ]
    stat_html = "".join(
        f"<div class='stat'><b>{html.escape(value)}</b>"
        f"<span>{html.escape(label)}</span></div>"
        for value, label in stats
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vein · {html.escape(run.name)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1><span>vein</span> · {html.escape(run.name)}</h1>
  <p class="cmd">{html.escape(" ".join(run.argv))}</p>
  <p class="cmd">{html.escape(run.started)} &middot; exit {run.exit_code}
     &middot; {html.escape(run.backend or "recorder")}</p>
</header>

<div class="stats">{stat_html}</div>

<div class="controls">
  <input id="q" type="search" placeholder="filter by file or function…">
  <select id="sort">
    <option value="self">sort: self time</option>
    <option value="cum">sort: cumulative</option>
    <option value="calls">sort: calls</option>
    <option value="name">sort: file order</option>
  </select>
  <button id="dead" class="toggle" aria-pressed="false">never ran only</button>
  <span class="cmd"><b id="shown">0</b> shown</span>
</div>

<div class="layout">
  <main id="files" class="card"></main>
  <aside><div class="card" id="panel">
    <h2>Function</h2>
    <p class="empty" style="padding:0">Pick a function to see who calls it and
    what it calls.</p>
  </div></aside>
</div>

<footer>Recorded with <a href="https://github.com/nikhilcherry/vein">vein</a>.
Self time excludes callees; cumulative includes them. &ldquo;Never ran&rdquo;
means this recording never entered the function &mdash; not that it is
unreachable.</footer>
</div>
<script>const DATA = {data};</script>
<script>{JS}</script>
</body>
</html>
"""
