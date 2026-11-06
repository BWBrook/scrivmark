"""Integrity regressions from the external audit of 5 November 2026
(docs/audits/2026-11-06-external-audit.md). The first twelve are the auditor's
acceptance tests, which failed on d1438b2; the rest cover the other findings.
"""
from pathlib import Path
import subprocess
import pytest
from scrivmark import record, review, rounds, state
from scrivmark.errors import ScrivError


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def ms(tmp_path, monkeypatch):
    for k, v in {'SCRIVMARK_AS':'Audit Reviewer', 'GIT_AUTHOR_NAME':'Audit',
                  'GIT_AUTHOR_EMAIL':'audit@example.invalid', 'GIT_COMMITTER_NAME':'Audit',
                  'GIT_COMMITTER_EMAIL':'audit@example.invalid', 'GIT_CONFIG_GLOBAL':'/dev/null'}.items():
        monkeypatch.setenv(k, v)
    root = tmp_path / 'ms'
    root.mkdir()
    (root / 'paper.md').write_text('# Results\n\nThere were 120 burrows.\n')
    (root / 'scrivmark.toml').write_text('[manuscript]\nfiles = ["paper.md"]\nbibliography = []\n')
    (root / '.gitignore').write_text('.scrivmark/\n')
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'Fixture')
    return root


@pytest.mark.parametrize('write_path', ['draft', 'all_drafts', 'ask'])
@pytest.mark.parametrize('headings', [False, True])
def test_deleted_target_never_changes_surviving_duplicate(ms, write_path, headings):
    paragraph = ('This is an unchanged introduction with enough context before the estimate: high '
                 'and this is an unchanged discussion with enough context after the estimate.\n')
    first = ('# North site\n\n' if headings else '') + paragraph
    second = ('# South site\n\n' if headings else '') + paragraph
    text = first + '\n' + second
    (ms / 'paper.md').write_text(text)
    git(ms, 'commit', '-qam', 'Duplicate passages')
    rv = rounds.start(ms)
    start = text.index('high')
    anchor = review.anchor_at(rv, 'paper.md', start, start + 4)
    if write_path == 'ask':
        item = review.add_ask(rv, anchor, 'Correct the North estimate.', 'AR')
        review.update_ask(rv, item['id'], answer='Use low.', wording='low', by='Agent')
    else:
        item = review.add_draft(rv, anchor, 'Correct the North estimate.', 'low', by='Agent')
    (ms / 'paper.md').write_text(second)  # Delete the target, preserve the other section.
    try:
        if write_path == 'draft':
            review.triage(rv, item['id'], True, 'AR')
        elif write_path == 'all_drafts':
            review.keep_all(rv, 'AR')
        else:
            review.apply_ask(rv, item['id'])
    except ScrivError:
        pass
    assert (ms / 'paper.md').read_text() == second
    if write_path != 'ask':
        assert any(d['id'] == item['id'] for d in review.drafts(rv))
    else:
        assert not review.asks(rv, include_done=True)[0].get('applied')


def test_apply_ask_refuses_proposal_changed_after_display(ms):
    rv = rounds.start(ms)
    item = review.add_ask(rv, review.anchor_quote(rv, 'paper.md', '120 burrows'), 'Check count.', 'AR')
    review.update_ask(rv, item['id'], answer='Use 118.', wording='118 burrows', by='Agent')
    shown_revision = rv.rev()
    before = (ms / 'paper.md').read_bytes()
    review.update_ask(rv, item['id'], answer='Use 10.', wording='10 burrows', by='Agent')
    with pytest.raises(ScrivError):
        review.apply_ask(rv, item['id'], expect=shown_revision)
    assert (ms / 'paper.md').read_bytes() == before


def test_keep_all_checks_revision_for_the_proposals_the_human_saw(ms):
    from starlette.testclient import TestClient
    from scrivmark.server import Viewer, build_app
    rv = rounds.start(ms)
    item = review.add_draft(rv, review.anchor_quote(rv, 'paper.md', '120 burrows'), '', '118 burrows', by='Agent')
    shown_revision = rv.rev()
    before = (ms / 'paper.md').read_bytes()
    review.revise_draft(rv, item['id'], wording='10 burrows', by='Agent')
    viewer = Viewer(ms)
    client = TestClient(build_app(viewer), base_url='http://127.0.0.1')
    client.cookies.set(viewer.cookie, viewer.token)
    response = client.post('/api/draft', json={'action':'keep_all', 'rev':shown_revision})
    assert response.status_code == 409
    assert (ms / 'paper.md').read_bytes() == before


