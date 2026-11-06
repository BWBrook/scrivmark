# Changelog

Each release is a git tag `vX.Y.Z`. To update an installed copy:

```bash
uv tool install --reinstall git+https://github.com/BWBrook/scrivmark
scrivmark skill --install
```

then restart any running viewer. `scrivmark doctor` says when a newer release
is out, and `scrivmark --version` shows which one you have.

## 0.5.0 (6 November 2026)

- **Reading view.** A button in the viewer opens the manuscript as it now
  reads, every edit in and nothing marked, in a second tab. It follows your
  place in the review tab, and a click on a passage there takes the review tab
  to it.
- **Items.** A drawer (button or <kbd>q</kbd>) lists every change, comment,
  draft and ask by section, with filters and a "show done" switch, says
  whether the round is ready to return or close and what is in the way, and
  offers a summary to download.
- **Nothing you were writing is lost.** Unsent replies and comment edits, the
  text of a comment on a selection, and unsaved source edits are kept in the
  browser and come back after a reload. A comment whose selection went stale
  keeps its text for the next try. An unsaved edit is put back only if the
  file has not changed since; otherwise you can copy it.
- `scrivmark return --dry-run` and `scrivmark close --dry-run` (with
  `--json`) say what the step would do and what blocks it, changing nothing.
- `scrivmark export [ROUND] [--html] [-o FILE]` writes a readable summary of a
  round: statement, changes and decisions, comment threads, asks, and what is
  outstanding.

## 0.4.0 (6 November 2026)

Fixes from an external audit (GPT-6 Pro, 5 November 2026); the record and its
dispositions are in `docs/audits/2026-11-06-external-audit.md`.

### Safety of edits

- **A proposed edit never lands on the wrong copy of a repeated passage.** If
  a passage appeared twice when it was anchored and one copy has since been
  removed, its comment, draft or ask now shows as having lost its place and
  must be re-attached; before, keeping the draft could change the other copy.
- **Approval applies only what you saw.** Keep all, keeping or dropping one
  draft, and applying an ask's wording are refused if a draft or answer
  changed after the page showed it (the revision now covers drafts and asks).
- A request that was waiting while the round was returned or closed can no
  longer add drafts or reply to a closed round.
- Manuscript files, the bibliography and the CSL style must be tracked files
  inside the repository; a path outside it, through a symlink, or ignored by
  git is refused, since its edits would not be recorded.
- Rejecting a change to a script keeps its executable mode.
- Recovery from an interrupted write no longer overwrites an edit made since;
  it sets the unfinished write aside and says so.

### Git and the command line

- A `return` that failed part-way (say, a pre-commit hook refusing the record
  commit) is finished properly by running it again.
- Metadata commits contain only scrivmark's files, never other staged work.
- A round can't start from a detached HEAD.
- `accept --all` / `reject --all` report what they did before a refusal, in
  text and `--json`, instead of "Nothing was changed".
- `keep --all --expect REV` checks the revision first.
- `scrivmark return --statement "..."` restates how the review was done, for
  when the agents or methods changed after the round started.

### LaTeX and rendering

- **LaTeX sources need pandoc 3.7 or later**, whose LaTeX reader cannot reach
  other files; on older pandoc a macro could pull a local file into the page.
  Markdown still works with pandoc 3.1, and can no longer reach the LaTeX
  reader at all. `doctor` reports it.
- `\input` on its own line within a paragraph renders at its place, not after
  the paragraph.
- Each entry of a compact one-line CSL JSON bibliography maps to its own key.
- Filenames containing quotes render correctly.
- Every pandoc run has a 120-second timeout.

### Viewer

- A comment on a repeated phrase highlights the right occurrence when
  formatting differs (`**low**` and `low`); when unsure it marks the block.
- Two quick clicks on Edit open one editor, not two.
- A live update no longer steals focus or moves the caret in a reply you are
  typing.
- A returned round with many changes renders several times faster (repeated
  git reads are cached).

## 0.3.0 (5 November 2026)

- **Edit your own comments** until the other side has seen them: the
  reviewer's until the round is returned, the author's until it is closed.
  Use the Edit button on the message in the viewer, or `scrivmark revise m-…
  --body` from the command line. An edited message is marked as edited. If an
  agent drafted it, its drafted text stays in the record.

## 0.2.0 (5 November 2026)

The first tagged release. If you installed scrivmark before 5 November 2026,
you have an untagged copy from somewhere between 3 and 5 November. This list
covers everything since then.

### Speed

- Saving an edit in the viewer and keeping all drafts are several times
  faster: on a 56 KB manuscript with 31 threads, a save went from about 15 s
  to 3 s and keeping 50 drafts from about a minute to under 4 s. Citations
  are cached, so a large CSL style is parsed only when a citation, the
  bibliography or the style changes.
- The viewer shows a progress bar while it saves or redraws, and stays
  responsive during a long render.

### Reviewing

- **Keep all.** A button at the end of the drafts keeps every draft at once,
  applying edits as tracked changes. A draft whose passage has changed is
  left for you.
- **Edit-only drafts.** An agent can propose new wording with no comment. The
  record notes which agent drafted each kept edit and comment.
- **Figures** fit the page and open in a zoomable view on click. Image
  `width=` and `height=` attributes are respected.
- Comments follow a rewritten passage. Comments on deleted text stay put, and
  orphaned comments can be re-attached.
- HTML comments in the source show as muted, unprinted notes, and
  `scrivmark import-comments` turns them into threads signed by their authors.
- `scrivmark status` lists round state not yet committed.

### Agents

- `scrivmark comment --from FILE` adds many comments or drafts in one step.
  `--dry-run` checks a batch first, showing each edit in context and any
  overlaps, and `--json --summary` keeps the output short.
- `scrivmark revise DRAFT` amends a draft in place, keeping its author and
  its earlier versions.
- `--by` records which agent drafted something.

### LaTeX

- LaTeX manuscripts can be reviewed like Markdown ones: blocks, tracked
  changes, `%` comments as notes, cross-references, `\input` files in place,
  PDF figures and TikZ pictures. See the README's LaTeX section.

### Viewer and setup

- One viewer per repository. A second `scrivmark serve` prints the address
  of the running viewer instead of starting another.
- `scrivmark doctor` exits 1 only when something essential is missing, and
  reports whether a newer release is out.
- `scrivmark --version`.
