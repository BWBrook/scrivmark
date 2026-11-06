"""Local, git-ignored state and the write discipline shared by the CLI and viewer.

Every mutation runs inside `transaction`: one exclusive lock, an optional check
of the caller's expected revision, an intent file naming each file's contents
before and after, then atomic replacement of each file, keeping its mode.
"""

import base64
import fcntl
import hashlib
import json
import os
import time
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
    if path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o7777)
    os.replace(tmp, path)


def _intent_path(root: Path) -> Path:
    return local_dir(root) / "intent.json"


def _digest(data: str | bytes | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _on_disk(path: Path) -> str | None:
    return _digest(path.read_bytes()) if path.exists() else None


def recover(root: Path) -> list[str]:
    """Finish a write that was interrupted between its intent and completion.
    A file still as it was before is completed and one already written is left;
    if any changed in between (someone edited it since), nothing is written,
    the unfinished intent is set aside, and the caller is told."""
    ip = _intent_path(root)
    if not ip.exists():
        return []
    intent = json.loads(ip.read_text())
    todo, clash = [], []
    for rel, entry in intent.items():
        if not isinstance(entry, dict):
            clash.append(rel)
            continue
        now = _on_disk(root / rel)
        if now == entry.get("post"):
            continue
        (todo if now == entry.get("pre", "?") else clash).append(rel)
    if clash:
        kept = ip.with_name(f"intent-unfinished-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}.json")
        os.replace(ip, kept)
        raise ScrivError(f"An interrupted scrivmark write could not be finished: {', '.join(clash)} changed "
                         f"since. Nothing was overwritten; what it was writing is kept in {kept}.")
    for rel in todo:
        atomic_write(root / rel, _decode(intent[rel]["data"]))
    ip.unlink()
    return todo


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
                atomic_write(ip, json.dumps({k: {"pre": _on_disk(root / k), "post": _digest(v), "data": _encode(v)}
                                             for k, v in writes.items()}))
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
