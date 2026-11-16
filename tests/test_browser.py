"""The viewer driven in a real browser (Chromium through Playwright).

Locally these skip when Chromium is not installed (`uv run playwright install
chromium`); under CI a missing browser is a failure."""
import os
import socket
import threading
import time

import pytest
import uvicorn

from conftest import edit
from scrivmark import review, rounds
from scrivmark.server import Viewer, build_app

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

SELECT = """text => {
  const walk = document.createTreeWalker(document.querySelector('#doc'), NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) {
    const i = n.data.indexOf(text);
    if (i < 0) continue;
    const r = document.createRange();
    r.setStart(n, i); r.setEnd(n, i + text.length);
    getSelection().removeAllRanges(); getSelection().addRange(r);
    return true;
  }
  return false;
}"""


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except sync_api.Error:
            if os.environ.get("CI"):
                raise
            pytest.skip("Chromium for Playwright is not installed: uv run playwright install chromium")
        yield b
        b.close()


@pytest.fixture
def view(repo, browser):
    """Call it once the round is set up: it serves the repository and returns a page on the viewer."""
    made = []

    def open_page(width=1200, height=800):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        viewer = Viewer(repo, port)
        server = uvicorn.Server(uvicorn.Config(build_app(viewer), host="127.0.0.1", port=port, log_level="error",
                                               timeout_graceful_shutdown=1))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        context = browser.new_context(viewport={"width": width, "height": height})
        made.append((server, thread, context))
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{port}/?t={viewer.token}")
        page.wait_for_selector("#doc .scrv-b")
        return page

    yield open_page
    for server, thread, context in made:
        context.close()
        server.should_exit = True
        thread.join(5)


def select(page, text):
    assert page.evaluate(SELECT, text), text
    expect(page.locator("#tool")).to_be_visible()


def test_a_comment_is_made_from_a_selection(repo, view):
    rounds.start(repo)
    page = view()
    expect(page.locator("#mode")).to_have_text("Reviewing")
    select(page, "imperfect detection")
    page.click('#tool [data-act="comment"]')
    page.fill("#pop textarea", "Say what the detection rate was.")
    page.click("#pop button.primary")
    card = page.locator("#margin .card.open")
    expect(card.locator(".body")).to_have_text("Say what the detection rate was.")
    expect(page.locator("mark.scrv-hl")).to_have_text("imperfect detection")
    thread = review.active(repo).rec["threads"][0]
    assert thread["anchor"]["exact"] == "imperfect detection"


def test_a_passage_is_edited_in_place_and_shows_as_a_tracked_change(repo, view):
    rounds.start(repo)
    page = view()
    block = page.locator("#doc .scrv-b", has_text="proves that")
    block.hover()
    block.locator("button.edit").click()
    box = page.locator(".editor textarea")
    box.fill(box.input_value().replace("proves that", "tests whether"))
    page.click(".editor button.primary")
    expect(page.locator("#doc .scrv-ins", has_text="tests whether")).to_be_visible()
    expect(page.locator("#doc .scrv-del", has_text="proves that")).to_be_visible()
    assert "tests whether wombats" in (repo / "paper.md").read_text()


def test_a_draft_arrives_live_and_is_kept_and_applied(repo, view):
    rv = rounds.start(repo)
    page = view()
    expect(page.locator(".card.draft")).to_have_count(0)
    review.add_draft(rv, review.anchor_quote(rv, "paper.md", "proves that"), "Too strong.", "tests whether", by="Agent")
    card = page.locator(".card.draft")
    expect(card.locator(".wording")).to_have_text("→ tests whether")
    expect(card.get_by_role("button", name="Keep & apply")).to_be_hidden()
    card.click()
    card.get_by_role("button", name="Keep & apply").click()
    expect(page.locator(".card.draft")).to_have_count(0)
    expect(page.locator("#margin .card.open .body")).to_have_text("Too strong.")
    assert "tests whether wombats" in (repo / "paper.md").read_text()


