import json

import pytest

from scrivmark import record, review, rounds
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


def test_comment_follows_an_edited_passage_and_marks_a_deleted_one_removed(repo):
    rv = rounds.start(repo)
    t1 = review.comment(rv, review.anchor_quote(rv, "paper.md", "120 burrows"), "How chosen?", "BWB")
    t2 = review.comment(rv, review.anchor_quote(rv, "paper.md", "Abundance was not estimated"), "Why?", "BWB")
    edit(repo, "We visited 120 burrows", "We visited 118 burrows")
    edit(repo, "Abundance was not estimated, although methods exist for it\n[@royle2003].\n", "")
    rv.reload()
    where = {t["id"]: t["where"] for t in review.threads(rv)}
    assert where[t1["id"]]["state"] == "ok"
    assert where[t2["id"]]["how"] == "removed" and len(set(where[t2["id"]]["range"])) == 1


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
    a = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaimed?", "BWB")
    review.update_ask(rv, a["id"], answer="Yes: aspect is only a proxy for temperature.")
    st = Viewer(repo).state()
    assert [x["answer"] for x in st["asks"]] == ["Yes: aspect is only a proxy for temperature."]
    assert st["asks"][0]["line"]
    review.update_ask(rv, a["id"], done=True)
    assert review.asks(rv) == []
    assert "Overclaimed" not in (repo / "reviews" / f"{rv.id}.json").read_text()


def test_answer_wording_applies_and_answered_asks_are_archived(repo, monkeypatch):
    rv = rounds.start(repo)
    a = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaimed?", "BWB")
    b = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "proves that"), "Too strong?", "BWB")
    review.add_ask(rv, review.anchor_quote(rv, "paper.md", "Methods"), "Never answered", "BWB")
    review.update_ask(rv, a["id"], answer="Aspect is only a proxy.", wording="aspect", by="Claude Opus 5.5")
    review.update_ask(rv, b["id"], answer="Yes.", wording="suggests that", by="Claude Opus 5.5")
    edit(repo, "proves that", "shows that")
    with pytest.raises(Conflict):
        review.apply_ask(rv, b["id"])
    applied = review.apply_ask(rv, a["id"])
    assert "aspect drive" in (repo / "paper.md").read_text()
    assert applied["anchor"]["exact"] == "aspect" and not applied["done"]
    assert review.asks(rv)[0]["where"]["state"] == "ok"
    with pytest.raises(ScrivError):
        review.apply_ask(rv, a["id"])
    review.update_ask(rv, b["id"], done=True)
    rid = rounds.return_round(repo, push=False)["round"]
    kept = record.load(repo, rid)["asks"]
    assert [(k["question"], k["answered_by"], bool(k.get("applied")), k["done"]) for k in kept] == [
        ("Overclaimed?", "Claude Opus 5.5", True, False), ("Too strong?", "Claude Opus 5.5", False, True)]


def test_comment_on_a_deleted_sentence_stays_at_the_deletion_point(repo, monkeypatch):
    rv = rounds.start(repo)
    t = review.comment(rv, review.anchor_quote(rv, "paper.md", "The data was clear."), "Data are plural.", "BWB")
    edit(repo, "The data was clear. ", "")
    where = review.threads(rv)[0]["where"]
    assert where["state"] == "ok" and where["how"] == "removed" and where["range"][0] == where["range"][1]
    rid = rounds.return_round(repo, push=False)["round"]
    kept = record.load(repo, rid)["threads"][0]["anchor"]
    assert kept["removed"] == "The data was clear." and kept["exact"] == ""
    assert t["id"] == record.load(repo, rid)["threads"][0]["id"]


def test_an_orphaned_thread_can_be_reattached(repo):
    rv = rounds.start(repo)
    t = review.comment(rv, review.anchor_quote(rv, "paper.md", "thermal benefits drive"), "Overclaimed?", "BWB")
    text = (repo / "paper.md").read_text()
    start = text.index("# Discussion")
    (repo / "paper.md").write_text(text[:start] + "# Discussion\n\nSomething else entirely is argued now.\n")
    assert review.threads(rv)[0]["where"]["state"] == "orphan"
    review.reattach(rv, t["id"], review.anchor_quote(rv, "paper.md", "Something else entirely"))
    th = review.threads(rv)[0]
    assert th["where"]["state"] == "ok" and " ".join(th["anchor"]["was"].split()) == "thermal benefits drive"


