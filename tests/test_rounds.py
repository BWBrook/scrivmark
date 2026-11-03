import json

import pytest

from scrivmark import review, rounds
from scrivmark.errors import Conflict, ScrivError

from conftest import edit, git


def reviewer_pass(root):
    rv = rounds.start(root, statement="Reviewed by BWB with Claude.")
    edit(root, "proves that", "tests whether")
    edit(root, "should should", "should")
    review.comment(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", "BWB")
    return rv


def test_full_round(repo, monkeypatch):
    reviewer_pass(repo)
    info = rounds.return_round(repo, push=False)
    assert info["changes"] == 2 and info["threads"] == 1
    assert "scrivmark/" + info["round"] + "/returned" in git(repo, "tag")
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    rv = review.active(repo)
    review.decide(rv, "c1", "accepted", "SS")
    review.decide(rv, "c2", "rejected", "SS")
    assert "should should" in (repo / "paper.md").read_text()
    with pytest.raises(ScrivError, match="still open"):
        rounds.close_round(repo, push=False)
    review.set_thread(rv, rv.rec["threads"][0]["id"], "resolved")
    out = rounds.close_round(repo, push=False)
    assert out["decisions"] == {"accepted": 1, "rejected": 1, "superseded": 0}
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    rec = json.loads((repo / "reviews" / f"{info['round']}.json").read_text())
    assert rec["round"]["status"] == "closed" and rec["round"]["author"]["initials"] == "SS"
    assert len(git(repo, "rev-list", "--parents", "-n1", "HEAD").split()) == 3


def test_reject_fails_closed_after_author_edit(repo, monkeypatch):
    reviewer_pass(repo)
    info = rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    edit(repo, "tests whether", "tests if")
    rv = review.active(repo)
    with pytest.raises(Conflict):
        review.decide(rv, "c1", "rejected", "SS")
    assert review.change_status(rv, rv.rec["changes"][0]) == "superseded"
    assert "tests if" in (repo / "paper.md").read_text()


def test_author_branch_moving_mid_round_is_merged(repo, monkeypatch):
    reviewer_pass(repo)
    info = rounds.return_round(repo, push=False)
    git(repo, "checkout", "-q", "main")
    edit(repo, "# Methods", "# Methods and materials")
    git(repo, "commit", "-qam", "author keeps writing")
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    res = rounds.open_round(repo, info["round"])
    assert res["merged"]
    assert "# Methods and materials" in (repo / "paper.md").read_text()
    rv = review.active(repo)
    assert [review.change_status(rv, c) for c in rv.rec["changes"]] == ["pending", "pending"]


def test_comment_follows_an_edited_passage_and_orphans_a_deleted_one(repo):
    rv = rounds.start(repo)
    t1 = review.comment(rv, review.anchor_quote(rv, "paper.md", "120 burrows"), "How chosen?", "BWB")
    t2 = review.comment(rv, review.anchor_quote(rv, "paper.md", "Abundance was not estimated"), "Why?", "BWB")
    edit(repo, "We visited 120 burrows", "We visited 118 burrows")
    edit(repo, "Abundance was not estimated, although methods exist for it\n[@royle2003].\n", "")
    rv.reload()
    where = {t["id"]: t["where"] for t in review.threads(rv)}
    assert where[t1["id"]]["state"] == "ok"
    assert where[t2["id"]]["state"] == "orphan"


def test_ambiguous_quote_is_refused(repo):
    rv = rounds.start(repo)
    with pytest.raises(Conflict):
        review.anchor_quote(rv, "paper.md", "burrows")


def test_return_refuses_drafts_and_stray_files(repo):
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "Wombats clearly prefer"), "Soften.", "Wombats appear to prefer")
    with pytest.raises(ScrivError, match="draft"):
        rounds.return_round(repo, push=False)
    review.triage(rv, d["id"], True, "BWB")
    assert "Wombats appear to prefer" in (repo / "paper.md").read_text()
    (repo / "scratch.txt").write_text("x")
    with pytest.raises(ScrivError, match="scratch.txt"):
        rounds.return_round(repo, push=False)


def test_reject_refuses_when_a_repeated_passage_shifts(repo, monkeypatch):
    edit(repo, "Abundance was not estimated", "The rate is high.\n\nThe rate is low.\n\nAbundance was not estimated")
    git(repo, "commit", "-qam", "repeated lines")
    rounds.start(repo)
    edit(repo, "The rate is high.", "The rate is low.")
    info = rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    edit(repo, "The rate is low.\n\nThe rate is low.", "The rate is low.")
    rv = review.active(repo)
    before = (repo / "paper.md").read_text()
    try:
        review.decide(rv, "c1", "rejected", "SS")
    except Conflict:
        pass
    assert (repo / "paper.md").read_text() == before


def test_kept_draft_wording_never_overwrites_an_edit(repo):
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "120 burrows"), "Fewer?", "100 burrows")
    edit(repo, "120 burrows", "118 burrows")
    with pytest.raises(Conflict):
        review.triage(rv, d["id"], True, "BWB")
    assert "118 burrows" in (repo / "paper.md").read_text()
    assert review.drafts(rv)


def test_structural_whitespace_edit_is_catalogued(repo):
    rounds.start(repo)
    edit(repo, "- east and west", "    - east and west")
    info = rounds.return_round(repo, push=False)
    assert info["changes"] == 1


def test_close_can_be_resumed(repo, monkeypatch):
    reviewer_pass(repo)
    info = rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    rv = review.active(repo)
    review.decide(rv, "c1", "accepted", "SS")
    review.decide(rv, "c2", "accepted", "SS")
    review.set_thread(rv, rv.rec["threads"][0]["id"], "resolved")
    real = rounds._merge_closed
    monkeypatch.setattr(rounds, "_merge_closed", lambda *a: (_ for _ in ()).throw(ScrivError("push failed")))
    with pytest.raises(ScrivError):
        rounds.close_round(repo, push=False)
    monkeypatch.setattr(rounds, "_merge_closed", real)
    assert rounds.close_round(repo, push=False)["merged_into"] == "main"


def test_asks_are_local_cards_that_agents_answer(repo):
    from scrivmark.server import Viewer

    rv = rounds.start(repo)
    a = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaimed?")
    review.update_ask(rv, a["id"], answer="Yes: aspect is only a proxy for temperature.")
    st = Viewer(repo).state()
    assert [x["answer"] for x in st["asks"]] == ["Yes: aspect is only a proxy for temperature."]
    assert st["asks"][0]["line"]
    review.update_ask(rv, a["id"], done=True)
    assert review.asks(rv) == []
    assert "Overclaimed" not in (repo / "reviews" / f"{rv.id}.json").read_text()
