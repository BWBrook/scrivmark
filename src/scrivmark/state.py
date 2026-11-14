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
    return revision_of({p: (root / p).read_bytes() if (root / p).exists() else None for p in paths})


def revision_of(contents: dict[str, bytes | None]) -> str:
    """The revision of files whose bytes are already in hand."""
    h = hashlib.sha256()
    for p in sorted(contents):
        h.update(p.encode())
        h.update(b"\0missing" if contents[p] is None else contents[p])
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


def _head(root: Path) -> str:
    """The branch that is checked out ("HEAD" when detached, "" outside git)."""
    from . import gitutil

    try:
        return gitutil.git(root, "rev-parse", "--symbolic-full-name", "HEAD").strip()
    except Exception:
        return ""


def recover(root: Path) -> list[str]:
    """Finish a write that was interrupted between its intent and completion.
    A file still as it was before is completed and one already written is left;
    if any changed in between (someone edited it since), nothing is written,
    the unfinished intent is set aside, and the caller is told."""
    ip = _intent_path(root)
    if not ip.exists():
        return []
    intent = json.loads(ip.read_text())
    files = intent.get("files")
    todo, clash = [], []
    if not isinstance(files, dict) or intent.get("head") != _head(root):
        # written by an earlier version, or on another branch: a file here that
        # happens to match is not the file the write was for
        clash, files = sorted(files if isinstance(files, dict) else intent), {}
    for rel, entry in files.items():
        now = _on_disk(root / rel)
        if now == entry.get("post"):
            continue
        (todo if now == entry.get("pre", "?") else clash).append(rel)
    if clash:
        kept = ip.with_name(f"intent-unfinished-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}.json")
        os.replace(ip, kept)
        raise ScrivError(f"An interrupted scrivmark write could not be finished: {', '.join(clash)} changed "
                         f"since, or another branch is checked out. Nothing was overwritten; what it was "
                         f"writing is kept in {kept}.")
    for rel in todo:
        atomic_write(root / rel, _decode(files[rel]["data"]))
    ip.unlink()
    return todo


class Writes(dict):
    """The files a transaction will write, path to contents (None deletes).
    Afterwards `rev` is the watched revision as the transaction left it,
    taken under the lock, for a caller chaining one `expect` through a batch."""
    rev: str | None = None


STALE = "The manuscript or review changed since you looked; reload and try again."


@contextmanager
def transaction(root: Path, expect: str | None = None, watched: list[str] | None = None):
    """The lock keeps scrivmark's own writers apart, but not an editor saving
    a file: the watched files are checked again just before writing, so an
    edit saved while the change was being worked out is not overwritten. Only
    a save in the instant between that check and the replacement can still
    race it; closing that needs the editor's cooperation."""
    lock = local_dir(root) / "lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            recover(root)
            before = revision(root, watched) if watched is not None else None
            if expect is not None and before is not None and before != expect:
                raise StaleRevision(STALE)
            writes = Writes()
            yield writes
            if writes and before is not None and revision(root, watched) != before:
                raise StaleRevision(STALE)
            if writes:
                ip = _intent_path(root)
                atomic_write(ip, json.dumps({"head": _head(root), "files": {
                    k: {"pre": _on_disk(root / k), "post": _digest(v), "data": _encode(v)} for k, v in writes.items()}}))
                for rel, content in writes.items():
                    atomic_write(root / rel, content)
                ip.unlink()
            if watched is not None:
                writes.rev = revision(root, watched)
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
