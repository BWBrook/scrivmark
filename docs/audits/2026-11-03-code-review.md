# Code review, 3 November 2026

Reviewer: Codex (`gpt-6.1-sol`, high effort), via `codex-delegate`, read-only, on commit e7d7626. Adjudication and fixes: Claude (Opus 5.5).

**Verdict.** Block release: confirmed manuscript corruption, executable manuscript HTML, and unrecoverable lifecycle failures. No files were edited. pytest was blocked by uv’s cache write in the read-only environment; small read-only Python reproductions confirmed the principal defects.

## 1. [blocker] src/scrivmark/render.py:207; src/scrivmark/static/app.js:82

Manuscript attributes allow JavaScript execution in the authenticated viewer.

- Failure: Rendering `![x](missing.png){onerror="alert(1)"}` produces an img with an executable onerror attribute. Likewise, span onclick attributes survive. app.js inserts this output through innerHTML, allowing manuscript content to read authenticated API responses and modify the manuscript.
- Proposed fix: Sanitise the final rendered HTML with an explicit allowlist before returning it. Remove event handlers and other executable attributes; add regression cases for Pandoc attribute syntax.
- **Adjudication:** Fixed: the render filter reduces manuscript attributes to an allowlist; regression test.

## 2. [blocker] src/scrivmark/textdiff.py:151; src/scrivmark/review.py:135

Reject can restore base wording into the wrong occurrence after repeated text is deleted.

- Failure: Reproduced with R containing two identical `The rate is low.` lines, where only the first was a reviewer change from `high`. After the author deletes the first line, projection maps its change onto the surviving second line. Reject changes that originally unchanged line to `high`.
- Proposed fix: Detect ambiguous alignments among repeated passages and refuse destructive operations. An arbitrary SequenceMatcher alignment plus equal text does not establish a unique projection.
- **Adjudication:** Fixed: reject also requires 32 characters of unchanged text either side, otherwise refuses; regression test.

## 3. [major] src/scrivmark/server.py:120

Anchor endpoints read arbitrary paths outside the repository.

- Failure: anchor_from accepts absolute paths and traversal without checking manuscript membership or containment. A manuscript can supply a nested `.scrv-b` div with `file="/etc/hostname"` and `lines="1-1"`; Pandoc preserves these data attributes. Selecting its innocent text and commenting falls back to anchoring the external file’s contents, which then enter the review record. External-path anchoring was reproduced.
- Proposed fix: Validate every anchor path against configured manuscript files and resolved repository containment, including symlinks. Reject manuscript-supplied mapping attributes or distinguish trusted wrappers from source markup.
- **Adjudication:** Fixed: only wrappers carrying a per-render nonce may claim source locations, and anchors must name a manuscript file; regression test.

## 4. [major] src/scrivmark/review.py:418

Keeping draft wording overwrites intervening edits to its passage.

- Failure: resolve deliberately returns `state: ok, edited: true` when surrounding context survives. triage accepts that result without checking the current text against the draft’s original quote. Reproduced: a draft targeting `120`, followed by a manual correction to `118`, applies its old proposed wording `100` over the correction.
- Proposed fix: Before applying wording, require an unambiguous unchanged projection and exact equality with anchor.exact. Refuse edited passages while retaining the draft.
- **Adjudication:** Fixed: keeping wording requires the passage to be exactly as drafted; the draft is kept otherwise; regression test.

## 5. [major] src/scrivmark/rounds.py:243

Close marks the round closed before operations that can fail, making retries impossible.

- Failure: The closed record is written before its commit, target-tip check, push, checkout, and merge. A commit-hook failure was simulated: the record remained closed, and retry immediately failed with `only a returned round can be closed`. Push or merge failures produce the same stranded lifecycle.
- Proposed fix: Make closure resumable with explicit progress and idempotent recovery, or restore returned status when finalisation fails. A closed record must not prevent completing an unfinished merge.
- **Adjudication:** Fixed: close is resumable; a closed but unmerged round finishes merging on rerun; regression test.

