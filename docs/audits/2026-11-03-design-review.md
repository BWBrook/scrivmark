# Design review, 3 November 2026

Reviewer: Codex (`gpt-6.1-sol`, high effort), via `codex-delegate`, on the first draft of `docs/DESIGN.md`. Adjudication: Claude (Opus 5.5).

**Verdict.** Viable direction, but not ready to lock. The main blockers are unstable change identity and potentially destructive rejection. Rendering also needs structural changes before it can reliably support real Quarto manuscripts. The settled workflow decisions can stand.

## 1. [blocker] Concepts: Change unit; Author decisions; Record file

Content-derived fingerprints cannot serve as persistent decision identities, and the record has no immutable catalogue of returned units.

- Failure: Rejecting one edit changes a neighbouring unit's context or diff segmentation, so its fingerprint changes and its existing decision disappears. Repeated passages can also produce identical fingerprints within one file.
- Proposed fix: At return, freeze a catalogue of units against B and R. Give each a unique stable ID, original source ranges, texts and anchors. Keep fingerprints as integrity checks. Relocate that catalogue during author work instead of rebuilding decision identities from the current diff.
- **Adjudication:** Accepted: change catalogue frozen at return with stable ids.

## 2. [blocker] Author decisions: Reject

Reviewer-text lookup is insufficient for safely reversing changes, especially deletions.

- Failure: A pure deletion has empty reviewer-side text and therefore no unique textual target. Repeated wording or nearby author edits can make rejection restore text at the wrong occurrence or overwrite author work.
- Proposed fix: Represent deletions with explicit insertion-point anchors in R. Reverse each unit through a three-way operation using its immutable R-side range and current source mapping. Require a unique, unchanged preimage; otherwise report a conflict and leave the file untouched.
- **Adjudication:** Accepted: projected R-range, exact-preimage check, insertion points for deletions, fail closed.

## 3. [major] Lifecycle: merging a moved main; close

Integration can invalidate earlier decisions, and the merge target can advance after the closure checks.

- Failure: An accepted unit changes during a later merge conflict resolution, but remains recorded as accepted. Alternatively, main advances between local validation and the GitHub merge, introducing text the author never reviewed.
- Proposed fix: Track the integrated author commit, invalidate affected decisions after integration, and block closure on unresolved relocation or merge conflicts. Bind final validation to exact review and target revisions; require reconciliation again if either changes before merging.
- **Adjudication:** Accepted: integrated commit recorded, touched decisions invalidated, merge bound to head commit.

## 4. [major] Lifecycle: return; Record file: round.returned

R needs an explicit content-snapshot definition separate from the commit recording its hash.

- Failure: A commit cannot contain its own SHA. Recording R after committing either leaves an uncommitted record or creates another commit whose relationship to the returned manuscript is unspecified.
- Proposed fix: Create the reviewer-content commit R first, then a metadata commit containing R and the returned status. Define the manuscript snapshot as R, prohibit further reviewer-content changes within that returned round, and make interrupted return operations resumable.
- **Adjudication:** Accepted: R content commit, then metadata commit.

## 5. [major] Record file; Lifecycle: close

Keeping SHA strings does not preserve the commits needed to reconstruct the review.

- Failure: A squash merge followed by review-branch deletion leaves R unreachable. Garbage collection eventually removes the source snapshot needed by thread anchors and the original redline.
- Proposed fix: Specify a merge method that preserves B and R as ancestors, or retain published immutable refs for those snapshots. Check this before closing; the archived record must remain reconstructable after branch deletion.
- **Adjudication:** Accepted: base/returned tags pushed; merge commits only.

## 6. [major] Record file

Pretty printing does not provide a semantic merge strategy for shared review records.

- Failure: The author replies from one clone while the reviewer resolves a thread from another. Appended array entries conflict, locally allocated IDs collide, or a manual JSON resolution silently loses a message or decision.
- Proposed fix: Use globally unique message and thread IDs, preserve messages as immutable entries, and implement ID-aware record merging with explicit conflicts for competing decisions or status transitions. Validate the merged record before serving or closing it.
- **Adjudication:** Accepted in part: phase ownership removes cross-clone concurrent writes; random ids and schema validation. ID-aware record merging not needed under phase ownership.

## 7. [major] Agent interface: lock file; Viewer: saving

Serialization alone does not prevent stale requests or races with ordinary source editors.

- Failure: A viewer submits a rejection from an old render after the CLI changed the source. Separately, an agent writes between the viewer's content-hash check and its replacement, allowing either writer to overwrite the other.
- Proposed fix: Require expected source and record revisions on mutations, and perform read, validation and write inside one shared lock. State that external editors do not participate in that lock; retain recoverable before/after snapshots and surface conflicts instead of promising that arbitrary concurrent writes cannot clobber.
- **Adjudication:** Accepted: expected revisions under one shared lock; external editors detected, not locked.

## 8. [major] Author decisions; Agent interface

Source mutations and their recorded decisions lack a crash-consistent transaction.

