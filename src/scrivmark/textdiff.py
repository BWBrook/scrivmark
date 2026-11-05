"""Word-level diff units between two versions of a source file, and projection
of ranges from one version onto another."""

import re
from bisect import bisect_right
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache

from . import latex
from .blocks import Segment, guarded_segments, pandoc_blocks

TOKEN = re.compile(
    r"""
    (?P<fnref>\[\^[^\]\s]+\](?!:))
   |(?P<cite>\[[^\[\]\n]*@[^\[\]\n]*\])
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
WORDS = re.compile(r"\s+|\w+|[^\w\s]")
ATOMIC = {"fnref", "cite", "link", "math", "code", "attr", "ref"}
TEXT_KINDS = {"para", "heading", "list", "footnote", "table", "div", "merged"}


@dataclass
class Token:
    text: str
    start: int
    kind: str


def tokens(text: str, base: int = 0, tex: bool = False) -> list[Token]:
    rx = latex.TOKEN if tex else TOKEN
    return [Token(m.group(), base + m.start(), m.lastgroup) for m in rx.finditer(text)]


@dataclass
class Unit:
    type: str  # text | block
    old: tuple[int, int]
    new: tuple[int, int]
    old_text: str
    new_text: str


def _key(t: Token) -> str:
    return " " if t.kind == "space" else t.text


def _boundary(seq: list[Token], y: int) -> int:
    """How natural a place position y is for an edit to start or end."""
    if y <= 0 or y >= len(seq):
        return 6
    a, b = seq[y - 1], seq[y]
    space = next((t for t in (a, b) if t.kind == "space"), None)
    if space:
        return 4 if "\n" in space.text else 3
    return 1 if "punct" in (a.kind, b.kind) else 0


def _slide(ops: list[tuple], ot: list[Token], nt: list[Token]) -> list[tuple]:
    """Move each pure insertion or deletion to the equivalent position whose
    edges fall at spaces or line breaks, as diff-match-patch's semantic cleanup
    does, so "(0.40); ... between" is not reported as "0.40); ... between (" ."""
    ops = [list(o) for o in ops]
    for k in range(1, len(ops) - 1):
        tag = ops[k][0]
        if tag not in ("insert", "delete") or ops[k - 1][0] != "equal" or ops[k + 1][0] != "equal":
            continue
        seq, x1, x2 = (nt, ops[k][3], ops[k][4]) if tag == "insert" else (ot, ops[k][1], ops[k][2])
        before, after = ops[k - 1][2] - ops[k - 1][1], ops[k + 1][2] - ops[k + 1][1]
        best, top = 0, _boundary(seq, x1) + _boundary(seq, x2)
        s = 0
        while s < before and _key(seq[x1 - 1 - s]) == _key(seq[x2 - 1 - s]):
            s += 1
            score = _boundary(seq, x1 - s) + _boundary(seq, x2 - s)
            if score > top:
                best, top = -s, score
        s = 0
        while s < after and _key(seq[x1 + s]) == _key(seq[x2 + s]):
            s += 1
            score = _boundary(seq, x1 + s) + _boundary(seq, x2 + s)
            if score > top or (score == top and best != 0 and s < abs(best)):
                best, top = s, score
        if best:
            for idx in range(1, 5):
                ops[k][idx] += best
            ops[k - 1][2] += best
            ops[k - 1][4] += best
            ops[k + 1][1] += best
            ops[k + 1][3] += best
    return [tuple(o) for o in ops if o[0] != "equal" or o[2] > o[1]]


def _token_hunks(old: str, new: str, oseg: Segment, nseg: Segment, tex: bool = False) -> list[Unit]:
    ot = tokens(old[oseg.b0 : oseg.b1], oseg.b0, tex)
    nt = tokens(new[nseg.b0 : nseg.b1], nseg.b0, tex)
    sm = SequenceMatcher(None, [_key(t) for t in ot], [_key(t) for t in nt], autojunk=False)
    ops = _slide(sm.get_opcodes(), ot, nt)
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
        if old[o0:o1].strip() or new[n0:n1].strip():
            units.append(Unit("text", (o0, o1), (n0, n1), old[o0:o1], new[n0:n1]))
    return units


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).quick_ratio() > 0.5


@dataclass
class Op:
    tag: str  # equal | pair | block
    old: list[Segment]
    new: list[Segment]
    units: list[Unit]


def align(old: str, new: str, tex: bool = False) -> list[Op]:
    """Align the blocks of two texts. Paired prose blocks carry word-level
    units; everything else that differs is one block unit."""
    os_, ns = guarded_segments(old, tex), guarded_segments(new, tex)
    okeys = [old[s.b0 : s.b1] for s in os_]
    nkeys = [new[s.b0 : s.b1] for s in ns]
    sm = SequenceMatcher(None, okeys, nkeys, autojunk=False)
    ops: list[Op] = []

    def block(i1: int, i2: int, j1: int, j2: int) -> None:
        o = (os_[i1].a, os_[i2 - 1].z) if i2 > i1 else (_point(os_, i1, len(old)),) * 2
        n = (ns[j1].a, ns[j2 - 1].z) if j2 > j1 else (_point(ns, j1, len(new)),) * 2
        unit = Unit("block", o, n, old[o[0] : o[1]], new[n[0] : n[1]])
        ops.append(Op("block", os_[i1:i2], ns[j1:j2], [unit]))

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(Op("equal", os_[i1:i2], ns[j1:j2], []))
        elif tag == "replace" and i2 - i1 == j2 - j1:
            for oi, nj in zip(range(i1, i2), range(j1, j2)):
                oseg, nseg = os_[oi], ns[nj]
                if oseg.kind in TEXT_KINDS and nseg.kind in TEXT_KINDS and _similar(okeys[oi], nkeys[nj]):
                    units = _token_hunks(old, new, oseg, nseg, tex)
                    if _structure_differs(okeys[oi], _undo(new, nseg, units), tex):
                        block(oi, oi + 1, nj, nj + 1)
                    else:
                        ops.append(Op("pair", [oseg], [nseg], units))
                else:
                    block(oi, oi + 1, nj, nj + 1)
        else:
            block(i1, i2, j1, j2)
    return ops


def _undo(new: str, seg: Segment, units: list[Unit]) -> str:
    """The new block with every unit reversed. If the catalogue is complete,
    this has the same meaning as the old block."""
    text = new[seg.b0 : seg.b1]
    for u in sorted(units, key=lambda u: u.new, reverse=True):
        a, b = u.new[0] - seg.b0, u.new[1] - seg.b0
        text = text[:a] + u.old_text + text[b:]
    return text


def _structure_differs(a: str, b: str, tex: bool = False) -> bool:
    """Whether two texts differ in meaning (beyond reflowed whitespace),
    judged by pandoc."""
    soft = lambda j: j.replace('"t": "SoftBreak"', '"t": "Space"')  # noqa: E731
    try:
        return soft(pandoc_blocks(a, tex)) != soft(pandoc_blocks(b, tex))
    except (RuntimeError, FileNotFoundError):
        return True


def diff_units(old: str, new: str, tex: bool = False) -> list[Unit]:
    if old == new:
        return []
    return [u for op in align(old, new, tex) for u in op.units]


def _point(segs: list[Segment], i: int, end: int) -> int:
    return segs[i].a if i < len(segs) else end


class Projection:
    """Maps character offsets in `old` to `new` through their unchanged stretches."""

    def __init__(self, old: str, new: str):
        self.identity = old == new
        self.old_len = len(old)
        self.new_len = len(new)
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
                self._match(old, new, oo[i1], oo[i2], no[j1], no[j2])
        self.runs.sort()
        merged: list[tuple[int, int, int]] = []
        for oa, na, n in self.runs:
            if merged and merged[-1][0] + merged[-1][2] == oa and merged[-1][1] + merged[-1][2] == na:
                merged[-1] = (merged[-1][0], merged[-1][1], merged[-1][2] + n)
            else:
                merged.append((oa, na, n))
        self.runs = merged
        self.starts = [r[0] for r in self.runs]

    def _match(self, old: str, new: str, a0: int, a1: int, b0: int, b1: int) -> None:
        """Unchanged stretches within a replaced run of lines: words first, then
        characters within each changed gap. Character matching over a whole
        paragraph is quadratic and dominated anchor resolution."""
        ta, tb = WORDS.findall(old, a0, a1), WORDS.findall(new, b0, b1)
        ao, bo = _offsets(ta), _offsets(tb)
        for tag, x1, x2, y1, y2 in SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
            oa, na = a0 + ao[x1], b0 + bo[y1]
            if tag == "equal":
                self.runs.append((oa, na, ao[x2] - ao[x1]))
            elif tag == "replace":
                a, b = old[oa : a0 + ao[x2]], new[na : b0 + bo[y2]]
                for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
                    if blk.size:
                        self.runs.append((oa + blk.a, na + blk.b, blk.size))

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

    def around(self, s: int, e: int, solid: int = 12, slack: int = 80) -> tuple[int, int] | None:
        """Where [s, e) went if it was rewritten: the new text between the
        nearest unchanged stretches of at least `solid` characters on either
        side, provided each lies within `slack` characters of the passage. The
        start and end of the file count as unchanged. A passage deleted outright
        comes back as the empty range where it was."""
        if self.identity:
            return s, e
        left = 0 if s <= slack else None
        right = self.new_len if self.old_len - e <= slack else None
        for oa, na, n in self.runs:
            if n < solid:
                continue
            if oa < s and oa + n >= s - slack:
                left = na + min(n, s - oa)
            if oa + n > e and oa <= e + slack and (right is None or right == self.new_len):
                right = na + max(0, e - oa)
        if left is None or right is None or left > right:
            return None
        return left, right

    def point(self, p: int, margin: int = 8) -> int | None:
        """Project an insertion point whose surroundings are unchanged."""
        s, e = max(0, p - margin), min(self.old_len, p + margin)
        r = self.range(s, e)
        return None if r is None else r[0] + (p - s)


@lru_cache(maxsize=64)
def projection(old: str, new: str) -> Projection:
    return Projection(old, new)


def _offsets(lines: list[str]) -> list[int]:
    out, pos = [], 0
    for line in lines:
        out.append(pos)
        pos += len(line)
    out.append(pos)
    return out
