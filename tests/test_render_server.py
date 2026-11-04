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


def test_port_closing_a_stopped_viewers_connection_counts_as_free():
    import socket

    from scrivmark.server import _port_free

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn's listener does
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen()
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    assert not _port_free(port)
    conn.close()
    server.close()
    assert _port_free(port)
    client.close()


def test_launcher_port_comes_after_the_flag_and_before_the_config(monkeypatch):
    from scrivmark import server

    monkeypatch.setattr(server, "_port_free", lambda port: True)
    monkeypatch.setenv("PORT", "9001")
    assert server.choose_port(9100, 9200) == 9100
    assert server.choose_port(0, 9200) == 9001
    monkeypatch.delenv("PORT")
    assert server.choose_port(0, 9200) == 9200
    assert server.choose_port(0, None) == 8765


def test_reference_list_entries_can_be_commented_on_and_edited(repo):
    rv = rounds.start(repo)
    viewer = Viewer(repo)
    html = viewer.state()["html"]
    entry = re.search(r'<div id="ref-royle2003" class="csl-entry scrv-b scrv-ref" data-file="refs.bib" data-lines="(\d+)-(\d+)"', html)
    assert entry
    lines = f"{entry.group(1)}-{entry.group(2)}"
    c = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    h = {"x-scrivmark": viewer.token}
    key = viewer.state()["key"]
    t = c.post("/api/comment", headers=h, json={"file": "refs.bib", "lines": lines, "text": "Ecology", "before": "",
                                                "key": key, "body": "Check the DOI.", "rev": rv.rev()}).json()
    assert t["anchor"]["file"] == "refs.bib" and t["anchor"]["exact"] == "Ecology"
    assert viewer.state()["threads"][0]["line"] == [int(entry.group(1))] * 2
    src = c.get(f"/api/source?file=refs.bib&lines={lines}", headers=h).json()
    assert src["text"].startswith("@article{royle2003")
    r = c.post("/api/source", headers=h, json={"file": "refs.bib", "first": src["first"], "last": src["last"],
                                               "text": src["text"].replace("year={2003}", "year={2004}"),
                                               "file_rev": src["file_rev"]})
    assert r.status_code == 200 and "year={2004}" in (repo / "refs.bib").read_text()


def test_bibliography_entries_span_whole_csl_yaml_and_json_items():
    from scrivmark.render import _bib_spans

    y = ["---", "references:", "- id: a1", "  author:", "    - family: Brook", "  title: T", "",
         "- type: book", '  id: "b2"', "..."]
    assert _bib_spans("r.yaml", y) == [(2, 6, "a1"), (7, 8, "b2")]
    j = ["[", '  {"id": "a1", "title": "Brace } in \\"t\\" {"},', "  {", '    "id": "b2"', "  }", "]"]
    assert _bib_spans("r.json", j) == [(1, 1, "a1"), (2, 4, "b2")]


def test_html_comments_show_as_muted_notes_not_literal_text(repo):
    edit(repo, "# Methods", "# Methods\n\n<!-- Reviewer, round 1\n[K1 -- scope] Keep this. -->\n\nWe used <!-- check --> traps.")
    html, _ = render.render(repo, config.load(repo))
    assert "&lt;!" not in html and "--&gt;" not in html and "–&gt;" not in html
    assert re.search(r'<div class="scrv-note">\s*<p>Reviewer, round 1 \[K1 – scope\] Keep this.</p>', html)
    assert 'We used <span class="scrv-note">check</span> traps.' in html


def test_second_serve_reuses_this_repositorys_viewer(repo, monkeypatch, capsys):
    import json
    import os
    import socket

    import pytest

    from scrivmark import server
    from scrivmark.errors import ScrivError

    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen()
    port = held.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?t=x"
    server.viewer_file(repo).write_text(json.dumps({"token": "x", "port": port, "url": url, "pid": os.getpid()}))
    monkeypatch.delenv("PORT", raising=False)
    assert server.run(repo) is False
    assert url in capsys.readouterr().out
    monkeypatch.setenv("PORT", str(port))
    assert server.run(repo) is False
    monkeypatch.setenv("PORT", str(port + 1))
    with pytest.raises(ScrivError, match="already running on port"):
        server.run(repo)
    held.close()


def test_doctor_fails_only_on_missing_essentials(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from scrivmark import config
    from scrivmark.cli import app

    monkeypatch.setattr(config, "IDENTITY", tmp_path / "none.toml")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    ok = CliRunner().invoke(app, ["doctor"])
    assert ok.exit_code == 0 and "· repository" in ok.output
    monkeypatch.delenv("SCRIVMARK_AS")
    bad = CliRunner().invoke(app, ["doctor"])
    assert bad.exit_code == 1 and "✗ identity" in bad.output
