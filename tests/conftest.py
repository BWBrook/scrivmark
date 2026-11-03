import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """The tutorial manuscript in a fresh repository, with the reviewer as current identity."""
    root = tmp_path / "ms"
    shutil.copytree(str(files("scrivmark") / "tutorial"), root)
    monkeypatch.setenv("SCRIVMARK_AS", "Barry W. Brook")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.org")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.org")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "draft")
    monkeypatch.chdir(root)
    return root


def edit(root: Path, old: str, new: str, rel: str = "paper.md") -> None:
    p = root / rel
    text = p.read_text()
    assert old in text, old
    p.write_text(text.replace(old, new, 1))
