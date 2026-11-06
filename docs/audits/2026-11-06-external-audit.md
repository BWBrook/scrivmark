# External audit of the built system: dispositions, 6 November 2026

Audit: ChatGPT (GPT-6 Pro), commissioned by Barry W. Brook, of commit
`d1438b2` (version 0.3.0 plus `return --statement`), delivered 5 November
2026 as a report, reproduction scripts and 12 acceptance tests. The report is
not in this repository; its findings are summarised here.

Fixes: Claude (Opus 5.5) did the integrity, lifecycle and git findings and
integrated the work; two Claude Sonnet 5.5 subagents did the rendering
(F04, F12, A03, A04, timeouts) and viewer (F09–F11) findings in separate
files. Each finding was checked against the current code before it was fixed,
and the auditor's acceptance tests were adopted as regression tests
(`tests/test_audit_integrity.py`) alongside tests for the other findings
(`tests/test_render_audit.py`, `tests/test_frontend_audit.py`). An
independent review of the fixes by Codex (`gpt-6.1-sol`) is recorded below.

| # | Finding | Verified | Disposition |
|---|---|---|---|
| F01 | A deleted passage's draft or ask could be applied to its surviving identical twin | Reproduced (6 acceptance cases) | Fixed. A passage that had a twin when anchored (the same text and stored context, or the same whole lines), one copy of which is gone, resolves as an orphan (`why: ambiguous`); the `moved` fallback also requires the passage to have been unique with its context when anchored. Wording is never applied to an orphan; it must be re-attached. Return lists such comments as orphans. A unique passage that moved is still found. |
| F02 | Ask wording and keep-all approvals were not bound to what the human saw | Reproduced | Fixed. The revision now covers drafts and asks; keep-all, keeping or dropping one draft, and applying an ask each send the revision of the page that showed them (`keep … --expect` from the CLI). |
| F03 | A source outside the repository could be edited with no record | Reproduced | Fixed. Sources, bibliography and CSL must be relative paths inside the repository, after symlinks, outside `.git`, `.scrivmark` and `reviews/`; checked on every load, and again, with git tracking, at the moment any review write touches a manuscript file. A round refuses to start on sources git does not track, which catches ignored files. |
| F04 | On pandoc 3.1.3 a LaTeX macro (or a Markdown file using internal classes) could pull a local file into the page | Reproduced by reading; 3.1.3 not available locally, so confirmed by CI | Fixed. Internal LaTeX blocks carry the render nonce and the filter ignores any without it, closing the Markdown route on every version. LaTeX sources need pandoc 3.7, whose reader sandbox blocks file reads; older versions are refused with a clear message, and `doctor` reports it. |
| F05 | Closed or newly returned rounds could still be changed | Reproduced | Fixed. Every mutation takes the lock, checks the revision, reloads the record and only then checks the round's phase. |
| F06 | A return that failed at the record commit was not repaired by a retry | Reproduced | Fixed. Sealing a return (record committed, both tags at the recorded commits) is a repeatable step run on every return and retry; a tag pointing elsewhere is refused. |
| F07 | Metadata commits swept in unrelated staged work | Reproduced | Fixed. Metadata commits use `git commit --only -- <paths>`. |
| F08 | `reject --all` could partly succeed yet say "Nothing was changed" | Reproduced | Fixed, keeping one-at-a-time semantics (each rejection changes the text the next one checks). On a refusal the command reports `done`, `failed`, `remaining` and `rev`, in text and JSON, and exits 1. |
| F09 | Formatting made a comment highlight the wrong repeated phrase | Confirmed from code | Fixed. The server gives the highlight text, occurrence and count in the reader's plain form (escaped characters now kept, maths and citations opaque on both sides); the page marks the span only when its count agrees, otherwise the whole block. Checked in headless Chromium, including a maths case. |
| F10 | Two quick Edit clicks opened two editors | Confirmed from code | Fixed. The editor slot is reserved before the source request, with a token that its save and cancel also check. Checked in headless Chromium. |
| F11 | A live update stole focus and moved the caret | Confirmed from code | Fixed. The focused composer and its selection are captured just before a rebuild and restored only if it had focus. Checked in headless Chromium. |
| F12 | `\input` inside a paragraph rendered after the paragraph | Reproduced | Fixed for an include on its own line: the block is split there, so the child renders in place with its own source mapping. An include sharing its line with text, inside a group (such as a tracked-change span), or after a macro defined earlier in the same block still follows its block; splitting those would duplicate line ranges or lose the macro. |
| F13 | Rejecting a change to a script lost its executable mode | Reproduced | Fixed: writes keep the existing file's mode. Restoring a mode change itself from git, or the mode of a deleted file, is not handled. |
| A01 | Recovery of an interrupted write could overwrite a later edit | Reproduced | Fixed. The intent records each file's hash before and after; recovery completes only files still in their before state, and otherwise writes nothing, sets the intent aside and reports it. |
| A02 | A detached HEAD became the author branch | Reproduced | Fixed. `start` needs a local branch, resolved as `refs/heads/…` so a tag of the same name cannot choose the base. |
| A03 | Compact CSL JSON mapped only the first entry | Confirmed from code | Fixed for keys. Entries sharing one line also share its line range, since mapping is by lines; the file is not reformatted. |
| A04 | A quote in a filename broke the wrapper | Confirmed from code | Fixed (escaped for pandoc's attribute syntax). |
| Perf | Repeated `git show` while building the viewer state | Reproduced: 639 reads of 4 file versions for a 202-change round | Fixed: reads at a full commit SHA are cached. That round's render went from 8.4 s to 1.5 s (cold) and 2.4 s to 1.1 s (warm). |
| Timeouts | Pandoc runs had no timeout | Confirmed from code | Fixed: 120 s, with a clear error. |
| CI | The wheel check passed if any one resource was present | Confirmed | Fixed: it requires each one. |

## Not done, and why

- The workflow features the audit suggests (a filtered review queue, a clean
  reading view beside the redline, a preview of what `return` and `close`
  will do, a review-summary export, recoverable selection drafts) are good
  ideas and are left for use to prioritise.
- Separating margin-only updates from re-rendering the manuscript, and a
  browser test in CI, are deferred; the viewer findings were checked in a
  headless browser by hand.
- The design document's status section has been brought up to date, as the
  audit noted it had drifted.

## Independent review of the fixes

Codex (`gpt-6.1-sol`, high effort, read-only, 438 s, about 3.8M input tokens,
mostly cached) reviewed the diff against the audit. It judged ten findings
fixed and seven partly fixed, and found seven new defects, each with a
concrete failing case. All were checked and fixed, with tests:

| Point | What Codex found | Fix |
|---|---|---|
| F01 | Twins whose lines differ far from the passage (`P North.` and `P South.`) escaped the identical-line check | Twins are also found by identical text and stored context |
| F02 | Single draft keep/drop and ask apply sent the latest revision, which can be newer than the cards on screen | Each card sends the revision it was drawn with |
| F03 | Writes after a round started did not recheck containment or tracking | Checked at write time for every review write |
| F09 / N06 | Escaped characters dropped in the plain form; maths annotations could match | Escapes kept; maths and citations opaque on both sides |
| F10 | Save and cancel cleared the editor slot without checking its token | They check it |
| F12 / N02 / N07 | Splitting mid-line duplicated line ranges and lost macros defined earlier in the block | Split only at includes on their own line, not after a definition |
| A02 | A tag named like the author branch could choose the base | Resolved as `refs/heads/…` |
| N01 | `./paper.md` failed the tracking check | Paths normalised on load |
| N03 | `keep d1 --expect` ignored `--expect` | Passed through |
| N04 | An intent in the earlier format crashed recovery | Set aside with the usual message |
| N05 | Two recovery archives in one second could collide | Random suffix |
