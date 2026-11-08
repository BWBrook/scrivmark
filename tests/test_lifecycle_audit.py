"""Regressions from the sanity pass of 8 November 2026: whose turn it is,
what crosses between clones, and writes that race other writers."""
import json
import shutil

import pytest
from starlette.testclient import TestClient

from scrivmark import review, rounds, server, state
from scrivmark.errors import ScrivError, StaleRevision
from scrivmark.server import Viewer, build_app

from conftest import edit, git
from test_turns import agents, app, barry, cli, reviewed, run


def test_the_reviewer_opening_a_returned_round_only_looks(repo, monkeypatch):
    rounds.start(repo)
    edit(repo, "proves that", "suggests that")
    rid = rounds.return_round(repo, push=False)["round"]
    head = git(repo, "rev-parse", "HEAD")
    info = rounds.open_round(repo, rid)
    assert info["waiting"] and git(repo, "rev-parse", "HEAD") == head
    assert review.active(repo).rec["round"]["author"] is None
    with pytest.raises(ScrivError, match="read-only for you"):
        run("accept", "c1")
    agents(monkeypatch)
    assert not rounds.open_round(repo, rid)["waiting"]
    assert review.active(repo).rec["round"]["author"]["initials"] == "CC"


def test_author_comments_survive_the_handover_to_another_clone(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    run("reject", "c1")
    rv = review.active(repo)
    review.comment(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "Kept as is.", "CC")
    assert review.active(repo).rec["threads"][-1]["anchor"]["rev"].startswith("snap:")
    run("accept", "--all")
    run("respond", "--no-push")
    shutil.rmtree(repo / ".scrivmark" / rv.id / "snapshots")
    rv = review.active(repo)
    assert all(t["anchor"]["rev"].startswith("git:") for t in rv.rec["threads"])
    assert all(review.resolve(rv, t["anchor"])["state"] == "ok" for t in rv.rec["threads"])


def test_sign_off_refuses_hand_edits_but_keeps_contested_text(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    run("reject", "c1")
    run("accept", "--all")
    run("respond", "--no-push")
    barry(monkeypatch)
    run("contest", "c1", "--because", "Please reconsider.")
    edit(repo, "dig burrows", "dig big burrows")
    for cmd in (["return", "--no-push"], ["close", "--force", "--no-push"]):
        with pytest.raises(ScrivError, match="edited during sign-off"):
            run(*cmd)
    assert not rounds.preflight(repo, step="return")["ready"]
    git(repo, "commit", "-qam", "slipped in")
    with pytest.raises(ScrivError, match="edited during sign-off"):
        run("close", "--force", "--no-push")
    edit(repo, "dig big burrows", "dig burrows")
    out = run("return", "--no-push")
    assert out["pending"] == ["c1"] and not git(repo, "status", "--porcelain", "--", "paper.md")
    agents(monkeypatch)
    run("reject", "c1")
    run("respond", "--no-push")
    barry(monkeypatch)
    edit(repo, "proves that", "suggests that")
    with pytest.raises(ScrivError, match="edited during sign-off"):
        run("close", "--force", "--no-push")


def test_a_rejected_deletion_comes_back_and_is_committed(repo, monkeypatch):
    rounds.start(repo)
    git(repo, "rm", "-q", "figures/burrows.svg")
    rid = rounds.return_round(repo, push=False)["round"]
    agents(monkeypatch)
    rounds.open_round(repo, rid)
    rv = review.active(repo)
    c = next(c for c in rv.rec["changes"] if c["type"] == "file")
    review.decide(rv, c["id"], "rejected", "CC")
    rounds.respond(repo, push=False)
    assert "figures/burrows.svg" in git(repo, "ls-files")


def test_an_editor_save_during_a_write_is_never_overwritten(repo):
    p = repo / "paper.md"
    with pytest.raises(StaleRevision):
        with state.transaction(repo, None, ["paper.md"]) as writes:
            p.write_text("saved by an editor meanwhile\n")
            writes["paper.md"] = "computed from what was there before\n"
    assert p.read_text() == "saved by an editor meanwhile\n"


def test_an_expect_covers_every_item_of_a_batch(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    rev = review.active(repo).rev()
    decide = review.decide

    def meanwhile(rv, *a, **k):
        out = decide(rv, *a, **k)
        other = review.active(repo)
        review.comment(other, review.anchor_quote(other, "paper.md", "dig burrows"), "Another agent.", "CC")
        return out

    monkeypatch.setattr(review, "decide", meanwhile)
    r = cli.invoke(app, ["accept", "c1", "c2", "--expect", rev, "--json"])
    assert r.exit_code == 1 and json.loads(r.output)["failed"]["id"] == "c2"
    assert [c["decision"] for c in review.active(repo).rec["changes"]] == ["accepted", None]


def test_the_viewer_revision_is_never_newer_than_what_it_shows(repo, monkeypatch):
    rv = rounds.start(repo)
    t = review.comment(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "First thought.", "BWB")
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    real = server.render.render

    def slow_render(*a, **k):
        monkeypatch.setattr(server.render, "render", real)
        review.edit_message(review.active(repo), t["id"], t["messages"][0]["id"], "Corrected from the CLI.", "BWB")
        return real(*a, **k)

    monkeypatch.setattr(server.render, "render", slow_render)
    st = client.get("/api/state").json()
    assert st["threads"][0]["messages"][0]["body"] == "First thought."
    r = client.post("/api/message", json={"thread": t["id"], "message": t["messages"][0]["id"],
                                          "body": "Edited in the page.", "rev": st["rev"]})
    assert r.status_code == 409
    assert review.active(repo).rec["threads"][0]["messages"][0]["body"] == "Corrected from the CLI."
