import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ScrivError

CONFIG = "scrivmark.toml"
IDENTITY = Path.home() / ".config" / "scrivmark" / "config.toml"
PROSE = (".md", ".qmd", ".markdown")
SKIP = {"README.md", "LICENSE.md", "CHANGELOG.md", "CONTRIBUTING.md", "TUTORIAL.md"}


@dataclass
class Manuscript:
    root: Path
    files: list[str]
    bibliography: list[str] = field(default_factory=list)
    csl: str | None = None
    title: str | None = None

    def is_prose(self, path: str) -> bool:
        return path in self.files


def load(root: Path) -> Manuscript:
    path = root / CONFIG
    if not path.exists():
        raise ScrivError(f"No {CONFIG} in {root}. Run `scrivmark init` first.")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ScrivError(f"{CONFIG} is not valid TOML: {e}") from e
    m = data.get("manuscript", {})
    files = m.get("files") or []
    if not files:
        raise ScrivError(f"{CONFIG} lists no manuscript files")
    bib = m.get("bibliography") or []
    return Manuscript(root, files, [bib] if isinstance(bib, str) else bib, m.get("csl"), m.get("title"))


def detect(root: Path) -> dict:
    files = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.suffix in PROSE
        and p.name not in SKIP
        and not any(part.startswith((".", "_")) or part in ("reviews", "node_modules") for part in p.relative_to(root).parts)
    )
    bibs = sorted(str(p.relative_to(root)) for p in root.rglob("*.bib") if ".git" not in p.parts)
    csl = sorted(str(p.relative_to(root)) for p in root.rglob("*.csl") if ".git" not in p.parts)
    return {"files": files, "bibliography": bibs, "csl": csl[0] if csl else None}


def _toml(s: str) -> str:
    """A TOML basic string. JSON's string escapes are valid TOML."""
    return json.dumps(s, ensure_ascii=False)


def write(root: Path, files: list[str], bibliography: list[str], csl: str | None) -> Path:
    lines = ["[manuscript]", "# Source files in reading order."]
    lines.append("files = [" + ", ".join(_toml(f) for f in files) + "]")
    lines.append("bibliography = [" + ", ".join(_toml(b) for b in bibliography) + "]")
    if csl:
        lines.append(f"csl = {_toml(csl)}")
    path = root / CONFIG
    path.write_text("\n".join(lines) + "\n")
    return path


@dataclass
class Identity:
    name: str
    initials: str
    statement: str | None = None


def initials_of(name: str) -> str:
    parts = [p for p in re.split(r"[\s.]+", name) if p]
    return "".join(p[0] for p in parts).upper() or "ME"


def identity() -> Identity:
    if name := os.environ.get("SCRIVMARK_AS"):
        return Identity(name, initials_of(name))
    if IDENTITY.exists():
        try:
            data = tomllib.loads(IDENTITY.read_text()).get("identity", {})
        except tomllib.TOMLDecodeError as e:
            raise ScrivError(f"{IDENTITY} is not valid TOML ({e}); fix it or rerun `scrivmark whoami`.") from e
        if data.get("name"):
            return Identity(data["name"], data.get("initials") or initials_of(data["name"]), data.get("statement"))
    proc = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    name = proc.stdout.strip()
    if not name:
        raise ScrivError(
            "Who are you? Set `git config --global user.name`, or run "
            "`scrivmark whoami --name 'Your Name' --initials YN`."
        )
    return Identity(name, initials_of(name))


def set_identity(name: str, initials: str, statement: str | None = None) -> Path:
    IDENTITY.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[identity]", f"name = {_toml(name)}", f"initials = {_toml(initials)}"]
    if statement:
        lines.append(f"statement = {_toml(statement)}")
    IDENTITY.write_text("\n".join(lines) + "\n")
    return IDENTITY


def default_statement(me: Identity) -> str:
    return me.statement or f"Reviewed by {me.name}."
