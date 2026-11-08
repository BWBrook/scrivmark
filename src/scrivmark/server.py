"""The local viewer: a small Starlette app serving the rendered review and an
API that performs the same operations as the CLI."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import webbrowser
from importlib.resources import files
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from watchfiles import awatch

from . import config, figures, gitutil, latex, record, render, review, rounds
from .blocks import guarded_segments, line_of
from .errors import Conflict, ScrivError, StaleRevision
from .selection import PLACEHOLDER, _plain, map_selection
from .state import atomic_write, local_dir, read_json, revision

STATIC = files("scrivmark") / "static"
COOKIE = "scrivmark"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


def _highlight_spec(block: str, s: int, e: int, tex: bool | dict[str, str]) -> dict | None:
    """Where the page should mark source range [s, e) of `block`: the plain
    text the reader sees, which occurrence of it within the block, and how many
    occurrences there are, all counted in the same plain form the page matches
    against. None when the range has no plain text, so the page marks the block."""
    plain, spans = _plain(block, tex)
    idx = [i for i, (a, z) in enumerate(spans) if a >= s and z <= e]
    if not idx:
        return None
    lo, hi = idx[0], idx[-1] + 1
    i = lo
    while i < hi and plain[i] == PLACEHOLDER:
        i += 1
    j = i
    while j < hi and plain[j] != PLACEHOLDER:
        j += 1
    needle = plain[i:j]
    if not needle.strip():
        return None
    starts = [m for m in range(len(plain)) if plain.startswith(needle, m)]
    return {"text": needle, "nth": starts.index(i), "count": len(starts)}


class Viewer:
    def __init__(self, root: Path, port: int = 0):
        self.root = root
        self.port = port
        self.cookie = f"{COOKIE}-{port}"
        saved = read_json(viewer_file(root), {})
        self.token = saved.get("token") or secrets.token_urlsafe(18)
        self.cache: tuple[str, dict] | None = None
        self.clean_cache: tuple[str, dict] | None = None
        self.lock = threading.Lock()
        self.listeners: set[asyncio.Queue] = set()

    # ------------------------------------------------------------ state

    def current(self) -> review.Review | None:
        try:
            return review.active(self.root)
        except ScrivError:
            return None

    def mode(self, rv: review.Review | None) -> str:
        if rv is None:
            return "read"
        if rv.status == "closed":
            return "closed"
        if review.other_side(rv.rec, review.me(self.root)):
            return "waiting"
        return {"open": "reviewer", "returned": "author", "responded": "signoff"}[rv.status]

    def state(self) -> dict:
        """Rendering runs in a worker thread; one at a time, so a burst of
        requests after a change renders once and the rest share the result."""
        with self.lock:
            return self._state()

    def clean(self) -> dict:
        """The manuscript as it now reads, every edit in and nothing marked:
        what the reading tab shows. It depends only on the sources."""
        with self.lock:
            ms = config.load(self.root)
            paths = [config.CONFIG, *ms.files, *ms.bibliography, *([ms.csl] if ms.csl else [])]
            key = f"clean:{gitutil.current_branch(self.root)}:{revision(self.root, paths)}"
            if self.clean_cache and self.clean_cache[0] == key:
                return self.clean_cache[1]
            try:
                html, _ = render.render(self.root, ms)
            except RuntimeError as e:
                html = f"<pre class='scrv-error'>{_esc(str(e))}</pre>"
            rv = self.current()
            data = {"key": key, "html": html, "title": ms.title, "round": rv.id if rv else None}
            self.clean_cache = (key, data)
            return data

    def _state(self) -> dict:
        """The page and the revision it carries back with each edit. The
        revision is taken first and everything shown is read after it, so a
        page is never older than its revision: a change made meanwhile by the
        CLI makes the page's next edit stale rather than overwriting it."""
        ms = config.load(self.root)
        rv = self.current()
        rev = rv.rev() if rv else None
        rv = self.current()
        branch = gitutil.current_branch(self.root)
        key_paths = [config.CONFIG, *ms.files, *ms.bibliography, *([ms.csl] if ms.csl else []),
                     *([rv.record_rel, rv.drafts_rel, str(review._asks_path(rv).relative_to(self.root))] if rv else [])]
        key = f"{branch}:{review.me(self.root)}:{rev}:{revision(self.root, key_paths)}"
        if self.cache and self.cache[0] == key:
            return self.cache[1]
        mode = self.mode(rv)
        base, pending = None, None
        if rv and rv.status in ("open", "returned", "responded"):
            base = rv.rec["round"]["base"]
        if rv and rv.status in ("returned", "responded"):
            pending = {}
            for c in rv.rec["changes"]:
                if c["type"] != "file" and review.change_status(rv, c) == "pending":
                    loc = review.locate(rv, c)
                    if loc:
                        pending.setdefault(c["file"], []).append((c["id"], loc[0], loc[1], c["type"], c["base"]))
        try:
            html, warnings = render.render(self.root, ms, base, pending)
        except RuntimeError as e:
            html, warnings = f"<pre class='scrv-error'>{_esc(str(e))}</pre>", ""
        data = {
            "key": key, "mode": mode, "branch": branch, "html": html,
            "warnings": [w for w in warnings.splitlines() if w.strip()][:20],
            "round": rv.rec["round"] if rv else None,
            "rev": rev,
            "me": review.who(rv) if rv else None,
            "threads": [], "drafts": [], "asks": [], "changes": [],
            "news": None,
        }
        if rv:
            me = review.who(rv)
            waiting = mode == "waiting"
            window = review.news_window(rv.rec)
            fresh = lambda at: review._within(at, window)  # noqa: E731
            data["news"] = {"since": window[0], "until": window[1]}
            data["threads"] = [dict(self._place(rv, t),
                                    editable=[] if waiting else [m["id"] for m in t["messages"] if review.can_edit(rv, m, me)],
                                    new=any(fresh(m["at"]) for m in t["messages"])
                                    or fresh((t.get("resolved_by") or {}).get("at")))
                               for t in review.threads(rv)]
            if rv.status == "open":
                data["drafts"] = [self._place(rv, d) for d in review.drafts(rv)]
            if rv.status in ("open", "returned", "responded"):
                data["asks"] = [self._place(rv, a) for a in review.asks(rv)]
            if rv.status in ("returned", "responded", "closed"):
                data["changes"] = [dict(c, new=fresh(c.get("at")) or any(fresh(k["at"]) for k in c.get("contested", [])))
                                   for c in review.changes(rv)]
        self.cache = (key, data)
        return data

    def _place(self, rv: review.Review, item: dict) -> dict:
        out = dict(item)
        where = item["where"]
        text = rv.text(item["anchor"]["file"]) or ""
        if where["state"] == "ok":
            s, e = where["range"]
            out["line"] = [line_of(text, s), line_of(text, max(s, e - 1))]
            out["current"] = text[s:e]
            tex = latex.is_tex(item["anchor"]["file"])
            seg = next((g for g in guarded_segments(text, tex) if g.a <= s < g.z), None)
            macros = latex.macro_text(text) if tex else False
            out["hl"] = _highlight_spec(text[seg.b0 : seg.b1], s - seg.b0, e - seg.b0, macros) if seg and e > s else None
        else:
            out["line"] = None
        return out

    # ------------------------------------------------------------ helpers

    def need(self) -> review.Review:
        rv = self.current()
        if rv is None:
            raise ScrivError("No review round is checked out. Run `scrivmark start` or `scrivmark open <round>`.")
        return rv

    def check_render(self, body: dict) -> None:
        if body.get("key") and body["key"] != self.state()["key"]:
            raise StaleRevision("The page was out of date when you selected that; it has refreshed, please select again.")

    def anchor_from(self, rv: review.Review, body: dict) -> dict:
        rel = body["file"]
        if rel not in rv.ms.sources:
            raise ScrivError(f"{rel} is not a manuscript file")
        first, last = (int(x) for x in body["lines"].split("-"))
        text = rv.text(rel)
        if text is None:
            raise ScrivError(f"{rel} does not exist")
        lines = text.splitlines(keepends=True)
        s0 = sum(len(x) for x in lines[: first - 1])
        e0 = sum(len(x) for x in lines[:last])
        block = text[s0:e0]
        tex = latex.macro_text(text) if latex.is_tex(rel) else False
        hit = (map_selection(block, body.get("text", ""), body.get("before", ""), tex)
               if body.get("text") else None)
        if hit:
            return review.anchor_at(rv, rel, s0 + hit[0], s0 + hit[1])
        return review.anchor_at(rv, rel, s0, e0 - (1 if block.endswith("\n") else 0))

    async def notify(self) -> None:
        for q in list(self.listeners):
            q.put_nowait("changed")

    async def watch(self) -> None:
        def relevant(_, path: str) -> bool:
            p = Path(path)
            if ".git" in p.parts:
                return p.name in ("HEAD",) or "refs" in p.parts
            if ".scrivmark" in p.parts:
                return p.name in ("drafts.json", "asks.json", "as.json")
            return not p.name.startswith(".")

        async for _ in awatch(self.root, watch_filter=relevant, debounce=250, recursive=True):
            await self.notify()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def viewer_file(root: Path) -> Path:
    return local_dir(root) / "viewer.json"