def test_batch_comments_are_all_or_none_and_source_comments_import(repo, tmp_path):
    rv = rounds.start(repo)
    good = {"file": "paper.md", "quote": "thermal benefits", "body": "It's a \"claim\" | (check)", "draft": True,
            "wording": "thermal effects"}
    bad = {"file": "paper.md", "quote": "no such text anywhere", "body": "x"}
    from typer.testing import CliRunner

    from scrivmark.cli import app
    f = tmp_path / "d.json"
    f.write_text(json.dumps([good, bad]))
    r = CliRunner().invoke(app, ["comment", "--from", str(f)])
    assert r.exit_code != 0 and review.drafts(review.active(repo)) == []
    f.write_text(json.dumps(good) + "\n" + json.dumps({"file": "paper.md", "quote": "dig burrows", "body": "Hm."}) + "\n")
    r = CliRunner().invoke(app, ["comment", "--from", str(f)])
    assert r.exit_code == 0, r.output
    rv = review.active(repo)
    assert review.drafts(rv)[0]["body"] == good["body"] and len(rv.rec["threads"]) == 1
    edit(repo, "# Methods", "<!-- Codex, 2026-10-23 [R1 — Methods]\ntighten the methods -->\n\n"
         "<!-- unsigned note -->\n\n# Methods")
    git(repo, "commit", "-qam", "reviewer keeps an old note")
    r = CliRunner().invoke(app, ["import-comments", "--dry-run"])
    assert "signed Codex: Codex, 2026-10-23 [R1 — Methods]" in r.output and "signed BWB: unsigned note" in r.output
    r = CliRunner().invoke(app, ["import-comments"])
    assert r.exit_code == 0 and "Imported 2" in r.output
    rv = review.active(repo)
    first = rv.rec["threads"][-2]
    assert first["anchor"]["exact"].startswith("<!-- Codex")
    assert first["messages"][0]["by"] == "Codex" and first["messages"][0]["body"].startswith("Codex, 2026-10-23 [R1")
    assert first["messages"][0]["at"] == "2026-10-23" and "imported_at" in first["messages"][0]
    assert rv.rec["threads"][-1]["messages"][0]["at"] != "2026-10-23"
    assert "reviews/" + rv.id + ".json" in review.summary(rv)["uncommitted"]
    assert review.source_comments(rv, "BWB") == []


def test_drafts_record_which_agent_wrote_them(repo):
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", None,
                         by="Codex (gpt-6.1-terra)")
    t = review.triage(review.active(repo), d["id"], True, "BWB")
    assert t["messages"][0]["by"] == "BWB" and t["messages"][0]["drafted_by"] == "Codex (gpt-6.1-terra)"
    record.validate(review.active(repo).rec)


def test_wording_only_drafts_apply_as_edits_without_a_thread(repo, tmp_path):
    from typer.testing import CliRunner

    from scrivmark.cli import app
    rounds.start(repo)
    f = tmp_path / "e.json"
    f.write_text(json.dumps([{"file": "paper.md", "quote": "should should", "wording": "should"}]))
    r = CliRunner().invoke(app, ["comment", "--from", str(f), "--draft", "--by", "Codex (gpt-6.1-terra)"])
    assert r.exit_code == 0, r.output
    rv = review.active(repo)
    d = review.drafts(rv)[0]
    assert d["body"] == "" and d["by"] == "Codex (gpt-6.1-terra)"
    assert review.triage(rv, d["id"], True, "BWB") is None
    assert "should should" not in (repo / "paper.md").read_text()
    rec = review.active(repo).rec
    record.validate(rec)
    assert rec["drafted_edits"][0]["by"] == "Codex (gpt-6.1-terra)" and rec["drafted_edits"][0]["was"] == "should should"


def test_keep_all_applies_every_draft_and_leaves_conflicts(repo):
    rv = rounds.start(repo)
    a = review.anchor_quote(rv, "paper.md", "should should")
    review.add_draft(rv, a, "", "should", by="Codex")
    review.add_draft(rv, a, "Also here", "should", by="Claude")
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", None)
    out = review.keep_all(review.active(repo), "BWB")
    assert len(out["kept"]) == 2 and len(out["left"]) == 1
    rv = review.active(repo)
    assert "should should" not in (repo / "paper.md").read_text() and len(review.drafts(rv)) == 1
    assert [t["messages"][0]["body"] for t in rv.rec["threads"]] == ["Overclaim?"]
    assert rv.rec["drafted_edits"][0]["by"] == "Codex"


def test_batch_dry_run_reports_failures_overlaps_and_wording_in_context(repo, tmp_path):
    from typer.testing import CliRunner

    from scrivmark.cli import app
    rv = rounds.start(repo)
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", None, by="Claude")
    f = tmp_path / "b.json"
    f.write_text(json.dumps([
        {"file": "paper.md", "quote": "thermal benefits", "wording": "thermal effects"},
        {"file": "paper.md", "quote": "should should", "wording": "should"},
        {"file": "paper.md", "quote": "not in the paper at all", "wording": "x"},
    ]))
    r = CliRunner().invoke(app, ["comment", "--from", str(f), "--draft", "--by", "Codex", "--dry-run", "--json"])
    assert r.exit_code == 1
    rep = json.loads(r.output)
    assert [e["item"] for e in rep["errors"]] == [3]
    first, second = rep["checked"]
    assert first["overlaps"] == ["draft " + review.drafts(rv)[0]["id"] + " by Claude"] and second["overlaps"] == []
    assert "«should»" in second["after"] and "«should should»" in second["before"]
    assert len(review.drafts(review.active(repo))) == 1


