---
name: scrivmark
description: Use when a repository has a scrivmark.toml or a review/ branch, or when asked to review a Markdown, Quarto or LaTeX manuscript, respond to a review, do a pre-pass, or "look at my selection" in the scrivmark viewer. Teaches the scrivmark CLI for tracked changes and comment threads on manuscript sources.
---

# Working with scrivmark

scrivmark runs manuscript review on Markdown, Quarto and LaTeX sources in git:

- a reviewer's edits are real edits on a `review/<round>` branch, and the
  redline is their diff against the author's version;
- comments are threads in `reviews/<round>.json`, anchored to quoted source
  text;
- a human reads the result in a local viewer (`scrivmark serve`), often in the
  browser pane beside your chat. You work through the CLI on the same state.

Always use `--json` when you need to read output; with `--json`, a refusal
also comes back as JSON (`{"error": ..., "kind": ...}`) with exit status 1.
Never edit `reviews/*.json` or `.scrivmark/` by hand.

This guide is loaded when your session starts. If scrivmark was upgraded since,
`scrivmark skill` prints the current one; read it before going on.
`scrivmark --version` shows the installed release. If `scrivmark doctor` says
a newer one is out, tell the human, with the update command it gives and the
changelog link; updating is their call, and it means restarting the viewer.

## What changed for agents

Read this when `scrivmark doctor` says your guide was out of date, or a
command you remember is refused. Each entry is a change that can break a habit,
a script or a note in a project's own instructions, with what to do instead.
Rounds already returned are never affected: their record is frozen.

- **0.8.0.** A rewritten clause is now one change, not one per fragment:
  `Wombats clearly prefer the sunniest aspect` → `Occupancy was highest on one
  aspect and lowest on the other` used to be two changes around the surviving
  "the". Expect fewer, longer changes in `scrivmark changes --json` for rounds
  returned from 0.8 on; decide each as a whole, and edit the passage afterwards
  if you want only part of it.
- **0.7.0.** `scrivmark init` no longer commits `scrivmark.toml`; `start`
  commits it on the round branch. Do not commit it to the author's branch
  yourself.
- **0.6.1.** Commands refuse when it is the other side's turn. To act for the
  authors under the reviewer's account, `scrivmark open <round> --as "Name"`.

## The viewer

If the repository has a `.claude/launch.json` entry for `scrivmark serve` and
you can show the browser pane, let the pane start the viewer from it. If you
cannot (no launcher, or the human is away from the window, say on a phone),
start it yourself in the background with `scrivmark serve`; a later pane
launch finds it and reuses it. It listens on
port 8765 (or `--port`, then `$PORT` from a launcher, then `[viewer] port` in
`scrivmark.toml`), keeps the same address across restarts, and
`http://localhost:8765` opens it directly on this machine. Only one viewer
runs per repository: a second `scrivmark serve` prints the running one's
address and exits. If 8765 is taken by another program, the default moves to
the next free port and says so. For the desktop apps' browser pane, a
`.claude/launch.json` entry running `scrivmark serve` with `"port": 8765` and
`"autoPort": false` is all it needs.
`scrivmark url` prints the full address of the running viewer, and
`scrivmark stop` stops it. After a CLI
change, the page updates within about a second; wait that long before reading
it back.

## Setting up a round

- `scrivmark init FILE...` writes `scrivmark.toml` (files in reading order)
  but does not commit it: `scrivmark start` commits it on the round branch,
  so the author's branch gets it only when the round is merged. Never commit
  it to the author's branch yourself in a repository you do not own. With no
  files named, `init` reuses the config an earlier round's branch carries
  (here or on origin), so both rounds merge cleanly; it warns when the files
  you name differ from that one. When there is no `.bib`, it counts the
  references written out under a References heading and how many have DOIs.
