"""From a practice round on a public 10,700-word LaTeX paper (the Astropy v2.0 paper), 16 November 2026
(docs/audits/2026-11-16-latex-round.md)."""
import base64

import pytest
from starlette.testclient import TestClient

from scrivmark import latex
from scrivmark.blocks import guarded_segments
from scrivmark.server import Viewer, build_app
from scrivmark.textdiff import diff_units

from conftest import edit
from test_latex import texrepo  # noqa: F401

pytestmark = pytest.mark.skipif(latex.reader_problem() is not None, reason=latex.reader_problem() or "")

PRE = "\\documentclass{article}\n\\usepackage{graphicx}\n% For commenting - can be deleted\n\\newcommand{\\pkg}[1]{\\texttt{#1}}\n\\begin{document}\n"
A = "Wombats dig burrows on north-facing slopes, where the winter sun warms the entrance by mid-morning."
B = "Occupancy was estimated from repeated visits to each burrow, with fresh sign taken as a detection."
C = "Detection fell in wet weather, so the surveys were confined to dry spells of three days or more."
FIG = ("\\begin{figure}\n\\includegraphics[width=\\textwidth]{aspect.pdf}\n\\caption{%\n    \\emph{Left panel}: Distribution of number "
       "of burrows per aspect class.\n    \\label{fig:aspect}\n}\n\\end{figure}")
DOC = (f"{PRE}\\section{{Introduction}}\n\\label{{sec:intro}}\n{A}\n\n{B}\n\n{C}\n\n\\section{{Organization of surveys}}\n\n\\label{{sec:org}}\n"
       f"Each transect was walked twice in winter.\n\n{FIG}\n\n\\end{{document}}\n")


def test_a_heading_and_the_label_on_the_lines_after_it_are_one_block():
    """Read apart, pandoc gives the heading no identifier, so the guard fused
    the heading with the paragraphs on both sides of it: 14 times in the
    practice paper."""
    segs = guarded_segments(DOC, True)
    assert "merged" not in [s.kind for s in segs]
    heads = [DOC[s.b0:s.b1] for s in segs if s.kind == "heading"]
    assert heads == ["\\section{Introduction}\n\\label{sec:intro}", "\\section{Organization of surveys}\n\n\\label{sec:org}"]


def test_a_paragraph_moved_to_just_before_a_labelled_heading_is_a_move_of_that_paragraph_alone():
    new = DOC.replace(f"{A}\n\n", "").replace(f"{C}\n\n", f"{C}\n\n{A}\n\n").replace("{Organization of", "{Organisation of")
    units = diff_units(DOC, new, True)
    assert [(u.type, u.old_text.strip(), u.new_text.strip()) for u in units] == [("move", A, A), ("text", "Organization", "Organisation")]


def test_a_one_line_edit_to_the_preamble_is_that_line():
    new = DOC.replace("can be deleted", "delete before submission")
    (u,) = diff_units(DOC, new, True)
    assert (u.type, u.old_text, u.new_text) == ("text", "% For commenting - can be deleted\n", "% For commenting - delete before submission\n")
    added = DOC.replace("\\usepackage{graphicx}\n", "\\usepackage{graphicx}\n\\usepackage{xspace}\n", 1)
    assert [(u.old_text, u.new_text) for u in diff_units(DOC, added, True)] == [("", "\\usepackage{xspace}\n")]


def test_a_one_line_edit_to_a_yaml_header_is_that_line():
    old = "---\ntitle: \"A test\"\nauthor: Sam\n---\n\nWombats dig burrows that shelter other species.\n"
    (u,) = diff_units(old, old.replace("A test", "A held-out test"))
    assert (u.old_text, u.new_text) == ("title: \"A test\"\n", "title: \"A held-out test\"\n")


def test_an_edit_to_a_figure_caption_is_a_word_level_change():
    new = DOC.replace("Distribution of number", "Distribution of the number")
    (u,) = diff_units(DOC, new, True)
    assert (u.type, u.old_text, u.new_text.strip()) == ("text", "", "the")


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def test_figures_are_found_under_graphicspath(texrepo):  # noqa: F811
    (texrepo / "tex" / "figures").mkdir()
    (texrepo / "tex" / "figures" / "burrow.png").write_bytes(PNG)
    edit(texrepo, "\\begin{document}", "\\graphicspath{{figures/}}\n\\begin{document}", "tex/main.tex")
    assert latex.graphics_paths(texrepo, ["tex/main.tex", "tex/methods.tex"]) == [("tex", "tex/figures")]
    viewer = Viewer(texrepo)
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    r = c.get("/files/tex/burrow.png")
    assert r.status_code == 200 and r.content == PNG
    assert c.get("/files/tex/absent.png").status_code == 404


def test_a_float_whose_environment_changed_is_one_change():
    """Codex review of 0.11.0, R1: \\begin{figure*} -> \\begin{figure} and its
    \\end were two changes, and rejecting one alone left mismatched LaTeX."""
    new = DOC.replace("\\begin{figure}", "\\begin{figure*}").replace("\\end{figure}", "\\end{figure*}")
    (u,) = diff_units(DOC, new, True)
    assert u.type == "block" and u.old_text.startswith("\\begin{figure}") and u.new_text.rstrip().endswith("\\end{figure*}")
    lists = "\\begin{document}\n\\begin{itemize}\n\\item Cameras at each burrow.\n\\item Occupancy models.\n\\end{itemize}\n\\end{document}\n"
    (u,) = diff_units(lists, lists.replace("itemize", "enumerate"), True)
    assert u.type == "block"


def test_a_deleted_yaml_title_is_not_shown_to_the_reviewer(repo):
    """Codex review of 0.11.0, R2."""
    from scrivmark import rounds
    p = repo / "paper.md"
    text = p.read_text()
    if not text.startswith("---\n"):
        text = "---\ntitle: Old title\nauthor: Sam\n---\n\n" + text
        p.write_text(text)
        from conftest import git
        git(repo, "commit", "-qam", "yaml")
    line = next(x for x in text.splitlines() if x.startswith("title:"))
    rounds.start(repo)
    p.write_text(text.replace(line + "\n", "", 1))
    html = Viewer(repo).state()["html"]
    assert line.split(":", 1)[1].strip().strip('"') not in html


def test_a_graphic_two_documents_could_mean_is_not_guessed(tmp_path):
    """Codex review of 0.11.0, R3."""
    from scrivmark import figures
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "result.png").write_bytes(PNG)
    assert figures.locate(tmp_path, "result.png", [(".", "a"), (".", "b")]) == "result.png"
    assert figures.locate(tmp_path, "result.png", [(".", "a")]) == "a/result.png"