def test_revise_keeps_author_and_history_and_summary_is_compact(repo, tmp_path):
    from typer.testing import CliRunner

    from scrivmark.cli import app
    rounds.start(repo)
    f = tmp_path / "b.json"
    f.write_text(json.dumps([{"file": "paper.md", "quote": "should should", "wording": "should not"}]))
    r = CliRunner().invoke(app, ["comment", "--from", str(f), "--draft", "--by", "Codex", "--json", "--summary"])
    out = json.loads(r.output)
    assert out["count"] == 1 and out["by"] == ["Codex"] and set(out) == {"count", "ids", "by", "rev"}
    did = out["ids"][0]
    r = CliRunner().invoke(app, ["revise", did, "--wording", "should", "--expect", out["rev"]])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(app, ["revise", did, "--body", "x", "--expect", out["rev"]])
    assert r.exit_code != 0
    rv = review.active(repo)
    d = review.drafts(rv)[0]
    assert d["by"] == "Codex" and d["wording"] == "should" and d["history"][0]["wording"] == "should not"
    review.triage(rv, did, True, "BWB")
    rec = review.active(repo).rec
    record.validate(rec)
    assert rec["drafted_edits"][0]["history"][0]["wording"] == "should not"


def test_keep_all_applies_neighbouring_edits_and_anchors_their_threads(repo):
    rv = rounds.start(repo)
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "should should"), "Doubled word", "should", by="Codex")
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", "thermal effects",
                     by="Codex")
    out = review.keep_all(review.active(repo), "BWB")
    assert len(out["kept"]) == 2 and not out["left"]
    rv = review.active(repo)
    text = (repo / "paper.md").read_text()
    assert "should should" not in text and "thermal effects" in text
    for t in review.threads(rv):
        assert t["where"]["state"] == "ok" and t["where"]["how"] == "exact"
        s, e = t["where"]["range"]
        assert text[s:e] == t["anchor"]["exact"]
    assert {e["wording"] for e in rv.rec["drafted_edits"]} == {"should", "thermal effects"}


def test_own_comments_can_be_edited_until_the_other_side_has_seen_them(repo, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from scrivmark.cli import app
    rv = rounds.start(repo)
    mine = review.comment(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"),
                          "A caveat rather than a finding.", "BWB")
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "should should"), "Doubled.", None, by="Terra")
    review.keep_all(review.active(repo), "BWB")
    rv = review.active(repo)
    drafted = next(t for t in rv.rec["threads"] if t["messages"][0].get("drafted_by"))
    review.edit_message(rv, mine["id"], mine["messages"][0]["id"], "A caveat.", "BWB")
    review.edit_message(rv, drafted["id"], drafted["messages"][0]["id"], "Doubled word.", "BWB")
    rv = review.active(repo)
    m = review._thread(rv, mine["id"])["messages"][0]
    assert m["body"] == "A caveat." and m["edited_at"] and "draft_history" not in m
    d = review._thread(rv, drafted["id"])["messages"][0]
    assert d["body"] == "Doubled word." and d["draft_history"][-1] == {"body": "Doubled.", "at": d["at"], "by": "Terra"}
    with pytest.raises(ScrivError):
        review.edit_message(rv, mine["id"], mine["messages"][0]["id"], "Not mine to change.", "SS")
    r = CliRunner().invoke(app, ["revise", mine["messages"][0]["id"], "--body", "A caveat, stated."])
    assert r.exit_code == 0, r.output
    assert review._thread(review.active(repo), mine["id"])["messages"][0]["body"] == "A caveat, stated."

    info = rounds.return_round(repo, push=False)
    with pytest.raises(ScrivError):
        review.edit_message(review.active(repo), mine["id"], mine["messages"][0]["id"], "Too late.", "BWB")
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    rv = review.active(repo)
    t = review.reply(rv, mine["id"], "Agreed, will fix.", "SS")
    reply_id = t["messages"][-1]["id"]
    assert not review.can_edit(rv, t["messages"][0], "SS") and review.can_edit(rv, t["messages"][-1], "SS")
    review.edit_message(rv, mine["id"], reply_id, "Agreed; fixed.", "SS")
    assert review._thread(review.active(repo), mine["id"])["messages"][-1]["body"] == "Agreed; fixed."


def test_return_can_restate_how_the_review_was_done(repo):
    rounds.start(repo)
    with pytest.raises(ScrivError):
        rounds.return_round(repo, push=False, statement="  ")
    rounds.return_round(repo, push=False, statement="Reviewed with Claude and Codex.")
    assert review.active(repo).rec["round"]["statement"] == "Reviewed with Claude and Codex."
    with pytest.raises(ScrivError, match="already returned"):
        rounds.return_round(repo, push=False, statement="Changed my mind.")