- Failure: Reject restores the source, then the process exits before recording the rejection. On restart the edit appears superseded or disappears from the diff, losing the actual decision. Reversing write order produces the opposite inconsistency.
- Proposed fix: Journal each mutation with expected revisions, intended file contents and record update. Use atomic individual-file replacement and recover or explicitly resolve incomplete transactions before accepting further writes.
- **Adjudication:** Accepted in lighter form: an intent file plus atomic replacement, not a general journal.

## 9. [major] Concepts: Reviewer changes and Change unit

Block-level word changes do not cover the full set of real manuscript edits.

- Failure: A reviewer deletes or renames a source file, edits YAML, changes a bibliography entry, or replaces a figure. Those changes reach the PR without corresponding decidable units, allowing close to claim that every change was reviewed.
- Proposed fix: Define the tracked path scope and unit types for file additions, deletions, renames, metadata, resources and binary replacements. Provide whole-file decisions where inline review is unavailable, and block closure on unrepresented changes within scope.
- **Adjudication:** Accepted: text, block and file unit types; nothing in B..R unrepresented.

## 10. [major] Viewer: Rendering and block splitting

Adding fenced divs before parsing can change the document's structure.

- Failure: An existing colon fence closes a wrapper, or a list, reference definition, footnote or nested construct acquires different parsing semantics after wrapping. A probe containing one paragraph does not establish structural preservation.
- Proposed fix: Parse the original document first and add wrapper Div nodes to Pandoc's AST. Obtain source ranges from a parser with position support, such as tree-sitter-markdown, while explicitly testing its supported syntax against Pandoc. Avoid structural wrappers in the input Markdown.
- **Adjudication:** Accepted with a different fix: keep wrapper divs but guard by AST equivalence and merge blocks on mismatch (probe caught a mis-split list). No second parser.

## 11. [major] Viewer: Redline

Splicing bracketed spans into Markdown can corrupt otherwise valid inline syntax.

- Failure: An edit crosses emphasis delimiters, a code span, an attribute or escaped brackets. The inserted redline syntax changes parsing or becomes literal text; atomic treatment of citations and links does not cover these cases.
- Proposed fix: Parse base and current source separately, then annotate compatible AST text nodes. Fall back to separately rendered old and new blocks whenever a change crosses incompatible syntax boundaries.
- **Adjudication:** Accepted: same AST guard for redline splices, block-level fallback.

## 12. [major] Viewer: Redline; citations and cross-references

Deleted material must not participate in the current document's semantic numbering.

- Failure: A struck-through citation still enters citeproc, changing the bibliography or author-year disambiguation. A deleted labelled figure consumes a number or duplicates an identifier, making the displayed references incorrect.
- Proposed fix: Resolve citations and cross-references independently for the current and historical snapshots. Overlay historical deletion fragments afterwards, with isolated IDs, so they cannot affect current numbering or bibliography generation.
- **Adjudication:** Accepted: deleted citations and cross-references rendered from the base snapshot.

## 13. [major] Viewer: Select text to comment

Rendered-text alignment is not an invertible source map.

- Failure: Selecting 'Smith et al. (2020)', an equation, generated figure number or code output finds no matching source wording. Repeated text within a block can map to the wrong occurrence, and selections can span blocks.
- Proposed fix: Carry explicit source ranges through render nodes and distinguish generated content from source-backed text. Map DOM selection endpoints through those ranges. Expand atomic source constructs deliberately, and require a block-level anchor when exact mapping is unavailable.
- **Adjudication:** Accepted: source-backed vs generated tagging, token expansion, block-level fallback.

## 14. [major] Anchoring

The fuzzy relocation policy can silently attach a thread to the wrong passage.

- Failure: The original paragraph is deleted while a similar paragraph remains nearby. 'Best context score' or neighbourhood matching selects the surviving passage, despite the promise never to move an anchor silently. The recorded commit is not given an operational role.
- Proposed fix: Store commit-relative positions and stable block identity alongside TextQuoteSelector. Use revision diffs to project anchors, detect tied candidates, and require explicit confirmation for uncertain fuzzy matches. Otherwise retain an orphan with candidate locations.
- **Adjudication:** Accepted: commit-range projection first, ties orphaned with candidates.

## 15. [major] Viewer: Rendering; Live update

File-only rerendering conflicts with document-wide citation and cross-reference processing.

- Failure: Adding a figure in the first source file leaves later files displaying old numbers. Adding a citation changes bibliography order or disambiguation without updating unaffected files.
- Proposed fix: For v1, rerender the complete manuscript after a debounced change. Introduce incremental rendering only after separating document-wide semantic passes from independently replaceable HTML fragments.
- **Adjudication:** Accepted: debounced full re-render.

## 16. [major] Viewer: Quarto code outputs

Order-and-label cache matching can attach outputs to the wrong code, and the freshness contract is undefined.

