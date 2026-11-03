"""Render the manuscript to HTML with source-mapped blocks and a redline.

Every block of current text is wrapped in a fenced div that names its file and
lines, so the page always knows which source a block came from. Changed blocks
carry `[old]{.scrv-del}` / `[new]{.scrv-ins}` spans, checked against pandoc:
removing the spans must give back exactly the current and the base text. Where
that fails, the old block is shown struck through above the new one.
"""

import json
import re
import secrets
import subprocess
from importlib.resources import files
from pathlib import Path

from . import gitutil, sanitize
from .blocks import PANDOC_FROM, fence_for, guarded_segments, line_of
from .config import Manuscript
from .textdiff import Op, Unit, align

ASSETS = files("scrivmark") / "assets"
INLINE = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps",
          "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math", "RawInline", "Link",
          "Image", "Note", "Span"}


def _attrs(**kv) -> str:
    return " ".join(f'{k}="{v}"' for k, v in kv.items() if v not in (None, ""))


class _Builder:
    def __init__(self, fence: str, rel: str, cur: str, nonce: str):
        self.fence, self.rel, self.cur, self.nonce = fence, rel, cur, nonce
        self.parts: list[str] = []
        self.checks: list[tuple[int, str, str, str, str]] = []

    def wrap(self, body: str, cls: str, s: int, e: int, change: str | None = None) -> None:
        lines = f"{line_of(self.cur, s)}-{line_of(self.cur, max(s, e - 1))}"
        classes = " ".join(f".{c}" for c in cls.split())
        attrs = _attrs(file=self.rel, lines=lines, c=change, n=self.nonce)
        self.parts.append(f"{self.fence} {{{classes} {attrs}}}\n{body}\n\n{self.fence}\n\n")

    def old(self, body: str, change: str | None) -> None:
        if body.strip():
            attrs = _attrs(file=self.rel, c=change, n=self.nonce)
            self.parts.append(f"{self.fence} {{.scrv-old {attrs}}}\n{_historical(body)}\n\n{self.fence}\n\n")

    def yaml(self, seg, first_file: bool) -> None:
        if first_file:
            self.parts.append(self.cur[seg.b0 : seg.b1] + "\n\n")

    def inline(self, seg, marks: list[tuple[Unit, str | None]]) -> None:
        spliced, reverted = _splice(self.cur, seg.b0, seg.b1, marks, self.nonce)
        cids = " ".join(sorted({c for _, c in marks if c}))
        self.checks.append((len(self.parts), spliced, self.cur[seg.b0 : seg.b1], reverted, cids))
        self.wrap(spliced, "scrv-b", seg.b0, seg.b1)

    def finish(self) -> list[str]:
        for i in sorted(_verify(self.checks, self.fence)):
            pos, _, current, reverted, cids = self.checks[i]
            head = self.parts[pos].split("\n", 1)[0].replace(".scrv-b", ".scrv-b .scrv-ins-block", 1)
            old = _attrs(file=self.rel, c=cids, n=self.nonce)
            self.parts[pos] = (f"{self.fence} {{.scrv-old {old}}}\n{_historical(reverted)}\n\n{self.fence}\n\n"
                               f"{head}\n{current}\n\n{self.fence}\n\n")
        return self.parts


FOOTNOTE_DEF = re.compile(r"^(\s{0,3})\[\^([^\]]+)\]:", re.M)


def _historical(body: str) -> str:
    """Old text shown for comparison must not define footnotes a second time."""
    return FOOTNOTE_DEF.sub(r"\1\\[^\2]:", body)


def _edges(text: str) -> tuple[str, str, str]:
    core = text.strip()
    if not core:
        return text, "", ""
    i = text.index(core)
    return text[:i], core, text[i + len(core) :]


