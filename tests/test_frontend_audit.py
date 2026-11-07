import pytest
from starlette.testclient import TestClient

from scrivmark import rounds
from scrivmark.server import Viewer, _highlight_spec, build_app

from conftest import git

CASES = [
    ("The rate is **low** while rate is low.", "rate is low", 1, 2),
    ("The *rate* is low and the rate is low.", "rate is low", 1, 2),
    ("See [rate is low](http://x.org/a) and rate is low.", "rate is low", 1, 2),
    ("Mark \\*star\\* b, star b.", "star b", 0, 1),
    ("A **b** x and b x.", "b x", 1, 2),
]


@pytest.mark.parametrize("source,quote,nth,count", CASES)
def test_highlight_spec_counts_in_the_plain_text_the_page_shows(source, quote, nth, count):
    s = source.rindex(quote) if source.count(quote) > 1 and nth else source.index(quote)
    spec = _highlight_spec(source, s, s + len(quote), False)
    assert spec["nth"] == nth and spec["count"] == count and spec["text"].endswith(quote.split()[-1])


def test_state_gives_second_occurrence_of_a_repeated_phrase(repo):
    (repo / "paper.md").write_text("# T\n\nThe rate is **low** while rate is low.\n")
    git(repo, "commit", "-qam", "fixture")
    rounds.start(repo)
    viewer = Viewer(repo)
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    c.cookies.set(viewer.cookie, viewer.token)
    rev = c.get("/api/state").json()["rev"]
    r = c.post("/api/comment", json={"file": "paper.md", "lines": "3-3", "text": "rate is low",
                                     "before": "The rate is low while ", "body": "x", "rev": rev})
    assert r.status_code == 200
    th = c.get("/api/state").json()["threads"][0]
    assert th["hl"] == {"text": "rate is low", "nth": 1, "count": 2}


def test_escaped_text_and_maths_count_as_the_page_shows_them():
    source = "The value N\\_e then $Ne$ and N\\_e again."
    s = source.index("N\\_e")
    assert _highlight_spec(source, s, s + 4, False) == {"text": "N_e", "nth": 0, "count": 2}
