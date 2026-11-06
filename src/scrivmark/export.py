"""A readable, archivable summary of one review round, as Markdown or HTML.

The JSON record in reviews/ stays authoritative; this is a view of it. Live
state (where a comment now sits, whether a change is still pending) is read
from the working tree only when the round's own branch is checked out;
otherwise the recorded anchors and decisions are used."""

import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from . import __version__, gitutil
from .errors import ScrivError
from .review import BRANCH_PREFIX, Review, change_status, drafts, resolve

QUOTE = 300
CELL = 70


def esc(text: str) -> str:
    """Single-line text made safe for Markdown: whitespace collapsed, markup escaped."""
    t = re.sub(r"\s+", " ", text or "").strip()
    t = re.sub(r"([\\`*_\[\]<>|#~$^&!@])", r"\\\1", t)
    return re.sub(r"^([-+]|\d+\.)", lambda m: m.group(1)[:-1] + "\\" + m.group(1)[-1], t)


def _short(text: str, n: int) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _day(ts: str | None) -> str:
    return (ts or "")[:10]


def _quote(body: str) -> list[str]:
    return [("> " + ln).rstrip() for ln in (body or "").splitlines()] or [">"]


def _line(text: str | None, offset: int | None) -> int | None:
    return None if text is None or offset is None else text.count("\n", 0, offset) + 1


def _live(rv: Review) -> bool:
    return rv.status != "closed" and gitutil.current_branch(rv.root) == f"{BRANCH_PREFIX}{rv.id}"


def _person(p: dict | None) -> str:
    return f"{p['name']} ({p['initials']})" if p else "not yet opened by the author"


def _change_row(rv: Review, c: dict, live: bool) -> str:
    rnd = rv.rec["round"]
    where = esc(c["file"])
    if c["type"] == "file":
        what = esc(c["reviewer"])
    else:
        text = gitutil.show(rv.root, rnd["returned"], c["file"]) if rnd.get("returned") else None
        n = _line(text, c["r_range"][0] if c.get("r_range") else None)
        where = f"{where}:{n}" if n else where
        old, new = _short(c["base"], CELL), _short(c["reviewer"], CELL)
        what = ((f"~~{esc(old)}~~" if old else "(inserted)") + " → " + (esc(new) if new else "(deleted)"))
    if c["decision"]:
        state = f"{c['decision']} by {esc(c['by'] or '?')} on {_day(c['at'])}"
    else:
        state = change_status(rv, c) if live else "pending"
    return f"| {c['id']} | {where} | {what} | {state} |"


def _thread_blocks(rv: Review, live: bool) -> list[tuple[tuple, list[str]]]:
    order = {f: i for i, f in enumerate(rv.ms.files)}
    blocks = []
    for t in rv.rec["threads"]:
        a = t["anchor"]
        where = resolve(rv, a) if live else None
        lost = where is not None and where["state"] != "ok"
        if where and where["state"] == "ok":
            text, pos = rv.text(a["file"]), where["range"][0]
        else:
            kind, _, ref = a["rev"].partition(":")
            text, pos = (gitutil.show(rv.root, ref, a["file"]) if kind == "git" else None), a["range"][0]
        n = _line(text, pos)
        head = f"### {esc(a['file'])}{f':{n}' if n else ''} · {t['status']}" + (" · lost its place" if lost else "")
        passage = a.get("removed") or a["exact"]
        lines = [head, ""]
        if lost:
            why = "; its passage is ambiguous" if where.get("why") == "ambiguous" else ""
            lines += [f"*The comment lost its place in the text{why}. It was made on:*", ""]
        elif a.get("removed"):
            lines += ["*The passage was deleted. It read:*", ""]
        lines += _quote(_short(passage, QUOTE)) if passage else ["> (a point in the text)"]
        for m in t["messages"]:
            meta = f"**{esc(m['by'])}**, {_day(m['at'])}"
            if m.get("edited_at"):
                meta += " (edited)"
            if m.get("drafted_by"):
                meta += f" (drafted by {esc(m['drafted_by'])})"
            lines += ["", meta, ""] + _quote(m["body"])
        blocks.append(((order.get(a["file"], len(order)), pos), lines))
    return sorted(blocks, key=lambda b: b[0])


