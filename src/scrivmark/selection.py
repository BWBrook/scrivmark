"""Map text selected in the rendered page back to a source range.

The block's source is reduced to plain text with a map back to source offsets:
emphasis markers and link targets drop out, and generated material (citations,
cross-references, maths) becomes one placeholder standing for its whole token.
The selection is then found by its start and end in that plain text. A
selection that cannot be placed exactly falls back to the whole block.
"""

import re

from .textdiff import tokens

GENERATED = {"fnref", "cite", "ref", "math"}
TYPOGRAPHY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                            "\u2013": "-", "\u2014": "-", "\u00a0": " "})
PLACEHOLDER = "￼"


def _plain(src: str) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []

    def emit(ch: str, s: int, e: int) -> None:
        ch = ch.translate(TYPOGRAPHY)
        if ch.isspace():
            if chars and chars[-1] == " ":
                return
            ch = " "
        if ch == "-" and chars and chars[-1] == "-" and spans[-1][1] == s:
            spans[-1] = (spans[-1][0], e)
            return
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
    s = s.translate(TYPOGRAPHY).replace("\u2026", "...")
    return re.sub(r"\s+", " ", s).strip()


def _before_score(plain: str, i: int, before: str) -> int:
    pre, b = plain[:i].rstrip(), _norm(before)
    n = 0
    while n < min(len(pre), len(b)) and pre[len(pre) - 1 - n] == b[len(b) - 1 - n]:
        n += 1
    return n


def map_selection(src: str, text: str, before: str = "") -> tuple[int, int] | None:
    """Offsets within `src` of the rendered selection, or None when it cannot
    be placed exactly (the caller then anchors to the whole block).

    The page sends the selection with generated material (citations,
    cross-references, maths, footnote marks) already replaced by the
    placeholder, so a faithful selection matches the plain source exactly.
    """
    plain, spans = _plain(src)
    sel = _norm(text)
    if len(sel) < 2:
        return None
    hits = [m.start() for m in re.finditer(re.escape(sel), plain)]
    if len(hits) > 1 and before:
        ranked = sorted(hits, key=lambda i: _before_score(plain, i, before), reverse=True)
        if _before_score(plain, ranked[0], before) > _before_score(plain, ranked[1], before):
            hits = ranked[:1]
    if len(hits) != 1:
        return None
    i = hits[0]
    return spans[i][0], spans[i + len(sel) - 1][1]
