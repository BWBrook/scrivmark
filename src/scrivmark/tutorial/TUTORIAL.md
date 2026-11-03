# Five minutes with scrivmark

This folder holds a one-page practice manuscript, `paper.md`, about wombats
and sunny burrows. It has a few problems for you to fix and comment on. You
will play both parts: first the **reviewer**, then the **author** who
responds. Nothing here touches Word.

You need `git` and `pandoc`. If you are working with Claude Code or Codex, it
can do any of the commands below for you; just ask.

## 0. Say who you are (once)

Your comments and edits are signed with your initials, and each review opens
with one line saying how it was done. Set both once:

```bash
scrivmark whoami --name "Your Name" --statement "Reviewed by Your Name working with Claude."
```

## 1. Review it (about 3 minutes)

Start a review round and open the viewer:

```bash
scrivmark start
scrivmark serve
```

`serve` prints an address. Open it in your browser, or in the browser pane
beside your Claude or Codex chat. (Ask the agent: "run `scrivmark serve` in
the background and open the address".)

Now, in the viewer:

1. **Edit.** In the Introduction, the note "proves" its result. Hover over
   that paragraph, press ✎, and change `proves that` to `tests whether`.
   Save with Ctrl-Enter or ⌘-Enter. Your change appears as redline.
2. **Comment.** In the Results, select *Occupancy was highest for north-west
   burrows* and press **Comment**: "Give the estimates with their intervals."
3. **Use your agent.** In the chat, say:

   > Do a scrivmark pre-pass on this manuscript: leave draft comments, with
   > suggested wording where it helps.

   Drafts appear in yellow in the margin. **Keep** the good ones (any wording
   is applied as a real edit) and **Drop** the rest.
4. **Ask about a passage.** Select *thermal benefits drive the choice of
   burrow* and tell the agent: "look at my selection; is this overclaimed?"

There's also a doubled word in the Discussion. Fix it or leave it for the
agent.

When you're done, hand the round back:

```bash
scrivmark return
```

## 2. Respond to it (about 2 minutes)

Now be the author. Stop the viewer (Ctrl-C), then:

```bash
scrivmark rounds
scrivmark open <round id> --as "Sam Student"
scrivmark serve
```

In the viewer, click a change to **Accept** or **Reject** it (or press `j` to
move to it and Enter to decide). Rejecting puts the original text back, and
each decision can be undone straight afterwards. Reply to the comments and
**Resolve** them. Edit anything you like with ✎ as well; your own edits never
need deciding.

Then finish:

```bash
scrivmark close
```

The round merges into `main`. Its full record, every change with its
decision and every comment thread, stays in `reviews/`. See it with:

```bash
cat reviews/*.json
git log --oneline --graph
```

## 3. On a real manuscript

In the manuscript's repository:

```bash
scrivmark init paper.md
```

Name the manuscript files in reading order
(`scrivmark init intro.md methods.md results.md discussion.md`), so notes and
old drafts in the repository stay out of the review. `scrivmark init` on its
own shows what it would pick, without writing anything. After that:

- the reviewer runs `scrivmark start`, reviews, then `scrivmark return`. With
  a GitHub remote, this pushes the round and opens a pull request, which
  notifies the author;
- the author runs `scrivmark open <round id>`, works through it, and runs
  `scrivmark close`.

An author can also ask for a review with
`scrivmark request --reviewer "Name" --github <user>`.

`scrivmark --help` lists everything, and `scrivmark doctor` checks your setup.