- `start` prints the statement shown to the author ("Reviewed by ... working
  with ..."), taken from `scrivmark whoami` unless `--statement` is given.
  Check that it names the agents and models actually at work. If it does not,
  `scrivmark return --statement "..."` replaces it at the return, and
  `return --dry-run` notes any agent named in the round (by `--by`) that the
  statement leaves out. It cannot change after the return.
- To restart or drop a round that has not been returned,
  `scrivmark abandon` goes back to the author's branch, keeping the round's
  last state under the tag `scrivmark/<round>/abandoned`. It refuses while
  the round holds kept comments or edits; `--force` sets them aside anyway,
  so ask the human first. Never clean up a round by hand with git.

## Where things stand

```bash
scrivmark status --json      # round, phase, counts, revision; author is null until the author opens the returned round
scrivmark threads --json     # comments, with anchors and where they sit now
scrivmark changes --json     # after return: each change, its id and state
scrivmark drafts --json      # reviewer phase: drafts awaiting triage
scrivmark news --json        # what the other side did in its last turn
```

The phase decides who acts, whatever the git identity (agents often run as
their human's account). While a round is `open`, it belongs to the reviewer:
edit, comment, draft. Once it is `returned`, it belongs to the authors:
accept, reject, reply, resolve, edit. If the authors run `scrivmark respond`,
it is `responded` and back with the reviewer to sign off: reply, resolve or
reopen threads, comment, `contest` a decision, then `close` or send it back
with `return`. It can go back and forth like this as often as needed; a
human author can also simply `close` from `returned`. Whoever is recognisably
the other side (by name) is refused: a round the authors hold is read-only for
its reviewer, and the reverse. If you act for the authors under the
reviewer's account, `scrivmark open <round> --as "Name"` first.

Name yourself on everything you do: set `SCRIVMARK_AGENT` (say
`SCRIVMARK_AGENT="Codex (gpt-6.1-sol)"`) or pass `--by`. It is recorded beside
the side's initials, so the record says which agent did what.

## Helping a reviewer

- **Pre-pass.** Read the manuscript files listed in `scrivmark.toml`, then
  leave drafts, not comments:

  ```bash
  scrivmark comment paper.md --quote "exact source text" \
    --body "One clear point, in a collegial reviewer's voice." --draft \
    [--wording "replacement text, if you propose wording"]
  ```

  Quote the source as written; whitespace and line breaks need not match. If
  the quote is ambiguous, add `--prefix` or `--suffix`. Make one point per
  draft and lead with it: the viewer shows only a card's first sentence until
  it is opened, so that sentence must carry the point on its own. Add a
  sentence or two of support only where it is needed. The reviewer keeps drafts as
  their own (wording becomes a real edit) or drops them, so write what you
  would be happy for them to sign. Do not put your name or labels in the
  text; instead pass `--by "Agent (model)"`, e.g. `--by "Codex (gpt-6.1-terra)"`
  (or set `SCRIVMARK_AGENT`), so the card says who drafted it and the record
  keeps it when the draft is kept. This matters when two agents give
  separate opinions in one round.
  For more than two or three drafts, write them to a JSON file and add them
  in one go, which avoids shell quoting and adds all or none:

  ```bash
  scrivmark comment --from drafts.json --draft
  # drafts.json: [{"file": "paper.md", "quote": "...", "body": "...", "wording": "..."}, ...]
  ```

  (`prefix`, `suffix` and `lines` work as on the command line; JSON Lines and
  `--from -` for stdin also work.) Check a batch first with
  `scrivmark comment --from drafts.json --draft --dry-run`: it reports every
  item that would fail, shows each wording in its sentence (`before` and
  `after`, changed part in «»), and lists overlaps with other items, drafts,
  open threads and source comments. Overlaps are advisory; for an item with
  wording, each overlap says what keeping it would do to that thread or
  draft: whether it stays anchored or would lose its passage, and, where
  both propose wording, that keep all applies only the first. Read the `after`
  lines for edits that fit alone but jar in context. When adding, `--json
  --summary` returns only the count, ids, authors and new revision.
- **Revising a draft.** `scrivmark revise DRAFT --wording "..."` (or `--body`,
  `--clear-wording`) amends it in place with the same author; the earlier
  version is kept as its history and goes into the record if the draft is
  kept. Pass `--by` if you are not the draft's author, and `--expect` to guard
  against concurrent changes.
- **Editing a comment.** `scrivmark revise m-… --body "..."` changes a message
  in a thread (the `m-` id from `scrivmark threads --json`). Only the side that
  wrote it can, and only until the other side has seen it: the reviewer's
  messages until the round is returned, the author's until it is closed. Edit
  the human's own comments only when they ask you to. In the viewer, the same
  edit is the Edit button on the message.
- **Line edits.** Propose line edits as drafts with `wording` and an empty
  `body`, not as edits to the files, whether you are the only agent or
  another has already edited the files in this round: each shows as "Line edit, no
  comment", and keeping it applies the edit as a tracked change, recorded as
  drafted by you. Quote the smallest passage that contains the change (a
  phrase or sentence), so each edit can be kept or dropped on its own. Add a
  body only when the edit needs a reason.
- **Comments left in the source.** If the Markdown carries `<!-- ... -->`
  comments from an earlier review, they show as grey notes but are not
  threads. `scrivmark import-comments --dry-run` lists them; at the start of a
  round, offer to run `scrivmark import-comments`, which makes each an open
  thread anchored to it so it can be replied to and resolved. A comment that
  opens "Name, YYYY-MM-DD" is signed by that name, others by the reviewer;
  the dry run shows who signs each. The comments stay in the source for the
  author to remove.
- **Keeping in bulk.** The viewer has a "Keep all drafts" button at the end of
  the document; `scrivmark keep --all` does the same. Drafts whose passage has
  changed are left for the reviewer. Use it only when the reviewer asks, and
  pass `--expect` with the revision the reviewer saw (`rev` from
  `scrivmark status --json`), so drafts changed since are not kept unseen.
- **"Look at my selection."** Run `scrivmark selection --json`. It gives the
  file, lines and exact source text the human has selected. If `stale` is
  true, say so and ask them to reselect.
- **References.** Entries in the rendered reference list map to their entries
  in the bibliography files, so comments, asks and selections there point at
  `refs.bib` (or the CSL YAML or JSON). If the bibliography is exported from a
  reference manager, say so when proposing a fix: edits to the file would be
  overwritten by the next export.
- **"Check my asks."** The human queues questions on passages with the
  viewer's Ask button; each shows as a blue card in their margin.
  `scrivmark asks --json` lists the open ones with their passages. Answer each
  with `scrivmark answer <id> --body "..." --by "<your model name>"`: the
  answer appears on its card, and the human can turn it into a comment for the
  author with one click, so write answers that could stand as such a comment.
  Mention anything longer in the chat too. If the answer implies a wording
  change, add `--wording "replacement for the asked-about passage"`; the card
  then offers "Apply wording", so no separate draft is needed. Leave
  dismissing to the human. Answered asks are archived in the round record
  when the round is returned or closed, as part of the published exchange.
- **Dictated comments and edits.** When the reviewer tells you what to say,
  use `scrivmark comment` without `--draft`; it is signed as theirs. When they
  ask for a change, edit the source file directly. That edit *is* the
  tracked change.
- **LaTeX sources.** Edit `.tex` files as LaTeX: keep commands, braces and
  `%` comments intact, and write `--wording` answers as LaTeX source. Quotes
  for `scrivmark comment --quote` are source text (`\USD{5,000}`, not
  `US$5,000`). The preamble is not shown in the viewer; anything changed there
  appears only in `scrivmark changes`.
- `scrivmark return` hands the round back. It refuses while drafts remain.
  Run `scrivmark return --dry-run --json` first and tell the reviewer what it
  reports (drafts left, untracked files, comments that lost their place, how
  many changes will be recorded, where it will be pushed); it changes nothing.

## Agents as the authors

Sometimes the manuscript is yours: you (and perhaps another agent) wrote it,
and the reviewer is a human supervisor or colleague. Then you are the authors,
and you decide; nobody else will. In that case:

- Open the round with the authors' name, once: `scrivmark open <round> --as
  "Claude and Codex"` (whatever names the writing agents). Then read
  `scrivmark news --json` (or, the first time, `changes --json` and
  `threads --open --json`).
- If two of you share the round, split it (say by section or by thread) and
  each set your own `SCRIVMARK_AGENT`. Each decision, reply and resolution
  then carries your name in its `agent` field (`changes --json`,
  `threads --json`), so either of you can see what the other has done.
  Coordinate between yourselves where you normally do, not in the review:
  every thread is part of the record the reviewer reads. Writes check
  revisions, so working at once is safe; on a `stale` refusal, re-read and
  retry.
- `changes --json` gives each change's `context`: the sentence as the reviewer
  left it, with the change marked «». You need not open the file to see what
  a small edit means.
- Decide every change on its merits. Accept what improves the paper; reject
  what does not, always with `--because "..."` giving the reason. Weigh the
  reviewer's points as an equal would: argue when you disagree, and say so
  plainly. Accepting leaves the text as the reviewer made it; rejecting puts
  your original text back in the file. Either way you may edit the passage
  afterwards; the decision stays recorded as made.
- If you rewrite a passage before deciding its change (or a rejection is
  refused because the text around it moved), the change becomes `superseded`
  and needs no decision; `scrivmark reject <id> --because "..."` then records
  why you rewrote it.
- If the reviewer edited text that a script or the claims ledger generates
  (tables, reported numbers), the edit is a request: change the generator or
  the analysis, rebuild, and reply saying what you did.
- Reply to every comment. Resolve a thread only when it is settled; leave a
  disagreement open, with your argument, for the reviewer to answer.
- You may open comments of your own (`scrivmark comment`) where you want the
  reviewer's view: on a passage you rewrote, or a question back. They are for
  the reviewer, stay open for them, and do not block `respond`.
- When every change is decided, check with `scrivmark respond --dry-run
  --json` (it lists what is undecided and which threads stay open), then hand
  the round back: `scrivmark respond`. Do not `close` it yourself unless the
  reviewer has said to; closing is normally theirs once they have signed off.
- When it comes back (`scrivmark news --json` shows contested decisions and
  new replies), answer and decide again, then `respond` again.

## Helping a reviewer sign off

When the authors have responded, the round is the reviewer's again
(`responded`). `scrivmark news --json` lists what the authors did: decisions
with reasons, replies, resolved threads. Summarise it for the reviewer. On
their say-so: reply or reopen threads, `scrivmark contest c4 --because "..."`
to send a decision back (a rejected edit is put back), and then `scrivmark
close` or `scrivmark return` to send the round back to the authors. The text
itself is the authors' during sign-off: suggest wording in a comment instead.
Do not edit the files; `close` and `return` refuse while a hand edit is there.

## Helping an author

- Summarise the review: `scrivmark changes --json` and
  `scrivmark threads --open --json`.
- Accept or reject only when the author says so (unless you are the authors;
  see above):
  `scrivmark accept c1 c3`, `scrivmark reject c2 --because "..."`. Rejection
  fails closed if the text has moved; report that rather than forcing it.
- Draft replies for the author to approve, then `scrivmark reply <thread>
  --body "..."` and `scrivmark resolve <thread>`.
- Edit the source freely when asked. Changes the author edits become
  `superseded` and need no decision.
- `scrivmark close` merges the round once every change is decided and every
  thread resolved. `scrivmark close --dry-run --json` lists what is still
  pending or open, and whether the author branch has moved, without changing
  anything.
- `scrivmark export [ROUND] [--html] [-o FILE]` writes a readable summary of a
  round, for archiving or for someone without scrivmark. The JSON record in
  `reviews/` stays the authoritative one.

## Conflicts and refusals

A comment whose passage was rewritten past recognition shows as orphaned. So
does one whose passage appeared twice when it was anchored, with one copy
since removed (`where.why` is `ambiguous`): scrivmark cannot tell which copy
it was about, and will not apply a draft's or ask's wording to either. If the
human tells you where it belongs, `scrivmark reattach <thread> <file>
--quote "..."` moves it there.

`accept --all` and `reject --all` decide one change at a time. If one is
refused, the command stops there and reports, also in `--json`, which it
`done`, which `failed` and why, which `remaining` were not attempted, and the
new `rev`. Read that before retrying; the done ones stay done.

scrivmark refuses rather than guess: an ambiguous quote, a change it cannot
locate exactly, a stale revision, untracked files at `return` or `close`.
Read the message, fix the cause, and try again. Never work round a refusal by
editing the record.