- Failure: Inserting an unlabelled chunk shifts cached outputs onto later chunks. Unchanged source can also use changed data or parameters while the viewer gives no indication that source matching cannot establish freshness.
- Proposed fix: Use Quarto's existing cache interpretation where feasible. Otherwise accept only a documented cache schema and verified source-to-output mapping; never guess by ordinal after edits. Distinguish matching cached source from verified output freshness, and show unmatched outputs as unavailable.
- **Adjudication:** Accepted: outputs only through Quarto's own freeze handling; no positional matching.

## 17. [major] Viewer: cross-reference Lua filter; Build plan

Reimplementing Quarto cross-references is a large v1 subsystem with unspecified processing order.

- Failure: Citeproc consumes @fig references as citations before the custom filter resolves them, or the filter mishandles subfigures, chapter numbering and project configuration while presenting authoritative-looking numbers.
- Proposed fix: Prefer Quarto's existing cross-reference processing for Quarto sources, with execution disabled. If v1 retains a custom filter, restrict it to an explicit syntax subset, resolve references before citeproc, and display unsupported constructs as source. Cut broader Quarto emulation from v1.
- **Adjudication:** Accepted: Quarto engine for .qmd; pandoc filter limited to a stated subset, run before citeproc.

## 18. [major] Concepts: Manuscript; Viewer: Rendering

Combining files loses the directory context of relative resources unless paths are explicitly rebased.

- Failure: Two chapter directories both contain figures/results.png. After concatenation, both links resolve against the server or repository root, displaying the wrong image or none. Includes and cached output resources have the same problem.
- Proposed fix: Preserve each node's originating file and resolve resource paths in that context before rendering. Map approved resources to server URLs, and specify how project YAML, includes and per-file front matter contribute to the combined document.
- **Adjudication:** Accepted: pandoc rebase_relative_paths.

## 19. [major] Thread: suggestion; Author decisions; Agent interface

Suggestions have no complete acceptance or rejection lifecycle.

- Failure: An author receives replacement text but no defined command to apply it. If applied after R, it falls outside B-to-R change units; if applied before return, both the suggestion and the resulting tracked edit can appear actionable.
- Proposed fix: Add explicit suggestion states and commands. Acceptance must validate the anchored preimage, apply once, record the application and resolve or update the thread. Link pre-return applications to their returned change units, and define post-return applications as author decisions.
- **Adjudication:** Accepted by cutting: no suggestions after return; draft wording becomes a real edit when kept.

## 20. [major] Viewer: local server and rendered content

Manuscript HTML and rich outputs share an unspecified trust boundary with source-writing endpoints.

- Failure: Raw HTML or a cached output contains JavaScript that runs in the viewer's origin and calls its edit API. A local server exposed beyond loopback or accepting cross-origin mutations introduces another route to manuscript modification.
- Proposed fix: Bind to loopback, authenticate mutation requests with a session capability, validate Origin, and constrain filesystem access. Sanitize manuscript HTML and isolate executable rich outputs in a sandbox without access to the viewer's origin or write API.
- **Adjudication:** Accepted: loopback, session token, Origin check, nh3, sandboxed outputs.

## 21. [major] Lifecycle: start, open and close

The lifecycle hardcodes main and leaves dirty-worktree handling unspecified.

- Failure: A student writes on a thesis branch, but start reviews main instead. Opening a round with uncommitted work either fails partway through or carries unrelated edits onto the review branch; return may then commit them.
- Proposed fix: Persist the chosen author branch, upstream and base revision in the round. Check repository and worktree state before lifecycle mutations. Refuse ambiguous dirty transitions or use a dedicated worktree, and explicitly scope what return stages and commits.
- **Adjudication:** Accepted: author branch recorded; dirty-tree refusal.

## 22. [major] Packaging and stack; Lifecycle; Build plan: onboarding

The advertised optional transport and student handoff are not executable as specified.

- Failure: A student without gh can install the tool but cannot complete return or close. A student with gh may still have no fetched review branch, missing rendering dependencies, or a clone without the ignored freeze outputs needed to read results.
- Proposed fix: Define a complete local-only lifecycle and a separate GitHub transport contract, including fetch and permission checks. Provide onboarding with the first usable release, a project doctor command, and an explicit handoff route for required cached outputs and resources.
- **Adjudication:** Accepted: full lifecycle over plain git; gh optional; doctor; committed _freeze.

## 23. [minor] Draft; Agent interface: selection and asks

Repository-local draft and interaction files are not scoped to a round or source revision.

- Failure: After switching branches or rounds, old drafts block return, or an agent answers a selection whose file contents have changed. Unscoped local files can also expose reviewer drafts in author mode.
- Proposed fix: Namespace local state by round and worktree. Include source revision, file hash and source range in selections and asks; reject stale actions. Enforce draft visibility in server responses as well as the UI.
- **Adjudication:** Accepted: local state per round; stale selections refused; server-side draft visibility.

## 24. [minor] Concepts: Round

Date plus initials is not a unique round identifier.

- Failure: The same reviewer starts a second round on the same day, colliding with the existing branch and record path.
- Proposed fix: Append a short unique suffix or allocate a checked sequence number. Keep the date and initials as the readable portion.
- **Adjudication:** Accepted: numeric suffix on collision.