def build_app(viewer: Viewer) -> Starlette:
    def local_host(request: Request) -> bool:
        """Refuse requests addressed to any other host name, so a web page that
        rebinds its own domain to 127.0.0.1 cannot reach the viewer."""
        host = request.headers.get("host", "")
        return host.rsplit(":", 1)[0] in ("127.0.0.1", "localhost", "[::1]")

    def authorised(request: Request) -> bool:
        if not local_host(request):
            return False
        return request.cookies.get(viewer.cookie) == viewer.token or request.headers.get("x-scrivmark") == viewer.token

    def local_navigation(request: Request) -> bool:
        """A page load typed, bookmarked or opened by an app on this machine,
        rather than one triggered by another website. Browsers mark the latter
        with Sec-Fetch-Site; the frame-ancestors policy stops it being framed."""
        return local_host(request) and request.headers.get("sec-fetch-site", "none") in ("none", "same-origin")

    def guard(handler, mutate: bool = False):
        async def inner(request: Request):
            if not authorised(request):
                return JSONResponse({"error": "Not authorised. Open the address scrivmark printed."}, status_code=403)
            if mutate and request.url.path not in ("/api/selection", "/api/source"):
                try:
                    sent = json.loads(await request.body() or b"{}")
                except ValueError:
                    sent = {}
                if not sent.get("rev"):
                    return JSONResponse({"error": "This page is out of date; it has refreshed, please try again.",
                                         "stale": True}, status_code=409)
            if mutate and request.url.path != "/api/selection":
                rv = viewer.current()
                if rv and viewer.mode(rv) == "waiting":
                    return JSONResponse({"error": "It is the other side's turn in this round, so this viewer is "
                                         "read-only until the round comes back to you."}, status_code=403)
            if mutate:
                origin = request.headers.get("origin")
                if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                    return JSONResponse({"error": "Cross-origin request refused."}, status_code=403)
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(request)
                else:
                    b = await body_of(request) if request.method == "POST" else {}
                    result = await asyncio.to_thread(handler, request, b)
            except StaleRevision as e:
                return JSONResponse({"error": str(e), "stale": True}, status_code=409)
            except Conflict as e:
                return JSONResponse({"error": str(e), "conflict": True}, status_code=409)
            except ScrivError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            if mutate:
                viewer.cache = None
                await viewer.notify()
            return result
        return inner

    async def index(request: Request):
        return page(request, "index.html")

    async def reading(request: Request):
        return page(request, "read.html")

    def page(request: Request, name: str):
        if not local_host(request):
            return HTMLResponse("<p>scrivmark answers only on 127.0.0.1 or localhost.</p>", status_code=403)
        if request.query_params.get("t") != viewer.token and not authorised(request) and not local_navigation(request):
            return HTMLResponse("<p>Open the address that <code>scrivmark url</code> prints.</p>", status_code=403)
        resp = HTMLResponse((STATIC / name).read_text(), headers={"Content-Security-Policy": CSP})
        resp.set_cookie(viewer.cookie, viewer.token, httponly=True, samesite="strict")
        return resp

    async def static(request: Request):
        name = request.path_params["name"]
        if name not in ("app.js", "app.css", "read.js"):
            return Response(status_code=404)
        return Response((STATIC / name).read_text(), media_type=mimetypes.guess_type(name)[0])

    async def repo_file(request: Request):
        if not authorised(request):
            return Response(status_code=403)
        rel = request.path_params["path"]
        p = (viewer.root / rel).resolve()
        if viewer.root.resolve() not in p.parents or ".git" in p.parts or ".scrivmark" in p.parts or not p.is_file():
            return Response(status_code=404)
        return FileResponse(p, headers={"Content-Security-Policy": "sandbox; script-src 'none'"})

    async def figure_png(request: Request):
        if not authorised(request):
            return Response(status_code=403)
        rel = request.path_params["path"]
        if not rel.endswith(".png"):
            return Response(status_code=404)
        out = await asyncio.to_thread(figures.png, viewer.root, rel[:-4])
        if out is None:
            return Response(figures.placeholder("Figure could not be shown"), media_type="image/svg+xml",
                            headers={"Content-Security-Policy": "sandbox; script-src 'none'"})
        return FileResponse(out, headers={"Content-Security-Policy": "sandbox; script-src 'none'"})

    async def tikz_svg(request: Request):
        if not authorised(request):
            return Response(status_code=403)
        name = request.path_params["name"]
        out, reason = await asyncio.to_thread(figures.tikz, viewer.root, name.removesuffix(".svg"))
        headers = {"Content-Security-Policy": "sandbox; script-src 'none'"}
        if out is None:
            return Response(figures.placeholder(reason), media_type="image/svg+xml", headers=headers)
        return FileResponse(out, media_type="image/svg+xml", headers=headers)

    def summary(request: Request, b: dict):
        from . import export

        rv = viewer.need()
        return HTMLResponse(export.html(rv), headers={
            "Content-Disposition": f'attachment; filename="scrivmark-{rv.id}.html"',
            "Content-Security-Policy": "sandbox; script-src 'none'"})

    def preflight(request: Request, b: dict):
        viewer.need()
        return JSONResponse(rounds.preflight(viewer.root))

    def clean(request: Request, b: dict):
        return JSONResponse(viewer.clean())

    def state(request: Request, b: dict):
        return JSONResponse(viewer.state())

    async def events(request: Request):
        q: asyncio.Queue = asyncio.Queue()
        viewer.listeners.add(q)

        async def stream():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), 20)
                        while not q.empty():
                            q.get_nowait()
                        yield f"data: {msg}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                viewer.listeners.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def body_of(request: Request) -> dict:
        return json.loads(await request.body() or b"{}")

    def comment(request: Request, b: dict):
        rv = viewer.need()
        viewer.check_render(b)
        anchor = viewer.anchor_from(rv, b)
        t = review.comment(rv, anchor, b["body"], review.who(rv), b.get("rev"))
        return JSONResponse(t)

    def reply(request: Request, b: dict):
        rv = viewer.need()
        return JSONResponse(review.reply(rv, b["thread"], b["body"], review.who(rv), b.get("rev")))

    def message(request: Request, b: dict):
        rv = viewer.need()
        return JSONResponse(review.edit_message(rv, b["thread"], b["message"], b["body"], review.who(rv), b.get("rev")))

    def thread_status(request: Request, b: dict):
        rv = viewer.need()
        if b.get("reattach"):
            viewer.check_render(b)
            return JSONResponse(review.reattach(rv, b["thread"], viewer.anchor_from(rv, b), b.get("rev")))
        return JSONResponse(review.set_thread(rv, b["thread"], b["status"], b.get("rev"), review.who(rv)))

    def decide(request: Request, b: dict):
        rv = viewer.need()
        decision = {"accept": "accepted", "reject": "rejected"}[b["decision"]]
        return JSONResponse(review.decide(rv, b["change"], decision, review.who(rv), b.get("rev"), b.get("because")))

    def contest(request: Request, b: dict):
        rv = viewer.need()
        return JSONResponse(review.contest(rv, b["change"], review.who(rv), b.get("because") or "", b.get("rev")))

    def undo(request: Request, b: dict):
        rv = viewer.need()
        return JSONResponse(review.undo(rv, b["change"], b.get("rev")))

    def triage(request: Request, b: dict):
        rv = viewer.need()
        if b.get("action") == "keep_all":
            return JSONResponse(review.keep_all(rv, review.who(rv), b.get("rev")))
        t = review.triage(rv, b["draft"], b["action"] == "keep", review.who(rv), b.get("body"),
                          b.get("wording"), b.get("rev"))
        return JSONResponse(t or {})

    def source_get(request: Request, b: dict):
        rel = request.query_params["file"]
        ms = config.load(viewer.root)
        if rel not in ms.sources:
            raise ScrivError(f"{rel} is not a manuscript file")
        first, last = (int(x) for x in request.query_params["lines"].split("-"))
        lines = (viewer.root / rel).read_text().splitlines(keepends=True)
        text = "".join(lines[first - 1 : last]).rstrip("\n")
        return JSONResponse({"file": rel, "first": first, "last": last, "text": text,
                             "file_rev": revision(viewer.root, [rel])})

    def source_put(request: Request, b: dict):
        rv = viewer.need()
        try:
            review.replace_lines(rv, b["file"], int(b["first"]), int(b["last"]), b["text"], b["file_rev"])
        except StaleRevision:
            raise StaleRevision("This file changed while you were editing. Your text is still in the box; "
                                "copy it, cancel, and edit again.") from None
        return JSONResponse({"ok": True})

    def selection(request: Request, b: dict):
        rv = viewer.need()
        sel_path = local_dir(viewer.root, rv.id) / "selection.json"
        if not b.get("text"):
            atomic_write(sel_path, None)
            return JSONResponse({"ok": True})
        viewer.check_render(b)
        a = viewer.anchor_from(rv, b)
        sel = {"file": a["file"], "lines": [int(x) for x in b["lines"].split("-")], "range": a["range"],
               "exact": a["exact"], "rendered": b["text"], "file_rev": revision(viewer.root, [a["file"]]),
               "at": record.now()}
        atomic_write(sel_path, json.dumps(sel, indent=2, ensure_ascii=False))
        return JSONResponse(sel)

    def ask(request: Request, b: dict):
        rv = viewer.need()
        viewer.check_render(b)
        return JSONResponse(review.add_ask(rv, viewer.anchor_from(rv, b), b["question"], review.who(rv), b.get("rev")))

    def ask_update(request: Request, b: dict):
        rv = viewer.need()
        item = next((a for a in review.asks(rv, include_done=True) if a["id"] == b["ask"]), None)
        if item is None:
            raise ScrivError(f"No ask {b['ask']}")
        if b.get("comment"):
            if item["where"]["state"] != "ok":
                raise Conflict("The passage this ask was about has changed; comment on it afresh.")
            s, e = item["where"]["range"]
            t = review.comment(rv, review.anchor_at(rv, item["anchor"]["file"], s, e), b["comment"], review.who(rv), b.get("rev"))
            review.update_ask(rv, item["id"], done=True)
            return JSONResponse(t)
        if b.get("apply"):
            return JSONResponse(review.apply_ask(rv, item["id"], b.get("rev")))
        return JSONResponse(review.update_ask(rv, item["id"], done=bool(b.get("done")), expect=b.get("rev")))

    routes = [
        Route("/", index),
        Route("/read", reading),
        Route("/static/{name}", static),
        Route("/files/{path:path}", repo_file),
        Route("/png/{path:path}", figure_png),
        Route("/scrv-tikz/{name}", tikz_svg),
        Route("/api/state", guard(state)),
        Route("/api/clean", guard(clean)),
        Route("/api/preflight", guard(preflight)),
        Route("/api/summary", guard(summary)),
        Route("/api/events", guard(events)),
        Route("/api/source", guard(source_get)),
        Route("/api/source", guard(source_put, True), methods=["POST"]),
        Route("/api/comment", guard(comment, True), methods=["POST"]),
        Route("/api/reply", guard(reply, True), methods=["POST"]),
        Route("/api/thread", guard(thread_status, True), methods=["POST"]),
        Route("/api/message", guard(message, True), methods=["POST"]),
        Route("/api/decide", guard(decide, True), methods=["POST"]),
        Route("/api/contest", guard(contest, True), methods=["POST"]),
        Route("/api/draft", guard(triage, True), methods=["POST"]),
        Route("/api/undo", guard(undo, True), methods=["POST"]),
        Route("/api/selection", guard(selection, True), methods=["POST"]),
        Route("/api/ask", guard(ask, True), methods=["POST"]),
        Route("/api/ask/update", guard(ask_update, True), methods=["POST"]),
    ]

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.get_running_loop().create_task(viewer.watch())
        yield
        task.cancel()

    return Starlette(routes=routes, lifespan=lifespan)


