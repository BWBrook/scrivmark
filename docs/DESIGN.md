# scrivmark design

Status: revised after independent review, for lock. 3 November 2026.

scrivmark lets a reviewer and an author do the whole tracked-changes-and-comments
cycle of manuscript review on Markdown and Quarto sources, without Word, HTML
exports or any reconstruction of edits from a rendered format. A local viewer
renders the manuscript readably, with redline and margin threads, beside the
chat of a Claude Code or Codex session; the agent works on the same files
through a CLI.

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

Barry on attribution: "having the labels inserted every time creates subtle but
cumulative cognitive bandwidth for the student/receiver … KISS principle."

## Concepts

**Manuscript.** An ordered list of Markdown or Quarto source files, plus a
bibliography, CSL style and resource paths, declared in `scrivmark.toml` at the
repo root. `scrivmark init` writes it, detecting a makedoc profile or
`_quarto.yml`.

**Round.** One reviewer's review of one version of the manuscript:

- id `<date>-<initials>`, with `-2`, `-3` … appended if taken
  (e.g. `2026-11-03-bwb`);
- the **author branch** it reviews (any branch, not assumed `main`) and the
  **base commit** `B` on it;
- branch `review/<round id>`;
- record file `reviews/<round id>.json`;
- status `open` → `returned` → `closed`.

**Phase ownership.** While `open`, only the reviewer side writes to the round;
while `returned`, only the author side. Further back-and-forth is a new round.
This removes concurrent cross-clone edits to the record by construction.

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

**Thread.** A comment anchored to source text, with immutable messages and a
status of `open` or `resolved`. Thread and message ids are random
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
                                                                 │
                                                               close
                                                                 ▼
                                                  merge commit into author
                                                  branch; record kept
```

- `start` (reviewer) or `request --reviewer <user>` (author) cuts the branch
  from the author branch and writes the record and workflow statement.
  `request` also opens a draft PR labelled `scrivmark:requested` when `gh` is
  available; `inbox` lists requested and returned rounds across repos.
- `return` (reviewer): refuses with untriaged drafts or uncommitted changes
  outside the manuscript scope; commits `R`, freezes the catalogue, writes the
  metadata commit, pushes branch and tags, and opens or updates the PR.
- `open <round>` (author): fetches and checks out the branch, starts the viewer
  in author mode.
- `close` (author): refuses while units are undecided, threads open, or
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
  the affected source files and the record. It is checked and applied under
  one lock file shared by the CLI and the server; a mismatch is refused with
  a fresh state for the caller to retry.
- Before a mutation that touches both source and record, scrivmark writes an
  intent file (the target contents of each); it then replaces each file
  atomically and removes the intent. On start-up a leftover intent is completed
  or reported. Git is the backstop beyond that.
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

## Anchoring

An anchor stores its file, the commit and character range it was made on, and
a W3C Web Annotation `TextQuoteSelector` (exact, prefix, suffix), as
Hypothesis does, all against **source** text. Re-anchoring:

1. project the range from its commit to the current text through the git diff;
   if the projected text still equals `exact`, done;
2. otherwise search for `exact` with its context; one clear winner is accepted;
3. otherwise, or on any tie, the thread is **orphaned** and shown with its quote
   and candidate locations for a human to re-attach. It is never moved
   silently.

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
  full reference on hover.
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

### Redline

- For each block that differs between the redline base (`B`, or the working
  tree's base during `open`) and current text, a token-level diff (words, with
  citations, inline maths, links, code spans and cross-references as atomic
  tokens) is spliced into the Markdown as `[old]{.scrv-del}` and
  `[new]{.scrv-ins}` spans.
- **Guard:** stripping the deletion spans must give the current block's AST,
  and stripping the insertion spans the base block's AST. If either fails, the
  block falls back to the old block struck through above the new one.
- Deleted material never takes part in current semantics: citations and
  cross-references inside deletions are replaced by their text from a render
  of the base snapshot, so they cannot change the bibliography or numbering.
- During `returned`, the redline shows the frozen catalogue projected onto the
  current text, with each unit's status.

### Margin and interactions

- Threads beside their anchors; drafts with keep, edit and drop in reviewer
  mode; orphans at the top with their candidates.
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
Manuscript raw HTML is sanitised with `nh3`; HTML-bearing code outputs render
in sandboxed iframes without access to the page or its API.

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
scrivmark doctor                          checks tools, config, resources, freeze cache
```

- **Select and ask.** The human selects text and tells the agent "look at my
  selection", or presses Ask and types a question. Selections and asks carry
  the file hash and source range; an agent acting on a stale one is refused.
- **Pre-pass.** The agent reads the manuscript and writes drafts, with wording
  where it proposes text, for the reviewer to triage.
- **Conversation.** The agent edits the source directly when asked, and
  comments as the reviewer when the reviewer dictates.

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
and produced this revision.
