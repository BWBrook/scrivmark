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

Always use `--json` when you need to read output. Never edit
`reviews/*.json` or `.scrivmark/` by hand.

This guide is loaded when your session starts. If scrivmark was upgraded since,
`scrivmark skill` prints the current one; read it before going on.

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
`scrivmark url` prints the full address of the running viewer. After a CLI
change, the page updates within about a second; wait that long before reading
it back.

## Where things stand

```bash
scrivmark status --json      # round, phase, counts, revision; author is null until the author opens the returned round
scrivmark threads --json     # comments, with anchors and where they sit now
scrivmark changes --json     # after return: each change, its id and state
scrivmark drafts --json      # reviewer phase: drafts awaiting triage
```

The phase decides who acts. While a round is `open`, it belongs to the
reviewer: edit, comment, draft. Once it is `returned`, it belongs to the
author: accept, reject, reply, resolve, edit.

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
  draft, and keep each to a sentence or three. The reviewer keeps drafts as
  their own (wording becomes a real edit) or drops them, so write what you
  would be happy for them to sign. Do not add your own name or labels: the
  round's statement already records that an agent helped.
  For more than two or three drafts, write them to a JSON file and add them
  in one go, which avoids shell quoting and adds all or none:

  ```bash
  scrivmark comment --from drafts.json --draft
  # drafts.json: [{"file": "paper.md", "quote": "...", "body": "...", "wording": "..."}, ...]
  ```

  (`prefix`, `suffix` and `lines` work as on the command line; JSON Lines and
  `--from -` for stdin also work.)
- **Comments left in the source.** If the Markdown carries `<!-- ... -->`
  comments from an earlier review, they show as grey notes but are not
  threads. `scrivmark import-comments --dry-run` lists them; at the start of a
  round, offer to run `scrivmark import-comments`, which makes each an open
  thread anchored to it so it can be replied to and resolved. A comment that
  opens "Name, YYYY-MM-DD" is signed by that name, others by the reviewer;
  the dry run shows who signs each. The comments stay in the source for the
  author to remove.
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

## Helping an author

- Summarise the review: `scrivmark changes --json` and
  `scrivmark threads --open --json`.
- Accept or reject only when the author says so:
  `scrivmark accept c1 c3`, `scrivmark reject c2`. Rejection fails closed if
  the text has moved; report that rather than forcing it.
- Draft replies for the author to approve, then `scrivmark reply <thread>
  --body "..."` and `scrivmark resolve <thread>`.
- Edit the source freely when asked. Changes the author edits become
  `superseded` and need no decision.
- `scrivmark close` merges the round once every change is decided and every
  thread resolved.

## Conflicts and refusals

A comment whose passage was rewritten past recognition shows as orphaned. If
the human tells you where it belongs, `scrivmark reattach <thread> <file>
--quote "..."` moves it there.

scrivmark refuses rather than guess: an ambiguous quote, a change it cannot
locate exactly, a stale revision, untracked files at `return` or `close`.
Read the message, fix the cause, and try again. Never work round a refusal by
editing the record.