def test_a_draft_revised_during_an_edit_is_not_overwritten(repo, view):
    """F01 of the 0.7.0 review: the editor was redrawn with the reader's text
    and the new revision, so Keep stored the old wording over the agent's."""
    rv = rounds.start(repo)
    d = review.add_draft(rv, review.anchor_quote(rv, "paper.md", "proves that"), "First version.", None, by="Agent")
    page = view()
    card = page.locator(".card.draft")
    card.get_by_role("button", name="Edit").click()
    card.locator("textarea").first.fill("The reader's wording.")
    review.revise_draft(review.active(repo), d["id"], body="Second version.", by="Agent")
    expect(page.locator(".card.draft")).to_contain_text("revised while you were editing")
    page.locator(".card.draft .acts").last.get_by_role("button", name="Keep", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("changed while you were working")
    now = review.active(repo)
    assert [x["body"] for x in review.drafts(now)] == ["Second version."] and not now.rec["threads"]


def returned(repo, monkeypatch):
    rv = rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    review.comment(rv, review.anchor_quote(rv, "paper.md", "imperfect detection"), "How imperfect?", "BWB")
    rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")


def test_the_author_accepts_a_change_and_can_undo_it(repo, view, monkeypatch):
    returned(repo, monkeypatch)
    page = view()
    expect(page.locator("#mode")).to_have_text("Responding")
    expect(page.locator("#counts")).to_contain_text("1 change to decide")
    page.locator('#doc [data-c="c1"]').first.click()
    expect(page.locator("#pop .diff")).to_contain_text("proves that")
    page.locator("#pop").get_by_role("button", name="Accept").click()
    expect(page.locator("#counts")).to_contain_text("0 changes to decide")
    assert review.changes(review.active(repo))[0]["state"] == "accepted"
    page.locator("#notice").get_by_role("button", name="Undo").click()
    expect(page.locator("#counts")).to_contain_text("1 change to decide")
    assert review.changes(review.active(repo))[0]["state"] == "pending"


def test_j_stops_once_at_a_replacement(repo, view, monkeypatch):
    """A replacement is drawn as a deleted and an inserted span; 0.7.0 stopped at each."""
    returned(repo, monkeypatch)
    page = view()
    assert page.locator('#doc [data-c="c1"]').count() == 2
    assert page.evaluate("queue().length") == 2
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    page.reload()
    page.wait_for_selector("#doc .scrv-b")
    expect(page.locator("#mode")).to_have_text("Waiting")
    assert page.evaluate("queue().length") == 2


def test_an_open_decision_box_closes_when_the_round_goes(repo, view, monkeypatch):
    """F02 of the 0.7.0 review: the box outlived its round and its buttons
    acted on whichever change had that number in the next one."""
    returned(repo, monkeypatch)
    page = view()
    page.locator('#doc [data-c="c1"]').first.click()
    expect(page.locator("#pop")).to_be_visible()
    rounds.close_round(repo, force=True, push=False)
    expect(page.locator("#mode")).not_to_have_text("Responding")
    expect(page.locator("#pop")).to_be_hidden()


def test_the_bar_and_help_fit_a_narrow_pane(repo, view):
    rv = rounds.start(repo)
    for quote in ("proves that", "imperfect detection"):
        review.add_draft(rv, review.anchor_quote(rv, "paper.md", quote), "Note.", None, by="Agent")
    page = view(width=640, height=480)
    assert page.evaluate("document.querySelector('#bar').offsetHeight") < 60
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.keyboard.press("?")
    expect(page.locator("#helpbox")).to_be_visible()
    box = page.evaluate("(r => [r.top, r.bottom])(document.querySelector('#helpbox').getBoundingClientRect())")
    assert box[0] >= 0 and box[1] <= 480
    button = page.locator("#helpbox button")
    button.scroll_into_view_if_needed()
    button.click()
    expect(page.locator("#helpbox")).to_be_hidden()


def test_a_moved_paragraph_is_one_stop_and_rejecting_it_puts_it_back(repo, view, monkeypatch):
    """Up to 0.9.0 a move was a removal and an insertion, each decided alone."""
    old = (repo / "paper.md").read_text()
    paras = old.split("\n\n")
    last = max(i for i, p in enumerate(paras) if p.startswith("Wombats clearly prefer"))
    paras.insert(2, paras.pop(last))
    rounds.start(repo)
    (repo / "paper.md").write_text("\n\n".join(paras))
    rounds.return_round(repo, push=False)
    monkeypatch.setenv("SCRIVMARK_AS", "Sam Student")
    page = view()
    expect(page.locator("#counts")).to_contain_text("1 change to decide")
    expect(page.locator('#doc .scrv-to[data-c="c1"]')).to_have_count(1)
    assert page.evaluate("queue().length") == 1
    page.locator('#doc .scrv-from[data-c="c1"]').click()
    expect(page.locator("#pop")).to_contain_text("Moved from line")
    page.locator("#pop").get_by_role("button", name="Reject").click()
    expect(page.locator("#counts")).to_contain_text("0 changes to decide")
    assert (repo / "paper.md").read_text() == old
