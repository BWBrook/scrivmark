"""Figures a browser cannot show directly: PDF graphics become PNGs, and each
TikZ picture (written out by latex.lua) is compiled on its own to SVG. Results
are cached under .scrivmark/cache/."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

SHOWN = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
CANDIDATES = (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps")
KEY = re.compile(r"^[0-9a-f]{40}$")
# Writes are confined by TeX itself. Reads are not: openin_any stopped having
# any effect in TeX Live 2026, and was never a real barrier before it, so the
# compile runs in an operating-system sandbox where there is one (sandbox()).
CONFINED = {**os.environ, "openin_any": "p", "openout_any": "p"}
PRIVATE_MAC = ("/Users", "/Volumes", "/private/tmp", "/private/var/folders", "/private/var/root")
PRIVATE_LINUX = ("/home", "/root", "/tmp", "/var/tmp", "/mnt", "/media", "/run/user")
TREES = ("SELFAUTOPARENT", "TEXMFDIST", "TEXMFLOCAL", "TEXMFHOME", "TEXMFVAR", "TEXMFCONFIG", "TEXMFSYSVAR",
         "TEXMFSYSCONFIG")
PLACEHOLDER = """<svg xmlns="http://www.w3.org/2000/svg" width="480" height="60" viewBox="0 0 480 60">
<rect x="0.5" y="0.5" width="479" height="59" fill="none" stroke="#999" stroke-dasharray="4 3"/>
<text x="240" y="35" font-family="sans-serif" font-size="13" fill="#666" text-anchor="middle">{}</text></svg>"""


def _cache(root: Path, kind: str) -> Path:
    d = root / ".scrivmark" / "cache" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(root: Path, rel: str) -> Path | None:
    """The graphic `rel` names, trying LaTeX's extensions when it has none."""
    base = root.resolve()
    p = (base / rel).resolve()
    if base not in p.parents or ".git" in p.parts or ".scrivmark" in p.parts:
        return None
    if p.is_file():
        return p
    for ext in CANDIDATES:
        q = (base / (rel + ext)).resolve()
        if base in q.parents and ".git" not in q.parts and ".scrivmark" not in q.parts and q.is_file():
            return q
    return None


def png(root: Path, rel: str) -> Path | None:
    src = resolve(root, rel)
    if src is None:
        return None
    if src.suffix.lower() in SHOWN:
        return src
    st = src.stat()
    key = hashlib.sha1(f"{src}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()
    out = _cache(root, "img") / f"{key}.png"
    if out.exists():
        return out
    pdf = src
    with tempfile.TemporaryDirectory() as tmp:
        if src.suffix.lower() == ".eps":
            if not shutil.which("epstopdf"):
                return None
            pdf = Path(tmp) / "fig.pdf"
            if subprocess.run(["epstopdf", str(src), f"--outfile={pdf}"], capture_output=True, timeout=60).returncode:
                return None
        stem = Path(tmp) / "page"
        proc = subprocess.run(["pdftocairo", "-png", "-singlefile", "-r", "144", "-f", "1", "-l", "1", str(pdf), str(stem)],
                              capture_output=True, timeout=120)
        made = stem.with_suffix(".png")
        if proc.returncode or not made.exists():
            return None
        shutil.move(made, out)
    return out


@lru_cache(maxsize=1)
def _tex_trees() -> tuple[str, ...]:
    """The directories TeX itself reads from, which may lie under a home directory."""
    out = []
    for var in TREES:
        try:
            got = subprocess.run(["kpsewhich", "-var-value", var], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for d in re.split(r"[:;]", got.strip().replace("!!", "")):
            if d.startswith("/") and os.path.isdir(d):
                out.append(os.path.realpath(d))
    return tuple(dict.fromkeys(out))


def _mac_profile(work: str) -> str:
    q = lambda p: '"' + p.replace("\\", "\\\\").replace('"', '\\"') + '"'  # noqa: E731
    private = " ".join(f"(subpath {q(p)})" for p in PRIVATE_MAC)
    allowed = " ".join(f"(subpath {q(p)})" for p in (*_tex_trees(), work))
    return (f"(version 1)(allow default)(deny network*)(deny file-read-data {private})(allow file-read-data {allowed})"
            f"(deny file-write* (subpath \"/\"))(allow file-write* (subpath {q(work)}) (literal \"/dev/null\"))")


def _linux_wrap(work: str) -> list[str]:
    cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for p in PRIVATE_LINUX:
        if os.path.isdir(p):
            cmd += ["--tmpfs", p]
    for tree in _tex_trees():
        if tree.startswith(tuple(p + "/" for p in PRIVATE_LINUX)):
            cmd += ["--ro-bind", tree, tree]
    return cmd + ["--bind", work, work, "--unshare-net", "--die-with-parent", "--chdir", work]


@lru_cache(maxsize=1)
def _sandbox_works() -> bool:
    try:
        if sys.platform == "darwin":
            probe = ["sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"]
        else:
            probe = ["bwrap", "--ro-bind", "/", "/", "--unshare-net", "true"]
        return subprocess.run(probe, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def sandbox(work: Path) -> list[str] | None:
    """The command prefix that runs a program able to read only system files,
    TeX's own trees and `work`, and to write only in `work`, with no network:
    sandbox-exec on macOS, bubblewrap on Linux. None where neither works, and
    then a TikZ picture can read any file its user can."""
    if not _sandbox_works():
        return None
    w = os.path.realpath(work)
    return ["sandbox-exec", "-p", _mac_profile(w)] if sys.platform == "darwin" else _linux_wrap(w)


def tikz(root: Path, key: str) -> tuple[Path | None, str]:
    """The SVG for a TikZ picture, or None and the reason it could not be drawn."""
    if not KEY.match(key):
        return None, "unknown picture"
    d = _cache(root, "tikz")
    svg = d / f"{key}.svg"
    if svg.exists():
        return svg, ""
    failed = d / f"{key}.failed"
    if failed.exists():
        return None, failed.read_text()
    if not shutil.which("pdflatex") or not shutil.which("pdftocairo"):
        return None, "TikZ needs pdflatex and pdftocairo on this machine"
    reason = "TikZ picture did not compile here"
    for name in (f"{key}.tex", f"{key}.min.tex"):
        src = d / name
        if not src.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            try:
                shutil.copy(src, Path(tmp) / src.name)
                subprocess.run([*(sandbox(Path(tmp)) or []), "pdflatex", "-no-shell-escape",
                                "-interaction=nonstopmode", "-halt-on-error", src.name],
                               capture_output=True, timeout=90, cwd=tmp, env=CONFINED)
            except subprocess.TimeoutExpired:
                continue
            pdf = Path(tmp) / (src.stem + ".pdf")
            if not pdf.exists():
                continue
            out = Path(tmp) / "out.svg"
            if subprocess.run(["pdftocairo", "-svg", str(pdf), str(out)], capture_output=True, timeout=60).returncode == 0:
                shutil.move(out, svg)
                return svg, ""
    failed.write_text(reason)
    return None, reason


def placeholder(message: str) -> str:
    from html import escape
    return PLACEHOLDER.format(escape(message + "; see the PDF"))
