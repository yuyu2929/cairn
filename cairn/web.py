"""cairn.web -- the local web UI (stdlib http.server, zero dependencies).

This is the face families and game servers actually touch: a single-page
app served from the database itself, with a search box, a timeline view,
an add-claim form, a scope switcher, and merge/export buttons.  No build
step, no node_modules, no framework -- `cairn serve` and you are done.

The page is deliberately plain: it reads the claim stream through the
same GraphView the CLI uses, so what the UI shows is exactly what the
theory says the database contains.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .claim import RESERVED_PREDICATES
from .graph import CairnGraph, SCOPE_LEVELS

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cairn &mdash; %(title)s</title>
<style>
:root { --bg:#f7f5f0; --card:#fff; --ink:#2b2b2b; --muted:#8a8577;
        --accent:#b3552b; --line:#e5e0d5; }
* { box-sizing:border-box; margin:0; padding:0 }
body { font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       background:var(--bg); color:var(--ink); line-height:1.55 }
.wrap { max-width:860px; margin:0 auto; padding:24px 16px 64px }
header { display:flex; align-items:baseline; gap:12px; margin-bottom:4px }
h1 { font-size:26px; letter-spacing:.5px }
h1 .cairn { color:var(--accent) }
.sub { color:var(--muted); font-size:13px; margin-bottom:20px }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:16px 18px; margin-bottom:14px }
.row { display:flex; gap:10px; flex-wrap:wrap; align-items:center }
input,select,button,textarea { font:inherit; padding:7px 10px; border:1px solid var(--line);
        border-radius:7px; background:#fff; color:var(--ink) }
input[type=text]{ flex:1; min-width:180px }
button { background:var(--accent); color:#fff; border-color:var(--accent); cursor:pointer }
button.ghost { background:#fff; color:var(--ink) }
button:hover { opacity:.9 }
table { width:100%%; border-collapse:collapse; font-size:14px }
td,th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top }
th { color:var(--muted); font-weight:600; font-size:12px; text-transform:uppercase }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:var(--muted) }
.pill { display:inline-block; font-size:11px; padding:1px 8px; border-radius:99px;
        background:#f0ece2; color:#6b6558; margin-left:6px }
.pill.disputed { background:#fde8e4; color:#a33 }
.pill.verified { background:#e6f4ea; color:#256b3a }
.pill.retracted { background:#eee; color:#999; text-decoration:line-through }
h2 { font-size:15px; margin-bottom:10px }
a { color:var(--accent) }
.footer { color:var(--muted); font-size:12px; text-align:center; margin-top:24px }
</style>
</head>
<body>
<div class="wrap">
<header><h1><span class="cairn">&#9951;</span> Cairn</h1></header>
<div class="sub">%(subtitle)s</div>

<div class="card">
  <form method="get" action="/">
    <input type="hidden" name="scope" value="%(scope)s">
    <div class="row">
      <input type="text" name="q" placeholder="搜索任何内容&hellip;（回车）" value="%(q)s">
      <select name="scope" onchange="this.form.submit()">
        %(scope_options)s
      </select>
    </div>
  </form>
</div>

%(body)s

<div class="footer">cairn %(version)s &middot; local-first &middot; 每条记录都是一块石头</div>
</div>
</body>
</html>
"""


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _pill(row) -> str:
    out = []
    if row["retracted"]:
        out.append('<span class="pill retracted">已撤回</span>')
    if row["disputed_by"]:
        out.append('<span class="pill disputed">争议:%s</span>'
                   % _esc(",".join(sorted(set(row["disputed_by"])))))
    if row["verified_by"]:
        out.append('<span class="pill verified">已确认:%s</span>'
                   % _esc(",".join(sorted(set(row["verified_by"])))))
    return " ".join(out)


def _search_body(graph: CairnGraph, view, q: str) -> str:
    if not q:
        subjects = sorted(view.subjects())
        rows = []
        for s in subjects[:200]:
            links = view.links(s)
            desc = ", ".join(f"{p}={v!r}" for p, v, _ in links[:4])
            rows.append(f'<tr><td><a href="/node?subject={_esc(s)}&scope={_esc(view.scope or "")}">'
                        f'{_esc(s)}</a></td><td>{_esc(desc)}</td>'
                        f'<td class="mono">{len(links)}</td></tr>')
        body = ('<div class="card"><h2>所有节点（%d）</h2><table>'
                '<tr><th>主体</th><th>当前属性</th><th>边数</th></tr>%s</table></div>'
                % (len(subjects), "".join(rows)))
        return body
    hits = view.search(q)
    if not hits:
        return '<div class="card">没有匹配 "%s" 的记录</div>' % _esc(q)
    rows = []
    for c in hits:
        rows.append(
            f'<tr><td><a href="/node?subject={_esc(c.subject)}&scope={_esc(view.scope or "")}">'
            f'{_esc(c.subject)}</a></td>'
            f'<td>{_esc(c.predicate)}</td><td>{_esc(c.value)}</td>'
            f'<td class="mono">{_esc(c.valid_from)}</td>'
            f'<td class="mono">{_esc(c.cid[:10])}</td></tr>')
    return ('<div class="card"><h2>搜索结果（%d）</h2><table>'
            '<tr><th>主体</th><th>谓词</th><th>值</th><th>生效于</th><th>cid</th></tr>'
            '%s</table></div>' % (len(hits), "".join(rows)))


