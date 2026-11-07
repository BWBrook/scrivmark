"""Rounds that go back and forth: authors respond, the reviewer signs off,
contests a decision and sends the round back; agents named on every act."""
import json

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from scrivmark import config, review, rounds
from scrivmark.cli import app
from scrivmark.errors import ScrivError
from scrivmark.server import Viewer, build_app

from conftest import edit, git

cli = CliRunner()


def run(*args, agent=None):
    r = cli.invoke(app, [*args, "--json"], env={"SCRIVMARK_AGENT": agent} if agent else {})
    assert r.exit_code == 0, r.output
    return json.loads(r.output) if r.output.strip() else None


def reviewed(repo, monkeypatch):
    """A round reviewed by Barry and returned to two agents as authors."""
    rv = rounds.start(repo)
    edit(repo, "should should", "should")
    edit(repo, "proves that", "suggests that")
    rv = review.active(repo)
    review.comment(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "Which species?", "BWB")
    review.comment(rv, review.anchor_quote(rv, "paper.md", "Bare-nosed"), "Common name first?", "BWB")
    info = rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    rounds.open_round(repo, info["round"])
    rv = review.active(repo)
    rv.rec["round"]["author"] = {"name": "Claude and Codex", "initials": "CC"}
    from scrivmark import record
    record.path(repo, rv.id).write_text(record.dumps(rv.rec))
    git(repo, "commit", "-qam", "authors")
    return info["round"]


def test_agents_respond_reviewer_contests_and_sends_back(repo, monkeypatch):
    rid = reviewed(repo, monkeypatch)
    cs = run("changes")
    by_text = {c["reviewer"].strip(): c["id"] for c in cs}
    keep, refuse = by_text["suggests"], next(c["id"] for c in cs if c["id"] != by_text["suggests"])
    run("accept", keep, agent="Claude")
    run("reject", refuse, "--because", "The duplicated word is deliberate here.", agent="Codex")
    t1, t2 = [t["id"] for t in run("threads")]
    run("reply", t1, "--body", "Vombatus ursinus; added to the text.", agent="Claude")
    run("resolve", t2, agent="Codex")
    out = run("respond", "--no-push", agent="Claude")
    assert out["to"] == "reviewer" and out["decided"]["accepted"] == 1 and out["decided"]["rejected"] == 1

    rv = review.active(repo)
    assert rv.status == "responded" and [h["to"] for h in rv.rec["round"]["handovers"]] == ["author", "reviewer"]
    c = next(x for x in rv.rec["changes"] if x["id"] == refuse)
    assert c["because"] == "The duplicated word is deliberate here." and c["agent"] == "Codex" and c["by"] == "CC"
    t = review._thread(rv, t1)
    assert t["messages"][-1]["agent"] == "Claude" and t["messages"][-1]["by"] == "CC"
    assert review._thread(rv, t2)["resolved_by"]["agent"] == "Codex"

    news = run("news")
    assert news["turn"] == "reviewer"
    assert {d["change"] for d in news["decisions"]} == {keep, refuse}
    assert [m["thread"] for m in news["messages"]] == [t1] and [r["thread"] for r in news["resolved"]] == [t2]

    with pytest.raises(ScrivError):
        review.replace_lines(rv, "paper.md", 1, 1, "x")
    run("contest", refuse, "--because", "It is a typo; please accept.")
    run("reply", t1, "--body", "Thanks, good.")
    rv = review.active(repo)
    assert "should should" not in (repo / "paper.md").read_text()
    reviewer_msgs = [m for m in review._thread(rv, t1)["messages"] if m["by"] == "BWB"]
    assert [review.can_edit(rv, m, "BWB") for m in reviewer_msgs] == [False, True]
    out = run("return", "--no-push")
    assert out["to"] == "author" and out["pending"] == [refuse]

    rv = review.active(repo)
    assert rv.status == "returned" and len(rv.rec["round"]["handovers"]) == 3
    news = run("news")
    assert news["turn"] == "author" and [k["change"] for k in news["contested"]] == [refuse]
    assert [m["body"] for m in news["messages"]] == ["Thanks, good."]
    run("accept", refuse, agent="Codex")
    run("resolve", t1, agent="Claude")
    closed = run("close", "--no-push")
    assert closed["decisions"]["accepted"] == 2
    final = review.active(repo, rid)
    contested = next(x for x in final.rec["changes"] if x["id"] == refuse)["contested"][0]
    assert contested["was"]["decision"] == "rejected" and contested["because"] == "It is a typo; please accept."


