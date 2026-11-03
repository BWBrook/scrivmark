"""Local, git-ignored state and the write discipline shared by the CLI and viewer.

Every mutation runs inside `transaction`: one exclusive lock, an optional check
of the caller's expected revision, an intent file naming the target contents,
then atomic replacement of each file.
"""

import base64
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from .errors import ScrivError, StaleRevision

LOCAL = ".scrivmark"


def local_dir(root: Path, round_id: str | None = None) -> Path:
    d = root / LOCAL / (round_id or "_")
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_ignored(root: Path) -> bool:
    gi = root / ".gitignore"
    text = gi.read_text() if gi.exists() else ""
    if any(line.strip() in (LOCAL, f"{LOCAL}/", f"/{LOCAL}/") for line in text.splitlines()):
        return False
    gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + f"{LOCAL}/\n")
    return True


def revision(root: Path, paths: list[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        f = root / p
        h.update(p.encode())
        h.update(f.read_bytes() if f.exists() else b"\0missing")
    return h.hexdigest()[:16]


def atomic_write(path: Path, data: str | bytes | None) -> None:
    if data is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.scrivmark-tmp")
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(tmp, mode) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _intent_path(root: Path) -> Path:
    return local_dir(root) / "intent.json"


def recover(root: Path) -> list[str]:
    """Finish a write that was interrupted between its intent and completion."""
    ip = _intent_path(root)
    if not ip.exists():
        return []
    intent = json.loads(ip.read_text())
    for rel, content in intent.items():
        atomic_write(root / rel, _decode(content))
    ip.unlink()
    return list(intent)


@contextmanager
def transaction(root: Path, expect: str | None = None, watched: list[str] | None = None):
    lock = local_dir(root) / "lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            recover(root)
            if expect is not None and watched is not None and revision(root, watched) != expect:
                raise StaleRevision("The manuscript or review changed since you looked; reload and try again.")
            writes: dict[str, str | None] = {}
            yield writes
            if writes:
                ip = _intent_path(root)
                atomic_write(ip, json.dumps({k: _encode(v) for k, v in writes.items()}))
                for rel, content in writes.items():
                    atomic_write(root / rel, content)
                ip.unlink()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _encode(v):
    return {"b64": base64.b64encode(v).decode()} if isinstance(v, bytes) else v


def _decode(v):
    return base64.b64decode(v["b64"]) if isinstance(v, dict) else v


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise ScrivError(f"{path} is not valid JSON: {e}") from e
