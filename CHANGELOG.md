# Changelog

Each release is a git tag `vX.Y.Z`. To update an installed copy:

```bash
uv tool install --reinstall git+https://github.com/BWBrook/scrivmark
scrivmark skill --install
```

then restart any running viewer. `scrivmark doctor` says when a newer release
is out, and `scrivmark --version` shows which one you have.

## Unreleased

- `scrivmark return --statement "..."` restates how the review was done, for
  when the agents or methods changed after the round started.

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
