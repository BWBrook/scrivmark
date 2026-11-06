import json
import re

import pytest

from scrivmark import blocks, config, latex, render
from scrivmark.errors import ScrivError

REAL_READER = latex.reader_problem() is None
needs_reader = pytest.mark.skipif(not REAL_READER, reason=latex.reader_problem() or "")

SENTINEL = "AUDIT_SENTINEL_ONLY_NOT_USER_DATA"


@pytest.fixture
def sentinel(tmp_path):
    p = tmp_path / "outside" / "sentinel.txt"
    p.parent.mkdir()
    p.write_text(SENTINEL)
    return p


def _root(tmp_path, files: dict[str, str]):
    root = tmp_path / "ms"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _html(root, order, bib=()):
    return render.render(root, config.Manuscript(root, list(order), list(bib)))[0]


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _tex(preamble: str, body: str) -> str:
    return f"\\documentclass{{article}}\n{preamble}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


@needs_reader
def test_macro_in_preamble_cannot_read_a_file(tmp_path, sentinel):
    src = _tex(f"\\newcommand{{\\sample}}{{\\lstinputlisting{{{sentinel}}}}}", "Before \\sample{} after.")
    root = _root(tmp_path, {"m.tex": src})
    assert SENTINEL not in _html(root, ["m.tex"])


@needs_reader
def test_direct_lstinputlisting_cannot_read_a_file(tmp_path, sentinel):
    root = _root(tmp_path, {"m.tex": _tex("", f"Before \\lstinputlisting{{{sentinel}}} after.")})
    assert SENTINEL not in _html(root, ["m.tex"])


@pytest.mark.parametrize("fake_nonce", ["", ' n="guess"', ' n=""'])
def test_markdown_cannot_reach_the_latex_reader(tmp_path, sentinel, fake_nonce):
    md = (f"``` {{.scrv-texdefs{fake_nonce}}}\n\\newcommand{{\\sample}}{{\\lstinputlisting{{{sentinel}}}}}\n```\n\n"
          f"``` {{.scrv-tex{fake_nonce}}}\nBefore \\sample{{}} after.\n```\n")
    root = _root(tmp_path, {"m.md": md})
    html = _html(root, ["m.md"])
    assert SENTINEL not in html and "Before \\sample" in _text(html)


def test_latex_is_refused_on_old_pandoc_but_markdown_still_renders(tmp_path, monkeypatch, sentinel):
    monkeypatch.setattr(latex, "pandoc_version", lambda: (3, 1, 3))
    root = _root(tmp_path, {"m.tex": _tex("", "Plain text."), "p.md": "Hello *there*.\n"})
    with pytest.raises(ScrivError, match=r"pandoc 3\.7 or later.*you have 3\.1\.3"):
        _html(root, ["m.tex"])
    with pytest.raises(ScrivError, match="pandoc 3.7 or later"):
        blocks.guarded_segments("\\begin{document}\nUnique refusal text.\n\\end{document}\n", True)
    assert "there" in _html(root, ["p.md"])
    assert "3.7" in latex.reader_problem()


@needs_reader
def test_input_without_blank_lines_renders_at_the_include_position(tmp_path):
    main = _tex("\\newcommand{\\animal}{wombat}", "Before.\n\\input{child}\nAfter.")
    root = _root(tmp_path, {"main.tex": main, "child.tex": "Middle \\animal{} child.\n"})
    html = _html(root, ["main.tex", "child.tex"])
    t = _text(html)
    assert t.index("Before.") < t.index("Middle wombat child.") < t.index("After.")
    tags = re.findall(r"<[^>]*data-file=[^>]*>", html)
    lines = [(re.search(r'data-file="([^"]+)"', x).group(1), re.search(r'data-lines="([^"]+)"', x).group(1))
             for x in tags if "data-lines=" in x]
    assert ("main.tex", "4-4") in lines and ("main.tex", "6-6") in lines and ("child.tex", "1-1") in lines


@needs_reader
def test_two_and_nested_includes_keep_their_order(tmp_path):
    main = _tex("", "One.\n\\input{a}\nTwo.\n\\input{b}\nThree.")
    root = _root(tmp_path, {"main.tex": main, "a.tex": "A1.\n\\input{c}\nA2.\n", "b.tex": "B.\n", "c.tex": "C.\n"})
    t = _text(_html(root, ["main.tex", "a.tex", "b.tex", "c.tex"]))
    order = ["One.", "A1.", "C.", "A2.", "Two.", "B.", "Three."]
    assert [t.index(x) for x in order] == sorted(t.index(x) for x in order)
    assert all(t.count(x) == 1 for x in order)


@needs_reader
def test_includes_sharing_a_line_with_text_follow_their_block(tmp_path):
    main = _tex("", "Start \\input{a} middle \\input{b} end.")
    root = _root(tmp_path, {"main.tex": main, "a.tex": "AAA\n", "b.tex": "BBB\n"})
    t = _text(_html(root, ["main.tex", "a.tex", "b.tex"]))
    order = ["Start", "middle", "end.", "AAA", "BBB"]
    assert [t.index(x) for x in order] == sorted(t.index(x) for x in order)


@needs_reader
def test_a_macro_defined_before_an_include_in_the_same_block_survives(tmp_path):
    main = _tex("", "\\newcommand{\\animal}{wombat}\nBefore.\n\\input{child}\nAfter \\animal{}.")
    root = _root(tmp_path, {"main.tex": main, "child.tex": "Child.\n"})
    assert "After wombat." in _text(_html(root, ["main.tex", "child.tex"]))


def test_compact_csl_json_maps_every_entry(tmp_path):
    root = _root(tmp_path, {"r.json": json.dumps([{"id": "first", "title": "One"}, {"id": "second", "title": "Two"}])})
    entries = render.bib_entries(root, config.Manuscript(root, ["m.md"], ["r.json"]))
    assert entries == {"first": ("r.json", 1, 1), "second": ("r.json", 1, 1)}


def test_double_quote_and_backslash_in_a_filename(tmp_path):
    name = 'odd"na\\me.md'
    root = _root(tmp_path, {name: "Some prose here.\n"})
    html = _html(root, [name])
    assert "Some prose here." in html and 'data-lines="1-1"' in html
    assert "{.scrv-b" not in html and "scrv-b" in html
    assert f'data-file="{name.replace(chr(34), "&quot;")}"' in html


def test_pandoc_timeout_is_reported(tmp_path, monkeypatch):
    root = _root(tmp_path, {"p.md": "Hello.\n"})
    monkeypatch.setattr(blocks, "PANDOC_TIMEOUT", 0.001)
    with pytest.raises(ScrivError, match="timeout"):
        render.to_html(root, config.Manuscript(root, ["p.md"]), "Hello.\n", "abc")


def test_unprinted_note_cannot_be_faked_from_markdown(tmp_path):
    root = _root(tmp_path, {"p.md": "Printed [prose]{.scrvtexnote} and [more]{.scrv-note}.\n"})
    assert "scrv-note" not in _html(root, ["p.md"])
