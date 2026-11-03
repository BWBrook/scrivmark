# External review, 3 November 2026

Reviewer: GPT-6 Pro, commissioned by Barry W. Brook, on commit `0d8e924`.
The full report was supplied to Claude as a Markdown file; this records its
findings in brief and how each was settled. Adjudication and fixes: Claude
(Opus 5.5). Every finding was accepted. Each has a regression test in
`tests/test_external_review.py` that asserts the promise behind it, not only
the reported example; the suite runs in CI on pandoc 3.1.3 (Ubuntu 24.04's
version) and 3.7.

## Findings

| ID | Finding | Resolution |
|---|---|---|
| F01 | Reject could change a surviving copy of a repeated passage. | Decisions are now checked against the *expected* text (the returned text with recorded rejections applied). A change is rejected only when it is `exact`: intact, its 40-character neighbourhood unchanged, and that neighbourhood unique, or repeated only within an untouched stretch. Otherwise the state is `shifted`, `ambiguous` or `gone`, and reject refuses. |
| F02 | A kept draft could rewrite a lookalike claim after its passage was deleted. | Anchor resolution distinguishes `exact`, `edited`, `moved` and `orphan`. A passage found elsewhere is adopted only if it occurs once with its full original context; a lookalike becomes a listed candidate. Keeping wording requires `exact` or `moved`. |
| F03 | Title, author and reference metadata bypassed the attribute sanitiser. | The Lua filter now sanitises the whole document, metadata included; a stdlib allowlist cleans the final HTML after template and citeproc; the viewer page carries a Content Security Policy without inline scripts. |
| F04 | Quoted git paths made a rejection on `résumé.md` a false success. | All git path parsing is NUL-delimited (`diff --name-status -z`, `status -z`, `ls-files -z`). A failed lookup of the returned file no longer counts as "unchanged". |
| F05 | Rejecting one change blocked rejecting its neighbour. | Solved by the expected-text model in F01: scrivmark's own rejections are part of what it expects. Both orders, and `reject --all`, restore the original sentence. |
| F06 | Return and close were resumable at only some failure points. | `return` on a returned round publishes it; `close` commits a closed record left uncommitted, separates local merge from remote publication, pushes when origin lacks the merge, and takes a round id so it can resume from the author branch. `gh` exit codes are checked and reported. |
| F07 | On pandoc 3.1.3 the tutorial fell back to a whole-body redline with duplicate footnotes. | Closing wrapper fences are preceded by a blank line, and historical copies of text no longer define footnotes. Deleted citations rendered by citeproc are unlinked. |
| F08 | A selection could extend past what was selected. | The page replaces generated material (citations, cross-references, maths, footnote marks) with a placeholder before sending a selection, and typography is normalised; the mapper then requires an exact, unique match (using preceding text to choose between repeats), or anchors to the whole block. |
| F09 | Structural whitespace edits mixed with word edits escaped the catalogue. | Each paired block is checked by undoing all its units and comparing the result with the original through pandoc; if the meaning differs, the block becomes one block change. |
| F10 | Margin redraws discarded unsent replies and draft edits. | Composition text is held by thread or draft id and restored on every rebuild; resizing only re-lays out the margin; cancelling one composition leaves the others. |
| F11 | A metadata change could be pending with nowhere to decide it. | Every pending change without a place in the running text appears in a panel above the manuscript with its before and after text and Accept and Reject. |
| F12 | `init` committed unrelated staged work. | `init` refuses while other changes are staged. With no files named, it previews its detection and writes nothing unless given `--yes`. |

## Smaller corrections

| ID | Resolution |
|---|---|
| C01 | TOML strings are written with proper escaping; invalid TOML gives a clear error. |
| C02 | The render cache key includes `scrivmark.toml` (and so file order), the bibliography and the CSL file. |
| C03 | Section references take the heading's current text, without deleted words. |
| C04 | Supported platforms are stated: Linux, macOS, WSL. |
| C05 | `docs/DESIGN.md` has an implementation-status section; `doctor` says `.qmd` renders with pandoc in this version. |

## Workflow and interface recommendations taken up

- `j`/`k` move through a complete queue: changes outside the text, orphaned
  comments, drafts, open comments and pending changes. Resolved comments are
  left out.
- Pending changes are focusable; Enter opens the decision with focus on
  Accept, Escape returns focus. The edit button shows on keyboard focus.
- Decisions can be undone, from the viewer straight after deciding or with
  `scrivmark undo`. Undoing a rejection checks that the restored text is
  still exactly in place.
- Whole-file changes show the `git diff` command that displays them.
- The header shows who you are; a hint line gives the next command; an
  offline badge shows when the viewer loses its server.
- The tutorial sets identity and the workflow statement before the first
  round, and the README and tutorial show `scrivmark init` with named files.

## Not taken up yet

- Re-attaching an orphaned comment from the viewer (candidates are computed
  but not yet offered).
- A browser walk-through in CI. Playwright runs of both sides were done by
  hand for this release.
