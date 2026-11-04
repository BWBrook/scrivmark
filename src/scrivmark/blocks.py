"""Split Markdown source into top-level blocks that partition the file.

A segment covers text[a:z]; consecutive segments tile the whole file. Its block
text is text[b0:b1]; the rest of the segment is the blank lines after it (and,
for the first segment, any blank lines before it).

The splitter is a fast line scanner. `guarded_segments` then checks it against
pandoc: wrapping each block in a fenced div and unwrapping again must give the
same AST as the original. Where it does not, neighbouring blocks are merged
until it does.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

from . import latex

LIST_MARKER = re.compile(r"^\s{0,3}([-*+]|\d+[.)]|#[.)]|\(?[a-zA-Z][.)])\s+")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
DIV_OPEN = re.compile(r"^\s{0,3}(:{3,})\s*\S")
DIV_CLOSE = re.compile(r"^\s{0,3}(:{3,})\s*$")
FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:")
COLON_RUN = re.compile(r"^\s*(:{3,})", re.M)

PANDOC_FROM = "markdown-raw_html-raw_attribute"
LATEX_LUA = str(files("scrivmark") / "assets" / "latex.lua")
HIDDEN = {"yaml", "preamble", "postamble"}


@dataclass(frozen=True)
class Segment:
    a: int
    b0: int
    b1: int
    z: int
    kind: str


def _lines(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    return lines, offsets


def _blank(line: str) -> bool:
    return not line.strip()


def scan(text: str) -> list[tuple[int, int, str]]:
    """Return (first line, last line exclusive, kind) for each block."""
    lines, _ = _lines(text)
    n = len(lines)
    blocks: list[tuple[int, int, str]] = []
    i = 0
    if n and lines[0].strip() == "---":
        for j in range(1, n):
            if lines[j].strip() in ("---", "..."):
                blocks.append((0, j + 1, "yaml"))
                i = j + 1
                break
    while i < n:
        if _blank(lines[i]):
            i += 1
            continue
        s, line = i, lines[i]
        if m := FENCE.match(line):
            mark = m.group(1)
            j = i + 1
            while j < n and not re.match(rf"^\s{{0,3}}{mark[0]}{{{len(mark)},}}\s*$", lines[j]):
                j += 1
            i = min(j + 1, n)
            blocks.append((s, i, "fence"))
            continue
        if DIV_OPEN.match(line):
            depth, j = 1, i + 1
            while j < n and depth:
                if DIV_CLOSE.match(lines[j]):
                    depth -= 1
                elif DIV_OPEN.match(lines[j]):
                    depth += 1
                j += 1
            i = j
            blocks.append((s, i, "div"))
            continue
        if line.lstrip().startswith("<!--"):
            j = i
            while j < n and "-->" not in lines[j]:
                j += 1
            i = min(j + 1, n)
            blocks.append((s, i, "comment"))
            continue
        if line.lstrip().startswith("#") and re.match(r"^\s{0,3}#{1,6}(\s|$)", line):
            i += 1
            blocks.append((s, i, "heading"))
            continue
        kind = "list" if LIST_MARKER.match(line) else "footnote" if FOOTNOTE_DEF.match(line) else "para"
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            kind = "table"
        j = i
        while True:
            while j < n and not _blank(lines[j]):
                j += 1
            k = j
            while k < n and _blank(lines[k]):
                k += 1
            if k < n and kind in ("list", "footnote") and (
                lines[k][:1] in (" ", "\t") or (kind == "list" and LIST_MARKER.match(lines[k]))
            ):
                j = k
                continue
            if k < n and kind == "table" and re.match(r"^\s*:\s", lines[k]):
                j = k
                continue
            break
        i = j
        blocks.append((s, i, kind))
    return blocks


def _to_segments(text: str, blocks: list[tuple[int, int, str]]) -> list[Segment]:
    lines, offsets = _lines(text)
    end = len(text)

    def off(line_no: int) -> int:
        return offsets[line_no] if line_no < len(offsets) else end

    segs = []
    for idx, (s, e, kind) in enumerate(blocks):
        b0 = off(s)
        b1 = off(e)
        while b1 > b0 and text[b1 - 1] == "\n":
            b1 -= 1
        a = 0 if idx == 0 else segs[-1].z
        z = off(blocks[idx + 1][0]) if idx + 1 < len(blocks) else end
        segs.append(Segment(a, b0, b1, z, kind))
    return segs


def fence_for(text: str) -> str:
    runs = [len(m.group(1)) for m in COLON_RUN.finditer(text)]
    return ":" * max(8, (max(runs) if runs else 0) + 3)


def pandoc_json(markdown: str, tex: bool = False) -> dict:
    """Pandoc's AST for scrivmark's Markdown, with LaTeX code blocks read."""
    cmd = ["pandoc", "-f", PANDOC_FROM, "-t", "json"] + (["--lua-filter", LATEX_LUA] if tex else [])
    proc = subprocess.run(cmd, input=markdown, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return json.loads(proc.stdout)


@lru_cache(maxsize=256)
def pandoc_blocks(text: str, tex: bool = False, defs: str = "") -> str:
    if tex:
        return json.dumps(pandoc_json(defs + "\n\n" + latex.code_block(text), True)["blocks"])
    proc = subprocess.run(
        ["pandoc", "-f", PANDOC_FROM, "-t", "json"], input=text, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return json.dumps(json.loads(proc.stdout)["blocks"])


def _strip_wrappers(blocks: list) -> tuple[list, list[int]]:
    flat, counts = [], []
    for b in blocks:
        if b["t"] == "Div" and "scrv-b" in b["c"][0][1]:
            flat.extend(b["c"][1])
            counts.append(len(b["c"][1]))
        else:
            flat.append(b)
            counts.append(1)
    return flat, counts


def _wrap(text: str, segs: list[Segment], fence: str, tex: bool = False) -> str:
    parts = []
    for i, s in enumerate(segs):
        body = text[s.b0 : s.b1]
        if s.kind == "yaml":
            parts.append(body + "\n\n")
        elif s.kind in HIDDEN:
            continue
        else:
            parts.append(f"{fence} {{.scrv-b}}\n{latex.code_block(body) if tex else body}\n\n{fence}\n\n")
    return "".join(parts)


def _merge(segs: list[Segment], i: int, j: int) -> list[Segment]:
    first, last = segs[i], segs[j]
    merged = Segment(first.a, first.b0, last.b1, last.z, "merged")
    return segs[:i] + [merged] + segs[j + 1 :]


def guarded_segments(text: str, tex: bool = False) -> list[Segment]:
    return list(_guarded(text, tex))


@lru_cache(maxsize=64)
def _guarded(text: str, tex: bool) -> tuple[Segment, ...]:
    return tuple(_guard(text, tex))


def _guard(text: str, tex: bool) -> list[Segment]:
    segs = _to_segments(text, latex.scan(text) if tex else scan(text))
    if not segs:
        return segs
    defs = latex.defs_block(text) if tex else ""
    try:
        if tex:
            shown_segs = [s for s in segs if s.kind not in HIDDEN]
            if not shown_segs:
                return segs
            shown = text[shown_segs[0].b0 : shown_segs[-1].b1]
            target = json.loads(pandoc_blocks(shown, True, defs))
        else:
            target = json.loads(pandoc_blocks(text))
    except (RuntimeError, FileNotFoundError):
        return segs
    fence = fence_for(text)
    for _ in range(len(segs) + 1):
        doc = _wrap(text, segs, fence, tex)
        wrapped = pandoc_json(defs + "\n\n" + doc, True)["blocks"] if tex else json.loads(pandoc_blocks(doc))
        flat, counts = _strip_wrappers(wrapped)
        if flat == target:
            return segs
        k = next((x for x, (p, q) in enumerate(zip(flat, target)) if p != q), min(len(flat), len(target)))
        body = [i for i, s in enumerate(segs) if s.kind not in HIDDEN]
        if len(counts) != len(body):
            break
        cum, culprit = 0, body[-1]
        for i, c in zip(body, counts):
            cum += c
            if cum > k:
                culprit = i
                break
        lo = max(culprit - 1, body[0])
        hi = min(culprit + 1, body[-1])
        if lo == hi:
            break
        segs = _merge(segs, lo, hi)
    body = [s for s in segs if s.kind not in HIDDEN]
    if not body:
        return segs
    merged = Segment(body[0].a, body[0].b0, body[-1].b1, body[-1].z, "merged")
    return [s for s in segs if s.kind in HIDDEN and s.b0 < body[0].b0] + [merged] + \
        [s for s in segs if s.kind in HIDDEN and s.b0 > body[-1].b0]


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1
