# scrivmark design

Status: design locked 3 November 2026; this document tracks what is built (6 November 2026, version 0.4).

scrivmark lets a reviewer and an author do the whole tracked-changes-and-comments
cycle of manuscript review on Markdown, Quarto and LaTeX sources, without Word, HTML
exports or any reconstruction of edits from a rendered format. A local viewer
renders the manuscript readably, with redline and margin threads, beside the
chat of a Claude Code or Codex session; the agent works on the same files
through a CLI.

## Implementation status (6 November 2026, version 0.4)

Built: the round lifecycle over plain git, with pull requests through `gh`;
the frozen change catalogue, decisions and undo; anchoring, with orphaned
comments re-attached from the viewer; the full agent CLI, including batches,
dry runs, draft revision and attribution of agent-drafted work; the viewer
(pandoc rendering, redline, margin, selection, flip-to-source editing, live
updates, keyboard decisions, keep-all, figure zoom, editing your own
comments, a reading view in a second tab, a filterable items drawer with the
round's readiness, recovery of unsent work); transition previews
(`return --dry-run`, `close --dry-run`) and summary export; LaTeX sources; detection of makedoc profiles in `init`; the
tutorial and the agent guide; tagged releases with an update check in
`doctor`.

Not yet built, though described below: rendering `.qmd` through Quarto
itself, with freeze-cache code outputs (`.qmd` renders with pandoc for now);
`scrivmark inbox`; `_quarto.yml` chapter order in `init`; the viewer starting
itself on `start` and `open`. The HTML clean-up is a stdlib
allowlist plus a Content Security Policy, not `nh3`. Supported platforms are
Linux, macOS and WSL; the file lock uses `fcntl`, so native Windows is not
supported.

## Principles

1. **The Markdown is the only text.** Every edit is a real edit to the source
   files. Nothing is ever parsed back out of a rendered document.
2. **Git is the track-changes engine.** A suggested edit is a change on a review
   branch; the redline is a diff against the author's version.
3. **Comments live beside the text, not in it.** Threads sit in a review record
   file, anchored to source text, so the manuscript never carries markup.
4. **Humans read rendered prose; agents read files.** The viewer is for people;
   the CLI and the record are for agents. Both act on the same state, so Claude
   and Codex are interchangeable.
5. **People sign their work; the round declares its method.** Every comment and
   edit is attributed to the human reviewer or author. Agent involvement is
   stated once, in the round's workflow statement.
6. **Fail closed on text.** When scrivmark cannot be certain where an operation
   applies, it reports a conflict and leaves the files alone.

## Decisions taken with Barry (3 November 2026)

| Question | Decision |
|---|---|
| Reading surface | A rendered page beside the chat, in the desktop app's browser pane |
| Tracked edits | Real edits on a review branch; redline is the diff against base |
| Comments | A sidecar record, quote-anchored |
| Agent role | Select-and-ask, agent pre-pass, conversation, and direct human editing |
| Direct editing | Click a block and it flips to its Markdown source |
| Author side | The same viewer on the author's machine; the PR is only the final merge |
| Rendering | Citations, figures and tables, equations, Quarto code outputs |
| Reviewers | One reviewer per round, asynchronous |
| Attribution | Everything as the human; agent use declared once per round |
| Record | Kept in the repository after merge |
| Home | New public repo, `BWBrook/scrivmark`, independent of makedoc |
| Who opens rounds | Either side |
| Asks to the agent | Answered asks are archived in the round record at return and close, apart from threads; the Ask box says so |

Barry on attribution: "having the labels inserted every time creates subtle but
cumulative cognitive bandwidth for the student/receiver … KISS principle."

## Concepts

**Manuscript.** An ordered list of Markdown or Quarto source files, plus a
bibliography, CSL style and resource paths, declared in `scrivmark.toml` at the
repo root. `scrivmark init` writes it, detecting a makedoc profile or LaTeX
documents, or reusing the config the newest `review/*` branch carries. It does
not commit: `start` commits it (and the `.gitignore` line) with the round
record on the round branch, so a reviewer never moves the author's branch in a
repository they do not own, and the config reaches the author's branch when
the round is merged. It counts as one of scrivmark's own files (`is_managed`),
so it never shows as a change. A round not yet returned can be abandoned: its
last state is committed and tagged `scrivmark/<id>/abandoned`, the branch is
deleted, and the id is not reused.

**Round.** One reviewer's review of one version of the manuscript:

- id `<date>-<initials>`, with `-2`, `-3` … appended if taken
  (e.g. `2026-11-03-bwb`);
- the **author branch** it reviews (any branch, not assumed `main`) and the
  **base commit** `B` on it;
- branch `review/<round id>`;
- record file `reviews/<round id>.json`;
- status `open` → `returned` (→ `responded` → `returned` …) → `closed`.

**Phase ownership.** Only the side whose turn it is writes to the round: the
reviewer while it is `open` or `responded`, the authors while it is
`returned`. This removes concurrent cross-clone edits to the record by
construction. Every write, from the CLI or the viewer, goes through one check
(`review.require_turn`): someone recognisably the other side (the reviewer
while the authors hold the round, or the reverse) is refused, and
`open` by them only checks the round out, committing nothing, so peeking at a
round never forks its history.

**Returned snapshot `R`.** The commit holding the reviewer's final manuscript
content. `return` creates `R`, then a separate metadata commit that records
`R`'s SHA and the status. Tags `scrivmark/<round id>/base` and
`scrivmark/<round id>/returned` are pushed with the branch, so `B` and `R` stay
reachable after the branch is merged and deleted.

**Change catalogue.** At return, scrivmark freezes every difference between `B`
and `R` into a list of **change units** in the record, each with a stable id
(`c1`, `c2`, …), its type, its base and reviewer text, and its exact range in
`R`. Decisions are keyed by id, never by recomputed content. Unit types:

- `text`: a run of word-level changes inside one aligned prose block;
- `block`: a whole block inserted, deleted or restructured (lists, tables, YAML
  front matter, fenced divs, code chunks);
- `file`: a file added, deleted, renamed or binary-replaced (figures, the
  bibliography, anything outside prose).

Nothing in `B..R` goes unrepresented, so `close` can truthfully say every
change was decided.

**Thread.** A comment anchored to source text, with messages and a status of
`open` or `resolved`. A message can be edited by whoever wrote it until the
other side has seen it (the reviewer's until return, the author's until
close); after that it is fixed. Thread and message ids are random
(`t-3f9a1c2e`), so they never collide.

**Draft.** A thread the agent proposes while the round is `open`, optionally
with suggested wording. Drafts live in local state (below) and only the
reviewer sees them. **Keep** moves the comment into the record as the
reviewer's; if it carried wording, keeping applies it as a real edit, which
then becomes part of `R` like any other. **Drop** deletes it. `return`
refuses while drafts remain. Authors therefore only ever see edits and
comments; there are no pending "suggestions" after return.

## Lifecycle

```
either side                  reviewer (+ agent)                author (+ agent)
───────────                  ──────────────────                ────────────────
start / request  ───►  open: edit, comment, triage   ───►  returned: accept,
                       drafts; live redline          return  reject, reply,
                                                             resolve, edit
                       responded: sign off:          ◄───        │
                       reply, reopen, comment,      respond      │
                       contest a decision           ───►         │
                         │                          return       │
                       close                                   close
                         ▼                                       ▼
                       merge commit into author branch; record kept
```

The phase decides who acts, not the git identity: the reviewer while the
round is `open` or `responded`, the authors while it is `returned`. Agents
often run under their human's account, and the authors may themselves be
agents, so identity cannot say whose turn it is. Each act also records the
agent that performed it (`agent`, from `--by` or `SCRIVMARK_AGENT`) beside the
side's initials. Identity is used only to stop acting for the other side: a
person whose name is the side not holding the round gets a read-only viewer
and refused commands. `open --as NAME` records who you are in that clone
(`.scrivmark/_/as.json`), so one person can play both parts, as in the
tutorial, or agents can act as the authors; `start` clears it. Every viewer
write carries the page's revision, taken before anything on the page was
read, so a write made before a handover, or over a change made meanwhile from
the CLI, cannot land; a batch command's `--expect` covers every item in it.
Each write also rechecks its files just before replacing them, so an editor's
save made while scrivmark was working out the change is not overwritten (only a
save in the instant between that check and the replacement can still race it).

Every handover re-anchors to the commit it makes any thread anchored to a
local snapshot (one written against uncommitted text), which the other clone
does not have; threads anchored to commits keep the text they quote. While the reviewer signs off, the text is
the authors': `return` and `close` refuse any change to the files since the
commit the authors handed over (recorded in the handover), committed or not,
since it would reach the authors, or the merge, untracked. What a contest in
this turn puts back is recognised by its hash, bound to that commit
(`.scrivmark/<round>/contested.json`).

- `start` (reviewer) or `request --reviewer <user>` (author) cuts the branch
  from the author branch and writes the record and workflow statement.
  `request` also opens a draft PR labelled `scrivmark:requested` when `gh` is
  available; `inbox` lists requested and returned rounds across repos.
- `return` (reviewer): refuses with untriaged drafts or uncommitted changes
  outside the manuscript scope; commits `R`, freezes the catalogue, writes the
  metadata commit, pushes branch and tags, and opens or updates the PR.
- `open <round>` (author): fetches and checks out the branch, starts the viewer
  in author mode.
- `respond` (authors, optional): refuses while any change is undecided;
  commits the authors' work, brings in the author branch, records a handover,
  pushes, and notes it on the PR. The reviewer then signs off: replies,
  reopens, comments, and may `contest` a decision with a reason, which makes
  it undecided again (a rejected edit is put back) and keeps the contest in
  the change's history. The reviewer cannot edit the text in this phase. They
  then `close`, or `return` the round to the authors, as often as needed.
  Each handover is recorded (`round.handovers`), and `news` lists what the
  other side did in its last turn. Messages can be edited by their writer
  only in the turn they were written. Times are recorded to the millisecond,
  so acts and handovers in the same second keep their order.
- `close` (author, or reviewer after signing off): refuses while units are undecided, threads open, or
  relocation conflicts unresolved (`--force` overrides and is recorded). It
  checks that the author branch is still at the integrated commit, writes the
  final record, and merges with a **merge commit** (never squash), so `B` and
  `R` remain ancestors. Through `gh` it uses `--match-head-commit`, so nothing
  that changed after the check is merged.
- Every transition refuses on a dirty worktree, apart from files scrivmark
  itself manages.

Without `gh`, the whole lifecycle still works over plain `git push` and
`git fetch`; `gh` adds the PR, its notifications and the remote merge.

**Author branch moved during the round.** `open` and `close` merge the author
branch into the round branch with ordinary `git merge`, and record the
**integrated commit**. Units whose `R` ranges the merge touched lose any
decision and return to undecided; units that can no longer be located are
listed as conflicts for a person or agent to settle in source.

## Author decisions

Each unit's `R` range is projected onto the current text through the git diff
from `R` to the working tree.

- **Accept** records the decision; the text is untouched.
- **Reject** restores the base text, only if the projected range is unique and
  its current text equals the reviewer text exactly. A pure deletion is
  stored as an insertion point with context, and restoring requires that
  point to project unambiguously. Otherwise scrivmark reports a conflict and
  changes nothing.
- **Edit** in source is always allowed. A unit whose projected range the
  author has edited becomes `superseded` and needs no decision.

## Writes and consistency

- Every mutation (CLI or viewer) carries the **expected revision**: a hash of
  the manuscript sources, the record, the drafts and the asks, everything the
  human or agent saw before acting. It is checked and applied under one lock
  file shared by the CLI and the server; a mismatch is refused with a fresh
  state for the caller to retry. Keep-all sends the revision the page showed,
  so a draft changed or added after the confirmation is not applied unseen.
- Under the lock, the record is reloaded and the round's phase checked before
  anything is written: a request that waited while the round was returned or
  closed loses the permission it had.
- Before a mutation that touches both source and record, scrivmark writes an
  intent file (each file's hash before and after, and its new contents); it
  then replaces each file atomically, keeping its mode, and removes the
  intent. A leftover intent is completed only for files still as they were
  before; if any changed since, nothing is overwritten and the intent is set
  aside with a message. Git is the backstop beyond that.
- Manuscript sources, the bibliography and the CSL style must be tracked files
  inside the repository (checked on every load, and again before an edit), so
  every edit lands on the review branch. Metadata commits name their paths
  (`git commit --only`), so other staged work never joins them. Return is
  sealed in a step that can be repeated: the record committed and both
  snapshot tags pointing where it says.
- External editors do not take the lock. The watcher notices their writes,
  and stale requests fail the revision check rather than clobbering text.
- The record is validated against a JSON Schema on every load and write.

## Record file

`reviews/<round id>.json`, pretty-printed with stable key order:

```json
{
  "scrivmark": 1,
  "round": {
    "id": "2026-11-03-bwb",
    "reviewer": {"name": "Barry W. Brook", "initials": "BWB"},
    "author": {"name": "…", "initials": "…"},
    "author_branch": "main",
    "base": "<sha B>",
    "returned": "<sha R>",
    "integrated": "<sha of author branch last merged in>",
    "status": "returned",
    "statement": "Reviewed by Barry W. Brook working with Claude (Opus 5.5) and Codex."
  },
  "changes": [
    {"id": "c1", "type": "text", "file": "text/intro.md",
     "base": "high", "reviewer": "moderate",
     "r_range": [412, 420], "context": ["probability was ", " across sites"],
     "decision": "accepted", "by": "AB", "at": "…"}
  ],
  "threads": [
    {"id": "t-3f9a1c2e", "status": "open",
     "anchor": {"file": "text/intro.md", "commit": "<sha>", "range": [430, 447],
                "exact": "at all elevations",
                "prefix": "high across sites ", "suffix": ". Occupancy models"},
     "messages": [
       {"id": "m-91b0d4aa", "by": "BWB", "at": "2026-11-03T10:12:00+11:00",
        "body": "Needs a confidence interval."}
     ]}
  ]
}
```

The record carries the base and reviewer text of every change, so it stays
readable on its own after merge, and the tags keep the full snapshots.

An optional `asks` array keeps the human–AI exchange behind the review: each
answered ask, with its anchor, question, asker (`by`), answer, answerer
(`answered_by`, the model when the agent gives it), any proposed `wording`
and when it was `applied`. It is filled from `.scrivmark/<round>/asks.json`
at return and again at close, so asks from both phases are kept, dismissed or
not. Barry, 3 November 2026, chose "Always, with a notice" over per-ask or
configurable archiving.

An optional `drafted_edits` array names the agent behind each edit that came
from a kept draft (`by`, `file`, the text it replaced as `was`, `wording`,
`at`). Edits made directly in the files carry no such attribution; when two
agents review in one round, the second proposes its line edits as drafts so
the record can tell them apart.

## Anchoring

An anchor stores its file, the commit and character range it was made on, and
a W3C Web Annotation `TextQuoteSelector` (exact, prefix, suffix), as
Hypothesis does, all against **source** text. Re-anchoring:

1. project the range from its commit to the current text through the git diff;
   if the projected text still equals `exact`, done;
2. if the passage was edited or rewritten but solid unchanged text (12 or more
   characters) survives within 80 characters on both sides, the thread follows
   the new text between them (`edited`);
3. otherwise search for `exact` with its full context; one clear winner is
   accepted (`moved`), but only if the passage was unique with that context
   when it was anchored;
4. if the passage was deleted outright with its surroundings intact, the
   thread stays at the point where it was (`removed`), and the record keeps
   the deleted text, so the card reads "“…” (removed)";
5. otherwise, or on any tie, the thread is **orphaned** and shown with its
   quote. That includes a passage whose lines had an identical twin when it
   was anchored, one of which has since gone (`why: ambiguous`): which copy
   survived is the diff's guess, not evidence, and wording applied to the
   wrong copy would change the wrong claim (external audit, 5 November
   2026, F01). It is never moved silently: the human selects the passage it belongs
   to and presses **Attach to selection** (`scrivmark reattach` for agents),
   and the anchor keeps what it was first about as `was`.

Proposed wording (drafts, ask answers) is applied only to an `exact` or
`moved` passage, never to an edited, removed or orphaned one.

## Viewer

`scrivmark serve` (also run by `start` and `open`) prints a URL for the
desktop app's browser pane.

### Rendering

- **Block mapping.** A splitter divides each source file into top-level blocks
  with line ranges. Each block is wrapped in a fenced div carrying its file and
  lines, using a fence longer than any colon fence in the document. **Guard:**
  the wrapped document is parsed by pandoc to its AST, the wrappers are
  stripped, and the result must equal the AST of the unwrapped source. Where it
  does not, the neighbouring blocks around the first difference are merged and
  the check repeats. Probe, 3 November 2026: footnotes and reference links
  survive wrapping; a crude splitter that broke a loose list was caught by the
  guard at once.
- **Engines.** `.md` manuscripts render with pandoc. `.qmd` manuscripts render
  with Quarto itself, with execution disabled, so Quarto's own cross-references,
  callouts and freeze-cache outputs apply. Whether the wrapper divs survive
  Quarto's pipeline is the first spike of layer 5; until it passes, `.qmd`
  renders through pandoc and code outputs show as unavailable.
- **Citations** through citeproc with the project's bibliography and CSL; the
  full reference on hover. `citations.lua` hands citeproc only the citations,
  in document order, and caches the result keyed on them and on the contents
  of the bibliography and style files, so editing prose does not re-run it.
  Parsing a large style dominated rendering: Chicago 18th edition (168 KB)
  took 2.4 s of every render of a 56 KB manuscript (measured 5 November 2026).
  Note styles, which move citations into footnotes, go to citeproc whole.
- **Cross-references** with the pandoc engine: a Lua filter numbers a stated
  subset (`{#fig-…}`, `{#tbl-…}`, `{#eq-…}`, `{#sec-…}` and `@` references to
  them), running before citeproc so those references are not read as
  citations. Anything outside the subset shows as source.
- **Resources** keep each file's directory context via pandoc's
  `rebase_relative_paths`; the server serves only files inside the repo.
- **Code outputs** are never produced by running code. With the Quarto engine
  they come from Quarto's freeze cache through Quarto. They are never matched
  to chunks by position after edits; unmatched outputs show as unavailable.
- **Live update.** Any change to a manuscript file or the record triggers a
  debounced full re-render, because citation and numbering are document-wide.
  Rendering runs in a worker thread, one at a time, so the viewer stays
  responsive and shows that it is working.

### Redline

- For each block that differs between the redline base (`B`, or the working
  tree's base during `open`) and current text, a token-level diff (words, with
  citations, inline maths, links, code spans and cross-references as atomic
  tokens) is spliced into the Markdown as `[old]{.scrv-del}` and
  `[new]{.scrv-ins}` spans.
- **LaTeX.** A `.tex` block travels inside the Markdown as a fenced code
  block of class `scrv-tex`, preceded per file by a `scrv-texdefs` block with
  the preamble's definitions. `assets/latex.lua` runs first and reads each
  with pandoc's LaTeX reader in place. Redline marks are
  `\textcolor{scrvdel-<nonce>-<change>}{...}`, which the reader keeps as a
  styled span and the filter turns into the same `scrv-del`/`scrv-ins` spans,
  so the guard, sanitising and numbering are shared. The splitter
  (`latex.scan`) cuts at blank lines, headings and block environments; the
  preamble and everything from `\end{document}` are hidden blocks; comment
  lines and display maths stay with their paragraph, as in TeX. Labels become
  `fig-`/`tbl-`/`eq-`/`sec-` identifiers and `\ref`-family links become
  cross-reference citations. TikZ pictures are written to `.scrivmark/cache`
  and compiled on request; PDF figures are converted to PNG.
- **Guard:** stripping the deletion spans must give the current block's AST,
  and stripping the insertion spans the base block's AST. If either fails, the
  block falls back to the old block struck through above the new one.
- Deleted material never takes part in current semantics: citations and
  cross-references inside deletions are replaced by their text from a render
  of the base snapshot, so they cannot change the bibliography or numbering.
- During `returned`, the redline shows the frozen catalogue projected onto the
  current text, with each unit's status.

### Reading view and items

The reading view is a second tab, not a pane: on a laptop the review tab
already holds the manuscript and the margin. It shows the current text with
nothing marked (`/api/clean`, cached on the sources alone) and follows the
review tab through a `BroadcastChannel`: the top block in view, matched by
file and line, and a click on a passage there sends the review tab to it.
The items drawer floats over the margin and is built from the same state as
the margin, ordered by position in the page and grouped by the nearest
heading. Its readiness line comes from `rounds.preflight`, the same function
behind `--dry-run`. Unsent work is kept in `localStorage`, per round, and an
unsaved source edit is restored only against the file revision it was made on.

### Margin and interactions

- Threads beside their anchors; drafts with keep, edit and drop in reviewer
  mode; orphans at the top with their candidates.
- Thread and draft cards fold to their first sentence; a fold button (or
  activating the card) opens one. The active card is laid out level with its
  anchor and the cards above it give way upwards, as far as the top of the
  margin allows; the cards below follow without overlapping.
- **Select to comment.** Rendered text is tagged as source-backed or generated
  (citation text, numbers, outputs). Selections in source-backed text map to
  source by alignment within the block; selections touching generated text
  expand to its whole source token; anything ambiguous, or spanning blocks,
  falls back to a block-level anchor.
- **Flip to source.** Clicking a block opens its source lines in an inline
  editor; save is a normal mutation with the expected revision.
- Accept, reject, reply, resolve; the selection and Ask (below).

### Security

The server binds to loopback only, requires a per-session token on every
request, checks `Origin` on mutations, and serves only repository files.
Manuscript raw HTML is sanitised (a stdlib allowlist plus a Content Security
Policy); HTML-bearing code outputs render in sandboxed iframes without access
to the page or its API.

LaTeX is read by pandoc's LaTeX reader inside a Lua filter, which must not
reach other files: `\input`, `\lstinputlisting` and the like, even through a
macro. Pandoc 3.7 added the sandbox that guarantees this, so LaTeX sources
need pandoc 3.7 or later and are refused on older versions. The internal
blocks that carry LaTeX through the Markdown pipeline carry the render nonce,
and the filter ignores any without it, so a Markdown manuscript cannot reach
the LaTeX reader on any version (external audit, 5 November 2026, F04).
Every pandoc run has a 120-second timeout. TikZ pictures compile in a
temporary directory with `openin_any=p`.

## Agent interface

One CLI, JSON with `--json`, used identically by Claude and Codex:

```
scrivmark status                          round, counts, viewer URL
scrivmark changes [--undecided]           change units with ids and text
scrivmark threads [--open] [--drafts]     threads with anchors and messages
scrivmark comment FILE --quote TEXT [--prefix …] [--suffix …]
                  --body TEXT [--draft [--wording TEXT]]
scrivmark reply THREAD --body TEXT
scrivmark resolve THREAD
scrivmark accept|reject CHANGE
scrivmark keep|drop DRAFT
scrivmark selection                       the human's current viewer selection
scrivmark asks [--done ID]                questions queued from the viewer
scrivmark answer ASK --body TEXT [--wording TEXT] [--by MODEL]
scrivmark doctor                          checks tools, config, resources, freeze cache
```

- **Select and ask.** The human selects text and tells the agent "look at my
  selection", or presses Ask and types a question. Selections and asks carry
  the file hash and source range; an agent acting on a stale one is refused.
- **Pre-pass.** The agent reads the manuscript and writes drafts, with wording
  where it proposes text, for the reviewer to triage.
- **Conversation.** The agent edits the source directly when asked, and
  comments as the reviewer when the reviewer dictates.

The rendered reference list maps each entry back to its entry in the
bibliography files (BibTeX, CSL YAML or CSL JSON), so it can be selected,
commented on, asked about and edited like a manuscript block. Edits there are
file-level changes in the catalogue.

Local state (drafts, selection, asks, intents) lives in
`.scrivmark/<round id>/`, git-ignored. The server enforces draft visibility,
not just the page. The repo ships an agent guide, `skills/scrivmark/SKILL.md`,
for both Claude and Codex.

## Packaging and stack

- Python ≥ 3.12 with `uv`; installed by both sides with
  `uv tool install git+https://github.com/BWBrook/scrivmark`.
- Starlette and uvicorn; `watchfiles`; `nh3`; `jsonschema`.
- Front end: plain HTML, CSS and JavaScript in the package, with no Node build
  step and no CDN at run time. MathML for equations.
- Host tools: `git` and `pandoc` ≥ 3.1 required; Quarto for `.qmd`
  manuscripts; `gh` optional.
- Identity from `~/.config/scrivmark/config.toml` (`name`, `initials`),
  falling back to `git config user.name`.
- Quarto projects must commit `_freeze/` (Quarto's own recommendation for
  shared projects) for outputs to show on the other side; `doctor` checks.

## Build plan (layers, each working end to end)

1. **Core, local.** Config, `init`, `doctor`; round model and record schema;
   `start`, `return`, `open`, `close` over plain git with tags and merge
   commits; change catalogue; anchoring; projection; reject with fail-closed
   checks; the full comment and decision CLI; locking and intents. Usable by
   agents and from the terminal.
2. **Viewer, reading.** Splitter with AST guard, pandoc render, citations,
   MathML, figures and tables, cross-reference subset filter, redline with its
   guard, margin threads, live update, security.
3. **Viewer, acting.** Select to comment, flip to source, accept and reject,
   reply and resolve, draft triage, selection and asks.
4. **GitHub transport.** `request`, `inbox`, PR on `return`, guarded merge on
   `close`, merging a moved author branch.
5. **Quarto engine.** Wrapper-survival spike, Quarto rendering, freeze outputs.
6. **Agent guide and student onboarding**, piloted on one student manuscript.

## Tests

- Fixtures: pandoc Markdown with citations, figures, tables, maths,
  footnotes and cross-references; a multi-file project with figures in
  per-chapter directories; a Quarto project with a freeze cache.
- Splitter guard and redline guard over the fixtures, including loose lists,
  nested divs and edits across emphasis.
- Round lifecycle in temporary repos: every transition, dirty-tree refusal,
  author branch moving mid-round, invalidated decisions, tag survival after
  branch deletion.
- Reject: repeated wording, pure deletions, author edits inside and next to a
  unit, all failing closed where they should.
- Anchoring: insertions before, edits inside, moves, deletions, ties.
- Concurrency: CLI and viewer mutations racing; crash between intent and write.
- Viewer smoke test with Playwright: render, select, comment, flip, save,
  accept, reject.

## Out of scope

- Word, DOCX import, or any reverse mapping from rendered formats.
- Several reviewers in one round; live co-presence.
- Hosted services or accounts.
- Running code.

## Open risks

- **Quarto's pipeline may not preserve wrapper divs**, in which case `.qmd`
  stays on the pandoc engine with a subset of Quarto syntax.
- **Inline selection mapping** in dense paragraphs; the block-level fallback
  keeps it safe but coarser.
- **Relocation after heavy author edits** produces conflicts that need a
  person or agent; the conflict report must make them quick to settle.

## Review history

An adversarial review by Codex (`gpt-6.1-sol`, high effort) on 3 November 2026
returned 24 findings on the first draft; see
`docs/audits/2026-11-03-design-review.md`. Claude (Opus 5.5) adjudicated them
and produced this revision. A second external audit (ChatGPT, GPT-6 Pro, 5
November 2026) of the built system and its dispositions are in
`docs/audits/2026-11-06-external-audit.md`.
