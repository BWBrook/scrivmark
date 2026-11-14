# Handover review of 0.7.0: findings and dispositions, 14 November 2026

On 14 November 2026 Barry W. Brook handed development of scrivmark to Claude
(Fable 5.2, in Claude Code). Two checks of commit `1a16842` (version 0.7.0)
followed:

- **A practice round.** Claude ran the tutorial manuscript through a whole
  round (agent drafts in a batch, keep all, return, author decisions, respond,
  contest, a second response, close, export) from the command line and in the
  viewer, in a browser pane 800 pixels wide, and ran the tests on macOS.
- **An independent code review.** Codex (`gpt-6.1-sol`, high effort) via
  `codex-delegate`, read-only, 818 s, about 4.6M input tokens (4.4M cached)
  and 23.5k output. It was asked for defects that remain after the earlier
  audits, each with a file, a line and a failing scenario. It could not create
  repositories or compile TeX in its sandbox, so it reproduced in memory.

Claude checked each finding against the code and fixed it, with a regression
test that fails on 0.7.0 (`tests/test_review_0_8.py`, `tests/test_text.py`,
`tests/test_latex.py`). The two viewer findings (F01, F02) were checked in a
browser by hand; there is still no browser test in CI.

## From the practice round

| # | Finding | Disposition |
|---|---|---|
| P1 | A rewritten sentence was frozen as one change per fragment around each surviving word (three for one sentence), each decided alone; rejecting one could leave nonsense | Fixed: edits separated by at most three unchanged words inside one clause, fewer than the edits on both sides, are one change (`textdiff._joins`) |
| P2 | TikZ compilation could read any file: TeX Live 2026 made `openin_any` a no-op (its `texmf.cnf` says so), and the 4 November fix relied on it. Found on macOS, where the existing test failed; Codex found it independently (S01) | Fixed: the compile runs under `sandbox-exec` (macOS) or `bubblewrap` (Linux), with no network, writes only in its directory, and no reads of home directories, other volumes or temporary directories beyond TeX's own trees. Without a working sandbox pictures are not compiled. The Linux path was checked in an Ubuntu 24.04 container: reads from `/home`, `/tmp` and `/root` failed and an ordinary picture compiled |
| P3 | In a narrow pane the top bar wrapped onto two lines and the help box ran off the top of the window | Fixed |
| P4 | `j`/`k` stopped twice at each replacement (its deleted and inserted halves) | Fixed |
| P5 | `status` said "`scrivmark return` commits the round" in every phase | Fixed |
| P6 | Two tests could not pass on macOS (`/etc/hostname`; P2) | Fixed |
| P7 | README and DESIGN had drifted (version, `nh3`, the guide's path) | Fixed |

## From the code review

| # | Finding | Severity | Disposition |
|---|---|---|---|
| I01 | The viewer read a block's source and hashed the file separately, so a save in between gave an editor old text with the new revision, and saving it overwrote that save | high | Fixed: one read gives both |
| I02 | A source editor opened in one round could save into another whose file had the same bytes | high | Fixed: the editor carries its round, checked on save |
| I03 | An interrupted write could be replayed on another branch whose file matched | high | Fixed: the intent records the branch; on any other it is set aside, as is an intent in the earlier format |
| I04 | Of two similar passages ("North: … high", "South: … high"), with one deleted and the other renamed, an anchor on the deleted one resolved as exact on the survivor, so wording could change the wrong claim | high | Fixed: an exact projection whose own context changed, with another copy lost in the same changed stretch, is an orphan (`review._rival_lost`). A passage on an untouched line, or one whose rival went in a separate edit, still resolves |
| S01 | As P2 | high | Fixed, as P2 |
| A01 | A selection that overlaps another occurrence of itself ("ana" in "banana") mapped to the wrong one | medium | Fixed |
| C01 | `keep`, `drop` and `resolve` with several ids hid what was done before one was refused | medium | Fixed: they report `done`, `failed`, `remaining` and `rev`, as `accept` and `reject` do |
| C02 | `comment --lines` out of range raised an exception instead of a refusal | low | Fixed |
| F01 | An open draft or message editor took the page's newest revision on each refresh, so its old wording could be kept over a version revised meanwhile | high | Fixed: an edit is sent with the revision it began on once the item underneath has changed, and the card says so |
| F02 | A decision box left open across a change of round decided the same-numbered change in the new round | high | Fixed: it closes when the round changes |

Codex reported no further defect in the loopback binding, the Host, token and
Origin checks, path containment, the sanitiser and Content Security Policy,
`read.js` or `app.css`.

## Noted, not changed

- Any program on the same machine can load the viewer's page and so obtain
  its cookie: the token keeps other websites out, not other local users. On a
  machine shared with people you do not trust, do not run the viewer.
- The Linux sandbox was tested in one container image, not on WSL.
