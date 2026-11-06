"""LaTeX sources: block splitting, preamble definitions, diff tokens and project
detection.

A .tex file is shown through pandoc's LaTeX reader. Each block travels to the
renderer as a fenced code block of class `scrv-tex`; `assets/latex.lua` reads
it as LaTeX in place, so the rest of the pipeline (wrappers, sanitising,
labels, citeproc) is shared with Markdown. Redline marks are
`\\textcolor{scrvdel-<nonce>-<change>}{...}`, which the reader keeps as a
styled span.
"""

import re
import subprocess
from functools import lru_cache
from pathlib import Path

from .errors import ScrivError

SUFFIXES = (".tex", ".ltx")
HEADING = re.compile(r"^\s*\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)\*?\s*[\[{]")
BEGIN = re.compile(r"\\begin\s*\{([^{}]+)\}")
END = re.compile(r"\\end\s*\{([^{}]+)\}")
LISTS = {"itemize", "enumerate", "description"}
PROSE_ENVS = {"quote", "quotation", "center", "abstract", "flushleft", "flushright", "verse"}
MATH_ENVS = {"equation", "align", "gather", "multline", "eqnarray", "displaymath", "math", "split", "alignat", "flalign"}
DISPLAY = re.compile(r"\s*\\begin\s*\{(?!(?:" + "|".join(MATH_ENVS) + r")\*?\})")
DEFINES = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareMathOperator|newenvironment|renewenvironment|def)\b\*?")
STRUCTURAL = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph|maketitle|caption|label|ref|cite\w*)\b")
INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}")
BIBLIO = re.compile(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\{([^{}]+)\}")
TIKZ_SKIP = re.compile(r"\\usepackage(\[[^\]]*\])?\{[^}]*\b(geometry|hyperref|titlesec|fancyhdr|titling|natbib|biblatex|caption|subcaption|cleveref|showframe|lineno)\b")

TOKEN = re.compile(
    r"""
    (?P<comment>(?<!\\)%[^\n]*)
   |(?P<attr>\\(?:begin|end)\s*\{[^{}\n]*\}(?:\[[^\]\n]*\])?|\\label\{[^{}\n]*\})
   |(?P<cite>\\(?:cite\w*|citep|citet|citealp|citeauthor|citeyear|parencite|textcite|autocite|footcite)\*?(?:\[[^\]\n]*\])*\{[^{}\n]*\})
   |(?P<ref>\\(?:ref|eqref|cref|Cref|autoref|pageref|nameref)\*?\{[^{}\n]*\})
   |(?P<link>\\(?:url|includegraphics|input|include)\*?(?:\[[^\]\n]*\])?\{[^{}\n]*\}|\\href\{[^{}\n]*\})
   |(?P<math>\$\$.+?\$\$|(?<!\\)\$(?:[^$\\]|\\.)+?\$|\\\(.+?\\\)|\\\[.+?\\\])
   |(?P<cmd>\\[A-Za-z@]+\*?|\\.)
   |(?P<word>\w+(?:['’\-]\w+)*)
   |(?P<space>\s+)
   |(?P<punct>.)
    """,
    re.X | re.S,
)
SYMBOLS = {"\\%": "%", "\\&": "&", "\\$": "$", "\\_": "_", "\\#": "#", "\\{": "{", "\\}": "}",
           "\\,": " ", "\\ ": " ", "\\;": " ", "\\:": " ", "\\!": "", "\\\\": " ", "\\-": "",
           "\\ldots": "...", "\\dots": "...", "\\textellipsis": "...", "\\enspace": " ", "\\quad": " ",
           "\\qquad": " ", "\\thinspace": " ", "\\textendash": "-", "\\textemdash": "-"}


LATEX_MIN_PANDOC = (3, 7)


def is_tex(path: str) -> bool:
    return path.endswith(SUFFIXES)


@lru_cache(maxsize=1)
def pandoc_version() -> tuple[int, ...]:
    """The installed pandoc's version, or () if it is not installed."""
    try:
        out = subprocess.run(["pandoc", "--version"], capture_output=True, text=True, timeout=30).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ()
    m = re.match(r"pandoc\S*\s+(\d+(?:\.\d+)*)", out)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def reader_problem() -> str | None:
    """Why LaTeX cannot be read safely here, or None. Only pandoc 3.7 and later
    can read LaTeX without reaching other files (the sandbox argument of
    `pandoc.read`)."""
    have = pandoc_version()
    if have >= LATEX_MIN_PANDOC:
        return None
    shown = ".".join(map(str, have)) or "none found"
    return (f"LaTeX manuscripts need pandoc {'.'.join(map(str, LATEX_MIN_PANDOC))} or later, which can read LaTeX "
            f"without reaching other files; you have {shown}")


def require_reader() -> None:
    if (why := reader_problem()):
        raise ScrivError(why)


def code_part(line: str) -> str:
    """The line without its comment."""
    i = 0
    while i < len(line):
        if line[i] == "\\":
            i += 2
            continue
        if line[i] == "%":
            return line[:i]
        i += 1
    return line


def _depth(code: str) -> int:
    return len(BEGIN.findall(code)) - len(END.findall(code))


def _braces(code: str) -> int:
    return len(re.findall(r"(?<!\\)\{", code)) - len(re.findall(r"(?<!\\)\}", code))


def balanced(code: str) -> bool:
    """Whether a fragment of comment-free source opens and closes the same
    groups and environments."""
    return _braces(code) == 0 and _depth(code) == 0


def scan(text: str) -> list[tuple[int, int, str]]:
    """(first line, last line exclusive, kind) for each block. The preamble up
    to `\\begin{document}` and everything from `\\end{document}` are blocks of
    their own that are never shown."""
    lines = text.splitlines(keepends=True)
    n = len(lines)
    codes = [code_part(x.rstrip("\n")) for x in lines]
    blocks: list[tuple[int, int, str]] = []
    i = 0
    begin = next((k for k, c in enumerate(codes) if re.match(r"\s*\\begin\s*\{document\}", c)), None)
    if begin is not None:
        blocks.append((0, begin + 1, "preamble"))
        i = begin + 1
    end = next((k for k in range(i, n) if re.match(r"\s*\\end\s*\{document\}", codes[k])), n)
    blank = lambda k: not lines[k].strip()  # noqa: E731
    comment_only = lambda k: lines[k].strip() and not codes[k].strip()  # noqa: E731

    while i < end:
        if blank(i):
            i += 1
            continue
        s, code, lead = i, codes[i], False
        if comment_only(i):
            j = i
            while j < end and comment_only(j):
                j += 1
            if j >= end or blank(j):
                i = j
                blocks.append((s, i, "comment"))
                continue
            lead = True
        if not lead and HEADING.match(code):
            depth = _braces(code)
            i += 1
            while depth > 0 and i < end:
                depth += _braces(codes[i])
                i += 1
            blocks.append((s, i, "heading"))
            continue
        if not lead and (m := re.match(r"\s*\\begin\s*\{([^{}]+)\}", code)):
            env = m.group(1)
            depth, j = 0, i
            while j < end:
                depth += len(re.findall(rf"\\begin\s*\{{{re.escape(env)}\}}", codes[j]))
                depth -= len(re.findall(rf"\\end\s*\{{{re.escape(env)}\}}", codes[j]))
                j += 1
                if depth <= 0:
                    break
            i = j
            base = env.rstrip("*")
            blocks.append((s, i, "list" if base in LISTS else "div" if base in PROSE_ENVS else "env"))
            continue
        if not lead and code.strip().startswith("\\["):
            j = i
            while j < end and "\\]" not in codes[j]:
                j += 1
            i = min(j + 1, end)
            blocks.append((s, i, "env"))
            continue
        depth, j = 0, i
        while j < end:
            if j > i and depth <= 0 and not comment_only(j) and (
                    blank(j) or HEADING.match(codes[j]) or DISPLAY.match(codes[j])):
                break
            depth += _depth(codes[j])
            j += 1
        i = j
        blocks.append((s, i, "para"))
    if end < n:
        blocks.append((end, n, "postamble"))
    return blocks


def _balanced(text: str, i: int) -> int:
    """Index just past the brace group starting at text[i] == '{'."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _definition(text: str, i: int, kind: str) -> int:
    """End of the definition starting at text[i]."""
    j = i
    if kind == "def":
        m = re.compile(r"\\def\s*\\[A-Za-z@]+[^{]*").match(text, i)
        j = m.end() if m else i + 4
        return _balanced(text, j) if j < len(text) and text[j] == "{" else j
    m = DEFINES.match(text, i)
    j = m.end()
    groups = 3 if kind in ("newenvironment", "renewenvironment") else 2
    seen = 0
    while j < len(text) and seen < groups:
        while j < len(text) and text[j].isspace():
            j += 1
        if j >= len(text):
            break
        if text[j] == "{":
            j = _balanced(text, j)
            seen += 1
        elif text[j] == "[":
            k = text.find("]", j)
            j = len(text) if k < 0 else k + 1
        elif text[j] == "\\" and seen == 0:
            m = re.compile(r"\\[A-Za-z@]+").match(text, j)
            j = m.end() if m else j + 2
            seen += 1
        else:
            break
    return j


def preamble(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for k, line in enumerate(lines):
        if re.match(r"\s*\\begin\s*\{document\}", code_part(line)):
            return "".join(lines[:k])
    return ""


def definitions(text: str) -> str:
    """The macro and environment definitions in the preamble, for pandoc to
    expand. Redefinitions of sectioning and referencing commands are left out:
    they change the printed layout, not the words."""
    pre = "\n".join(code_part(x) for x in preamble(text).splitlines())
    out, i = [], 0
    while (m := DEFINES.search(pre, i)):
        end = _definition(pre, m.start(), m.group(1))
        piece = pre[m.start() : end]
        head = piece[: piece.find("{", len(m.group(0))) + 40] if m.group(1) != "def" else piece[:40]
        if not (m.group(1).startswith("renew") and STRUCTURAL.search(head)):
            out.append(piece)
        i = max(end, m.end())
    return "\n".join(out)


def macro_text(text: str) -> dict[str, str]:
    """For each simple \\newcommand in the preamble, the source its expansion
    starts with, before any argument: `\\USD{70,000}` reads as `US\\$` then
    the argument. Used to place selections that cross a macro."""
    out = {}
    for d in definitions(text).splitlines():
        m = re.match(r"\\(?:newcommand|renewcommand|providecommand)\*?\s*\{?(\\[A-Za-z@]+)\}?\s*(?:\[\d\])?(?:\[[^\]]*\])?\s*\{(.*)\}\s*$", d)
        if m:
            out[m.group(1)] = m.group(2).split("#", 1)[0]
    return out


def tikz_preambles(text: str) -> tuple[str, str]:
    """Two standalone preambles for drawing a tikzpicture on its own: the
    document's packages, and a minimal fallback."""
    pre = preamble(text)
    keep, libs = [], []
    for line in pre.splitlines():
        code = code_part(line).strip()
        if not code or code.startswith("\\documentclass") or TIKZ_SKIP.match(code):
            continue
        if code.startswith(("\\usepackage", "\\usetikzlibrary", "\\tikzset", "\\pgfplotsset", "\\definecolor", "\\RequirePackage")):
            keep.append(code)
            if code.startswith(("\\usetikzlibrary", "\\tikzset", "\\definecolor")):
                libs.append(code)
    head = "\\documentclass[tikz,border=2pt]{standalone}\n"
    defs = definitions(text)
    full = head + "\n".join(keep) + "\n" + defs + "\n"
    minimal = head + "\\usepackage{tikz}\n\\usepackage{xcolor}\n" + "\n".join(libs) + "\n" + defs + "\n"
    return full, minimal


def code_block(body: str, nonce: str, cls: str = "scrv-tex") -> str:
    """A fenced block that latex.lua reads as LaTeX. Only blocks carrying this
    render's nonce are read; a Markdown manuscript cannot forge one."""
    run = max((len(m) for m in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, run + 1)
    return f'{fence} {{.{cls} n="{nonce}"}}\n{body}\n{fence}'


def defs_block(text: str, nonce: str) -> str:
    full, minimal = tikz_preambles(text)
    body = definitions(text) + "\n%%scrv-tikz\n" + full + "\n%%scrv-tikz-min\n" + minimal
    return code_block(body, nonce, "scrv-texdefs")


def mark(kind: str, body: str, cid: str | None, nonce: str) -> str:
    return f"\\textcolor{{scrv{kind}-{nonce}-{cid or ''}}}{{{body}}}"


def resolve_input(root: Path, parent: str, name: str) -> str | None:
    """The repository file an \\input{name} in `parent` reads, as LaTeX finds it
    from the document's directory or the repository root."""
    base = root.resolve()
    name = name.strip()
    for d in ((base / parent).parent, base):
        cand = (d / (name if name.endswith(".tex") else name + ".tex")).resolve()
        if cand.is_file() and base in cand.parents:
            return str(cand.relative_to(base))
    return None


def input_spans(body: str) -> list[tuple[int, int, str]]:
    """(start, end, name) of each \\input or \\include in a block, in order,
    ignoring comments."""
    out, offset = [], 0
    for line in body.splitlines(keepends=True):
        out += [(offset + m.start(), offset + m.end(), m.group(1))
                for m in INPUT.finditer(code_part(line.rstrip("\n")))]
        offset += len(line)
    return out


def inputs(body: str) -> list[str]:
    """Names \\input or \\include'd in a block, in order."""
    return [name for _, _, name in input_spans(body)]


def is_root(text: str) -> bool:
    return bool(re.search(r"^\s*\\documentclass", text, re.M)) and "\\begin{document}" in text


def detect(root: Path, skip) -> dict | None:
    """LaTeX documents in the repository: each root (a file with
    \\documentclass and \\begin{document}) followed by the files it inputs,
    largest document first. Where a directory holds a .latexmkrc, only the
    documents built there count."""
    roots = []
    for p in root.rglob("*.tex"):
        rel = p.relative_to(root)
        if skip(rel):
            continue
        text = p.read_text(errors="replace")
        if is_root(text):
            roots.append((p, text))
    built = {p.parent for p in root.rglob(".latexmkrc") if not skip(p.parent.relative_to(root))}
    if built and any(p.parent in built for p, _ in roots):
        roots = [r for r in roots if r[0].parent in built]
    if not roots:
        return None
    roots.sort(key=lambda r: (-len(r[1]), str(r[0])))
    files: list[str] = []
    bibs: list[str] = []

    def visit(p: Path, text: str, depth: int = 0) -> None:
        rel = str(p.relative_to(root))
        if rel in files or depth > 8:
            return
        files.append(rel)
        body = "\n".join(code_part(x) for x in text.splitlines())
        for m in BIBLIO.finditer(body):
            for name in m.group(1).split(","):
                name = name.strip()
                cand = (p.parent / (name if name.endswith(".bib") else name + ".bib")).resolve()
                if cand.is_file() and root.resolve() in cand.parents:
                    b = str(cand.relative_to(root.resolve()))
                    if b not in bibs:
                        bibs.append(b)
        for name in inputs(text):
            child = resolve_input(root, rel, name)
            if child:
                visit(root / child, (root / child).read_text(errors="replace"), depth + 1)

    for p, text in roots:
        visit(p, text)
    return {"files": files, "bibliography": bibs}