DEFAULT_PORT = 8765


def _port_free(port: int) -> bool:
    """Free means nothing is listening and we could bind it. SO_REUSEADDR, as
    uvicorn sets, lets a port still closing a stopped viewer's connections count
    as free."""
    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def named_port(asked: int, configured: int | None) -> int:
    """--port, else $PORT (set by launchers such as the desktop apps'), else
    scrivmark.toml's; 0 when none is named."""
    env = os.environ.get("PORT", "")
    return asked or (int(env) if env.isdigit() else 0) or configured or 0


def choose_port(asked: int, configured: int | None) -> int:
    """The named port, else 8765. Only the default moves along to the next free
    port, and says so; a named port that is busy is an error."""
    port = named_port(asked, configured)
    if port:
        if not _port_free(port):
            raise ScrivError(f"Port {port} is in use by another program; free it or name another port with --port.")
        return port
    for port in range(DEFAULT_PORT, DEFAULT_PORT + 40):
        if _port_free(port):
            if port != DEFAULT_PORT:
                print(f"scrivmark: port {DEFAULT_PORT} is in use by another program; using {port}.",
                      file=sys.stderr, flush=True)
            return port
    raise ScrivError("No free port between 8765 and 8804; pass --port.")


def run(root: Path, port: int = 0, open_browser: bool = False) -> bool:
    """Serve until stopped. If this repository's viewer is already running, say
    where and return False instead of starting a second one."""
    ms = config.load(root)
    if existing := running_url(root):
        named = named_port(port, ms.viewer_port)
        held = read_json(viewer_file(root), {}).get("port")
        if named and named != held:
            raise ScrivError(f"This repository's viewer is already running on port {held}, not {named}: {existing}\n"
                             "Use that address, or stop that viewer first.")
        print(f"scrivmark viewer already running: {existing}", flush=True)
        return False
    port = choose_port(port, ms.viewer_port)
    viewer = Viewer(root, port)
    url = f"http://127.0.0.1:{port}/?t={viewer.token}"
    atomic_write(viewer_file(root), json.dumps({"token": viewer.token, "port": port, "url": url, "pid": os.getpid()}))
    print(f"scrivmark viewer: {url}\n(on this machine, http://localhost:{port} also works)\n"
          "Open it in your browser or the app's browser pane. Ctrl-C stops it.", flush=True)
    if open_browser:
        webbrowser.open(url)
    # The live-update streams never end by themselves, so stopping waits for them only briefly.
    uvicorn.run(build_app(viewer), host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1)
    return True


def running_url(root: Path) -> str | None:
    """The address of this repository's running viewer, if there is one."""
    saved = read_json(viewer_file(root), {})
    if not saved.get("port") or _port_free(saved["port"]):
        return None
    try:
        os.kill(saved["pid"], 0)
    except (OSError, KeyError, TypeError):
        return None
    return saved["url"]
