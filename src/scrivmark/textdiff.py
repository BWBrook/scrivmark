"""Word-level diff units between two versions of a source file, and projection
of ranges from one version onto another."""

import re
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher

from .blocks import Segment, guarded_segments

TOKEN = re.compile(
    r"""
    (?P<cite>\[[^\[\]\n]*@[^\[\]\n]*\])
   |(?P<link>!?\[[^\[\]\n]*\]\([^()\s]*(?:\s+"[^"]*")?\)(?:\{[^{}\n]*\})?)
   |(?P<math>\$\$.+?\$\$|\$[^$\s](?:[^$\n]*[^$\s])?\$)
   |(?P<code>`[^`\n]*`)
   |(?P<attr>\{[#.][^{}\n]*\})
   |(?P<ref>@[A-Za-z][\w:.#$%&+?<>~/-]*\w)
   |(?P<word>\w+(?:['’\-]\w+)*)
   |(?P<space>\s+)
   |(?P<punct>.)
    """,
    re.X | re.S,
)
ATOMIC = {"cite", "link", "math", "code", "attr", "ref"}
TEXT_KINDS = {"para", "heading", "list", "footnote", "table", "div", "merged"}


@dataclass
class Token:
    text: str
    start: int
    kind: str


def tokens(text: str, base: int = 0) -> list[Token]:
    return [Token(m.group(), base + m.start(), m.lastgroup) for m in TOKEN.finditer(text)]


@dataclass
class Unit:
    type: str  # text | block
    old: tuple[int, int]
    new: tuple[int, int]
    old_text: str
    new_text: str


def _token_hunks(old: str, new: str, oseg: Segment, nseg: Segment) -> list[Unit]:
    ot = tokens(old[oseg.b0 : oseg.b1], oseg.b0)
    nt = tokens(new[nseg.b0 : nseg.b1], nseg.b0)
    sm = SequenceMatcher(None, [t.text for t in ot], [t.text for t in nt], autojunk=False)
    ops = sm.get_opcodes()
    groups: list[list[tuple]] = []
    cur: list[tuple] = []
    for idx, op in enumerate(ops):
        tag, i1, i2, _, _ = op
        if tag != "equal":
            cur.append(op)
        elif cur and idx < len(ops) - 1 and all(t.kind == "space" for t in ot[i1:i2]):
            cur.append(op)
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    units = []
    for g in groups:
        while g and g[-1][0] == "equal":
            g.pop()
        i1, j1 = g[0][1], g[0][3]
        i2, j2 = g[-1][2], g[-1][4]
        o0 = ot[i1].start if i1 < len(ot) else oseg.b1
        o1 = ot[i2 - 1].start + len(ot[i2 - 1].text) if i2 > i1 else o0
        n0 = nt[j1].start if j1 < len(nt) else nseg.b1
        n1 = nt[j2 - 1].start + len(nt[j2 - 1].text) if j2 > j1 else n0
        if i2 == i1 and i1 < len(ot):
            o0 = o1 = ot[i1].start
        if j2 == j1 and j1 < len(nt):
            n0 = n1 = nt[j1].start
        units.append(Unit("text", (o0, o1), (n0, n1), old[o0:o1], new[n0:n1]))
    return units


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).quick_ratio() > 0.5


def diff_units(old: str, new: str) -> list[Unit]:
    if old == new:
        return []
    os_, ns = guarded_segments(old), guarded_segments(new)
    okeys = [old[s.b0 : s.b1] for s in os_]
    nkeys = [new[s.b0 : s.b1] for s in ns]
    sm = SequenceMatcher(None, okeys, nkeys, autojunk=False)
    units: list[Unit] = []

    def block(i1: int, i2: int, j1: int, j2: int) -> None:
        o = (os_[i1].a, os_[i2 - 1].z) if i2 > i1 else (_point(os_, i1, len(old)),) * 2
        n = (ns[j1].a, ns[j2 - 1].z) if j2 > j1 else (_point(ns, j1, len(new)),) * 2
        units.append(Unit("block", o, n, old[o[0] : o[1]], new[n[0] : n[1]]))

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            for oi, nj in zip(range(i1, i2), range(j1, j2)):
                oseg, nseg = os_[oi], ns[nj]
                if oseg.kind in TEXT_KINDS and nseg.kind in TEXT_KINDS and _similar(okeys[oi], nkeys[nj]):
                    units.extend(_token_hunks(old, new, oseg, nseg))
                else:
                    block(oi, oi + 1, nj, nj + 1)
        else:
            block(i1, i2, j1, j2)
    return units


def _point(segs: list[Segment], i: int, end: int) -> int:
    return segs[i].a if i < len(segs) else end


class Projection:
    """Maps character offsets in `old` to `new` through their unchanged stretches."""

    def __init__(self, old: str, new: str):
        self.identity = old == new
        self.old_len = len(old)
        self.runs: list[tuple[int, int, int]] = []
        self.starts: list[int] = []
        if self.identity:
            return
        ol, nl = old.splitlines(keepends=True), new.splitlines(keepends=True)
        oo, no = _offsets(ol), _offsets(nl)
        sm = SequenceMatcher(None, ol, nl, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                self.runs.append((oo[i1], no[j1], oo[i2] - oo[i1]))
            elif tag == "replace":
                a, b = old[oo[i1] : oo[i2]], new[no[j1] : no[j2]]
                for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
                    if blk.size:
                        self.runs.append((oo[i1] + blk.a, no[j1] + blk.b, blk.size))
        self.runs.sort()
        merged: list[tuple[int, int, int]] = []
        for oa, na, n in self.runs:
            if merged and merged[-1][0] + merged[-1][2] == oa and merged[-1][1] + merged[-1][2] == na:
                merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + n)
            else:
                merged.append((oa, na, n))
        self.runs = merged
        self.starts = [r[0] for r in self.runs]

    def range(self, s: int, e: int) -> tuple[int, int] | None:
        """Project [s, e) if it lies wholly inside one unchanged stretch."""
        if self.identity:
            return s, e
        k = bisect_right(self.starts, s) - 1
        if k < 0:
            return None
        oa, na, n = self.runs[k]
        if oa <= s and e <= oa + n:
            return na + (s - oa), na + (e - oa)
        return None

    def point(self, p: int, margin: int = 8) -> int | None:
        """Project an insertion point whose surroundings are unchanged."""
        s, e = max(0, p - margin), min(self.old_len, p + margin)
        r = self.range(s, e)
        return None if r is None else r[0] + (p - s)


def projection(old: str, new: str) -> Projection:
    return Projection(old, new)


def _offsets(lines: list[str]) -> list[int]:
    out, pos = [], 0
    for line in lines:
        out.append(pos)
        pos += len(line)
    out.append(pos)
    return out
