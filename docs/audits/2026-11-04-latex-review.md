# LaTeX support: independent review, 4 November 2026

Commit reviewed: `44d4dc0` (LaTeX manuscripts), with `2174086` landing during
the review. Reviewer: Codex (`gpt-6.1-sol`, high effort) via
`codex-delegate`, read-only, 462 s. Author and adjudicator: Claude Opus 5.5.
Findings were checked against the code and reproduced before fixing; each fix
has a regression test in `tests/test_latex.py`.

| # | Finding | Severity | Disposition |
|---|---|---|---|
| 1 | `\lstinputlisting` and similar inline files outside the repository | high | Already fixed in `2174086` (pandoc sandbox, stubs for older pandoc) |
| 2 | TikZ compilation can read any file (`\pdffiledump`, `\input` with an absolute path) | high | Fixed: pdflatex runs with `openin_any=p`, `openout_any=p` on a copy in a temporary directory |
| 3 | Extension fallback in figure lookup skipped the containment check for symlinks | high | Fixed: each candidate is resolved and checked |
| 4 | `[text]{.scrvtexnote}` in Markdown became a trusted note | medium | Fixed: LaTeX notes carry the render nonce like other trusted classes |
| 5 | `\input` files lost the root document's macros | medium | Fixed: files without a preamble inherit the last root's definitions |
| 6 | `\input` files rendered after the whole parent | medium | Fixed: included files render at their `\input` |
| 7 | Labels already prefixed `sec-`/`fig-` gave `??` | medium | Fixed |
| 8 | `%` inside verbatim environments and `\verb` treated as comments | medium | Fixed |
| 9 | Comments inside a tikzpicture broke its compilation | medium | Fixed: pictures are extracted before comments are converted |
| 10 | Text on the same line as `\begin{document}` is hidden | medium | Not fixed: rare layout; recorded as a limitation |
| 11 | Only the first label in an `align` resolved | medium | Fixed: every label in a display refers to its one shown number |

Codex's tokens: about 1.9M input (1.8M cached), 17.5k output.