def test_respond_needs_every_change_decided(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    with pytest.raises(ScrivError, match="still need accepting or rejecting"):
        rounds.respond(repo, push=False)


def test_the_other_sides_viewer_is_read_only(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    st = client.get("/api/state").json()
    assert st["mode"] == "waiting" and all(not t["editable"] for t in st["threads"])
    t = st["threads"][0]["id"]
    assert client.post("/api/reply", json={"thread": t, "body": "As the authors?", "rev": st["rev"]}).status_code == 403
    assert client.post("/api/selection", json={}).status_code == 200
    monkeypatch.setenv("SCRIVMARK_AS", "Claude and Codex")
    assert client.get("/api/state").json()["mode"] == "author"


def test_human_author_still_closes_straight_from_returned(repo, monkeypatch):
    rounds.start(repo)
    edit(repo, "should should", "should")
    info = rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    rounds.open_round(repo, info["round"])
    rv = review.active(repo)
    review.decide(rv, "c1", "accepted", "SS")
    out = rounds.close_round(repo, push=False)
    assert out["decisions"]["accepted"] == 1
    assert config.load(repo)


def test_viewer_marks_what_is_new_and_can_contest(repo, monkeypatch):
    rid = reviewed(repo, monkeypatch)
    monkeypatch.setenv("SCRIVMARK_AS", "Claude and Codex")
    run("accept", "--all", agent="Claude")
    run("respond", "--no-push", agent="Claude")
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    st = client.get("/api/state").json()
    assert st["mode"] == "signoff" and all(c["new"] for c in st["changes"] if c["state"] == "accepted")
    cid = st["changes"][0]["id"]
    r = client.post("/api/contest", json={"change": cid, "because": "Reconsider.", "rev": st["rev"]})
    assert r.status_code == 200 and r.json()["decision"] is None
    assert review.active(repo, rid).status == "responded"


def test_export_names_agents_reasons_and_turns(repo, monkeypatch):
    from scrivmark import export
    reviewed(repo, monkeypatch)
    cs = run("changes")
    run("accept", cs[0]["id"], agent="Claude")
    run("reject", cs[1]["id"], "--because", "Deliberate repetition.", agent="Codex")
    t = run("threads")[0]["id"]
    run("reply", t, "--body", "Done.", agent="Claude")
    run("respond", "--no-push", agent="Claude")
    md = export.markdown(review.active(repo))
    assert "rejected by CC · Codex" in md and "Deliberate repetition." in md
    assert "**CC · Claude**" in md and "with the reviewer, signing off" in md


def test_agent_conveniences(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    cs = run("changes")
    assert all("«" in c["context"] for c in cs if c["type"] != "file")
    r = cli.invoke(app, ["respond", "--dry-run", "--json"])
    pf = json.loads(r.output)
    assert r.exit_code == 1 and pf["transition"] == "respond" and pf["blockers"][0]["kind"] == "pending"
    import subprocess
    import sys
    bad = subprocess.run([sys.executable, "-m", "scrivmark.cli", "accept", "c99", "--json"], cwd=repo,
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "error" in json.loads(bad.stdout)
    target = next(c for c in cs if c["reviewer"].strip() == "suggests")
    edit(repo, "suggests that", "indicates that")
    out = run("reject", target["id"], "--because", "Rewrote it more plainly.", agent="Codex")
    assert out[0]["decision"] is None and out[0]["because"] == "Rewrote it more plainly."
    assert next(c for c in run("changes") if c["id"] == target["id"])["state"] == "superseded"


def _viewer(repo):
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    return client


def test_tutorial_author_open_as_then_serve_is_author_mode(repo, monkeypatch):
    rounds.start(repo)
    edit(repo, "should should", "should")
    rid = rounds.return_round(repo, push=False)["round"]
    client = _viewer(repo)
    assert client.get("/api/state").json()["mode"] == "waiting"
    r = cli.invoke(app, ["open", rid, "--as", "Sam Student"])
    assert r.exit_code == 0, r.output
    monkeypatch.delenv("SCRIVMARK_AS", raising=False)
    monkeypatch.setattr(config, "IDENTITY", repo / "none.toml")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    assert client.get("/api/state").json()["mode"] == "author"
    rounds.close_round(repo, force=True, push=False)
    git(repo, "checkout", "-q", "main")
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    rounds.start(repo)
    assert config.identity(repo).name == "Barry W. Brook"


def test_a_write_from_before_a_handover_is_refused(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    run("accept", "--all", agent="Claude")
    run("respond", "--no-push")
    client = _viewer(repo)
    st = client.get("/api/state").json()
    assert st["mode"] == "signoff"
    run("return", "--no-push")
    r = client.post("/api/reply", json={"thread": st["threads"][0]["id"], "body": "Late.", "rev": st["rev"]})
    assert r.status_code in (403, 409)
    assert all(m["body"] != "Late." for t in review.active(repo).rec["threads"] for m in t["messages"])


def test_sign_off_cannot_apply_ask_wording(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    run("accept", "--all")
    run("respond", "--no-push")
    rv = review.active(repo)
    q = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "Better verb?", "BWB")
    review.update_ask(rv, q["id"], answer="Use 'excavate'.", wording="excavate burrows", by="Claude")
    with pytest.raises(ScrivError):
        review.apply_ask(review.active(repo), q["id"])
    assert "dig burrows" in (repo / "paper.md").read_text()


def test_handover_keeps_answered_asks_in_the_record(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    rv = review.active(repo)
    q = review.add_ask(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "Is this right?", "CC")
    review.update_ask(rv, q["id"], answer="Yes.", by="Claude")
    run("accept", "--all")
    run("respond", "--no-push")
    assert [a["id"] for a in review.active(repo).rec.get("asks", [])] == [q["id"]]


def test_respond_finishes_after_a_failed_record_commit(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    run("accept", "--all")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ngit diff --cached -- 'reviews/*.json' | grep -q '\"status\": \"responded\"' && exit 1\nexit 0\n")
    hook.chmod(0o755)
    with pytest.raises(ScrivError):
        rounds.respond(repo, push=False)
    hook.unlink()
    rounds.respond(repo, push=False)
    head = json.loads(git(repo, "show", f"HEAD:{review.active(repo).record_rel}"))["round"]
    assert head["status"] == "responded"


def test_old_records_and_signed_dates_do_not_break_news(repo, monkeypatch):
    from scrivmark import record
    rid = reviewed(repo, monkeypatch)
    rv = review.active(repo)
    rnd = rv.rec["round"]
    rnd.pop("handovers", None)
    rnd["returned_at"] = "2026-11-07T12:00:00+11:00"
    rv.rec["threads"][0]["messages"][0]["at"] = "2026-11-07T12:00:00+11:00"
    rv.rec["threads"][1]["messages"][0]["at"] = "2026-10-23"
    record.path(repo, rid).write_text(record.dumps(rv.rec))
    n = review.news(review.active(repo))
    assert len(n["messages"]) == 2
    assert _viewer(repo).get("/api/state").status_code == 200


def test_superseded_reasons_reach_news_and_previews_cover_send_back(repo, monkeypatch):
    reviewed(repo, monkeypatch)
    cs = run("changes")
    target = next(c for c in cs if c["reviewer"].strip() == "suggests")
    edit(repo, "suggests that", "indicates that")
    run("reject", target["id"], "--because", "Rewrote it.", agent="Codex")
    run("accept", *[c["id"] for c in cs if c["id"] != target["id"]])
    run("respond", "--no-push")
    news = run("news")
    assert any(d["decision"] == "superseded" and d["because"] == "Rewrote it." and d["agent"] == "Codex"
               for d in news["decisions"])
    r = cli.invoke(app, ["return", "--dry-run", "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["transition"] == "return"


def test_batch_comments_and_first_return_record_the_agent(repo, tmp_path):
    rounds.start(repo)
    f = tmp_path / "c.json"
    f.write_text(json.dumps([{"file": "paper.md", "quote": "dig burrows", "body": "Species?"}]))
    run("comment", "--from", str(f), agent="Codex")
    assert review.active(repo).rec["threads"][0]["messages"][0]["agent"] == "Codex"
    run("return", "--no-push", agent="Claude")
    assert review.active(repo).rec["round"]["handovers"][0]["agent"] == "Claude"
