"""Regressions from the independent review of 0.7.0 by Codex (gpt-6.1-sol),
14 November 2026 (docs/audits/2026-11-14-handover-review.md). Each fails on
0.7.0."""
import json

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from conftest import edit, git
from scrivmark import review, rounds, state
from scrivmark.cli import app
from scrivmark.errors import ScrivError
from scrivmark.selection import map_selection
from scrivmark.server import Viewer, build_app


def client(root):
    viewer = Viewer(root)
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    return c


def test_source_text_and_its_revision_come_from_one_read(repo, monkeypatch):
    """I01: a save between reading the text and hashing the file paired old
    text with the new revision, so saving the editor overwrote the save."""
    rounds.start(repo)
    from scrivmark import server

    def racing(*a, **k):
        raise AssertionError("the file was read a second time for its revision")
    monkeypatch.setattr(server, "revision", racing)
    r = client(repo).get("/api/source?file=paper.md&lines=1-2")
    assert r.status_code == 200
    assert r.json()["file_rev"] == state.revision(repo, ["paper.md"])


def test_an_editor_from_another_round_cannot_save(repo):
    """I02: the same bytes in a new round passed the file check."""
    first = rounds.start(repo)
    c = client(repo)
    src = c.get("/api/source?file=paper.md&lines=8-8").json()
    assert src["round"] == first.id
    rounds.abandon(repo)
    second = rounds.start(repo)
    assert second.id != first.id
    before = (repo / "paper.md").read_text()
    c = client(repo)
    r = c.post("/api/source", json={"file": "paper.md", "first": 8, "last": 8, "text": "Stale edit.",
                                    "file_rev": src["file_rev"], "round": src["round"]})
    assert r.status_code == 409 and (repo / "paper.md").read_text() == before
    ok = c.post("/api/source", json={"file": "paper.md", "first": 8, "last": 8, "text": "Fresh edit.",
                                     "file_rev": src["file_rev"], "round": second.id})
    assert ok.status_code == 200 and "Fresh edit." in (repo / "paper.md").read_text()


def test_an_interrupted_write_is_not_replayed_on_another_branch(repo, monkeypatch):
    """I03: the intent named no branch, so a matching file elsewhere got the edit."""
    real = state.atomic_write

    def interrupt(path, content):
        if path.name == "paper.md":
            raise OSError("interrupted")
        return real(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(state, "atomic_write", interrupt)
        with pytest.raises(OSError):
            with state.transaction(repo) as writes:
                writes["paper.md"] = "Edit meant for main.\n"
    original = (repo / "paper.md").read_text()
    git(repo, "checkout", "-q", "-b", "elsewhere")
    with pytest.raises(ScrivError, match="another branch"):
        with state.transaction(repo):
            pass
    assert (repo / "paper.md").read_text() == original
    assert list(state.local_dir(repo).glob("intent-unfinished-*.json"))


def test_a_survivor_of_two_similar_passages_is_not_guessed(repo):
    """I04: North and South both say "high"; North goes and South is renamed.
    The anchor on North's "high" must not land on the survivor."""
    text = "North: The rate is high.\nSouth: The rate is high.\n"
    (repo / "paper.md").write_text(text)
    git(repo, "commit", "-qam", "Two sites")
    rv = rounds.start(repo)
    s = text.index("high")
    d = review.add_draft(rv, review.anchor_at(rv, "paper.md", s, s + 4), "", "low", by="Agent")
    (repo / "paper.md").write_text("East: The rate is high.\n")
    assert review.resolve(rv, d["anchor"])["state"] == "orphan"
    with pytest.raises(ScrivError):
        review.triage(review.active(repo), d["id"], True, "BWB")
    assert (repo / "paper.md").read_text() == "East: The rate is high.\n"


def test_a_repeated_word_still_follows_its_own_line(repo):
    """The I04 rule must not orphan a passage whose own line is untouched, or
    whose rival went in a separate edit with an unchanged line between."""
    text = "North: The rate is high.\n\nA line that stays.\n\nSouth: The rate is high.\n"
    (repo / "paper.md").write_text(text)
    git(repo, "commit", "-qam", "Two sites")
    rv = rounds.start(repo)
    s = text.index("high")
    a = review.anchor_at(rv, "paper.md", s, s + 4)
    (repo / "paper.md").write_text("Site N: the rate is high.\n\nA line that stays.\n")
    got = review.resolve(rv, a)
    assert got["state"] == "ok" and got["how"] == "exact"


def test_overlapping_selections_use_what_comes_before(repo):
    """A01: only non-overlapping matches were considered."""
    assert map_selection("banana", "ana", "ban") == (3, 6)
    assert map_selection("banana", "ana", "b") == (1, 4)


def test_keep_reports_what_was_done_before_a_refusal(repo):
    """C01: keep, drop and resolve hid the items done before one failed."""
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "should should"), "", "should", by="Agent")
    r = CliRunner().invoke(app, ["keep", d["id"], "d-missing", "--json"])
    out = json.loads(r.stdout)
    assert r.exit_code == 1 and out["done"] == [d["id"]] and out["failed"]["id"] == "d-missing"
    assert out["remaining"] == [] and out["rev"] == review.active(repo).rev()
    assert "should should" not in (repo / "paper.md").read_text()


def test_lines_out_of_range_are_refused_cleanly(repo, monkeypatch):
    """C02: an IndexError escaped instead of a refusal."""
    rounds.start(repo)
    for bad in ("999", "3-1", "x", "0"):
        r = CliRunner().invoke(app, ["comment", "paper.md", "--lines", bad, "--body", "note"])
        assert isinstance(r.exception, ScrivError), (bad, r.exception)
