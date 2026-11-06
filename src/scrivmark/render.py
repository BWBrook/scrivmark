"""Render the manuscript to HTML with source-mapped blocks and a redline.

Every block of current text is wrapped in a fenced div that names its file and
lines, so the page always knows which source a block came from. Changed blocks
carry `[old]{.scrv-del}` / `[new]{.scrv-ins}` spans, checked against pandoc:
removing the spans must give back exactly the current and the base text. Where
that fails, the old block is shown struck through above the new one.
"""

import json
import re
from html import escape, unescape
from functools import lru_cache
import secrets
from importlib.resources import files
from pathlib import Path

from . import gitutil, latex, sanitize
from .blocks import HIDDEN, LATEX_LUA, PANDOC_FROM, fence_for, guarded_segments, line_of, pandoc_json, run_pandoc
from .config import Manuscript
from .textdiff import Op, Unit, align

ASSETS = files("scrivmark") / "assets"
INLINE = {"Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps",
          "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak", "Math", "RawInline", "Link",
          "Image", "Note", "Span"}


def _quoted(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _attrs(**kv) -> str:
    return " ".join(f'{k}="{_quoted(v)}"' for k, v in kv.items() if v not in (None, ""))


class _Builder:
    def __init__(self, fence: str, rel: str, cur: str, nonce: str, defs: str = "", includes=None):
        self.fence, self.rel, self.cur, self.nonce = fence, rel, cur, nonce
        self.tex = latex.is_tex(rel)
        self.defs = defs
        self.includes = includes
        self.parts: list[str] = [self.defs + "\n\n"] if self.tex else []
        self.checks: list[tuple[int, str, str, str, str]] = []
        self.spans: dict[int, tuple[int, int, int]] = {}

    def wrap(self, body: str, cls: str, s: int, e: int, change: str | None = None) -> None:
        self.parts.extend(self._emit(body, cls, s, e, change))

    def _emit(self, body: str, cls: str, s: int, e: int, change: str | None = None) -> list[str]:
        """The wrapped block, cut at each \\input that stands on its own line,
        so the child's content comes at the include position. No cut is made
        for an include sharing its line with text (fragments would share a
        line range), inside a group or environment (say, within a tracked
        change), or after a macro defined earlier in the block (the later
        fragment, read separately, would lose it); that child follows the
        block."""
        classes = " ".join(f".{c}" for c in cls.split())
        exact = body == self.cur[s:e]

        def div(text: str, a: int, z: int) -> str:
            if exact:
                lead = len(text) - len(text.lstrip())
                a, z = s + a + lead, s + a + len(text.rstrip())
            else:
                a, z = s, e
            lines = f"{line_of(self.cur, a)}-{line_of(self.cur, max(a, z - 1))}"
            attrs = _attrs(file=self.rel, lines=lines, c=change, n=self.nonce)
            return f"{self.fence} {{{classes} {attrs}}}\n{self._src(text)}\n\n{self.fence}\n\n"

        out: list[str] = []
        if not (self.tex and self.includes):
            return [div(body, 0, len(body))]
        pos, late = 0, []
        for a, z, name in latex.input_spans(body):
            child = self.includes(self.rel, name)
            if not child:
                continue
            piece = "\n".join(latex.code_part(x) for x in body[pos:a].splitlines())
            before = body[body.rfind("\n", 0, a) + 1 : a]
            after = body[z : (body.find("\n", z) + 1 or len(body) + 1) - 1]
            alone = not before.strip() and not latex.code_part(after).strip()
            if alone and latex.balanced(piece) and not latex.DEFINES.search(latex.code_part(body[:a])):
                if body[pos:a].strip():
                    out.append(div(body[pos:a], pos, a))
                out.append(INCLUDE.format(child))
                pos = z
            else:
                late.append(child)
        if body[pos:].strip() or not out:
            out.append(div(body[pos:], pos, len(body)))
        return out + [INCLUDE.format(c) for c in late]

    def _src(self, body: str) -> str:
        return latex.code_block(body, self.nonce) if self.tex else body

    def old(self, body: str, change: str | None) -> None:
        if body.strip():
            attrs = _attrs(file=self.rel, c=change, n=self.nonce)
            self.parts.append(f"{self.fence} {{.scrv-old {attrs}}}\n{self._src(_historical(body, self.tex))}\n\n{self.fence}\n\n")

    def yaml(self, seg, first_file: bool) -> None:
        if first_file and seg.kind == "yaml":
            self.parts.append(self.cur[seg.b0 : seg.b1] + "\n\n")

    def inline(self, seg, marks: list[tuple[Unit, str | None]]) -> None:
        spliced, reverted = _splice(self.cur, seg.b0, seg.b1, marks, self.nonce, self.tex)
        cids = " ".join(sorted({c for _, c in marks if c}))
        pos = len(self.parts)
        self.checks.append((pos, spliced, self.cur[seg.b0 : seg.b1], reverted, cids))
        self.wrap(spliced, "scrv-b", seg.b0, seg.b1)
        self.spans[pos] = (len(self.parts) - pos, seg.b0, seg.b1)

    def finish(self) -> list[str]:
        for i in sorted(_verify(self.checks, self.fence, self.nonce, self.defs), reverse=True):
            pos, _, current, reverted, cids = self.checks[i]
            count, b0, b1 = self.spans[pos]
            old = _attrs(file=self.rel, c=cids, n=self.nonce)
            self.parts[pos : pos + count] = [
                f"{self.fence} {{.scrv-old {old}}}\n{self._src(_historical(reverted, self.tex))}\n\n{self.fence}\n\n",
                *self._emit(current, "scrv-b scrv-ins-block", b0, b1)]
        return self.parts


INCLUDE = "\x00scrv-include:{}\x00"
INCLUDE_RE = re.compile("\x00scrv-include:(.*)\x00")
FOOTNOTE_DEF = re.compile(r"^(\s{0,3})\[\^([^\]]+)\]:", re.M)


def _historical(body: str, tex: bool = False) -> str:
    """Old text shown for comparison must not define footnotes a second time."""
    return body if tex else FOOTNOTE_DEF.sub(r"\1\\[^\2]:", body)


def _edges(text: str) -> tuple[str, str, str]:
    core = text.strip()
    if not core:
        return text, "", ""
    i = text.index(core)
    return text[:i], core, text[i + len(core) :]


def _splice(cur: str, b0: int, b1: int, marked: list[tuple[Unit, str | None]], nonce: str,
            tex: bool = False) -> tuple[str, str]:
    spliced, reverted, pos = [], [], b0
    for u, cid in sorted(marked, key=lambda m: m[0].new):
        n0, n1 = u.new
        spliced.append(cur[pos:n0])
        reverted.append(cur[pos:n0])
        c = (f' c="{cid}"' if cid else "") + f' n="{nonce}"'
        o, n = _edges(u.old_text), _edges(u.new_text)
        spliced.append(o[0] or n[0])
        if o[1]:
            spliced.append(latex.mark("del", o[1], cid, nonce) if tex else f"[{o[1]}]{{.scrv-del{c}}}")
        if n[1]:
            spliced.append(latex.mark("ins", n[1], cid, nonce) if tex else f"[{n[1]}]{{.scrv-ins{c}}}")
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


def _verify(checks: list[tuple[int, str, str, str, str]], fence: str, nonce: str, defs: str = "") -> set[int]:
    """Return the indices of checks whose spliced redline does not round-trip.
    `defs` is the LaTeX definitions block when the file is LaTeX."""
    if not checks:
        return set()
    doc = [defs + "\n"] if defs else []
    for i, (_, spliced, cur, rev, _) in enumerate(checks):
        for kind, body in (("s", spliced), ("c", cur), ("r", rev)):
            body = latex.code_block(body, nonce) if defs else body
            doc.append(f"{fence} {{.scrv-g k=\"{i}{kind}\"}}\n{body}\n\n{fence}\n")
    try:
        parsed = pandoc_json("\n".join(doc), bool(defs), nonce)
    except RuntimeError:
        return set(range(len(checks)))
    pieces = {}
    for b in parsed["blocks"]:
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
    ops = align(old, cur, b.tex) if old is not None and old != cur else [Op("equal", [], guarded_segments(cur, b.tex), [])]
    for op in ops:
        if op.tag == "equal" or (op.tag == "pair" and not op.units):
            for seg in op.new:
                b.yaml(seg, first) if seg.kind in HIDDEN else b.wrap(cur[seg.b0 : seg.b1], "scrv-b", seg.b0, seg.b1)
        elif op.tag == "pair":
            b.inline(op.new[0], [(u, None) for u in op.units])
        else:
            b.old("\n\n".join(old[s.b0 : s.b1] for s in op.old if s.kind not in HIDDEN), None)
            for seg in op.new:
                b.yaml(seg, first) if seg.kind in HIDDEN else b.wrap(cur[seg.b0 : seg.b1], "scrv-b scrv-ins-block", seg.b0, seg.b1)


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

    for seg in guarded_segments(cur, b.tex):
        for p in blocks:
            if p[1] == p[2] and seg.a <= p[1] <= seg.b0:
                old_once(p)
        if seg.kind in HIDDEN:
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
    built: dict[str, list[str]] = {}
    included: set[str] = set()
    root_defs = ""

    def includes(parent: str, name: str) -> str | None:
        child = latex.resolve_input(root, parent, name)
        if child in ms.files and child != parent:
            included.add(child)
            return child
        return None

    for idx, rel in enumerate(ms.files):
        path = root / rel
        if not path.exists():
            built[rel] = [f"::: {{.scrv-missing}}\n*{rel} has been deleted.*\n:::\n\n"]
            continue
        cur = path.read_text()
        old = (gitutil.show(root, base, rel) or "") if base else None
        defs = ""
        if latex.is_tex(rel):
            if latex.is_root(cur):
                root_defs = latex.defs_block(cur, nonce)
            defs = latex.defs_block(cur, nonce) if latex.preamble(cur) or not root_defs else root_defs
        b = _Builder(fence_for(cur + (old or "")), rel, cur, nonce, defs, includes)
        if pending is not None:
            _author_view(b, pending.get(rel, []), idx == 0)
        else:
            _reviewer_view(b, old, idx == 0)
        built[rel] = b.finish()

    def emit(rel: str, seen: tuple[str, ...]) -> list[str]:
        out = []
        for part in built.get(rel, []):
            m = INCLUDE_RE.fullmatch(part)
            if not m:
                out.append(part)
            elif m.group(1) not in seen:
                out.extend(emit(m.group(1), seen + (m.group(1),)))
                if latex.is_tex(rel):
                    out.append(built[rel][0])
        return out

    return "".join(p for rel in ms.files if rel not in included for p in emit(rel, (rel,)))


def render(root: Path, ms: Manuscript, base: str | None = None,
           pending: dict[str, Pending] | None = None) -> tuple[str, str]:
    nonce = secrets.token_hex(8)
    html, warnings = to_html(root, ms, manuscript_markdown(root, ms, nonce, base, pending), nonce)
    return sanitize.clean(_tag_references(html, bib_entries(root, ms))), warnings


BIB_KEY = re.compile(r"\s*@(\w+)\s*[{(]\s*([^,\s]+)\s*,")
YAML_ID = re.compile(r"\s*(?:- )?id:\s*['\"]?([^'\"\s]+)")
JSON_ID = re.compile(r'"id"\s*:\s*"([^"]+)"')
REF_DIV = re.compile(r'<div id="ref-([^"]+)" class="csl-entry"')


def _json_id(entry: str) -> str | None:
    try:
        key = json.loads(entry).get("id")
    except (ValueError, AttributeError):
        return next(iter(JSON_ID.findall(entry)), None)
    return str(key) if key is not None else None


def _bib_spans(rel: str, lines: list[str]) -> list[tuple[int, int, str | None]]:
    """(first, last, key) for each entry of a BibTeX, CSL YAML or CSL JSON file, 0-based.
    Entries that share a line (compact CSL JSON) each get their own key but the same
    line range, since source ranges are whole lines."""
    if rel.endswith(".bib"):
        starts = [(i, m.group(2)) for i, line in enumerate(lines)
                  if (m := BIB_KEY.match(line)) and m.group(1).lower() not in ("comment", "string", "preamble")]
        ends = [s for s, _ in starts[1:]] + [len(lines)]
        return [(i, end - 1, key) for (i, key), end in zip(starts, ends)]
    if rel.endswith((".yaml", ".yml")):
        items = [i for i, line in enumerate(lines) if re.match(r"\s*- ", line)]
        if not items:
            return []
        indent = min(len(lines[i]) - len(lines[i].lstrip()) for i in items)
        tops = [i for i in items if len(lines[i]) - len(lines[i].lstrip()) == indent]
        out = []
        for i, end in zip(tops, tops[1:] + [len(lines)]):
            body = lines[i:end]
            stop = next((k for k, line in enumerate(body[1:], 1)
                         if line.strip() and len(line) - len(line.lstrip()) <= indent), len(body))
            key = next((m.group(1) for line in body[:stop] if (m := YAML_ID.match(line))), None)
            out.append((i, i + stop - 1, key))
        return out
    if rel.endswith(".json"):
        out, depth, start, in_str, esc, buf = [], 0, 0, False, False, []
        for i, line in enumerate(lines):
            for ch in line + "\n":
                if in_str:
                    in_str, esc = (not (ch == '"' and not esc)), (ch == "\\" and not esc)
                    if depth >= 2:
                        buf.append(ch)
                    continue
                if ch == '"':
                    in_str = True
                elif ch in "[{":
                    depth += 1
                    if depth == 2:
                        start, buf = i, []
                elif ch in "]}":
                    if depth == 2:
                        out.append((start, i, _json_id("".join(buf) + ch)))
                    depth -= 1
                if depth >= 2:
                    buf.append(ch)
        return out
    return []


def bib_entries(root: Path, ms: Manuscript) -> dict[str, tuple[str, int, int]]:
    """Each citation key's entry in the bibliography files, as (file, first line, last line)."""
    out: dict[str, tuple[str, int, int]] = {}
    for rel in ms.bibliography:
        p = root / rel
        if not p.is_file():
            continue
        lines = p.read_text().splitlines()
        for first, last, key in _bib_spans(rel, lines):
            while last > first and not lines[last].strip():
                last -= 1
            if key:
                out.setdefault(key, (rel, first + 1, last + 1))
    return out


def _tag_references(html: str, entries: dict[str, tuple[str, int, int]]) -> str:
    """Point each generated reference-list entry at its source, so it can be
    commented on and edited like a manuscript block."""
    def tag(m: re.Match) -> str:
        hit = entries.get(unescape(m.group(1)))
        if not hit:
            return m.group(0)
        rel, first, last = hit
        return (f'<div id="ref-{m.group(1)}" class="csl-entry scrv-b scrv-ref" '
                f'data-file="{escape(rel)}" data-lines="{first}-{last}"')
    return REF_DIV.sub(tag, html)


def tikz_cache(root: Path) -> Path:
    d = root / ".scrivmark" / "cache" / "tikz"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cite_cache(root: Path, keep: int = 40) -> Path:
    """Processed citations (citecache.lua), newest `keep` results."""
    d = root / ".scrivmark" / "cache" / "cites"
    d.mkdir(parents=True, exist_ok=True)
    old = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]
    for p in old:
        p.unlink(missing_ok=True)
    return d


