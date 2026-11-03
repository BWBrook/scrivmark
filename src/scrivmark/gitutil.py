import subprocess
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


def show(repo: Path, commit: str, path: str) -> str | None:
    proc = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="surrogateescape")


def show_bytes(repo: Path, commit: str, path: str) -> bytes | None:
    proc = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def dirty_paths(repo: Path, ignore_prefixes: tuple[str, ...] = ()) -> list[str]:
    out = git(repo, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if not path.startswith(ignore_prefixes):
            paths.append(path)
    return paths


def name_status(repo: Path, a: str, b: str) -> list[tuple[str, str, str | None]]:
    """(status letter, path, old path for renames) for every change from a to b."""
    out = git(repo, "diff", "--name-status", "-M", "--no-color", a, b)
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        status = parts[0][0]
        if status == "R":
            result.append(("R", parts[2], parts[1]))
        else:
            result.append((status, parts[1], None))
    return result


def has_remote(repo: Path, name: str = "origin") -> bool:
    return name in git(repo, "remote").split()


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]