def test_closed_round_cannot_be_reopened_by_reply_or_status(ms, monkeypatch):
    rv = rounds.start(ms)
    t = review.comment(rv, review.anchor_quote(rv, 'paper.md', '120 burrows'), 'Check.', 'AR')
    rid = rounds.return_round(ms, push=False)['round']
    monkeypatch.setenv('SCRIVMARK_AS', 'Audit Author')
    rounds.open_round(ms, rid)
    rv = review.active(ms)
    review.set_thread(rv, t['id'], 'resolved')
    rounds.close_round(ms, push=False)
    rounds.open_round(ms, rid)
    rv = review.active(ms)
    before = (ms / rv.record_rel).read_bytes()
    with pytest.raises(ScrivError):
        review.reply(rv, t['id'], 'After closure.', 'AA')
    with pytest.raises(ScrivError):
        review.set_thread(rv, t['id'], 'open')
    assert (ms / rv.record_rel).read_bytes() == before


def test_waiting_draft_request_checks_current_round_status(ms):
    stale = rounds.start(ms)
    anchor = review.anchor_quote(stale, 'paper.md', '120 burrows')
    rounds.return_round(ms, push=False)
    with pytest.raises(ScrivError):
        review.add_draft(stale, anchor, 'Arrived after return.', '10 burrows', by='Agent')
    assert review.drafts(review.active(ms)) == []


def test_rejecting_script_content_preserves_original_executable_mode(ms, monkeypatch):
    script = ms / 'build.sh'
    original = '#!/bin/sh\nexit 0\n'
    script.write_text(original)
    script.chmod(0o755)
    git(ms, 'add', 'build.sh')
    git(ms, 'commit', '-qm', 'Manuscript build script')
    rounds.start(ms)
    script.write_text('#!/bin/sh\nexit 1\n')
    rid = rounds.return_round(ms, push=False)['round']
    monkeypatch.setenv('SCRIVMARK_AS', 'Audit Author')
    rounds.open_round(ms, rid)
    rv = review.active(ms)
    review.decide(rv, 'c1', 'rejected', 'AA')
    assert script.read_text() == original
    assert script.stat().st_mode & 0o777 == 0o755


def test_recovery_does_not_discard_intervening_external_edit(ms, monkeypatch):
    real_write = state.atomic_write
    def interrupt_record(path, content):
        if path.name == 'test-record.json':
            raise OSError('Injected interruption after manuscript replacement')
        return real_write(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(state, 'atomic_write', interrupt_record)
        with pytest.raises(OSError):
            with state.transaction(ms) as writes:
                writes['paper.md'] = 'Transaction edit.\n'
                writes['test-record.json'] = '{"decision": "accepted"}'
    later = 'Later correction made in a text editor.\n'
    (ms / 'paper.md').write_text(later)
    try:
        with state.transaction(ms):
            pass
    except ScrivError:
        pass  # A conflict with a recovery artefact is the safe outcome.
    assert (ms / 'paper.md').read_text() == later


def test_returning_freezes_no_guess_between_identical_passages(ms):
    para = "An unchanged introduction before the estimate: high, and an unchanged discussion after it.\n"
    text = para + "\n" + para
    (ms / "paper.md").write_text(text)
    git(ms, "commit", "-qam", "Twins")
    rv = rounds.start(ms)
    t = review.comment(rv, review.anchor_at(rv, "paper.md", text.index("high"), text.index("high") + 4), "Check.", "AR")
    (ms / "paper.md").write_text(para)
    info = rounds.return_round(ms, push=False)
    assert info["orphans"] == [t["id"]]


def test_a_unique_passage_that_moved_is_still_found(ms):
    text = "# A\n\nThe estimate is high in this one place only.\n\n# B\n\nOther words.\n"
    (ms / "paper.md").write_text(text)
    git(ms, "commit", "-qam", "One copy")
    rv = rounds.start(ms)
    s = text.index("high")
    d = review.add_draft(rv, review.anchor_at(rv, "paper.md", s, s + 4), "", "low", by="Agent")
    (ms / "paper.md").write_text("# B\n\nOther words.\n\n# A\n\nThe estimate is high in this one place only.\n")
    review.triage(review.active(ms), d["id"], True, "AR")
    assert "is low in this one place" in (ms / "paper.md").read_text()


def test_recovery_still_finishes_an_interrupted_write(ms, monkeypatch):
    real_write = state.atomic_write

    def interrupt_record(path, content):
        if path.name == "test-record.json":
            raise OSError("Injected interruption")
        return real_write(path, content)
    with monkeypatch.context() as patch:
        patch.setattr(state, "atomic_write", interrupt_record)
        with pytest.raises(OSError):
            with state.transaction(ms) as writes:
                writes["paper.md"] = "Transaction edit.\n"
                writes["test-record.json"] = '{"decision": "accepted"}'
    with state.transaction(ms):
        pass
    assert (ms / "paper.md").read_text() == "Transaction edit.\n"
    assert (ms / "test-record.json").read_text() == '{"decision": "accepted"}'


@pytest.mark.parametrize("path", ["../shared.md", "/tmp/shared.md", ".git/config", "link/paper.md"])
def test_sources_must_stay_inside_the_repository(ms, tmp_path, path):
    (tmp_path / "shared.md").write_text("Outside.\n")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "paper.md").write_text("Outside.\n")
    (ms / "link").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ScrivError):
        rounds.init(ms, [path])
    (ms / "scrivmark.toml").write_text(f'[manuscript]\nfiles = ["{path}"]\n')
    with pytest.raises(ScrivError):
        rounds.start(ms)
    assert (tmp_path / "shared.md").read_text() == "Outside.\n"


