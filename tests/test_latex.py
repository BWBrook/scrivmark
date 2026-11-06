import re
import shutil
import subprocess

import pytest
from starlette.testclient import TestClient

from scrivmark import config, latex, render, review, rounds, selection
from scrivmark.blocks import _to_segments, guarded_segments
from scrivmark.server import Viewer, build_app

from conftest import edit, git

pytestmark = pytest.mark.skipif(latex.reader_problem() is not None, reason=latex.reader_problem() or "")

MAIN = r"""\documentclass[11pt]{article}
\usepackage{graphicx,tikz,natbib}
\usetikzlibrary{positioning}
\newcommand{\USD}[1]{US\$#1}
\newcommand{\lead}[1]{\textbf{#1}\enspace}
\renewcommand{\section}{\oops}
\begin{document}
\section{Aims}\label{sec:aims}
\lead{Motivation.} Wombats dig burrows that shelter other species \citep{royle2003}.
They are hard to count, so abundance is uncertain.
% Check this claim with Sam.
We request \USD{5,000} for camera traps, a 1\,kpc-free budget.

% A note between paragraphs.

Figure~\ref{fig:loop} shows the loop, and \cref{eq:n} gives the estimator.
\begin{equation}
N = C / p \label{eq:n}
\end{equation}

\begin{figure}
\centering
\begin{tikzpicture}
\node (a) {survey}; \node[right=of a] (b) {model};
\end{tikzpicture}
\caption{The survey loop.}\label{fig:loop}
\end{figure}

\input{methods}

\bibliographystyle{unsrtnat}
\bibliography{refs}
\end{document}
"""

METHODS = r"""\section{Methods}
We surveyed ``burrows'' along transects --- a standard design.
\begin{itemize}
\item Cameras at each burrow.
\item Occupancy models.
\end{itemize}
"""

BIB = """@article{royle2003,
  author = {Royle, J. Andrew and Nichols, James D.},
  title = {Estimating abundance from repeated presence-absence data},
  journal = {Ecology}, year = {2003}, volume = {84}, pages = {777--790}
}
"""


@pytest.fixture
def texrepo(tmp_path, monkeypatch):
    root = tmp_path / "grant"
    (root / "tex").mkdir(parents=True)
    (root / "tex" / "main.tex").write_text(MAIN)
    (root / "tex" / "methods.tex").write_text(METHODS)
    (root / "tex" / "refs.bib").write_text(BIB)
    (root / "tex" / ".latexmkrc").write_text("$pdf_mode = 1;\n")
    (root / "old").mkdir()
    (root / "old" / "draft.tex").write_text(MAIN)
    for k, v in {"SCRIVMARK_AS": "Barry W. Brook", "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@example.org",
                 "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_GLOBAL": "/dev/null"}.items():
        monkeypatch.setenv(k, v)
    git(root, "init", "-q", "-b", "main")
    monkeypatch.chdir(root)
    info = rounds.init(root, None, write=True)
    assert info["files"] == ["tex/main.tex", "tex/methods.tex"] and info["bibliography"] == ["tex/refs.bib"]
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "draft")
    return root


def test_blocks_survive_pandoc_and_the_preamble_is_hidden():
    segs = guarded_segments(MAIN, True)
    assert len(segs) == len(_to_segments(MAIN, latex.scan(MAIN)))
    kinds = [s.kind for s in segs]
    assert kinds[0] == "preamble" and kinds[-1] == "postamble" and "merged" not in kinds
    assert MAIN[segs[2].b0:segs[2].b1].startswith(r"\lead{Motivation.}")
    assert "% Check this claim" in MAIN[segs[2].b0:segs[2].b1]
    defs = latex.definitions(MAIN)
    assert r"\USD" in defs and r"\oops" not in defs