def markdown(rv: Review) -> str:
    rec, rnd = rv.rec, rv.rec["round"]
    live = _live(rv)
    title = rv.ms.title or rv.ms.files[0]
    out = [f"# Review of {esc(title)}", ""]
    meta = [("Round", f"`{rv.id}`"), ("Status", rv.status + (" (forced)" if rnd.get("forced") else "")),
            ("Reviewer", esc(_person(rnd["reviewer"]))), ("Author", esc(_person(rnd.get("author")))),
            ("Author branch", f"`{rnd['author_branch']}`"), ("Started from", f"`{rnd['base'][:10]}`")]
    if rnd.get("returned"):
        meta.append(("Returned", f"{_day(rnd.get('returned_at'))} at `{rnd['returned'][:10]}`"))
    if rnd.get("closed"):
        meta.append(("Closed", _day(rnd["closed"])))
    meta.append(("Files", ", ".join(f"`{f}`" for f in rv.ms.files)))
    out += [f"- **{k}:** {v}" for k, v in meta]
    out += ["", "**Statement**", ""] + _quote(rnd["statement"]) + [""]

    cs = rec["changes"]
    if rv.status != "open":
        out += [f"## Changes ({len(cs)})", ""]
        if cs:
            out += ["| id | where | change | decision |", "|---|---|---|---|"]
            out += [_change_row(rv, c, live) for c in cs]
        else:
            out.append("No tracked changes.")
        out.append("")

    out += [f"## Comment threads ({len(rec['threads'])})", ""]
    if rec["threads"]:
        for _, lines in _thread_blocks(rv, live):
            out += lines + [""]
    else:
        out += ["No comments.", ""]

    asks = rec.get("asks", [])
    if asks:
        out += [f"## Questions put to the assistant ({len(asks)})", ""]
        for a in asks:
            who = f"asked by {esc(a['by'])}" if a.get("by") else "asked"
            ans = f"answered by {esc(a.get('answered_by') or '?')}" + \
                  (f", {_day(a['answered_at'])}" if a.get("answered_at") else "")
            out += [f"### {esc(a['anchor']['file'])} · {who}; {ans}", ""]
            out += _quote(_short(a["anchor"]["exact"], QUOTE)) + ["", "**Question**", ""] + _quote(a["question"])
            out += ["", "**Answer**", ""] + _quote(a["answer"]) + [""]

    pending = [c["id"] for c in cs if not c["decision"] and (change_status(rv, c) == "pending" if live else True)]
    pending = pending if rv.status == "returned" else []
    open_threads = [t["id"] for t in rec["threads"] if t["status"] == "open"] if rv.status != "closed" else []
    left = [d["id"] for d in drafts(rv)] if rv.status == "open" and live else []
    out += ["## Outstanding", ""]
    todo = []
    if pending:
        todo.append(f"{len(pending)} change(s) awaiting a decision: {', '.join(pending)}")
    if open_threads:
        todo.append(f"{len(open_threads)} comment thread(s) open: {', '.join(open_threads)}")
    if left:
        todo.append(f"{len(left)} draft comment(s) not yet kept or dropped")
    if rnd.get("forced"):
        todo.append("The round was closed with items still undecided or open")
    out += [f"- {t}" for t in todo] or ["Nothing outstanding."]
    out += ["", "---", "",
            f"*Generated by scrivmark {__version__} on {date.today().isoformat()}. "
            f"The JSON record, `{rv.record_rel}`, is authoritative.*", ""]
    return "\n".join(out)


STYLE = """<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>
body{font:17px/1.55 Georgia,'Times New Roman',serif;max-width:44em;margin:2em auto;padding:0 1em;color:#222;background:#fff}
h1{font-size:1.7em;margin-bottom:.4em}h2{margin-top:2em;border-bottom:1px solid #ccc;padding-bottom:.2em}
h3{font-size:1.05em;margin-top:1.6em;font-family:system-ui,sans-serif}
blockquote{margin:.4em 0 .4em 0;padding:.1em 1em;border-left:3px solid #bbb;color:#333}
table{border-collapse:collapse;width:100%;font-size:.88em;font-family:system-ui,sans-serif}
th,td{border:1px solid #ccc;padding:.3em .5em;text-align:left;vertical-align:top}th{background:#f2f2f2}
code{font-size:.9em;background:#f4f4f4;padding:0 .2em}hr{border:0;border-top:1px solid #ccc;margin-top:2em}
@media print{body{font-size:11pt;max-width:none;margin:0}}
</style>
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$pagetitle$</title>
$for(header-includes)$
$header-includes$
$endfor$
</head>
<body>
$body$
</body>
</html>
"""

FILTER = """
local ok = {http = true, https = true, mailto = true}
function Link(l)
  local scheme = l.target:match('^(%a[%w+.-]*):')
  if (scheme and not ok[scheme:lower()]) then return l.content end
end
function Image(i)
  return pandoc.Str('[image: ' .. pandoc.utils.stringify(i.caption) .. ']')
end
function RawInline() return {} end
function RawBlock() return {} end
"""


def html(rv: Review) -> str:
    if not shutil.which("pandoc"):
        raise ScrivError("pandoc is needed for HTML export (it is also needed for the viewer). Install it, or use Markdown.")
    title = rv.ms.title or rv.ms.files[0]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "head.html").write_text(STYLE)
        (Path(tmp) / "clean.lua").write_text(FILTER)
        (Path(tmp) / "page.html").write_text(TEMPLATE)
        proc = subprocess.run(
            ["pandoc", "-f", "markdown-raw_html-raw_tex-raw_attribute-yaml_metadata_block-pandoc_title_block-tex_math_dollars-citations"
             "-link_attributes-inline_code_attributes-fenced_code_attributes-header_attributes-bracketed_spans"
             "-fenced_divs-native_divs-native_spans",
             "-t", "html5", "-s", "--template", str(Path(tmp) / "page.html"), "--sandbox", "--no-highlight", "-M", f"pagetitle=Review of {title}", "-H", str(Path(tmp) / "head.html"),
             "--lua-filter", str(Path(tmp) / "clean.lua")],
            input=markdown(rv), capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScrivError(f"pandoc failed:\n{proc.stderr.strip()}")
    return proc.stdout