def test_an_ignored_source_cannot_join_a_round(ms):
    (ms / "notes.md").write_text("Private.\n")
    (ms / ".gitignore").write_text(".scrivmark/\nnotes.md\n")
    (ms / "scrivmark.toml").write_text('[manuscript]\nfiles = ["paper.md", "notes.md"]\n')
    git(ms, "commit", "-qam", "Ignore notes")
    with pytest.raises(ScrivError, match="not tracked"):
        rounds.start(ms)


def test_return_retry_repairs_a_failed_record_commit_and_tags(ms):
    rv = rounds.start(ms)
    (ms / "paper.md").write_text("# Results\n\nThere were 118 burrows.\n")
    hook = ms / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ngit diff --cached -- 'reviews/*.json' | grep -q '\"status\": \"returned\"' && exit 1\nexit 0\n")
    hook.chmod(0o755)
    with pytest.raises(ScrivError):
        rounds.return_round(ms, push=False)
    hook.unlink()
    rounds.return_round(ms, push=False)
    import json
    head = json.loads(git(ms, "show", f"HEAD:{rv.record_rel}"))["round"]
    assert head["status"] == "returned"
    for which in ("base", "returned"):
        assert git(ms, "rev-parse", f"scrivmark/{rv.id}/{which}^{{commit}}").strip() == head[which]
    again = git(ms, "rev-parse", "HEAD")
    rounds.return_round(ms, push=False)
    assert git(ms, "rev-parse", "HEAD") == again


def test_metadata_commits_leave_other_staged_work_alone(ms, monkeypatch):
    rv = rounds.start(ms)
    rounds.return_round(ms, push=False)
    (ms / "paper.md").write_text("# Results\n\nUnrelated staged author work.\n")
    git(ms, "add", "paper.md")
    monkeypatch.setenv("SCRIVMARK_AS", "Audit Author")
    with pytest.raises(ScrivError, match="Commit or stash"):
        rounds.open_round(ms, rv.id)
    assert git(ms, "show", "--format=", "--name-only", "HEAD").split() == [rv.record_rel]
    assert "paper.md" in git(ms, "diff", "--cached", "--name-only")


def test_a_detached_head_is_not_an_author_branch(ms):
    git(ms, "checkout", "-q", "--detach")
    with pytest.raises(ScrivError, match="not a local branch"):
        rounds.start(ms)