def test_redline_notes_crossrefs_and_macros(texrepo):
    rv = rounds.start(texrepo)
    edit(texrepo, "hard to count", "difficult to count", "tex/main.tex")
    edit(texrepo, "They are", "Wombats are", "tex/main.tex")
    edit(texrepo, "\\citep{royle2003}.", "\\citep{royle2003} and on farms.", "tex/main.tex")
    edit(texrepo, "Occupancy models.", "N-mixture models.", "tex/methods.tex")
    html, warnings = render.render(texrepo, config.load(texrepo), rv.rec["round"]["base"])
    assert 'class="scrv-del">hard<' in html and 'class="scrv-ins">difficult<' in html
    assert 'class="scrv-ins">and on farms<' in html and 'class="scrv-del">Occupancy<' in html
    assert "scrv-old" not in html
    assert "Check this claim with Sam." in html and "A note between paragraphs." in html
    assert html.count('class="scrv-note"') == 2
    assert "US$5,000" in html and "documentclass" not in html and "oops" not in html
    assert re.search(r'Figure\u00a0<a href="#fig-fig:loop" class="scrv-xref">1</a>', html)
    assert '>Equation 1</a>' in html and "Figure 1:" in html and "(1)" in html
    assert "/scrv-tikz/" in html and "“burrows”" in html
    assert "Royle and Nichols 2003</a>)" in html and "Sam.</span> We request" in html
    assert re.search(r'class="scrv-b"[^>]*data-file="tex/methods.tex"', html)
    assert ' n="' not in html


def test_author_rejects_a_latex_change(texrepo, monkeypatch):
    rounds.start(texrepo)
    edit(texrepo, "hard to count", "difficult to count", "tex/main.tex")
    edit(texrepo, "\\item Occupancy models.\n", "", "tex/methods.tex")
    info = rounds.return_round(texrepo, push=False)
    assert info["changes"] == 2
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(texrepo, info["round"])
    rv = review.active(texrepo)
    by_file = {c["file"]: c["id"] for c in rv.rec["changes"]}
    review.decide(rv, by_file["tex/main.tex"], "accepted", "SS")
    review.decide(rv, by_file["tex/methods.tex"], "rejected", "SS")
    assert "difficult to count" in (texrepo / "tex/main.tex").read_text()
    assert (texrepo / "tex/methods.tex").read_text() == METHODS
    html = Viewer(texrepo).state()["html"]
    assert "scrv-err" not in html and "Occupancy" in html


def test_selection_maps_through_commands_macros_and_tex_typography():
    macros = latex.macro_text(MAIN)
    src = MAIN[MAIN.index(r"\lead{"):MAIN.index(r"\end{equation}")]
    for shown, expect in [
        ("Motivation. Wombats dig", r"Motivation.} Wombats dig"),
        ("We request US$5,000 for", r"We request \USD{5,000} for"),
        ("1\u2009kpc-free", r"1\,kpc-free"),
    ]:
        hit = selection.map_selection(src, shown, "", macros)
        assert hit and src[hit[0]:hit[1]] == expect, shown
    m = METHODS
    hit = selection.map_selection(m, "surveyed “burrows” along transects – a", "", True)
    assert hit and m[hit[0]:hit[1]] == "surveyed ``burrows'' along transects --- a"


def test_comment_from_the_page_lands_on_latex_source(texrepo):
    rv = rounds.start(texrepo)
    viewer = Viewer(texrepo)
    html = viewer.state()["html"]
    m = re.search(r'<div class="scrv-b"(?=[^>]*data-file="tex/main.tex")[^>]*data-lines="(\d+-\d+)"[^>]*>\s*<p><strong>Motivation', html)
    assert m
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    h = {"x-scrivmark": viewer.token}
    t = c.post("/api/comment", headers=h, json={
        "file": "tex/main.tex", "lines": m.group(1), "text": "request US$5,000 for camera", "before": "",
        "key": viewer.state()["key"], "body": "Enough?", "rev": rv.rev()}).json()
    assert t["anchor"]["exact"] == r"request \USD{5,000} for camera"


