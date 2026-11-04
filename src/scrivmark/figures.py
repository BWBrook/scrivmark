"""Figures a browser cannot show directly: PDF graphics become PNGs, and each
TikZ picture (written out by latex.lua) is compiled on its own to SVG. Results
are cached under .scrivmark/cache/."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SHOWN = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
CANDIDATES = (".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps")
KEY = re.compile(r"^[0-9a-f]{40}$")
# TeX may read and write only below its working directory (and its own trees)
CONFINED = {**os.environ, "openin_any": "p", "openout_any": "p"}
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
                subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", src.name],
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
