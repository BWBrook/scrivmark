"""From the practice round on a 13,700-word paper, 16 November 2026
(docs/audits/2026-11-16-stress-round.md): whatever the reviewer did, rejecting
every change restores the authors' text exactly."""
import random

import pytest

from conftest import git
from scrivmark import review, rounds
from scrivmark.server import Viewer

A, B, C = "Alpha one two.", "Bravo three four.", "Charlie five six."
WORDS = ("burrow wombat aspect slope occupancy detection survey winter sun north model estimate "
         "probability visit sign recent thermal benefit choice data season interval forecast error").split()


def round_trip(repo, monkeypatch, old, new, order=None):
    (repo / "paper.md").write_text(old)
    git(repo, "commit", "-qam", "fixture")
    rounds.start(repo)
    (repo / "paper.md").write_text(new)
    rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    ids = [c["id"] for c in review.active(repo).rec["changes"]]
    (order or random.Random(0)).shuffle(ids)
    for cid in ids:
        review.decide(review.active(repo), cid, "rejected", "SS")
    rejected = (repo / "paper.md").read_text()
    for cid in ids:
        review.undo(review.active(repo), cid)
    return rejected, (repo / "paper.md").read_text()


@pytest.mark.parametrize("old,new", [
    (f"{A}\n\n{B}\n", f"{A}\n"),
    (f"{A}\n", f"{A}\n\n{B}\n"),
    (f"{A}\n\n{B}\n\n{C}\n", f"{C}\n\n{B}\n"),
    (f"{A}\n\n{B}\n\n{C}\n", f"{B}\n\n{C}\n\n{A}\n"),
    (f"{A}\n\n{B}\n\n{C}\n", f"{A}\n\n{C}\n\n{B}\n"),
], ids=["last removed", "one appended", "first removed, last moved up", "first moved last", "last two swapped"])
def test_rejecting_a_change_at_the_end_of_the_file_restores_the_blank_line(repo, monkeypatch, old, new):
    """Up to 0.8.0 a removed last paragraph, rejected, came back joined to the
    paragraph above: the blank line between them was in neither version's change."""
    rejected, undone = round_trip(repo, monkeypatch, old, new)
    assert rejected == old and undone == new


def test_a_removed_last_paragraph_still_shows_as_removed(repo):
    (repo / "paper.md").write_text(f"{A}\n\n{B}\n")
    git(repo, "commit", "-qam", "fixture")
    rounds.start(repo)
    (repo / "paper.md").write_text(f"{A}\n")
    html = Viewer(repo).state()["html"]
    assert "Bravo three four." in html and "scrv-old" in html


def paragraph(rng):
    return " ".join(" ".join(rng.choice(WORDS) for _ in range(rng.randint(6, 14))).capitalize() + "."
                    for _ in range(rng.randint(1, 4)))


def mutate(rng, paras):
    paras = list(paras)
    for _ in range(rng.randint(1, 6)):
        kind = rng.choice("edit edit rewrite move delete insert split join".split())
        i = rng.randrange(len(paras))
        w = paras[i].split(" ")
        if kind == "edit":
            w[rng.randrange(len(w))] = rng.choice(["quite", "(new)", "the"])
            paras[i] = " ".join(w)
        elif kind == "rewrite" and len(w) > 8:
            a = rng.randrange(len(w) - 6)
            for j in range(a, a + 6, 2):
                w[j] = rng.choice(["alpha", "beta", "gamma"])
            paras[i] = " ".join(w)
        elif kind == "move":
            paras.insert(rng.randrange(len(paras)), paras.pop(i))
        elif kind == "delete" and len(paras) > 2:
            paras.pop(i)
        elif kind == "insert":
            paras.insert(i, paragraph(rng))
        elif kind == "split" and len(w) > 10:
            paras[i : i + 1] = [" ".join(w[: len(w) // 2]), " ".join(w[len(w) // 2 :])]
        elif kind == "join" and i + 1 < len(paras):
            paras[i : i + 2] = [paras[i] + " " + paras[i + 1]]
    return paras


@pytest.mark.parametrize("seed", range(12))
def test_any_mix_of_edits_moves_splits_and_joins_can_be_rejected_whole(repo, monkeypatch, seed):
    rng = random.Random(seed)
    paras = [paragraph(rng) for _ in range(rng.randint(3, 7))]
    old, new = "\n\n".join(paras) + "\n", "\n\n".join(mutate(rng, paras)) + "\n"
    if old == new:
        return
    rejected, undone = round_trip(repo, monkeypatch, old, new, rng)
    assert rejected == old and undone == new
