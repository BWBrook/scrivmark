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
    rewritten = _rewritten(ops, ot, nt, by_line=oseg.kind in ("list", "table"))
    groups: list[list[tuple]] = []
    cur: list[tuple] = []
    weight = 0
    for idx, op in enumerate(ops):
        tag, i1, i2, j1, j2 = op
        if tag != "equal":
            cur.append(op)
            weight += max(_words(ot[i1:i2]), _words(nt[j1:j2]))
        elif cur and idx < len(ops) - 1 and (idx in rewritten or _joins(ot[i1:i2], weight, ops[idx + 1], ot, nt)):
            cur.append(op)
        elif cur:
            groups.append(cur)
            cur, weight = [], 0
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


def _words(ts: list[Token]) -> int:
    return sum(t.kind not in ("space", "punct") for t in ts)


def _joins(kept: list[Token], before: int, nxt: tuple, ot: list[Token], nt: list[Token]) -> bool:
    """Whether the unchanged stretch `kept` between two edits belongs to one
    rewrite: it is only whitespace, or it is a few words inside one clause
    (commas allowed, other punctuation not), fewer than the edits on both
    sides of it. "Wombats clearly prefer the
    sunniest aspect" rewritten around a surviving "the" is one change to
    decide, not two whose halves make no sense apart."""
    if all(t.kind == "space" for t in kept):
        return True
    n = _words(kept)
    if n > 3 or any(t.kind == "space" and "\n\n" in t.text for t in kept):
        return False
    if any(t.kind == "punct" and t.text != "," for t in kept):
        return False
    after = max(_words(ot[nxt[1] : nxt[2]]), _words(nt[nxt[3] : nxt[4]]))
    return n < min(before, after)


def _ends_sentence(ts: list[Token], by_line: bool) -> bool:
    for k, t in enumerate(ts):
        if t.kind == "space" and ("\n\n" in t.text or (by_line and "\n" in t.text)):
            return True
        if t.kind == "punct" and t.text in ".?!" and (k + 1 == len(ts) or ts[k + 1].kind == "space"):
            return True
    return False


def _rewritten(ops: list[tuple], ot: list[Token], nt: list[Token], by_line: bool) -> set[int]:
    """The unchanged stretches that lie inside a rewritten sentence: one with
    two or more edits that together change four words or more, and at least as
    many as they leave standing between them. Such a sentence is one change to
    decide; two word swaps in a short sentence stay two. In a list or table a
    line counts as a sentence."""
    spans: list[list[int]] = [[]]
    for idx, (tag, i1, i2, _, _) in enumerate(ops):
        if tag != "equal":
            spans[-1].append(idx)
        elif _ends_sentence(ot[i1:i2], by_line):
            spans.append([])
    out: set[int] = set()
    for edits in spans:
        if len(edits) < 2:
            continue
        between = [k for k in range(edits[0], edits[-1]) if ops[k][0] == "equal"]
        changed = sum(max(_words(ot[ops[k][1] : ops[k][2]]), _words(nt[ops[k][3] : ops[k][4]])) for k in edits)
        if changed >= max(4, sum(_words(ot[ops[k][1] : ops[k][2]]) for k in between)):
            out.update(between)
    return out


PAIR = 0.3
REFLOW = 0.6
WORD = re.compile(r"\w+")


def _ratio(a: list[str], b: list[str]) -> float:
    """How alike two blocks are, by their words in order: 0 if below PAIR."""
    if not a or not b:
        return 0.0
    sm = SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < PAIR or sm.quick_ratio() < PAIR:
        return 0.0
    r = sm.ratio()
    return r if r >= PAIR else 0.0


def _reflow(one: list[str], first: list[str], second: list[str]) -> float:
    """Whether one block was split into two (or two joined into one): most of
    each piece's words are found, in order, in the single block."""
    if not one or not first or not second:
        return 0.0
    sm = SequenceMatcher(None, one, first + second, autojunk=False)
    cut = len(first)
    in_first = sum(max(0, min(m.b + m.size, cut) - m.b) for m in sm.get_matching_blocks())
    in_second = sum(max(0, m.b + m.size - max(m.b, cut)) for m in sm.get_matching_blocks())
    if in_first < REFLOW * len(first) or in_second < REFLOW * len(second):
        return 0.0
    return sm.ratio()