@lru_cache(maxsize=1)
def _math_flag() -> str:
    """--mathml became --math-method=mathml in later pandoc releases."""
    help_text = run_pandoc(["pandoc", "--help"]).stdout
    return "--math-method=mathml" if "--math-method" in help_text else "--mathml"


def to_html(root: Path, ms: Manuscript, markdown: str, nonce: str) -> tuple[str, str]:
    if any(latex.is_tex(f) for f in ms.files):
        latex.require_reader()
    cmd = [
        "pandoc", "-f", PANDOC_FROM, "-t", "html5", _math_flag(), "--wrap=none", "--standalone",
        "--template", str(ASSETS / "template.html"),
        "--lua-filter", LATEX_LUA, "--metadata", f"scrv-cache={tikz_cache(root)}",
        "--lua-filter", str(ASSETS / "scrivmark.lua"),
        "--lua-filter", str(ASSETS / "citations.lua"), "--metadata", f"scrv-cites={cite_cache(root)}",
        "--metadata", "link-citations=true", "--metadata", "pagetitle=scrivmark",
        "--metadata", "reference-section-title=References", "--metadata", f"scrv-nonce={nonce}",
    ]
    for bib in ms.bibliography:
        cmd += ["--bibliography", str(root / bib)]
    if ms.csl:
        cmd += ["--csl", str(root / ms.csl)]
    proc = run_pandoc(cmd, markdown, cwd=root)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout, proc.stderr
