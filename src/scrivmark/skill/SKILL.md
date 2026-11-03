---
name: scrivmark
description: Use when a repository has a scrivmark.toml or a review/ branch, or when asked to review a Markdown/Quarto manuscript, respond to a review, do a pre-pass, or "look at my selection" in the scrivmark viewer. Teaches the scrivmark CLI for tracked changes and comment threads on manuscript sources.
---

# Working with scrivmark

scrivmark runs manuscript review on Markdown and Quarto sources in git:

- a reviewer's edits are real edits on a `review/<round>` branch, and the
  redline is their diff against the author's version;
- comments are threads in `reviews/<round>.json`, anchored to quoted source
  text;
- a human reads the result in a local viewer (`scrivmark serve`), often in the
  browser pane beside your chat. You work through the CLI on the same state.

Always use `--json` when you need to read output. Never edit
`reviews/*.json` or `.scrivmark/` by hand.

## The viewer

Start it in the background with `scrivmark serve`. It listens on port 8765
(or `--port`, then `$PORT` from a launcher, then `[viewer] port` in
`scrivmark.toml`), keeps the same address across restarts, and
`http://localhost:8765` opens it directly on this machine. For the desktop
apps' browser pane, a `.claude/launch.json` entry running `scrivmark serve`
with `"port": 8765` and `"autoPort": false` is all it needs.
`scrivmark url` prints the full address of the running viewer. After a CLI
change, the page updates within about a second; wait that long before reading
it back.

## Where things stand

```bash
scrivmark status --json      # round, phase, counts, revision
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
- **"Look at my selection."** Run `scrivmark selection --json`. It gives the
  file, lines and exact source text the human has selected. If `stale` is
  true, say so and ask them to reselect.
- **"Check my asks."** The human queues questions on passages with the
  viewer's Ask button; each shows as a blue card in their margin.
  `scrivmark asks --json` lists the open ones with their passages. Answer each
  with `scrivmark answer <id> --body "..."`: the answer appears on its card,
  and the human can turn it into a comment for the author with one click, so
  write answers that could stand as such a comment. Mention anything longer
  in the chat too. If an answer implies a wording change, also leave a
  `--draft` with `--wording`. Leave dismissing to the human.
- **Dictated comments and edits.** When the reviewer tells you what to say,
  use `scrivmark comment` without `--draft`; it is signed as theirs. When they
  ask for a change, edit the Markdown file directly. That edit *is* the
  tracked change.
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

scrivmark refuses rather than guess: an ambiguous quote, a change it cannot
locate exactly, a stale revision, untracked files at `return` or `close`.
Read the message, fix the cause, and try again. Never work round a refusal by
editing the record.
