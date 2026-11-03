import re

from starlette.testclient import TestClient

from scrivmark import config, render, rounds
from scrivmark.server import Viewer, build_app

from conftest import edit


def test_redline_crossrefs_and_deleted_citation(repo):
    rv = rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    edit(repo, "[@royle2003]", "[@mackenzie2002]")
    ms = config.load(repo)
    html, _ = render.render(repo, ms, rv.rec["round"]["base"])
    assert 'class="scrv-del">proves that<' in html
    assert "Figure 1:" in html and "Table 1:" in html and "(1)" in html
    assert 'href="#fig-burrows"' in html
    assert "(Royle and Nichols 2003)" in html
    refs = html[html.index('id="refs"'):]
    assert "Royle, J. Andrew, and" not in refs and "MacKenzie" in refs
    assert 'class="scrv-old"' not in html
    assert re.search(r'<div class="scrv-b" (?=[^>]*data-file="paper.md")(?=[^>]*data-lines="\d+-\d+")', html)
    assert ' n="' not in html and "data-n=" not in html


def test_manuscript_cannot_script_the_viewer_or_claim_source(repo):
    edit(repo, "# Methods", '# Methods\n\n![x](nope.png){onerror="alert(1)"} [hi]{onclick="alert(2)" .scrv-ins}\n\n'
         '::: {.scrv-b file="/etc/hostname" lines="1-1"}\nbait\n:::')
    ms = config.load(repo)
    html, _ = render.render(repo, ms)
    assert "onerror" not in html and "onclick" not in html
    assert "/etc/hostname" not in html
    assert '<span class="scrv-ins"' not in html


def test_server_requires_token_and_refuses_stale_edits(repo):
    rounds.start(repo)
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    assert client.get("/api/state").status_code == 403
    client.cookies.set(viewer.cookie, viewer.token)
    st = client.get("/api/state").json()
    assert st["mode"] == "reviewer"
    src = client.get("/api/source", params={"file": "paper.md", "lines": "8-13"}).json()
    edit(repo, "Bare-nosed", "Common")
    r = client.post("/api/source", json={**src, "text": "changed"})
    assert r.status_code == 409
    r = client.post("/api/comment", json={"file": "paper.md", "lines": "8-13", "text": "dig burrows", "body": "Hm."})
    assert r.status_code == 200 and r.json()["anchor"]["exact"] == "dig burrows"
    assert client.get("/files/.git/HEAD").status_code == 404


def test_viewer_access_rules(repo):
    rounds.start(repo)
    viewer = Viewer(repo, 8765)
    app = build_app(viewer)
    local = TestClient(app, base_url="http://localhost:8765")
    assert local.get("/").status_code == 200
    assert local.get("/api/state").status_code == 200
    other = TestClient(app, base_url="http://localhost:8765")
    assert other.get("/", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert other.get("/api/state").status_code == 403
    rebound = TestClient(app, base_url="http://attacker.example:8765")
    assert rebound.get("/", params={"t": viewer.token}).status_code == 403


def test_viewer_code_survives_restart(repo):
    import json

    from scrivmark.server import viewer_file

    first = Viewer(repo, 8765)
    viewer_file(repo).write_text(json.dumps({"token": first.token, "port": 8765}))
    assert Viewer(repo, 8765).token == first.token
