"""Regression tests for the GPT-6 Pro external review (docs/audits/2026-11-03-external-review.md).
Each asserts the promise behind a finding, not only its reported example."""

import json
import subprocess

import pytest

from scrivmark import config, record, render, review, rounds
from scrivmark.errors import Conflict, ScrivError
from scrivmark.server import Viewer

from conftest import edit, git

LONG = ("This is an unchanged introduction with enough context before the estimate: {} and this is an "
        "unchanged discussion with enough context after the estimate.")


def write(repo, rel, text):
    (repo / rel).write_text(text)


def as_author(monkeypatch, repo, rid):
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, rid)
    return review.active(repo)


def one_file_repo(repo, text, rel="paper.md"):
    write(repo, rel, text)
    write(repo, "scrivmark.toml", f'[manuscript]\nfiles = ["{rel}"]\nbibliography = []\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture")


@pytest.mark.parametrize("delete", ["first", "second"])
def test_f01_reject_never_touches_a_surviving_repeat(repo, monkeypatch, delete):
    one_file_repo(repo, LONG.format("high") + "\n" + LONG.format("low") + "\n")
    rounds.start(repo)
    edit(repo, LONG.format("high"), LONG.format("low"))
    rid = rounds.return_round(repo, push=False)["round"]
    rv = as_author(monkeypatch, repo, rid)
    lines = (repo / "paper.md").read_text().splitlines(keepends=True)
    write(repo, "paper.md", lines[1] if delete == "first" else lines[0])
    before = (repo / "paper.md").read_text()
    try:
        review.decide(rv, "c1", "rejected", "SS")
    except Conflict:
        pass
    assert (repo / "paper.md").read_text() == before


def test_f02_draft_never_moves_to_a_lookalike_claim(repo):
    one_file_repo(repo, "North site: detection was high.\n\nSouth site: detection was high.\n")
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "detection was high", prefix="North site: "),
                         "Lower?", "detection was low")
    write(repo, "paper.md", "South site: detection was high.\n")
    with pytest.raises(Conflict):
        review.triage(rv, d["id"], True, "BWB")
    assert (repo / "paper.md").read_text() == "South site: detection was high.\n"
    assert [x["id"] for x in review.drafts(rv)] == [d["id"]]
    w = review.drafts(rv)[0]["where"]
    assert w["state"] == "orphan" or (w["how"] == "removed" and w["range"] == [0, 0])


def test_f03_metadata_cannot_script_the_viewer(repo):
    edit(repo, 'author: "Sam Student"', "author: '[Sam]{onclick=\"alert(1)\"}'")
    edit(repo, 'title: "Do wombats prefer sunny burrows?"',
         "title: '![x](missing.png){onerror=\"document.documentElement.dataset.audit=1\"}'\n"
         "references:\n- id: evil\n  type: article-journal\n  title: '[t]{onmouseover=\"alert(2)\"} [l](javascript:alert(3))'\n"
         "  issued: 2020\nnocite: '@evil'")
    html, _ = render.render(repo, config.load(repo))
    assert "onerror" not in html and "onclick" not in html and "onmouseover" not in html
    assert "javascript:" not in html


@pytest.mark.parametrize("name", ["résumé.md", "my paper.md"])
def test_f04_unusual_filenames_reject_for_real(repo, monkeypatch, name):
    one_file_repo(repo, "One line.\n", name)
    rounds.start(repo)
    write(repo, name, "Changed line.\n")
    rid = rounds.return_round(repo, push=False)["round"]
    rv = as_author(monkeypatch, repo, rid)
    assert [c["type"] for c in rv.rec["changes"]] == ["text"]
    review.decide(rv, "c1", "rejected", "SS")
    assert (repo / name).read_text() == "One line.\n"


@pytest.mark.parametrize("order", [["c1", "c2"], ["c2", "c1"]])
def test_f05_independent_rejections_compose(repo, monkeypatch, order):
    one_file_repo(repo, "The cat often ate food.\n")
    rounds.start(repo)
    write(repo, "paper.md", "The dog often consumed food.\n")
    rid = rounds.return_round(repo, push=False)["round"]
    rv = as_author(monkeypatch, repo, rid)
    for cid in order:
        review.decide(rv, cid, "rejected", "SS")
    assert (repo / "paper.md").read_text() == "The cat often ate food.\n"
    review.undo(rv, order[0])
    assert review.change_status(rv, review._find_change(rv, order[0])) == "pending"


def test_f06_return_and_close_resume_after_push_failures(repo, monkeypatch, tmp_path):
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "-u", "origin", "main")
    rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(ScrivError):
        rounds.return_round(repo)
    git(repo, "remote", "set-url", "origin", str(bare))
    info = rounds.return_round(repo)
    assert info.get("resumed") and info["pushed"]
    assert git(bare, "rev-parse", f"review/{info['round']}").strip() == git(repo, "rev-parse", "HEAD").strip()

    rv = as_author(monkeypatch, repo, info["round"])
    review.decide(rv, "c1", "accepted", "SS")
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(ScrivError):
        rounds.close_round(repo)
    git(repo, "remote", "set-url", "origin", str(bare))
    rounds.close_round(repo, round_id=info["round"])
    assert git(bare, "rev-parse", "main").strip() == git(repo, "rev-parse", "main").strip()
    assert record.load(repo, info["round"])["round"]["status"] == "closed"


