"""A passage moved from one place to another is one change (0.10.0). Before,
it was a removal and an insertion decided separately: accepting one and
rejecting the other left the passage twice or not at all."""
import time

import pytest

from conftest import git
from scrivmark import review, rounds
from scrivmark.server import Viewer
from scrivmark.textdiff import diff_units

P = [
    "Wombats dig their burrows on slopes that face the winter sun, and the pattern holds across the survey.",
    "Detection was imperfect, so each burrow was visited four times in the season to estimate occupancy.",
    "The thermal benefit of a north aspect was largest in the coldest month of the year.",
    "Forecasts from the fitted model were checked against a second season of sign surveys.",
    "We found no effect of slope once aspect had been accounted for in the occupancy model.",
]


def doc(*order):
    return "\n\n".join(P[i] if isinstance(i, int) else i for i in order) + "\n"


def kinds(old, new):
    return [u.type for u in diff_units(old, new)]


def test_a_moved_paragraph_is_one_change():
    u, = diff_units(doc(0, 1, 2, 3, 4), doc(0, 3, 1, 2, 4))
    assert u.type == "move" and u.old_text.strip() == u.new_text.strip() == P[3]
    assert u.left.new[0] > u.arrived.new[1]


def test_a_paragraph_moved_and_edited_is_one_change():
    edited = P[3].replace("a second season", "two further seasons")
    u, = diff_units(doc(0, 1, 2, 3, 4), doc(edited, 0, 1, 2, 4))
    assert u.type == "move" and "second" in u.old_text and "further" in u.new_text


def test_paragraphs_moved_together_are_one_change():
    u, = diff_units(doc(0, 1, 2, 3, 4), doc(2, 3, 0, 1, 4))
    assert u.type == "move" and u.new_text.count("\n\n") >= 1


def test_a_move_beside_other_changes_stays_one_change():
    """The paragraph above the one that moved is deleted, and a new one is written where it lands."""
    assert sorted(kinds(doc(0, 1, 2, 3, 4), doc(0, "A new paragraph about nothing in particular.", 3, 1, 4))) == ["block", "block", "move"]


def test_a_removal_and_an_unrelated_insertion_are_not_a_move():
    assert kinds(doc(0, 1, 2, 3), doc(0, 2, 3, 4)) == ["block", "block"]


def test_a_split_or_joined_paragraph_is_not_a_move():
    a, b = P[1].split(", so ")
    assert kinds(doc(0, 1, 2), doc(0, a + ".", "So " + b, 2)) == ["block"]
    assert kinds(doc(0, 1, 2), doc(0, P[1] + " " + P[2])) == ["block"]


def test_a_long_run_of_edited_paragraphs_is_not_searched_for_moves():
    """Found by Codex's review of this change: a wording edit through 2,048
    paragraphs took 50 s where it had taken 0.6 s, every removed paragraph
    being compared with every added one."""
    old = "\n\n".join(f"Observation {i} describes ecological variation across mountains and valleys." for i in range(300)) + "\n"
    start = time.perf_counter()
    assert kinds(old, old.replace("ecological variation", "seasonal patterns")) == ["block"]
    assert time.perf_counter() - start < 5


def returned(repo, monkeypatch, old, new):
    (repo / "paper.md").write_text(old)
    git(repo, "commit", "-qam", "fixture")
    rounds.start(repo)
    (repo / "paper.md").write_text(new)
    rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    return review.active(repo)


def test_rejecting_a_move_puts_the_passage_back_and_undo_moves_it_again(repo, monkeypatch):
    old, new = doc(0, 1, 2, 3, 4), doc(0, 3, 1, 2, 4)
    rv = returned(repo, monkeypatch, old, new)
    c, = review.changes(rv)
    assert c["type"] == "move" and c["state"] == "pending" and (c["line"], c["from_line"]) == (3, 9)
    review.decide(rv, "c1", "rejected", "SS")
    assert (repo / "paper.md").read_text() == old
    review.undo(review.active(repo), "c1")
    assert (repo / "paper.md").read_text() == new
    review.decide(review.active(repo), "c1", "accepted", "SS")
    assert (repo / "paper.md").read_text() == new


@pytest.mark.parametrize("old,new", [
    (doc(0, 1, 2), doc(0, 2, 1)),
    (doc(0, 1, 2), doc(2, 0, 1)),
    (doc(0, 1, 2, 3, 4), doc(3, 4, 0)),
    (doc(0, 1, 2, 3, 4), doc(4, 0, 2, 1)),
], ids=["last two swapped", "last moved first", "two removed, two moved up", "two moves from the end"])
def test_moves_at_the_end_of_the_file_reject_exactly(repo, monkeypatch, old, new):
    rv = returned(repo, monkeypatch, old, new)
    assert "move" in {c["type"] for c in rv.rec["changes"]}
    for c in rv.rec["changes"]:
        review.decide(review.active(repo), c["id"], "rejected", "SS")
    assert (repo / "paper.md").read_text() == old


def test_a_move_whose_passage_the_author_edited_is_superseded(repo, monkeypatch):
    rv = returned(repo, monkeypatch, doc(0, 1, 2, 3, 4), doc(0, 3, 1, 2, 4))
    (repo / "paper.md").write_text((repo / "paper.md").read_text().replace("second season", "third season"))
    c, = review.changes(review.active(repo))
    assert c["state"] == "superseded"
    with pytest.raises(review.Conflict):
        review.decide(review.active(repo), "c1", "rejected", "SS")


def test_a_move_is_not_rejected_when_the_place_it_left_was_edited(repo, monkeypatch):
    rv = returned(repo, monkeypatch, doc(0, 1, 2, 3, 4), doc(0, 3, 1, 2, 4))
    before = (repo / "paper.md").read_text().replace("coldest month of the year.", "coldest month.")
    (repo / "paper.md").write_text(before)
    with pytest.raises(review.Conflict):
        review.decide(review.active(repo), "c1", "rejected", "SS")
    assert (repo / "paper.md").read_text() == before


def test_the_viewer_marks_both_ends_of_a_move(repo, monkeypatch):
    (repo / "paper.md").write_text(doc(0, 1, 2, 3, 4))
    git(repo, "commit", "-qam", "fixture")
    rounds.start(repo)
    (repo / "paper.md").write_text(doc(0, 3, 1, 2, 4))
    html = Viewer(repo).state()["html"]
    assert html.count("scrv-from") == 1 and html.count("scrv-to") == 1
    rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    html = Viewer(repo).state()["html"]
    assert html.count('data-c="c1"') == 2 and html.count("scrv-from") == 1 and html.count("scrv-to") == 1


def test_a_removed_last_paragraph_is_shown_in_place_to_the_author(repo, monkeypatch):
    """In 0.9.0 it was listed under the text as "not shown in the running text"."""
    returned(repo, monkeypatch, doc(0, 1, 2), doc(0, 1))
    html = Viewer(repo).state()["html"]
    assert "scrv-old" in html and "coldest month" in html
