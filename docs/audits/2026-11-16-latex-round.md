# LaTeX practice round, 16 November 2026

Claude (Fable 5.2), owner of scrivmark since 14 November 2026, ran scrivmark
0.10.0 on public LaTeX papers, at Barry Brook's suggestion: the stress round
earlier the same day ([2026-11-16-stress-round.md](2026-11-16-stress-round.md))
had no long LaTeX manuscript to work on.

## Material

Shallow clones, kept outside this repository and deleted afterwards:

- `astropy/astropy-v2.0-paper`: `main.tex` (10,700 words, 1,470 lines, hard
  wrapped), with `author.tex` and `registry.tex` read through `\input`, the
  AASTeX class, eight figures under `\graphicspath{{figures/}}`.
- `scipy/scipy-articles`: `scipy-1.0/paper.tex` and `scipy-2.0/src/paper.tex`,
  used as a source of blocks for random edits.

None of their text is in this repository; the tests that came out of the
round use sentences written for them.

## Method

1. One scripted round on the Astropy paper with 23 reviewer edits: sentence
   rewrites, word swaps, a title, a heading, a footnote, a number in maths, a
   caption, an equation, a preamble comment, a sentence deleted, a paragraph
   inserted, split, joined and re-wrapped, a paragraph and a whole subsection
   moved, a subsection deleted, a citation, a `%` comment added, a graphic's
   width, and edits in the two `\input` files. Returned, then every change
   rejected, every change accepted, and alternately accepted and rejected.
2. Random edits (word edits, rewrites, moves, deletions, insertions, splits,
   joins, re-wrapping, caption edits) to runs of three to nine consecutive
   blocks taken from the three papers, each with its real preamble.
3. The returned round read in the viewer, in a browser pane 800 pixels wide.

## Found and fixed in 0.11.0

- **A heading with its `\label` on the next line.** The splitter made the
  label a paragraph of its own. Pandoc gives a heading its identifier only
  when it reads the two together, so the guard fused the heading with the
  blocks on both sides: 14 fused blocks in the Astropy paper. A paragraph
  moved to just before `\section{...}` was recorded as "moved and edited" with
  the heading inside it, the reworded heading was not a change of its own, and
  the paragraph after it was recorded as removed and added again. Rejecting
  everything still restored the text, but the changes could not be decided one
  at a time. The heading now takes the label lines after it. No fused blocks
  remain, and reading the file fell from 4.0 s to 0.7 s.
- **`\graphicspath` was ignored**, so all eight figures showed as "Figure
  could not be shown".
- **A caption edit showed the whole figure twice.** `figure` and `table`
  floats are now compared by word. Where a mark cannot be drawn inside the
  float the existing guard falls back to the whole float.
- **One edited comment in the preamble was one change of 48 lines.** Hidden
  blocks (LaTeX preamble and postamble, YAML header) are now compared by line.

## Checks after the fixes

- The scripted round: reject-all gives the base text in all three files
  (in `main.tex` apart from line breaks inside two paragraphs, see below);
  accept-all gives the returned text exactly; the alternating run completes
  with no refusal.
- 160 random rounds through the real return, reject and undo path (251
  changes, 63 moves): rejecting everything restored the authors' text and
  undoing restored the reviewer's, in every round.
- 1,850 random pairs in memory (3,003 changes, 749 moves): one failure,
  where the random edit put a blank line inside a macro's argument, which is
  not valid LaTeX. Such a blank line is not recorded as a change.
- In the viewer: figures shown, the caption edit marked on its two words, the
  preamble change listed as one line, both moves marked at both ends.
- 227 tests pass, six of them new (`tests/test_latex_0_11.py`).

## Left as it is

- Line breaks inside a paragraph are not changes, in LaTeX or Markdown. A
  paragraph the reviewer re-wrapped stays re-wrapped after every change in it
  is rejected, and a `%` comment the reviewer added takes its line break with
  it when rejected. The words and paragraph breaks are restored exactly.
- A change with nothing to see (a `%` comment added, a `\label` moved) is
  shown as the whole paragraph or heading, old and new.
- `\title{...}` is not in the running text under AASTeX, so an edit to it is
  listed above the text.
- A graphic named in a file read by `\input` from a subdirectory is looked
  for from that file's directory, where LaTeX looks from the root document's.
- Not tested: a round on WSL, `beamer` or `memoir` classes, a document split
  over `\include` chapters, and bibliographies in `biblatex`.
