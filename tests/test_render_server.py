import re

from starlette.testclient import TestClient

from scrivmark import config, render, rounds
from scrivmark.server import COOKIE, Viewer, build_app

from conftest import edit


def test_redline_crossrefs_and_deleted_citation(repo):
    rv = rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    edit(repo, "[@royle2003]", "[@mackenzie2002]")
    ms = config.load(repo)
    html, _ = render.to_html(repo, ms, render.manuscript_markdown(repo, ms, rv.rec["round"]["base"]))
    assert 'class="scrv-del">proves that<' in html
    assert "Figure 1:" in html and "Table 1:" in html and "(1)" in html
    assert 'href="#fig-burrows"' in html
    assert "(Royle and Nichols 2003)" in html
    refs = html[html.index('id="refs"'):]
    assert "Royle, J. Andrew, and" not in refs and "MacKenzie" in refs
    assert 'class="scrv-old"' not in html
    assert re.search(r'data-file="paper.md" data-lines="\d+-\d+"', html)


def test_server_requires_token_and_refuses_stale_edits(repo):
    rounds.start(repo)
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer))
    assert client.get("/api/state").status_code == 403
    client.cookies.set(COOKIE, viewer.token)
    st = client.get("/api/state").json()
    assert st["mode"] == "reviewer"
    src = client.get("/api/source", params={"file": "paper.md", "lines": "8-13"}).json()
    edit(repo, "Bare-nosed", "Common")
    r = client.post("/api/source", json={**src, "text": "changed"})
    assert r.status_code == 409
    r = client.post("/api/comment", json={"file": "paper.md", "lines": "8-13", "text": "dig burrows", "body": "Hm."})
    assert r.status_code == 200 and r.json()["anchor"]["exact"] == "dig burrows"
    assert client.get("/files/.git/HEAD").status_code == 404