def _splice(cur: str, b0: int, b1: int, marked: list[tuple[Unit, str | None]], nonce: str) -> tuple[str, str]:
    spliced, reverted, pos = [], [], b0
    for u, cid in sorted(marked, key=lambda m: m[0].new):
        n0, n1 = u.new
        spliced.append(cur[pos:n0])
        reverted.append(cur[pos:n0])
        c = (f' c="{cid}"' if cid else "") + f' n="{nonce}"'
        o, n = _edges(u.old_text), _edges(u.new_text)
        spliced.append(o[0] or n[0])
        if o[1]:
            spliced.append(f"[{o[1]}]{{.scrv-del{c}}}")
        if n[1]:
            spliced.append(f"[{n[1]}]{{.scrv-ins{c}}}")
        spliced.append(o[2] or n[2])
        reverted.append(u.old_text)
        pos = n1
    spliced.append(cur[pos:b1])
    reverted.append(cur[pos:b1])
    return "".join(spliced), "".join(reverted)


def _normal(node, mode: str):
    """Pandoc JSON with redline spans resolved for `mode` ('cur' or 'old').
    Inline spacing is ignored: the check guards markup structure, and pandoc
    trims spaces at span edges."""
    if isinstance(node, list):
        if node and all(isinstance(x, dict) and x.get("t") in INLINE for x in node):
            out = []
            for x in node:
                if x["t"] == "Span" and ("scrv-del" in x["c"][0][1] or "scrv-ins" in x["c"][0][1]):
                    keep = ("scrv-del" in x["c"][0][1]) == (mode == "old")
                    if keep:
                        out.extend(_normal(x["c"][1], mode))
                    continue
                out.append(_normal(x, mode))
            merged = []
            for x in out:
                if x["t"] in ("Space", "SoftBreak"):
                    continue
                if merged and x["t"] == "Str" and merged[-1]["t"] == "Str":
                    merged[-1] = {"t": "Str", "c": merged[-1]["c"] + x["c"]}
                    continue
                merged.append(x)
            return merged
        return [_normal(x, mode) for x in node]
    if isinstance(node, dict):
        return {k: (0 if k in ("citationHash", "citationNoteNum") else _normal(v, mode)) for k, v in node.items()}
    return node


