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
    viewer_port: int | None = None

    def is_prose(self, path: str) -> bool:
        return path in self.files

    @property
    def sources(self) -> list[str]:
        """Files a reader can comment on or edit from the viewer."""
        return self.files + [b for b in self.bibliography if b not in self.files]


RESERVED = (".git", ".scrivmark", "reviews")


def inside(root: Path, rel, what: str = "Manuscript file") -> str:
    """`rel` as a path inside the repository, or an error. Sources are edited
    on the review branch and recorded from git, so a file outside it (by path
    or through a symlink) would be changed with no record of the change."""
    if not isinstance(rel, str) or not rel.strip():
        raise ScrivError(f"{what} {rel!r} in {CONFIG} is not a file path.")
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ScrivError(f"{what} {rel!r} must be a path inside the repository, so its edits are recorded.")
    base = root.resolve()
    full = (root / p).resolve()
    if base not in full.parents:
        raise ScrivError(f"{what} {rel!r} leads outside the repository (through a symlink), so its edits "
                         "would not be recorded. Use the file itself.")
    if full.relative_to(base).parts[0] in RESERVED or full == base / CONFIG:
        raise ScrivError(f"{what} {rel!r} is one of scrivmark's own files.")
    return p.as_posix()


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
    bib = [bib] if isinstance(bib, str) else bib
    if not isinstance(files, list) or not isinstance(bib, list):
        raise ScrivError(f"{CONFIG}: files and bibliography must be lists of paths.")
    files = [inside(root, f) for f in files]
    bib = [inside(root, b, "Bibliography") for b in bib]
    csl = inside(root, m["csl"], "CSL style") if m.get("csl") else None
    port = data.get("viewer", {}).get("port")
    return Manuscript(root, files, bib, csl, m.get("title"), int(port) if port else None)


def _makedoc(root: Path) -> dict | None:
    """makedoc's settings/build.yaml names the default profile's sources,
    bibliography and CSL, relative to the settings file."""
    import yaml

    for cfg in sorted([*root.glob("settings/*.yaml"), *root.glob("settings/*.yml")]):
        try:
            data = yaml.safe_load(cfg.read_text())
        except yaml.YAMLError:
            continue
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, dict):
            continue
        prof = profiles.get(data.get("default_profile")) or next(iter(profiles.values()), None)
        if not isinstance(prof, dict) or not prof.get("manuscript_sources"):
            continue
        rel = lambda p: os.path.relpath((cfg.parent / p).resolve(), root.resolve())  # noqa: E731
        bib = prof.get("bibliography")
        return {"files": [rel(p) for p in prof["manuscript_sources"]],
                "bibliography": [rel(b) for b in (bib if isinstance(bib, list) else [bib] if bib else [])],
                "csl": rel(prof["csl"]) if prof.get("csl") else None,
                "source": f"{cfg.relative_to(root)} (makedoc)"}
    return None


def _skip(rel: Path) -> bool:
    return any(part.startswith((".", "_")) or part in ("reviews", "node_modules") for part in rel.parts)


def detect(root: Path) -> dict:
    found = _makedoc(root)
    if found:
        return found
    from . import latex

    tex = latex.detect(root, _skip)
    if tex:
        csl = sorted(str(p.relative_to(root)) for p in root.rglob("*.csl") if ".git" not in p.parts)
        return {**tex, "csl": csl[0] if csl else None, "source": "LaTeX documents"}
    files = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.suffix in PROSE
        and p.name not in SKIP
        and not _skip(p.relative_to(root))
    )
    bibs = sorted(str(p.relative_to(root)) for p in root.rglob("*.bib") if ".git" not in p.parts)
    csl = sorted(str(p.relative_to(root)) for p in root.rglob("*.csl") if ".git" not in p.parts)
    return {"files": files, "bibliography": bibs, "csl": csl[0] if csl else None, "source": None}


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
