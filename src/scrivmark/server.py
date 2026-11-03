"""The local viewer: a small Starlette app serving the rendered review and an
API that performs the same operations as the CLI."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import mimetypes
import secrets
import socket
import webbrowser
from importlib.resources import files
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from watchfiles import awatch

from . import config, gitutil, record, render, review
from .blocks import guarded_segments, line_of
from .errors import Conflict, ScrivError, StaleRevision
from .selection import map_selection
from .state import atomic_write, local_dir, read_json, revision

STATIC = files("scrivmark") / "static"
COOKIE = "scrivmark"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
       "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")


class Viewer:
    def __init__(self, root: Path, port: int = 0):
        self.root = root
        self.cookie = f"{COOKIE}-{port}"
        self.token = secrets.token_urlsafe(18)
        self.cache: tuple[str, dict] | None = None
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
        return {"open": "reviewer", "returned": "author", "closed": "closed"}[rv.status]

    def who(self, rv: review.Review) -> str:
        rnd = rv.rec["round"]
        if rnd["status"] == "open":
            return rnd["reviewer"]["initials"]
        return (rnd["author"] or {"initials": config.identity().initials})["initials"]

    def state(self) -> dict:
        ms = config.load(self.root)
        rv = self.current()
        branch = gitutil.current_branch(self.root)
        key_paths = [config.CONFIG, *ms.files, *ms.bibliography, *([ms.csl] if ms.csl else []),
                     *([rv.record_rel, rv.drafts_rel] if rv else [])]
        key = f"{branch}:{revision(self.root, key_paths)}"
        if self.cache and self.cache[0] == key:
            return self.cache[1]
        mode = self.mode(rv)
        base, pending = None, None
        if rv and rv.status in ("open", "returned"):
            base = rv.rec["round"]["base"]
        if rv and rv.status == "returned":
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
            "rev": rv.rev() if rv else None,
            "me": self.who(rv) if rv else None,
            "threads": [], "drafts": [], "changes": [],
        }
        if rv:
            data["threads"] = [self._place(rv, t) for t in review.threads(rv)]
            if rv.status == "open":
                data["drafts"] = [self._place(rv, d) for d in review.drafts(rv)]
            if rv.status in ("returned", "closed"):
                data["changes"] = review.changes(rv)
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
            seg = next((g for g in guarded_segments(text) if g.a <= s < g.z), None)
            start = seg.b0 if seg else 0
            out["nth"] = text[start:s].count(text[s:e]) if e > s else 0
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
        if rel not in rv.ms.files:
            raise ScrivError(f"{rel} is not a manuscript file")
        first, last = (int(x) for x in body["lines"].split("-"))
        text = rv.text(rel)
        if text is None:
            raise ScrivError(f"{rel} does not exist")
        lines = text.splitlines(keepends=True)
        s0 = sum(len(x) for x in lines[: first - 1])
        e0 = sum(len(x) for x in lines[:last])
        block = text[s0:e0]
        hit = map_selection(block, body.get("text", ""), body.get("before", "")) if body.get("text") else None
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
                return p.name == "drafts.json"
            return not p.name.startswith(".")

        async for _ in awatch(self.root, watch_filter=relevant, debounce=250, recursive=True):
            await self.notify()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_app(viewer: Viewer) -> Starlette:
    def authorised(request: Request) -> bool:
        return request.cookies.get(viewer.cookie) == viewer.token or request.headers.get("x-scrivmark") == viewer.token

    def guard(handler, mutate: bool = False):
        async def inner(request: Request):
            if not authorised(request):
                return JSONResponse({"error": "Not authorised. Open the address scrivmark printed."}, status_code=403)
            if mutate:
                origin = request.headers.get("origin")
                if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
                    return JSONResponse({"error": "Cross-origin request refused."}, status_code=403)
            try:
                result = await handler(request)
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
        if request.query_params.get("t") != viewer.token and not authorised(request):
            return HTMLResponse("<p>Open the address that <code>scrivmark serve</code> printed.</p>", status_code=403)
        resp = HTMLResponse((STATIC / "index.html").read_text(), headers={"Content-Security-Policy": CSP})
        resp.set_cookie(viewer.cookie, viewer.token, httponly=True, samesite="strict")
        return resp

    async def static(request: Request):
        name = request.path_params["name"]
        if name not in ("app.js", "app.css"):
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

    async def state(request: Request):
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

    async def comment(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        viewer.check_render(b)
        anchor = viewer.anchor_from(rv, b)
        t = review.comment(rv, anchor, b["body"], viewer.who(rv), b.get("rev"))
        return JSONResponse(t)

    async def reply(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        return JSONResponse(review.reply(rv, b["thread"], b["body"], viewer.who(rv), b.get("rev")))

    async def thread_status(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        return JSONResponse(review.set_thread(rv, b["thread"], b["status"], b.get("rev")))

    async def decide(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        decision = {"accept": "accepted", "reject": "rejected"}[b["decision"]]
        return JSONResponse(review.decide(rv, b["change"], decision, viewer.who(rv), b.get("rev")))

    async def undo(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        return JSONResponse(review.undo(rv, b["change"], b.get("rev")))

    async def triage(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        t = review.triage(rv, b["draft"], b["action"] == "keep", viewer.who(rv), b.get("body"),
                          b.get("wording"), b.get("rev"))
        return JSONResponse(t or {})

    async def source_get(request: Request):
        rel = request.query_params["file"]
        ms = config.load(viewer.root)
        if rel not in ms.files:
            raise ScrivError(f"{rel} is not a manuscript file")
        first, last = (int(x) for x in request.query_params["lines"].split("-"))
        lines = (viewer.root / rel).read_text().splitlines(keepends=True)
        text = "".join(lines[first - 1 : last]).rstrip("\n")
        return JSONResponse({"file": rel, "first": first, "last": last, "text": text,
                             "file_rev": revision(viewer.root, [rel])})

    async def source_put(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        try:
            review.replace_lines(rv, b["file"], int(b["first"]), int(b["last"]), b["text"], b["file_rev"])
        except StaleRevision:
            raise StaleRevision("This file changed while you were editing. Your text is still in the box; "
                                "copy it, cancel, and edit again.") from None
        return JSONResponse({"ok": True})

    async def selection(request: Request):
        b = await body_of(request)
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

    async def ask(request: Request):
        b = await body_of(request)
        rv = viewer.need()
        viewer.check_render(b)
        a = viewer.anchor_from(rv, b)
        p = local_dir(viewer.root, rv.id) / "asks.json"
        items = read_json(p, [])
        item = {"id": record.new_id("q"), "file": a["file"], "range": a["range"], "exact": a["exact"],
                "question": b["question"], "file_rev": revision(viewer.root, [a["file"]]), "at": record.now()}
        items.append(item)
        atomic_write(p, json.dumps(items, indent=2, ensure_ascii=False))
        return JSONResponse(item)

    routes = [
        Route("/", index),
        Route("/static/{name}", static),
        Route("/files/{path:path}", repo_file),
        Route("/api/state", guard(state)),
        Route("/api/events", guard(events)),
        Route("/api/source", guard(source_get)),
        Route("/api/source", guard(source_put, True), methods=["POST"]),
        Route("/api/comment", guard(comment, True), methods=["POST"]),
        Route("/api/reply", guard(reply, True), methods=["POST"]),
        Route("/api/thread", guard(thread_status, True), methods=["POST"]),
        Route("/api/decide", guard(decide, True), methods=["POST"]),
        Route("/api/draft", guard(triage, True), methods=["POST"]),
        Route("/api/undo", guard(undo, True), methods=["POST"]),
        Route("/api/selection", guard(selection, True), methods=["POST"]),
        Route("/api/ask", guard(ask, True), methods=["POST"]),
    ]

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.get_running_loop().create_task(viewer.watch())
        yield
        task.cancel()

    return Starlette(routes=routes, lifespan=lifespan)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(root: Path, port: int = 0, open_browser: bool = False) -> None:
    config.load(root)
    port = port or _free_port()
    viewer = Viewer(root, port)
    url = f"http://127.0.0.1:{port}/?t={viewer.token}"
    print(f"scrivmark viewer: {url}\nOpen it in your browser or the app's browser pane. Ctrl-C stops it.", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(build_app(viewer), host="127.0.0.1", port=port, log_level="warning")