def _match(okeys: list[str], nkeys: list[str], texty: list[bool], ntexty: list[bool]) -> list[tuple[str, int, int, int, int]]:
    """Pair the blocks of a changed region by likeness, in order, where a
    positional pairing would set a moved or inserted paragraph against an
    unrelated neighbour. Steps are ("same" | "pair" | "block", i1, i2, j1, j2);
    a block step covers what was removed, added, split or joined."""
    n, m = len(okeys), len(nkeys)
    if n * m > 900:
        return [("block", 0, n, 0, m)]
    ow = [WORD.findall(k.lower()) if t else [] for k, t in zip(okeys, texty)]
    nw = [WORD.findall(k.lower()) if t else [] for k, t in zip(nkeys, ntexty)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    how: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
    for a in range(n + 1):
        for b in range(m + 1):
            if a == 0 and b == 0:
                continue
            options = []
            if a:
                options.append((best[a - 1][b], "del"))
            if b:
                options.append((best[a][b - 1], "ins"))
            if a and b:
                if okeys[a - 1] == nkeys[b - 1]:
                    options.append((best[a - 1][b - 1] + 1.0, "same"))
                elif r := _ratio(ow[a - 1], nw[b - 1]):
                    options.append((best[a - 1][b - 1] + r, "pair"))
            if a and b >= 2 and (r := _reflow(ow[a - 1], nw[b - 2], nw[b - 1])):
                options.append((best[a - 1][b - 2] + 2 * r, "split"))
            if a >= 2 and b and (r := _reflow(nw[b - 1], ow[a - 2], ow[a - 1])):
                options.append((best[a - 2][b - 1] + 2 * r, "join"))
            best[a][b], how[a][b] = max(options, key=lambda o: o[0])
    steps: list[tuple[str, int, int, int, int]] = []
    a, b = n, m
    while a or b:
        da, db = {"del": (1, 0), "ins": (0, 1), "same": (1, 1), "pair": (1, 1), "split": (1, 2), "join": (2, 1)}[how[a][b]]
        kind = how[a][b] if how[a][b] in ("same", "pair") else "block"
        steps.append((kind, a - da, a, b - db, b))
        a, b = a - da, b - db
    steps.reverse()
    out: list[tuple[str, int, int, int, int]] = []
    for st in steps:
        loose = st[0] == "block" and (st[2] - st[1]) + (st[4] - st[3]) == 1
        if loose and out and out[-1][0] == "gap":
            out[-1] = ("gap", out[-1][1], st[2], out[-1][3], st[4])
        else:
            out.append(("gap" if loose else st[0], *st[1:]))
    return [("block" if k == "gap" else k, *rest) for k, *rest in out]


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
        # The blank line before a block belongs to the block before it. Where
        # that differs between the versions (the file's last paragraph removed,
        # or one added after it), the change takes it in, or rejecting a
        # removed last paragraph would put it back joined to the one above.
        if i1 and j1 and ops and ops[-1].tag != "block":
            po, pn = os_[i1 - 1].b1, ns[j1 - 1].b1
            if old[po : o[0]] != new[pn : n[0]]:
                o, n = (po, o[1]), (pn, n[1])
        unit = Unit("block", o, n, old[o[0] : o[1]], new[n[0] : n[1]])
        ops.append(Op("block", os_[i1:i2], ns[j1:j2], [unit]))

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(Op("equal", os_[i1:i2], ns[j1:j2], []))
        elif tag == "replace":
            steps = _match(okeys[i1:i2], nkeys[j1:j2], [s.kind in TEXT_KINDS for s in os_[i1:i2]],
                           [s.kind in TEXT_KINDS for s in ns[j1:j2]])
            for kind, a1, a2, b1, b2 in steps:
                oi, nj = i1 + a1, j1 + b1
                if kind == "same":
                    ops.append(Op("equal", os_[oi : oi + 1], ns[nj : nj + 1], []))
                elif kind == "pair":
                    oseg, nseg = os_[oi], ns[nj]
                    units = _token_hunks(old, new, oseg, nseg, tex)
                    if _structure_differs(okeys[oi], _undo(new, nseg, units), tex):
                        block(oi, oi + 1, nj, nj + 1)
                    else:
                        ops.append(Op("pair", [oseg], [nseg], units))
                else:
                    block(oi, i1 + a2, nj, j1 + b2)
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