def test_bulk_reject_reports_what_it_did_before_stopping(ms, monkeypatch):
    import json
    import sys
    original = ("# Title\n\nAlpha is old.\n\n" + "An independent neutral paragraph gives ample separation. " * 3
                + "\n\nBeta is stale.\n")
    (ms / "paper.md").write_text(original)
    git(ms, "commit", "-qam", "Two passages")
    rv = rounds.start(ms)
    edited = original.replace("old", "new").replace("stale", "fresh")
    (ms / "paper.md").write_text(edited)
    rounds.return_round(ms, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Audit Author")
    rounds.open_round(ms, rv.id)
    (ms / "paper.md").write_text(edited.replace("Beta is", "Beta appears"))
    r = subprocess.run([sys.executable, "-m", "scrivmark.cli", "reject", "--all", "--json"], cwd=ms,
                       capture_output=True, text=True)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["done"] == ["c1"] and out["failed"]["id"] == "c2" and out["remaining"] == []
    assert [c["decision"] for c in record.load(ms, rv.id)["changes"]] == ["rejected", None]


def test_twins_that_differ_only_far_away_are_still_ambiguous(ms):
    p = ("This is an unchanged introduction with enough context before the estimate: high "
         "and this is an unchanged discussion with enough context after the estimate.")
    text = p + " North.\n\n" + p + " South.\n"
    (ms / "paper.md").write_text(text)
    git(ms, "commit", "-qam", "Near twins")
    rv = rounds.start(ms)
    s = text.index("high")
    d = review.add_draft(rv, review.anchor_at(rv, "paper.md", s, s + 4), "", "low", by="Agent")
    survivor = (p + " South.\n").replace("after the estimate", "after a estimate")
    (ms / "paper.md").write_text(survivor)
    review.keep_all(review.active(ms), "AR")
    assert (ms / "paper.md").read_text() == survivor
    assert [x["id"] for x in review.drafts(review.active(ms))] == [d["id"]]


def test_writes_recheck_the_repository_boundary(ms, tmp_path):
    (ms / "ch").mkdir()
    (ms / "ch" / "paper.md").write_text("There were 120 burrows.\n")
    (ms / "scrivmark.toml").write_text('[manuscript]\nfiles = ["ch/paper.md"]\n')
    git(ms, "add", "-A")
    git(ms, "commit", "-qm", "Chapter")
    rv = rounds.start(ms)
    review.add_draft(rv, review.anchor_quote(rv, "ch/paper.md", "120 burrows"), "", "118 burrows", by="Agent")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "paper.md").write_text("There were 120 burrows.\n")
    (ms / "ch" / "paper.md").unlink()
    (ms / "ch").rmdir()
    (ms / "ch").symlink_to(outside)
    rv.ms.files = ["ch/paper.md"]
    with pytest.raises(ScrivError):
        review.keep_all(rv, "AR")
    assert (outside / "paper.md").read_text() == "There were 120 burrows.\n"


def test_a_source_added_mid_round_must_be_tracked_to_be_edited(ms):
    rv = rounds.start(ms)
    (ms / "notes.md").write_text("Private.\n")
    (ms / ".gitignore").write_text(".scrivmark/\nnotes.md\n")
    (ms / "scrivmark.toml").write_text('[manuscript]\nfiles = ["paper.md", "notes.md"]\n')
    with pytest.raises(ScrivError, match="not tracked"):
        review.replace_lines(review.active(ms), "notes.md", 1, 1, "Changed.")
    assert (ms / "notes.md").read_text() == "Private.\n"


def test_a_dotted_path_to_a_tracked_source_is_accepted(ms):
    (ms / "scrivmark.toml").write_text('[manuscript]\nfiles = ["./paper.md"]\n')
    git(ms, "commit", "-qam", "Dotted path")
    rv = rounds.start(ms)
    assert rv.ms.files == ["paper.md"]


def test_a_tag_named_like_the_author_branch_does_not_choose_the_base(ms):
    first = git(ms, "rev-parse", "HEAD").strip()
    (ms / "paper.md").write_text("# Results\n\nThere were 121 burrows.\n")
    git(ms, "commit", "-qam", "Later")
    git(ms, "tag", "main", first)
    rv = rounds.start(ms, author_branch="main")
    assert rv.rec["round"]["base"] == git(ms, "rev-parse", "refs/heads/main").strip()


def test_keeping_a_named_draft_checks_the_revision(ms):
    from typer.testing import CliRunner

    from scrivmark.cli import app
    rv = rounds.start(ms)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "120 burrows"), "", "118 burrows", by="Agent")
    seen = rv.rev()
    review.revise_draft(rv, d["id"], wording="10 burrows", by="Agent")
    r = CliRunner().invoke(app, ["keep", d["id"], "--expect", seen])
    assert r.exit_code != 0
    assert "120 burrows" in (ms / "paper.md").read_text()


def test_an_old_format_intent_is_set_aside_not_crashed_on(ms):
    import json
    ip = state._intent_path(ms)
    ip.write_text(json.dumps({"paper.md": "Pending replacement.\n"}))
    with pytest.raises(ScrivError, match="could not be finished"):
        with state.transaction(ms):
            pass
    assert (ms / "paper.md").read_text() == "# Results\n\nThere were 120 burrows.\n"
    assert not ip.exists() and list(ip.parent.glob("intent-unfinished-*.json"))
