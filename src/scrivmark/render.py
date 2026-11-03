"""Render the manuscript to HTML with source-mapped blocks and a redline.

Every block of current text is wrapped in a fenced div that names its file and
lines, so the page always knows which source a block came from. Changed blocks
carry `[old]{.scrv-del}` / `[new]{.scrv-ins}` spans, checked against pandoc:
removing the spans must give back exactly the current and the base text. Where
that fails, the old block is shown struck through above the new one.
"""

import json
import subprocess
from importlib.resources import files
from pathlib import Path

from . import gitutil
from .blocks import PANDOC_FROM, fence_for, guarded_segments, line_of
from .config import Manuscript
from .textdiff import Op, Unit, align

ASSETS = files("scrivmark") / "assets"
INLINE = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps",
          "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math", "RawInline", "Link",
          "Image", "Note", "Span"}


def _attrs(**kv) -> str:
    return " ".join(f'{k}="{v}"' for k, v in kv.items() if v not in (None, ""))


def _overlaps(n: tuple[int, int], loc: tuple[int, int]) -> bool:
    (n0, n1), (l0, l1) = n, loc
    if n0 == n1 or l0 == l1:
        return l0 <= n1 and n0 <= l1
    return n0 < l1 and l0 < n1


class _Builder:
    def __init__(self, fence: str, rel: str, cur: str):
        self.fence, self.rel, self.cur = fence, rel, cur
        self.parts: list[str] = []
        self.checks: list[tuple[int, str, str, str, str]] = []

    def wrap(self, body: str, cls: str, s: int, e: int, change: str | None = None) -> None:
        lines = f"{line_of(self.cur, s)}-{line_of(self.cur, max(s, e - 1))}"
        classes = " ".join(f".{c}" for c in cls.split())
        self.parts.append(f"{self.fence} {{{classes} {_attrs(file=self.rel, lines=lines, c=change)}}}\n{body}\n{self.fence}\n\n")

    def old(self, body: str, change: str | None) -> None:
        if body.strip():
            self.parts.append(f"{self.fence} {{.scrv-old {_attrs(file=self.rel, c=change)}}}\n{body}\n{self.fence}\n\n")


def _edges(text: str) -> tuple[str, str, str]:
    core = text.strip()
    if not core:
        return text, "", ""
    i = text.index(core)
    return text[:i], core, text[i + len(core) :]


def _splice(cur: str, b0: int, b1: int, marked: list[tuple[Unit, str | None]]) -> tuple[str, str]:
    spliced, reverted, pos = [], [], b0
    for u, cid in sorted(marked, key=lambda m: m[0].new):
        n0, n1 = u.new
        spliced.append(cur[pos:n0])
        reverted.append(cur[pos:n0])
        c = f' c="{cid}"' if cid else ""
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
            doc.append(f"{fence} {{.scrv-g k=\"{i}{kind}\"}}\n{body}\n{fence}\n")
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


def manuscript_markdown(root: Path, ms: Manuscript, base: str | None = None,
                        located: dict[str, list[tuple[tuple[int, int], str]]] | None = None) -> str:
    """Build the wrapped Markdown. With `base`, show the redline from that commit;
    `located` restricts marks to those changes (returned rounds)."""
    out = []
    for idx, rel in enumerate(ms.files):
        cur = (root / rel).read_text()
        old = (gitutil.show(root, base, rel) or "") if base else None
        fence = fence_for(cur + (old or ""))
        b = _Builder(fence, rel, cur)
        ops = align(old, cur) if old is not None and old != cur else [Op("equal", [], guarded_segments(cur), [])]
        for op in ops:
            if op.tag == "equal":
                for seg in op.new:
                    if seg.kind == "yaml":
                        if idx == 0:
                            b.parts.append(cur[seg.b0 : seg.b1] + "\n\n")
                        continue
                    b.wrap(cur[seg.b0 : seg.b1], "scrv-b", seg.b0, seg.b1)
                continue
            marks = []
            for u in op.units:
                cid = None
                if located is not None:
                    hit = next((c for loc, c in located.get(rel, []) if _overlaps(u.new, loc)), None)
                    if hit is None:
                        continue
                    cid = hit
                marks.append((u, cid))
            if op.tag == "pair":
                seg = op.new[0]
                if not marks:
                    b.wrap(cur[seg.b0 : seg.b1], "scrv-b", seg.b0, seg.b1)
                    continue
                spliced, reverted = _splice(cur, seg.b0, seg.b1, marks)
                cids = " ".join(sorted({c for _, c in marks if c}))
                b.checks.append((len(b.parts), spliced, cur[seg.b0 : seg.b1], reverted, cids))
                b.wrap(spliced, "scrv-b", seg.b0, seg.b1)
                continue
            cid = marks[0][1] if marks else None
            if marks:
                b.old("\n\n".join(old[s.b0 : s.b1] for s in op.old if s.kind != "yaml"), cid)
            for seg in op.new:
                if seg.kind == "yaml":
                    if idx == 0:
                        b.parts.append(cur[seg.b0 : seg.b1] + "\n\n")
                    continue
                b.wrap(cur[seg.b0 : seg.b1], "scrv-b scrv-ins-block" if marks else "scrv-b", seg.b0, seg.b1, cid)
        for i in sorted(_verify(b.checks, fence)):
            pos, _, current, reverted, cids = b.checks[i]
            seg_lines = b.parts[pos].split("\n", 1)[0]
            b.parts[pos] = (f"{fence} {{.scrv-old {_attrs(file=rel, c=cids)}}}\n{reverted}\n{fence}\n\n"
                            + seg_lines.replace(".scrv-b", ".scrv-b .scrv-ins-block", 1)
                            + f"\n{current}\n{fence}\n\n")
        out.extend(b.parts)
    return "".join(out)


def to_html(root: Path, ms: Manuscript, markdown: str) -> tuple[str, str]:
    cmd = [
        "pandoc", "-f", PANDOC_FROM, "-t", "html5", "--mathml", "--wrap=none", "--standalone",
        "--template", str(ASSETS / "template.html"), "--lua-filter", str(ASSETS / "scrivmark.lua"),
        "--citeproc", "--metadata", "link-citations=true", "--metadata", "pagetitle=scrivmark",
        "--metadata", "reference-section-title=References",
    ]
    for bib in ms.bibliography:
        cmd += ["--bibliography", str(root / bib)]
    if ms.csl:
        cmd += ["--csl", str(root / ms.csl)]
    proc = subprocess.run(cmd, input=markdown, capture_output=True, text=True, cwd=root)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout, proc.stderr