## 6. [major] src/scrivmark/rounds.py:115

Return can silently discard concurrent viewer comments.

- Failure: return_round loads a record, then commits work and builds the catalogue without taking the shared transaction lock. A viewer comment saved after that initial load is overwritten when line 126 writes the older in-memory record. A draft added after the initial draft check can also escape triage.
- Proposed fix: Run lifecycle mutations under the same lock as viewer and CLI edits, reloading and validating state inside the lock before committing or replacing records.
- **Adjudication:** Fixed: return and close run under the shared lock and reload state inside it.

## 7. [major] src/scrivmark/server.py:274

Source-save revision checking occurs outside the mutation lock.

- Failure: source_put verifies file_rev, then calls replace_lines without an expected revision. A concurrent CLI mutation can change the file after that check or while replace_lines waits for the lock. The stale line replacement then overwrites the newer text.
- Proposed fix: Check file_rev inside the same transaction that reads and replaces the lines. Pass the expected file revision into that transaction.
- **Adjudication:** Fixed: the file revision is checked inside the write transaction.

## 8. [major] src/scrivmark/textdiff.py:52

Semantically significant whitespace edits disappear from the change catalogue.

- Failure: Reproduced: changing a soft line break to a Markdown hard break, or indenting `- Beta` beneath `- Alpha`, changes Pandoc’s AST but produces zero diff units. These changes can therefore merge without any accept/reject decision or redline.
- Proposed fix: Ignore whitespace only when its semantics are unchanged. Represent structural whitespace edits as block changes, with a coverage check ensuring all semantic B-to-R differences are catalogued.
- **Adjudication:** Fixed in part: a whitespace-only block edit that changes pandoc's AST becomes a block change; regression test. A structural whitespace edit inside a block that also has word changes is still not separately catalogued.

## 9. [major] src/scrivmark/rounds.py:171

Retrying after manual merge-conflict resolution skips decision invalidation.

- Failure: integrate instructs users to resolve conflicts, commit, and retry. On retry, the author tip is already an ancestor of HEAD, so this early return bypasses all change checks. An accepted unit whose text was changed during resolution retains its acceptance. The bypass was reproduced with a mocked integration.
- Proposed fix: Reconcile affected decisions before recording a newly integrated tip, including when the merge was completed manually. Preserve enough pre-integration state to identify disturbed units.
- **Adjudication:** Fixed: decisions are re-checked whenever the integrated tip changes, however the merge was completed.

## 10. [major] src/scrivmark/render.py:159

Returned redlines recompute changes and can lose frozen change IDs.

- Failure: Reproduced: base `The cat often ate food.`, returned `The dog often consumed food.` creates c1 and c2. If the author changes `often` to `frequently`, the recomputed diff combines both changes. next() assigns the entire combined span to c1; c2 remains pending but has no clickable target.
- Proposed fix: Build returned redlines directly from the frozen catalogue and its projected ranges. Do not assign frozen IDs by first overlap with recomputed B-to-current hunks.
- **Adjudication:** Fixed: the author view is built from the frozen catalogue's projected ranges, not a recomputed diff.

## 11. [major] src/scrivmark/selection.py:81

Selection mapping can truncate a selection at an earlier repeated suffix.

- Failure: Reproduced with source `Start alpha beta. Gap [@smith] alpha beta.` and rendered selection `Start alpha beta. Gap (Smith 2020) alpha beta`. map_selection returns only `Start alpha beta`, because _find_end chooses the first matching suffix after the start.
- Proposed fix: Align the complete selection, accounting for generated tokens, and require consistent unambiguous boundaries. Fall back to a block anchor when that cannot be established.
- **Adjudication:** Fixed: the end boundary is chosen by span length, with ties refused; regression test.

## 12. [major] src/scrivmark/static/app.js:160