def test_f06_close_finishes_an_uncommitted_closed_record(repo, monkeypatch):
    rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    rid = rounds.return_round(repo, push=False)["round"]
    rv = as_author(monkeypatch, repo, rid)
    review.decide(rv, "c1", "accepted", "SS")
    real = rounds._commit
    calls = {"n": 0}

    def flaky(root, message, paths=None):
        if message.startswith("scrivmark: close") and not calls["n"]:
            calls["n"] += 1
            raise ScrivError("hook refused")
        return real(root, message, paths)

    monkeypatch.setattr(rounds, "_commit", flaky)
    with pytest.raises(ScrivError):
        rounds.close_round(repo, push=False)
    assert rounds.close_round(repo, push=False)["merged_into"] == "main"
    assert git(repo, "status", "--porcelain").strip() == ""


def test_f07_tutorial_keeps_local_redlines_and_one_footnote(repo):
    rv = rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    html, warnings = render.render(repo, config.load(repo), rv.rec["round"]["base"])
    assert 'class="scrv-del">proves that<' in html
    assert html.count('id="fn1"') == 1 and 'id="fn2"' not in html
    assert "Duplicate note" not in warnings


def test_f09_structure_and_wording_edits_are_all_catalogued(repo):
    one_file_repo(repo, "- Alpha\n- Beta\n\nHard line\nbreak here.\n")
    rounds.start(repo)
    write(repo, "paper.md", "- Alfa\n    - Beta\n\nHard line  \nbreak there.\n")
    rid = rounds.return_round(repo, push=False)["round"]
    rec = record.load(repo, rid)
    undone = (repo / "paper.md").read_text()
    for c in sorted(rec["changes"], key=lambda c: c["r_range"][0], reverse=True):
        s, e = c["r_range"]
        undone = undone[:s] + c["base"] + undone[e:]
    as_ast = lambda t: subprocess.run(["pandoc", "-t", "json"], input=t, capture_output=True, text=True).stdout  # noqa: E731
    assert json.loads(as_ast(undone))["blocks"] == json.loads(as_ast("- Alpha\n- Beta\n\nHard line\nbreak here.\n"))["blocks"]


def test_f11_metadata_change_is_pending_and_reported(repo, monkeypatch):
    rounds.start(repo)
    edit(repo, 'title: "Do wombats prefer sunny burrows?"', 'title: "Do wombats prefer warm burrows?"')
    rid = rounds.return_round(repo, push=False)["round"]
    as_author(monkeypatch, repo, rid)
    st = Viewer(repo).state()
    assert [c["state"] for c in st["changes"]] == ["pending"]
    assert "data-c=" not in st["html"]


def test_f12_init_refuses_unrelated_staged_work(tmp_path, monkeypatch):
    root = tmp_path / "r"
    root.mkdir()
    monkeypatch.chdir(root)
    git(root, "init", "-q", "-b", "main")
    (root / "paper.md").write_text("Text.\n")
    (root / "unrelated.txt").write_text("x\n")
    git(root, "add", "unrelated.txt")
    with pytest.raises(ScrivError, match="staged"):
        rounds.init(root, ["paper.md"])
    assert not (root / "scrivmark.toml").exists()


def test_c01_identity_round_trips_awkward_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IDENTITY", tmp_path / "id.toml")
    monkeypatch.delenv("SCRIVMARK_AS", raising=False)
    config.set_identity('Barry "Bazz" Brook', "BWB", "Reviewed with \\ backslash\nand Unicode é.")
    me = config.identity()
    assert me.name == 'Barry "Bazz" Brook' and me.statement == "Reviewed with \\ backslash\nand Unicode é."


def test_c02_render_cache_follows_file_order(repo):
    write(repo, "two.md", "# Two\n\nSecond file.\n")
    write(repo, "scrivmark.toml", '[manuscript]\nfiles = ["paper.md", "two.md"]\nbibliography = ["refs.bib"]\n')
    v = Viewer(repo)
    first = v.state()["key"]
    write(repo, "scrivmark.toml", '[manuscript]\nfiles = ["two.md", "paper.md"]\nbibliography = ["refs.bib"]\n')
    assert v.state()["key"] != first


def test_c03_renamed_section_reference_uses_the_new_title(repo):
    edit(repo, "# Results", "# Results {#sec-results}\n\nSee @sec-results.")
    git(repo, "commit", "-qam", "label")
    rv = rounds.start(repo)
    edit(repo, "# Results {#sec-results}", "# Findings {#sec-results}")
    html, _ = render.render(repo, config.load(repo), rv.rec["round"]["base"])
    assert ">Findings</a>" in html and "ResultsFindings" not in html


def test_init_reads_makedoc_build_settings(tmp_path):
    root = tmp_path / "paper"
    (root / "settings").mkdir(parents=True)
    (root / "text").mkdir()
    for f in ("text/main.md", "text/figures.md", "notes.md"):
        (root / f).write_text("Text.\n")
    (root / "settings" / "build.yaml").write_text(
        "default_profile: draft\nprofiles:\n  other:\n    manuscript_sources: [../notes.md]\n  draft:\n"
        "    manuscript_sources:\n      - ../text/main.md\n      - ../text/figures.md\n"
        "    bibliography: ../refs.yaml\n    csl: ../style.csl\n")
    found = config.detect(root)
    assert found["files"] == ["text/main.md", "text/figures.md"]
    assert found["bibliography"] == ["refs.yaml"] and found["csl"] == "style.csl"
