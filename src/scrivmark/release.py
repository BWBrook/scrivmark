"""Which release this is, and whether a newer one is out. Releases are git
tags `vX.Y.Z` on the repository; nothing is published to a package index."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import __version__

REPO = "https://github.com/BWBrook/scrivmark"
INSTALL = f"uv tool install --reinstall git+{REPO}"


def parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def latest(timeout: float = 5) -> str | None:
    """The newest release tag (0.0.0 if none), or None if the repository can't be reached."""
    try:
        out = subprocess.run(["git", "ls-remote", "--tags", "--refs", REPO], capture_output=True, text=True,
                             timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    tags = re.findall(r"refs/tags/v(\d+\.\d+\.\d+)$", out.stdout, re.M)
    return max(tags, key=parse) if tags else "0.0.0"


def checkout() -> Path | None:
    """The git checkout this copy runs from, for a development install."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() and (root / "pyproject.toml").exists() else None


def status() -> tuple[bool, str]:
    """(up to date, detail) for `scrivmark doctor`."""
    new = latest()
    if new is None:
        return False, f"{__version__}; could not check for a newer release (offline?)"
    if parse(new) <= parse(__version__):
        return True, f"{__version__}, the latest release"
    src = checkout()
    how = f"`git pull` in {src}" if src else f"`{INSTALL}`, then `scrivmark skill --install`"
    return False, f"{__version__}; {new} is out. Update with {how}, and restart any running viewer. " \
                  f"What changed: {REPO}/blob/main/CHANGELOG.md"