def _node_body(graph: CairnGraph, view, subject: str) -> str:
    if subject not in view.subjects() and subject not in graph.by_subject:
        return f'<div class="card">没有关于 "{_esc(subject)}" 的记录</div>'
    rows = []
    for row in view.timeline(subject):
        c = row["claim"]
        rows.append(
            f'<tr><td class="mono">{_esc(c.valid_from)}</td>'
            f'<td><b>{_esc(c.predicate)}</b></td><td>{_esc(c.value)}</td>'
            f'<td>{_esc(c.recorder)}</td><td>{_pill(row)}</td>'
            f'<td class="mono">{_esc(c.cid[:10])}</td></tr>')
    body = ('<div class="card"><h2>%s 的全部历史（含争议与撤回）</h2><table>'
            '<tr><th>生效于</th><th>谓词</th><th>值</th><th>记录者</th><th>状态</th><th>cid</th></tr>'
            '%s</table></div>' % (_esc(subject), "".join(rows)))
    form = f"""<div class="card"><h2>为 {_esc(subject)} 添加记录</h2>
<form method="post" action="/add">
<input type="hidden" name="subject" value="{_esc(subject)}">
<input type="hidden" name="scope" value="{_esc(view.scope or 'private')}">
<div class="row">
  <input type="text" name="predicate" placeholder="谓词，如 role / 住址 / 事件" required>
  <input type="text" name="value" placeholder="值" required>
  <input type="text" name="valid_from" placeholder="生效时间（默认现在）">
  <input type="text" name="recorder" placeholder="记录者" required>
  <button>记录</button>
</div></form></div>"""
    return body + form


def _add_form_body(view) -> str:
    return f"""<div class="card"><h2>记录一条新事实</h2>
<form method="post" action="/add">
<input type="hidden" name="scope" value="{_esc(view.scope or 'private')}">
<div class="row">
  <input type="text" name="subject" placeholder="主体（人/服务器/事件）" required>
  <input type="text" name="predicate" placeholder="谓词" required>
  <input type="text" name="value" placeholder="值" required>
  <input type="text" name="valid_from" placeholder="生效时间（默认现在）">
  <input type="text" name="recorder" placeholder="记录者" required>
  <button>记录</button>
</div></form></div>"""


class Handler(BaseHTTPRequestHandler):
    graph: CairnGraph = None
    default_scope = None

    def log_message(self, fmt, *args):  # quieter logs
        pass

    def _view(self, scope):
        return self.graph.view(scope or self.default_scope)

    def _render(self, body, scope, q="", subtitle="本地共享记忆"):
        options = "".join(
            f'<option value="{s}" {"selected" if s == (scope or self.default_scope) else ""}>{s}</option>'
            for s in SCOPE_LEVELS)
        page = PAGE % {
            "title": "cairn", "subtitle": subtitle, "scope": scope or self.default_scope or "",
            "scope_options": options, "q": _esc(q), "body": body,
            "version": "1.0.0"}
        data = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            scope = qs.get("scope", [None])[0] or self.default_scope
            view = self._view(scope)
            if url.path == "/":
                q = qs.get("q", [""])[0]
                self._render(_search_body(self.graph, view, q), scope, q)
            elif url.path == "/node":
                subj = qs.get("subject", [""])[0]
                self._render(_node_body(self.graph, view, subj), scope, "",
                             subtitle=f"节点：{subj}")
            elif url.path == "/new":
                self._render(_add_form_body(view), scope, "", subtitle="新记录")
            elif url.path == "/api/stats":
                data = json.dumps(self.graph.view(scope).stats(),
                                  ensure_ascii=False, indent=1).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._render('<div class="card">未知路径</div>', scope)
        except Exception as e:  # noqa: BLE001 - the UI must never crash the db
            body = f'<div class="card">出错了：{_esc(e)}</div>'
            self._render(body, self.default_scope)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(n).decode("utf-8"))
            url = urlparse(self.path)
            if url.path == "/add":
                subject = form.get("subject", [""])[0].strip()
                predicate = form.get("predicate", [""])[0].strip()
                value = form.get("value", [""])[0]
                recorder = form.get("recorder", [""])[0].strip() or "web"
                vf = form.get("valid_from", [""])[0].strip() or "now"
                scope = form.get("scope", [self.default_scope or "private"])[0]
                if subject and predicate and predicate not in RESERVED_PREDICATES:
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        pass
                    self.graph.record(subject, predicate, value, vf,
                                      recorder=recorder, source="web-ui",
                                      scope=scope)
                self.send_response(303)
                self.send_header("Location",
                                 f"/node?subject={subject}&scope={scope}")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()
        except Exception as e:  # noqa: BLE001
            data = str(e).encode()
            self.send_response(500)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def serve(db_path: str, port: int = 8777, scope: str = None) -> None:
    graph = CairnGraph(db_path)
    Handler.graph = graph
    Handler.default_scope = scope
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"cairn web UI: http://127.0.0.1:{port}  (database: {db_path})")
    print("Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
