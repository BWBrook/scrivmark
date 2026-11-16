# Stress round on a real paper, 16 November 2026

Claude Fable 5.2 ran a complete round on a scratch clone of
`BWBrook/transit-forecast-audit` (manuscript 13,668 words in 571 lines, one
paragraph per line, plus a 1,075-word brief): 20 scripted edits, 12 comments
and 6 agent drafts added in one batch, `keep --all`, `return`, then every
change decided as the author. The clone was discarded; the paper's own
repository was not touched. This note records what the round showed in
scrivmark 0.8.0 and what 0.9.0 does about it.

## Edits made

A sentence rewrite; single-word swaps; two small corrections in one sentence;
a sentence deleted; a number reformatted; a list item, a heading, a table
cell, a figure caption, the YAML title and a citation group edited; a
paragraph inserted, one moved, one split and two joined; a paragraph rewritten
throughout; two separate edits in one long paragraph; edits in a second file;
a section deleted.

## Findings

| # | Finding | Disposition |
|---|---|---|
| R1 | The paragraph rewritten throughout was 12 changes and a rewritten sentence 5, each a fragment such as `an` → `on` | Fixed: a sentence with two or more edits changing at least four words, and at least as many as stand between them, is one change (`textdiff._rewritten`) |
| R2 | Blocks in an edited stretch were paired by position and accepted as a pair when their characters overlapped by half, which unrelated English paragraphs always do. The moved paragraph was word-diffed against its unrelated neighbour: 7 changes of nonsense | Fixed: blocks are paired by word-order likeness (0.3 or more; unrelated paragraphs of this paper score 0.05 at the median and 0.16 at the 99th percentile), aligned in order (`textdiff._match`). A split or joined paragraph is one whole-block change |
| R3 | Two quotes occurred twice (the abstract repeats the results). The refusal did not say where, and the first prefix tried also matched both | Fixed: the refusal names the lines, and `--lines` with `--quote` picks the occurrence |
| R4 | The batch refusal ended "choose one.. Nothing was added" | Fixed |

| R5 | Found by the random rounds below, not the scripted one, and present before 0.8.0: a block change at the end of a file left out the blank line before it, which exists in only one version. Rejecting a removed last paragraph joined it to the paragraph above; rejecting an appended one left a blank line | Fixed: a block change takes in the separator before it where the versions differ there (`textdiff.align`). `tests/test_stress_0_9.py` holds five such cases and twelve seeded random rounds; six of those tests fail on 0.8.0 |

The round's 48 changes became 28 under 0.9.0.

## Random rounds

To test the new block pairing, 120 rounds were generated from the paper's
paragraphs, each with one to six random operations (word edit, partial
rewrite, move, delete, insert, split, join), returned, then rejected change by
change in random order and undone. Before the R5 fix 7 of the first 40 failed
to restore the original, and 3 of 40 failed with the 0.8.0 diff code. After
it, all 120 (355 changes) restored the original exactly, and undoing restored
the reviewer's text.

## Checked and sound

- Accepting all 28 changes left both files identical to the returned version;
  rejecting all 28 left them identical to the authors' original; alternating
  reject and accept, then undoing all 28, returned to the reviewer's text.
- Render: 0.74 s cold for the unedited paper, 1.5 s after the edits, 0.05 s
  when nothing changed. Adding 18 items in one batch took 0.4 s.
- All 12 comments and 6 drafts anchored where intended, including on a table
  cell, a heading and the moved paragraph. Rejecting everything orphaned 2 of
  16 threads, both on text the reviewer had added.
- The viewer showed the round correctly at 800 px, with 259 blocks, 28 changes
  and 16 cards.

## Left as it is

- A move is a removal and an insertion decided separately, so accepting one
  and rejecting the other duplicates or loses the paragraph. (Done in 0.10.0,
  the same day: a move is one change. Checked on 3,000 random pairs of texts
  in memory, 731 moves among 8,684 changes, and on 160 random rounds through
  the real return, reject and undo path.)
- Two coupled one-word edits (`5.9–6.7` → `between 5.9 and 6.7`) stay two
  changes, as two independent word swaps must.
- A split paragraph with an edit inside shows as a whole paragraph replaced.
- No LaTeX manuscript of this length was on the machine, so the LaTeX path was
  not stressed.

## Follow-up the same day: a move as one change (0.10.0)

Claude (Fable 5.2) made a moved passage one change with two places. Checks:

- 3,000 random pairs of texts in memory (731 moves among 8,684 changes) and
  160 random rounds through the real return, reject and undo path: rejecting
  everything gave the base text exactly, undoing gave the reviewer's.
- 1,100 further random pairs moving, duplicating, deleting and editing lists,
  tables, code blocks, headings, footnote definitions, fenced divs, display
  equations and repeated identical paragraphs, with YAML front matter, without
  a final newline, and in LaTeX: all exact.
- In the viewer, in a browser pane 800 pixels wide: both places marked, one
  decision box from either, reject and undo.
- An independent read-only review by Codex (`gpt-6.1-sol`, high effort, via
  `codex-delegate`). It confirmed no text-corruption defect and found one
  regression, fixed with a test: the search for moves compared every removed
  block with every added one, so a wording edit through 2,048 paragraphs took
  50 s where 0.9.0 took 0.6 s. Exact matches are now looked up, and near
  matches are sought only between different changes and for no more blocks
  than the alignment itself compares.

Left as it is: a file that mixes CRLF paragraph breaks with LF line ends
inside blocks can fail to restore exactly (1 of 150 random pairs; 0.9.0 fails
on such files too). A moved passage that was also edited is decided whole.