def _verify(checks: list[tuple[int, str, str, str, str]], fence: str) -> set[int]:
    """Return the indices of checks whose spliced redline does not round-trip."""
    if not checks:
        return set()
    doc = []
    for i, (_, spliced, cur, rev, _) in enumerate(checks):
        for kind, body in (("s", spliced), ("c", cur), ("r", rev)):
            doc.append(f"{fence} {{.scrv-g k=\"{i}{kind}\"}}\n{body}\n\n{fence}\n")
    proc = subprocess.run(["pandoc", "-f", PANDOC_FROM, "-t", "json"], input="\n".join(doc),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return set(range(len(checks)))
    pieces = {}
    for b in json.loads(proc.stdout)["blocks"]:
        if b["t"] == "Div" and "scrv-g" in b["c"][0][1]:
            pieces[dict(b["c"][0][2])["k"]] = b["c"][1]
    bad = set()
    for i in range(len(checks)):
        s, c, r = pieces.get(f"{i}s"), pieces.get(f"{i}c"), pieces.get(f"{i}r")
        if s is None or _normal(s, "cur") != _normal(c, "cur") or _normal(s, "old") != _normal(r, "old"):
            bad.add(i)
    return bad


Pending = list[tuple[str, int, int, str, str]]  # (change id, start, end, type, base text)


def _reviewer_view(b: _Builder, old: str | None, first: bool) -> None:
    """Open round: everything that differs from base is the reviewer's."""
    cur = b.cur
    ops = align(old, cur) if old is not None and old != cur else [Op("equal", [], guarded_segments(cur), [])]
    for op in ops:
        if op.tag == "equal" or (op.tag == "pair" and not op.units):
            for seg in op.new:
                b.yaml(seg, first) if seg.kind == "yaml" else b.wrap(cur[seg.b0 : seg.b1], "scrv-b", seg.b0, seg.b1)
        elif op.tag == "pair":
            b.inline(op.new[0], [(u, None) for u in op.units])
        else:
            b.old("\n\n".join(old[s.b0 : s.b1] for s in op.old if s.kind != "yaml"), None)
            for seg in op.new:
                b.yaml(seg, first) if seg.kind == "yaml" else b.wrap(cur[seg.b0 : seg.b1], "scrv-b scrv-ins-block", seg.b0, seg.b1)


def _author_view(b: _Builder, pending: Pending, first: bool) -> None:
    """Returned round: show only the frozen catalogue's pending changes, each
    at its projected place in the current text."""
    cur = b.cur
    blocks = [p for p in pending if p[3] == "block"]
    shown: set[str] = set()

    def old_once(p) -> None:
        if p[0] not in shown:
            shown.add(p[0])
            b.old(p[4].strip("\n"), p[0])

    for seg in guarded_segments(cur):
        for p in blocks:
            if p[1] == p[2] and seg.a <= p[1] <= seg.b0:
                old_once(p)
        if seg.kind == "yaml":
            b.yaml(seg, first)
            continue
        hit = next((p for p in blocks if p[1] < p[2] and p[1] <= seg.b0 and seg.b1 <= p[2]), None)
        if hit:
            old_once(hit)
            b.wrap(cur[seg.b0 : seg.b1], "scrv-b scrv-ins-block", seg.b0, seg.b1, hit[0])
            continue
        inside = [p for p in pending if p[3] == "text" and seg.b0 <= p[1] and p[2] <= seg.b1]
        if not inside:
            b.wrap(cur[seg.b0 : seg.b1], "scrv-b", seg.b0, seg.b1)
            continue
        b.inline(seg, [(Unit("text", (0, 0), (s, e), base, cur[s:e]), cid) for cid, s, e, _, base in inside])
    for p in blocks:
        if p[1] == p[2]:
            old_once(p)


def manuscript_markdown(root: Path, ms: Manuscript, nonce: str, base: str | None = None,
                        pending: dict[str, Pending] | None = None) -> str:
    """The wrapped Markdown for the whole manuscript. With `base` and no
    `pending`, the redline is everything changed since `base` (reviewer view);
    with `pending`, it is those frozen changes only (author view)."""
    out = []
    for idx, rel in enumerate(ms.files):
        path = root / rel
        if not path.exists():
            out.append(f"::: {{.scrv-missing}}\n*{rel} has been deleted.*\n:::\n\n")
            continue
        cur = path.read_text()
        old = (gitutil.show(root, base, rel) or "") if base else None
        b = _Builder(fence_for(cur + (old or "")), rel, cur, nonce)
        if pending is not None:
            _author_view(b, pending.get(rel, []), idx == 0)
        else:
            _reviewer_view(b, old, idx == 0)
        out.extend(b.finish())
    return "".join(out)


def render(root: Path, ms: Manuscript, base: str | None = None,
           pending: dict[str, Pending] | None = None) -> tuple[str, str]:
    nonce = secrets.token_hex(8)
    html, warnings = to_html(root, ms, manuscript_markdown(root, ms, nonce, base, pending), nonce)
    return sanitize.clean(html), warnings


def to_html(root: Path, ms: Manuscript, markdown: str, nonce: str) -> tuple[str, str]:
    cmd = [
        "pandoc", "-f", PANDOC_FROM, "-t", "html5", "--mathml", "--wrap=none", "--standalone",
        "--template", str(ASSETS / "template.html"), "--lua-filter", str(ASSETS / "scrivmark.lua"),
        "--citeproc", "--metadata", "link-citations=true", "--metadata", "pagetitle=scrivmark",
        "--metadata", "reference-section-title=References", "--metadata", f"scrv-nonce={nonce}",
    ]
    for bib in ms.bibliography:
        cmd += ["--bibliography", str(root / bib)]
    if ms.csl:
        cmd += ["--csl", str(root / ms.csl)]
    proc = subprocess.run(cmd, input=markdown, capture_output=True, text=True, cwd=root)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout, proc.stderr
