import re
import subprocess
from functools import lru_cache
from pathlib import Path

from .errors import ScrivError


def git(repo: Path, *args: str, check: bool = True, input: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, input=input
    )
    if check and proc.returncode != 0:
        raise ScrivError(f"git {' '.join(args)} failed:\n{proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True).returncode == 0


def root(start: Path) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise ScrivError(f"{start} is not inside a git repository")
    return Path(proc.stdout.strip())


def rev(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def current_branch(repo: Path) -> str:
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


SHA = re.compile(r"[0-9a-f]{40}")


def show(repo: Path, commit: str, path: str) -> str | None:
    data = show_bytes(repo, commit, path)
    return None if data is None else data.decode("utf-8", errors="surrogateescape")


def show_bytes(repo: Path, commit: str, path: str) -> bytes | None:
    """A file as it is in `commit`. A full SHA names fixed contents, so those
    reads are cached; a branch or HEAD is read afresh."""
    if SHA.fullmatch(commit):
        return _show_at(str(Path(repo).resolve()), commit, path)
    return _show(repo, commit, path)


def _show(repo, commit: str, path: str) -> bytes | None:
    proc = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


@lru_cache(maxsize=256)
def _show_at(repo: str, commit: str, path: str) -> bytes | None:
    return _show(repo, commit, path)


def _nul(out: str) -> list[str]:
    return [x for x in out.split("\0") if x]


def dirty_paths(repo: Path, ignore_prefixes: tuple[str, ...] = ()) -> list[str]:
    fields = _nul(git(repo, "status", "--porcelain", "-z", "--untracked-files=all"))
    paths, i = [], 0
    while i < len(fields):
        entry = fields[i]
        code, path = entry[:2], entry[3:]
        i += 2 if ("R" in code or "C" in code) else 1
        if not path.startswith(ignore_prefixes):
            paths.append(path)
    return paths


def untracked(repo: Path) -> list[str]:
    return _nul(git(repo, "ls-files", "--others", "--exclude-standard", "-z"))


def name_status(repo: Path, a: str, b: str) -> list[tuple[str, str, str | None]]:
    """(status letter, path, old path for renames) for every change from a to b."""
    fields = _nul(git(repo, "diff", "--name-status", "-M", "-z", "--no-color", a, b))
    result, i = [], 0
    while i < len(fields):
        status = fields[i][0]
        if status in "RC":
            result.append(("R" if status == "R" else "A", fields[i + 2], fields[i + 1]))
            i += 3
        else:
            result.append((status, fields[i + 1], None))
            i += 2
    return result


def has_remote(repo: Path, name: str = "origin") -> bool:
    return name in git(repo, "remote").split()


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]
