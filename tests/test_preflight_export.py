import json

import pytest
from typer.testing import CliRunner

from scrivmark import export, review, rounds
from scrivmark.cli import app
from scrivmark.errors import ScrivError

from conftest import edit, git

runner = CliRunner()


def snapshot(root):
    return git(root, "status", "--porcelain", "--untracked-files=all"), git(root, "rev-parse", "HEAD"), \
        git(root, "tag"), git(root, "branch")


def test_return_preview_with_draft_blocks_and_writes_nothing(repo):
    rv = rounds.start(repo)
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", None)
    edit(repo, "proves that", "tests whether")
    before = snapshot(repo)
    pf = rounds.preflight(repo)
    assert snapshot(repo) == before
    assert pf["transition"] == "return" and not pf["ready"]
    blocker = next(b for b in pf["blockers"] if b["kind"] == "drafts")
    assert blocker["count"] == 1 and blocker["ids"] and "1 draft" in blocker["text"]
    assert pf["files"] == ["paper.md"] and pf["changes"] >= 1
    res = runner.invoke(app, ["return", "--dry-run"])
    assert res.exit_code == 1 and "✗" in res.output and "draft" in res.output
    assert snapshot(repo) == before


def test_ready_return_preview_counts_what_return_records(repo):
    rv = rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    edit(repo, "should should", "should")
    review.comment(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim?", "BWB")
    before = snapshot(repo)
    res = runner.invoke(app, ["return", "--dry-run", "--json"])
    assert snapshot(repo) == before
    pf = json.loads(res.output)
    assert res.exit_code == 0 and pf["ready"] and pf["blockers"] == []
    assert pf["publication"]["remote"] is None and pf["lost_comments"] == []
    info = rounds.return_round(repo, push=False)
    assert pf["changes"] == info["changes"] == 2 and pf["threads"] == info["threads"] == 1


def test_untracked_file_blocks_return(repo):
    rounds.start(repo)
    (repo / "stray.txt").write_text("x")
    pf = rounds.preflight(repo)
    assert [b["kind"] for b in pf["blockers"]] == ["untracked"] and pf["blockers"][0]["files"] == ["stray.txt"]


def test_phase_mismatch_is_refused(repo):
    rounds.start(repo)
    res = runner.invoke(app, ["close", "--dry-run"])
    assert res.exit_code != 0 and "does not apply" in str(res.exception or res.output)
    rounds.return_round(repo, push=False)
    res = runner.invoke(app, ["return", "--dry-run"])
    assert res.exit_code != 0 and "does not apply" in str(res.exception or res.output)


def returned(repo):
    rv = rounds.start(repo, statement="Reviewed by BWB with Claude.")
    edit(repo, "proves that", "tests whether")
    review.comment(rv, review.anchor_quote(rv, "paper.md", "thermal benefits"), "Overclaim <script>alert(1)</script>?", "BWB")
    return rounds.return_round(repo, push=False)["round"]


def test_close_preview_with_pending_change(repo):
    rid = returned(repo)
    before = snapshot(repo)
    pf = rounds.preflight(repo)
    assert snapshot(repo) == before
    assert pf["transition"] == "close" and not pf["ready"]
    assert {b["kind"] for b in pf["blockers"]} == {"pending", "open_threads"}
    assert pf["pending"] == ["c1"] and pf["merge_target"] == "main" and pf["author_moved"] is False
    assert pf["decisions"]["accepted"] == 0
    res = runner.invoke(app, ["close", "--dry-run", "--json"])
    assert res.exit_code == 1 and json.loads(res.output)["round"] == rid


def test_close_preview_sees_author_branch_moving(repo):
    returned(repo)
    git(repo, "checkout", "-q", "main")
    edit(repo, "# Methods", "# Methods and materials")
    git(repo, "commit", "-qam", "author keeps writing")
    git(repo, "checkout", "-q", "-")
    assert rounds.preflight(repo)["author_moved"] is True


def test_closed_round_has_no_transition(repo, monkeypatch):
    rid = returned(repo)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rv = review.active(repo)
    review.decide(rv, "c1", "accepted", "SS")
    review.set_thread(rv, rv.rec["threads"][0]["id"], "resolved")
    rounds.close_round(repo, push=False)
    git(repo, "checkout", "-q", f"review/{rid}")
    pf = rounds.preflight(repo)
    assert pf["transition"] is None and pf["ready"] is False and pf["notes"] == ["closed"]


def test_markdown_export_of_returned_round(repo):
    rid = returned(repo)
    md = export.markdown(review.active(repo))
    assert rid in md and "Reviewed by BWB with Claude." in md
    assert "tests whether" in md and "paper.md:" in md and "pending" in md
    assert "Overclaim" in md and "**BW" in md and "thermal benefits" in md
    assert "Outstanding" in md and "authoritative" in md


def test_export_of_closed_round_from_main_via_cli(repo, monkeypatch, tmp_path):
    rid = returned(repo)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rv = review.active(repo)
    review.decide(rv, "c1", "accepted", "SS")
    review.set_thread(rv, rv.rec["threads"][0]["id"], "resolved")
    rounds.close_round(repo, push=False)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    res = runner.invoke(app, ["export", rid])
    assert res.exit_code == 0 and "accepted by SS" in res.output and "closed" in res.output
    out = tmp_path / "r.html"
    assert runner.invoke(app, ["export", rid, "--html", "-o", str(out)]).exit_code == 0
    assert "<table" in out.read_text()


def test_html_export_is_inert(repo):
    returned(repo)
    rv = review.active(repo)
    rv.rec["threads"][0]["messages"].append(
        {"id": "m-x", "by": "SS", "at": "2026-11-06T10:00:00+11:00",
         "body": "<img src=http://evil/x.png onerror=alert(1)> [click](javascript:alert(1)) ![pic](http://evil/p.png) **bold**"})
    out = export.html(rv)
    low = out.lower()
    assert "<script" not in low and "<img" not in low and "href=\"javascript" not in low and "<a " not in low
    assert "&lt;script&gt;" in out and "&lt;img" in out and "<strong>bold</strong>" in out
    assert "<style>" in out and "<table" in out


def test_html_export_drops_attributes_and_carries_its_own_policy(repo):
    returned(repo)
    rv = review.active(repo)
    rv.rec["threads"][0]["messages"].append(
        {"id": "m-y", "by": "SS", "at": "2026-11-06T10:00:00+11:00",
         "body": '[hover](https://example.org){onmouseover="alert(1)" style="background:url(https://evil/p)"} '
                 '[x]{onclick="alert(2)"}\n\n::: {onclick="alert(3)"}\nblock\n:::'})
    out = export.html(rv)
    import re
    assert not re.search(r"<[^>]*\b(onmouseover|onclick|style)=", out.split("</style>")[1])
    assert "Content-Security-Policy" in out and "default-src 'none'" in out


def test_previews_honour_the_command_options(repo):
    rounds.start(repo)
    rv = review.active(repo)
    assert rounds.preflight(repo, statement="")["ready"] is False
    assert any("--no-push" in w for w in rounds.preflight(repo, push=False)["will"])
    edit(repo, "proves that", "suggests that")
    rounds.return_round(repo, push=False)
    assert rounds.preflight(repo)["ready"] is False
    forced = rounds.preflight(repo, force=True)
    assert forced["ready"] is True and any("--force" in n for n in forced["notes"])
    assert rv.id == forced["round"]
