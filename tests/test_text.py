from scrivmark.blocks import guarded_segments, scan
from scrivmark.selection import map_selection
from scrivmark.textdiff import diff_units, projection

LOOSE = "Intro para.\n\n- one\n\n- two\n\n  continued\n\nTail [^1].\n\n[^1]: A note\n    over two lines.\n"


def test_segments_tile_the_file():
    segs = guarded_segments(LOOSE)
    assert segs[0].a == 0 and segs[-1].z == len(LOOSE)
    assert all(a.z == b.a for a, b in zip(segs, segs[1:]))


def test_guard_repairs_a_bad_split(monkeypatch):
    import scrivmark.blocks as b

    broken = "- one\n\n- two\n\n  continued\n"
    monkeypatch.setattr(b, "scan", lambda t: [(0, 1, "para"), (2, 3, "para"), (4, 5, "para")])
    segs = b.guarded_segments(broken)
    assert len(segs) == 1


def test_word_level_units_and_whitespace_ignored():
    old = "Detection was high\nacross sites.\n"
    new = "Detection was moderate across\nsites.\n"
    units = diff_units(old, new)
    assert [(u.old_text, u.new_text) for u in units] == [("high", "moderate")]


def test_projection_range_and_point():
    old = "alpha beta gamma delta\n"
    new = "ALPHA beta gamma delta\n"
    pr = projection(old, new)
    i = old.index("gamma")
    assert pr.range(i, i + 5) == (i, i + 5)
    assert pr.range(0, 5) is None
    assert pr.point(old.index("delta")) == new.index("delta")


def test_selection_maps_through_emphasis_and_citations():
    src = "We saw *very* strong effects [@smith2020] in the north.\n"
    s, e = map_selection(src, "very strong effects (Smith 2020) in the")
    assert src[s:e].startswith("very") and src[s:e].endswith("in the")
    s, e = map_selection(src, "strong effects")
    assert src[s:e] == "strong effects"