@pytest.mark.skipif(not (shutil.which("pdflatex") and shutil.which("pdftocairo")), reason="needs pdflatex and pdftocairo")
def test_tikz_and_pdf_figures_are_drawn_for_the_browser(texrepo):
    rounds.start(texrepo)
    viewer = Viewer(texrepo)
    html = viewer.state()["html"]
    key = re.search(r"/scrv-tikz/([0-9a-f]{40})\.svg", html).group(1)
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    r = c.get(f"/scrv-tikz/{key}.svg")
    assert r.status_code == 200 and b"<svg" in r.content and b"see the PDF" not in r.content
    subprocess.run(["pdflatex", "-interaction=nonstopmode", "-output-directory", str(texrepo / "tex"),
                    str(texrepo / ".scrivmark" / "cache" / "tikz" / f"{key}.tex")], capture_output=True, cwd=texrepo)
    pdf = texrepo / "tex" / f"{key}.pdf"
    assert pdf.exists()
    edit(texrepo, "\\begin{tikzpicture}", f"\\includegraphics{{{key}}}\n\\begin{{tikzpicture}}", "tex/main.tex")
    html = viewer.state()["html"]
    assert f'src="/png/tex/{key}.png"' in html
    r = c.get(f"/png/tex/{key}.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert c.get("/png/../etc/passwd.png").status_code in (404, 200) and b"root:" not in c.get("/png/../etc/passwd.png").content


def test_latex_cannot_pull_other_files_into_the_page(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    body = (f"A \\lstinputlisting[language=C]{{{secret}}} B \\verbatiminput{{{secret}}} "
            f"C \\input {secret} D \\input{{{secret}}} E")
    root = tmp_path / "ms"
    root.mkdir()
    (root / "m.tex").write_text(f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n")
    html, _ = render.render(root, config.Manuscript(root, ["m.tex"]))
    assert "TOPSECRET" not in html


def _render_tex(tmp_path, files: dict[str, str], order: list[str]) -> str:
    root = tmp_path / "doc"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    html, _ = render.render(root, config.Manuscript(root, order))
    return html


def test_input_renders_in_place_with_the_root_documents_macros(tmp_path):
    main = ("\\documentclass{article}\n\\newcommand{\\animal}{wombat}\n\\begin{document}\nIntroduction here.\n\n"
            "\\input{methods}\n\nResults here.\n\\end{document}\n")
    html = _render_tex(tmp_path, {"main.tex": main, "methods.tex": "Child \\animal{} methods.\n"},
                       ["main.tex", "methods.tex"])
    text = re.sub(r"<[^>]+>", "", html)
    assert text.index("Introduction") < text.index("Child wombat methods") < text.index("Results")
    assert text.count("Child wombat") == 1


def test_labels_verbatim_and_aligned_equations(tmp_path):
    body = ("\\section{Methods}\\label{sec-methods}\nSee \\ref{sec-methods} and \\ref{eq:b}.\n\n"
            "\\begin{verbatim}\n100% real\n\\end{verbatim}\n\n"
            "\\begin{align}\na &= b \\label{eq:a}\\\\\nc &= d \\label{eq:b}\n\\end{align}\n")
    html = _render_tex(tmp_path, {"m.tex": f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"},
                       ["m.tex"])
    assert "??" not in html and "100% real" in html and "scrvnote" not in html


def test_figure_lookup_stays_inside_the_repository(texrepo):
    rounds.start(texrepo)
    (texrepo / "leak.png").symlink_to("/etc/hostname")
    viewer = Viewer(texrepo)
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    r = c.get("/png/leak.png")
    assert open("/etc/hostname", "rb").read() not in r.content


@pytest.mark.skipif(not (shutil.which("pdflatex") and shutil.which("pdftocairo")), reason="needs pdflatex and pdftocairo")
def test_tikz_cannot_read_files_and_tolerates_comments(texrepo, tmp_path):
    secret = tmp_path / "secret.tex"
    secret.write_text("TOPSECRET")
    rounds.start(texrepo)
    edit(texrepo, "\\node (a) {survey};", f"% a comment\n\\node (a) {{\\input{{{secret}}}}};", "tex/main.tex")
    edit(texrepo, "\\end{figure}", "\\end{figure}\n\n\\begin{tikzpicture}\n% A node\n\\node {Hi};\n\\end{tikzpicture}",
         "tex/main.tex")
    viewer = Viewer(texrepo)
    keys = re.findall(r"/scrv-tikz/([0-9a-f]{40})\.svg", viewer.state()["html"])
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    leaky, plain = (c.get(f"/scrv-tikz/{k}.svg").content for k in keys[-2:])
    assert b"see the PDF" in leaky
    assert b"see the PDF" not in plain and b"<svg" in plain
