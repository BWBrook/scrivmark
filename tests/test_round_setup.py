"""Setting a round up and taking it down (0.7): the config travels on the round
branch, a round can be abandoned, the viewer stopped, and what init and the
return preview say."""
import json

import pytest
from starlette.testclient import TestClient

from scrivmark import config, review, rounds
from scrivmark.errors import ScrivError
from scrivmark.server import Viewer, build_app

from conftest import edit, git


@pytest.fixture
def bare(repo):
    """The tutorial manuscript with no scrivmark config committed: someone
    else's repository, before anyone has reviewed it with scrivmark."""
    git(repo, "rm", "-q", "scrivmark.toml")
    git(repo, "commit", "-qm", "no config yet")
    return repo


def test_config_goes_on_the_round_branch_and_survives_abandoning(bare):
    main = git(bare, "rev-parse", "main")
    rounds.init(bare, ["paper.md"])
    assert git(bare, "rev-parse", "HEAD") == main and (bare / "scrivmark.toml").exists()
    rv = rounds.start(bare)
    assert "scrivmark.toml" in git(bare, "show", "--name-only", "--format=", "HEAD")
    assert git(bare, "rev-parse", "main") == main
    review.comment(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "Kept.", "BWB")
    edit(bare, "proves that", "suggests that")
    with pytest.raises(ScrivError, match="1 kept comment"):
        rounds.abandon(bare)
    info = rounds.abandon(bare, force=True)
    assert git(bare, "branch", "--show-current").strip() == "main" and git(bare, "rev-parse", "main") == main
    assert info["files"] == ["paper.md"] and "suggests that" in git(bare, "show", f"{info['tag']}:paper.md")
    assert (bare / "scrivmark.toml").exists() and "proves that" in (bare / "paper.md").read_text()
    assert rounds.start(bare).id != rv.id


def test_init_reuses_the_config_an_earlier_round_carries_and_open_makes_way_for_it(bare):
    rounds.init(bare, ["paper.md"])
    first = (bare / "scrivmark.toml").read_text()
    rid = rounds.start(bare).id
    git(bare, "checkout", "-q", "main")
    assert not (bare / "scrivmark.toml").exists()
    assert "differs_from" in rounds.init(bare, ["TUTORIAL.md"], write=False)
    found = rounds.init(bare, write=False)
    assert found["source"].endswith(f"review/{rid}") and found["files"] == ["paper.md"]
    rounds.init(bare, write=True)
    assert (bare / "scrivmark.toml").read_text() == first
    rounds.open_round(bare, rid)
    assert git(bare, "branch", "--show-current").strip() == f"review/{rid}"
    git(bare, "checkout", "-q", "main")
    (bare / "scrivmark.toml").write_text(first.replace("paper.md", "TUTORIAL.md"))
    with pytest.raises(ScrivError, match="differs from the one"):
        rounds.open_round(bare, rid)


def test_init_counts_references_written_out_in_the_text(bare):
    (bare / "refs.bib").unlink()
    (bare / "paper.md").write_text("# T\n\nText.\n\n## References {#refs}\n\n"
                                   "Smith J (2020) A paper. https://doi.org/10.1000/xyz1\n\n"
                                   "- Jones K (2019) Another. doi:10.1000/abc\n- Lee C (2021) No identifier.\n\n"
                                   "# Appendix\n\nNot a reference.\n")
    info = rounds.init(bare, ["paper.md"], write=False)
    assert info["inline_references"] == {"count": 3, "doi": 2, "files": ["paper.md"]}


def test_the_return_preview_names_agents_the_statement_leaves_out(repo):
    rv = rounds.start(repo)
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "dig burrows"), "A point.", None, by="Claude (model B)")
    review.keep_all(review.active(repo), "BWB")
    notes = rounds.preflight(repo)["notes"]
    assert any("does not name Claude (model B)" in n for n in notes)
    assert not any("does not name" in n for n in rounds.preflight(
        repo, statement="Reviewed by Barry W. Brook working with Claude (model B).")["notes"])


def test_only_a_caller_holding_the_token_can_stop_the_viewer(repo):
    rounds.start(repo)
    viewer = Viewer(repo)

    class Server:
        should_exit = False

    viewer.server = Server()
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    assert client.post("/api/stop").status_code == 403 and not viewer.server.should_exit
    assert client.post("/api/stop", headers={"x-scrivmark": viewer.token}).json() == {"stopping": True}
    assert viewer.server.should_exit
