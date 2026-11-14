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
    assert client.post("/api/comment", json={"file": "paper.md", "lines": "8-13", "text": "dig burrows",
                                              "body": "Hm."}).status_code == 409
    rev = client.get("/api/state").json()["rev"]
    r = client.post("/api/comment", json={"file": "paper.md", "lines": "8-13", "text": "dig burrows", "body": "Hm.",
                                          "rev": rev})
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
                                               "file_rev": src["file_rev"], "round": src["round"]})
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
    from scrivmark import release
    monkeypatch.setattr(release, "latest", lambda: "99.0.0")
    ok = CliRunner().invoke(app, ["doctor"])
    assert ok.exit_code == 0 and "· repository" in ok.output
    assert "99.0.0 is out" in ok.output and "restart any running viewer" in ok.output
    monkeypatch.delenv("SCRIVMARK_AS")
    bad = CliRunner().invoke(app, ["doctor"])
    assert bad.exit_code == 1 and "✗ identity" in bad.output


def test_image_size_survives_but_other_styles_do_not():
    from scrivmark import sanitize

    html = sanitize.clean('<p><img src="a.png" style="width:95%" /><img src="b.png" style="width:5em;background:url(x)" />'
                          '<span style="width:9px">t</span></p>')
    assert 'style="width:95%"' in html and "background" not in html and "<span>" in html


def test_citations_are_cached_and_follow_the_bibliography(repo):
    rv = rounds.start(repo)
    ms = config.load(repo)
    first, _ = render.render(repo, ms, rv.rec["round"]["base"])
    cached = list((repo / ".scrivmark" / "cache" / "cites").glob("*.json"))
    assert cached
    edit(repo, "proves that", "tests whether")
    second, _ = render.render(repo, ms, rv.rec["round"]["base"])
    assert sorted((repo / ".scrivmark" / "cache" / "cites").glob("*.json")) == sorted(cached)
    assert "Royle and Nichols 2003" in second
    edit(repo, "author={Royle, J. Andrew and Nichols", "author={Rolye, J. Andrew and Nichols", "refs.bib")
    third, _ = render.render(repo, ms, rv.rec["round"]["base"])
    assert "Rolye and Nichols 2003" in third and "Royle and Nichols 2003" not in third


def test_a_missing_citation_still_warns_when_the_cache_is_used(repo):
    edit(repo, "[@royle2003]", "[@nosuch2020]")
    ms = config.load(repo)
    warnings = [render.render(repo, ms, None)[1] for _ in range(2)]
    assert all("citation nosuch2020 not found" in w for w in warnings)


def test_version_is_stated_once_per_file_and_has_a_changelog_entry():
    import tomllib
    from pathlib import Path

    from typer.testing import CliRunner

    from scrivmark import __version__
    from scrivmark.cli import app

    root = Path(__file__).resolve().parents[1]
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == __version__
    assert f"## {__version__} " in (root / "CHANGELOG.md").read_text()
    assert f"version: {__version__}" in (root / "CITATION.cff").read_text()
    assert CliRunner().invoke(app, ["--version"]).output.strip() == f"scrivmark {__version__}"


def test_viewer_marks_and_edits_your_own_comments(repo):
    rounds.start(repo)
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    client.cookies.set(viewer.cookie, viewer.token)
    rev = client.get("/api/state").json()["rev"]
    t = client.post("/api/comment", json={"file": "paper.md", "lines": "8-13", "text": "dig burrows", "body": "Hm.",
                                          "rev": rev}).json()
    mid = t["messages"][0]["id"]
    st = client.get("/api/state").json()
    assert st["threads"][0]["editable"] == [mid]
    r = client.post("/api/message", json={"thread": t["id"], "message": mid, "body": "Hm, why?", "rev": st["rev"]})
    assert r.status_code == 200
    st = client.get("/api/state").json()
    assert st["threads"][0]["messages"][0]["body"] == "Hm, why?" and st["threads"][0]["messages"][0]["edited_at"]
    assert client.post("/api/message", json={"thread": t["id"], "message": mid, "body": " ",
                                             "rev": st["rev"]}).status_code == 400


def test_reading_view_and_preflight_endpoints(repo):
    rounds.start(repo)
    edit(repo, "proves that", "tests whether")
    viewer = Viewer(repo)
    client = TestClient(build_app(viewer), base_url="http://127.0.0.1")
    assert client.get("/api/clean").status_code == 403
    client.cookies.set(viewer.cookie, viewer.token)
    page = client.get("/read")
    assert page.status_code == 200 and "/static/read.js" in page.text
    assert client.get("/static/read.js").status_code == 200
    clean = client.get("/api/clean").json()
    assert "tests whether" in clean["html"] and "proves that" not in clean["html"] and "scrv-del" not in clean["html"]
    pre = client.get("/api/preflight").json()
    assert pre["transition"] == "return" and pre["ready"] is True
    summary = client.get("/api/summary")
    assert summary.status_code == 200 and "attachment" in summary.headers["content-disposition"]
    assert "<script" not in summary.text and "Reviewed by" in summary.text