Thread highlighting always chooses the first matching phrase within a block.

- Failure: A correctly anchored comment on the second `was high` in one paragraph highlights the first occurrence instead. highlight ignores the source range and uses text.indexOf, so the displayed comment points at the wrong claim even when backend anchoring is correct.
- Proposed fix: Map the resolved source range to rendered nodes. If repeated wording cannot be distinguished, highlight the block rather than an arbitrary occurrence.
- **Adjudication:** Fixed: the server sends which occurrence within the block the anchor is; the page highlights that one.

## 13. [major] src/scrivmark/config.py:37

Deleting a configured manuscript file prevents returning the round.

- Failure: After a reviewer deletes a listed Markdown file, return_round calls active, which calls config.load and fails because the file no longer exists. The deletion never reaches the catalogue’s file-change handling, despite deletion being a supported change type.
- Proposed fix: Allow lifecycle code to load configuration containing deleted sources and catalogue their Git changes. Handle missing files explicitly in rendering rather than making the entire round inaccessible.
- **Adjudication:** Fixed: configuration no longer requires listed files to exist; deleted files are catalogued and shown as deleted.

## 14. [major] src/scrivmark/rounds.py:199

Opening the currently checked-out round never updates it from origin.

- Failure: open_round fetches, but the fast-forward merge is inside the branch-switch condition. If the round is already checked out and its remote branch has been returned or updated, reopening it leaves the local record and manuscript stale, potentially still in reviewer mode.
- Proposed fix: Perform the clean-state and remote fast-forward checks regardless of whether checkout is necessary, and surface update failures.
- **Adjudication:** Fixed: open fast-forwards from origin whether or not the round was already checked out.

## 15. [major] src/scrivmark/server.py:286; src/scrivmark/static/app.js:359

Selections and asks can receive a fresh hash for text selected from a stale rendering.

- Failure: If an external edit shifts blocks before a selection or ask is posted, the server applies the old rendered line numbers to current source. When quote matching fails, it anchors a different block and stamps the current file hash. The CLI consequently reports the wrong selection as non-stale.
- Proposed fix: Carry the rendering’s revision with captured selections and asks, and validate it under the shared lock before mapping. Retain the captured revision through comment composition as well.
- **Adjudication:** Fixed: selections, asks and comments carry the render key and are refused if the page was stale.

## 16. [minor] src/scrivmark/cli.py:275

Batch decisions with --expect fail after the first successful mutation.

- Failure: `accept c1 c2 --expect REV` passes the same initial revision to both transactions. Accepting c1 changes the record hash, so c2 fails as stale and the command leaves a partially applied batch. Batch resolve has the same problem.
- Proposed fix: Apply the batch in one transaction checked against REV, or deliberately advance the expected revision after each successful operation and report partial results.
- **Adjudication:** Fixed: --expect applies to the first operation of a batch.

## 17. [minor] src/scrivmark/server.py:186

Two viewers on different localhost ports overwrite each other’s authentication cookie.

- Failure: Cookies are scoped by host and path, not port. Both servers set `scrivmark` at `/` on 127.0.0.1. Opening the second project replaces the first project’s token, causing its API and event-stream requests to receive 403 responses.
- Proposed fix: Give each viewer a distinct cookie name, or use a per-viewer token mechanism that does not collide across ports.
- **Adjudication:** Fixed: the cookie name includes the port.

## 18. [minor] src/scrivmark/assets/scrivmark.lua:178

Relative manuscript links bypass the repository file route.

- Failure: Images are rebased through /files/, but links are only scheme-checked. Reproduced: `[Appendix](appendix.pdf)` renders with href `appendix.pdf`, which resolves to `/appendix.pdf` and returns 404; links from nested manuscript directories also lose their source-directory context.
- Proposed fix: Rebase relative local link targets using the source directory and /files/, while preserving fragment links and allowed external URLs.
- **Adjudication:** Fixed: relative links are rebased like images.

