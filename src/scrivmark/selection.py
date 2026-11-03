"""Map text selected in the rendered page back to a source range.

The block's source is reduced to plain text with a map back to source offsets:
emphasis markers and link targets drop out, and generated material (citations,
cross-references, maths) becomes one placeholder standing for its whole token.
The selection is then found by its start and end in that plain text. A
selection that cannot be placed exactly falls back to the whole block.
"""

import re

from .textdiff import tokens

GENERATED = {"cite", "ref", "math"}
PLACEHOLDER = "￼"


def _plain(src: str) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []

    def emit(ch: str, s: int, e: int) -> None:
        if ch.isspace():
            if chars and chars[-1] == " ":
                return
            ch = " "
        chars.append(ch)
        spans.append((s, e))

    line_start = True
    for t in tokens(src):
        if t.kind in GENERATED:
            emit(PLACEHOLDER, t.start, t.start + len(t.text))
        elif t.kind == "link":
            m = re.match(r"!?\[([^\]]*)\]", t.text)
            inner = m.group(1) if m else ""
            off = t.text.index(inner) if inner else 0
            for i, ch in enumerate(inner):
                if ch not in "*_`":
                    emit(ch, t.start + off + i, t.start + off + i + 1)
        elif t.kind == "attr":
            continue
        elif t.kind == "code":
            for i, ch in enumerate(t.text.strip("`")):
                emit(ch, t.start + 1 + i, t.start + 2 + i)
        else:
            for i, ch in enumerate(t.text):
                if ch in "*_\\`" or (line_start and ch in "#>|-+"):
                    continue
                emit(ch, t.start + i, t.start + i + 1)
        line_start = t.kind == "space" and "\n" in t.text
    return "".join(chars), spans


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _find_start(plain: str, sel: str, before: str) -> int | None:
    for k in range(min(24, len(sel)), 3, -1):
        hits = [m.start() for m in re.finditer(re.escape(sel[:k]), plain)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            b = _norm(before)
            def score(i: int) -> int:
                pre = plain[:i].rstrip()
                n = 0
                while n < min(len(pre), len(b)) and pre[len(pre) - 1 - n] == b[len(b) - 1 - n]:
                    n += 1
                return n
            ranked = sorted(hits, key=score, reverse=True)
            if len(ranked) == 1 or score(ranked[0]) > score(ranked[1]):
                return ranked[0]
            return None
    return None


def _find_end(plain: str, sel: str, start: int) -> int | None:
    """The end whose span length best matches the selection; refuse ties."""
    for k in range(min(24, len(sel)), 3, -1):
        ends = [m.start() + k for m in re.finditer(re.escape(sel[-k:]), plain) if m.start() >= start]
        if not ends:
            continue
        ranked = sorted(ends, key=lambda x: abs((x - start) - len(sel)))
        if len(ranked) > 1 and abs((ranked[0] - start) - len(sel)) == abs((ranked[1] - start) - len(sel)):
            return None
        return ranked[0]
    return None


def map_selection(src: str, text: str, before: str = "") -> tuple[int, int] | None:
    """Offsets within `src` of the rendered selection `text`, or None."""
    plain, spans = _plain(src)
    sel = _norm(text)
    if len(sel) < 2:
        return None
    if sel in plain and plain.count(sel) == 1:
        i = plain.index(sel)
        return spans[i][0], spans[i + len(sel) - 1][1]
    start = _find_start(plain, sel, before)
    if start is None:
        return None
    end = _find_end(plain, sel, start)
    if end is None or end <= start:
        return None
    return spans[start][0], spans[end - 1][1]
