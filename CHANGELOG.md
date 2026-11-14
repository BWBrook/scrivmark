# Changelog

Each release is a git tag `vX.Y.Z`. To update an installed copy:

```bash
uv tool install --reinstall git+https://github.com/BWBrook/scrivmark
scrivmark skill --install
```

then restart any running viewer. `scrivmark doctor` says when a newer release
is out, and `scrivmark --version` shows which one you have.

## 0.8.0 (14 November 2026)

From a full practice round run by a new maintainer (Claude Fable 5.2), and an
independent code review by Codex.

- **A rewritten sentence is one change.** Edits separated only by a few
  unchanged words inside one clause are now a single tracked change, so a
  rewrite is accepted or rejected whole. It used to be cut into a fragment
  either side of each surviving word, each decided separately, and rejecting
  one of them alone could leave nonsense. Small separate corrections stay
  separate. Rounds already returned keep the changes they recorded.
- **TikZ pictures compile in a sandbox.** Compiling a picture from a manuscript
  you are reviewing could read any file you can: TeX Live 2026 turned off the
  setting scrivmark relied on (`openin_any`). The compile now runs under
  `sandbox-exec` on macOS or `bubblewrap` on Linux, with no network and no
  access to home directories, other volumes or temporary files. Without a
  working sandbox, pictures show as placeholders: on Linux, install
  `bubblewrap` (`scrivmark doctor` says whether it is working).
- **The viewer fits a narrow pane.** The top bar no longer wraps beside a
  chat, the help box scrolls instead of running off the top, and
  <kbd>j</kbd>/<kbd>k</kbd> stop once at a replacement rather than at its
  deleted and inserted halves.
- `scrivmark status` names the command that commits the round in each phase
  (it always said `return`).
- **Integrity fixes from the code review**, each with a test that fails on
  0.7.0:
  - an edit box could save over a change made while it was opening, or into
    a different round;
  - an interrupted write could be finished on the wrong branch;
  - a draft's wording could land on a similar passage when its own was
    deleted and the other renamed (it now asks to be re-attached);
  - a draft being edited in the viewer could be kept over a version an agent
    had revised meanwhile;
  - a decision box left open across a change of round could decide the wrong
    change.
- `keep`, `drop` and `resolve` with several ids now report what was done
  before a refusal (`done`, `failed`, `remaining`, `rev`), as `accept` and
  `reject` do, and `comment --lines` out of range is a refusal, not a crash.
- The full list is in
  [docs/audits/2026-11-14-handover-review.md](docs/audits/2026-11-14-handover-review.md).
- **For agents:** the guide has a new first section, "What changed for
  agents", listing changes that can break a habit or a script, with what to do
  instead. Run `scrivmark skill --install` after updating.

## 0.7.0 (10 November 2026)

From a supervisor's pass over a PhD chapter: the setup steps no longer touch
the author's branch, a round can be abandoned, and margin cards stay level
with their passage.

- **`init` no longer commits to the author's branch.** It writes
  `scrivmark.toml`, and `start` commits it on the round branch, so the
  author's branch gets it only when the round is merged. Reviewing someone
  else's repository used to leave their `main` a commit ahead of origin. With
  no files named, `init` reuses the config an earlier round's branch carries
  (here or on origin), so two rounds do not add conflicting configs; it warns
  when the files you name differ. When there is no `.bib`, it reports the
  references written out under a References heading and how many have DOIs.
- **`scrivmark abandon`** gives up a round that has not been returned and goes
  back to the author's branch. Its last state stays under the tag
  `scrivmark/<round>/abandoned`; kept comments or edits need `--force`.
- **`scrivmark stop`** stops this repository's viewer, through a request only
  a caller holding its token can make.
- **The statement is visible.** `start` prints the statement the author will
  see and where it came from, and `return --dry-run` notes any agent named in
  the round (by `--by`) that the statement leaves out. Help examples say
  `'Agent (model)'` rather than naming a real model.
- **Overlaps say what will happen.** In `comment --from ... --dry-run`, an item
  with wording that overlaps a thread or draft now says whether that thread
  stays anchored after the edit, or that both propose wording and keep all
  applies only the first.
- **Margin cards fold.** Comment and draft cards show their first sentence
  until clicked; "+N replies" or "more" opens one. The active card (clicked,
  or reached by clicking its highlight) opens and moves level with its passage,
  with its neighbours moving aside. The skill now asks agents to lead each
  draft with its point, since that sentence is all a folded card shows.
- **The skill** covers setting up a round, the statement, abandoning and
  stopping, and says that any line-edit pass uses wording-only drafts, not
  just a second agent's.

## 0.6.1 (8 November 2026)

A sanity pass over the round lifecycle, looking for state the code relied on
but did not enforce. Every fix has a regression test that fails on 0.6.0.

- **No acting out of turn from the command line.** The viewer was already
  read-only for the side whose turn it is not; commands now refuse the same
  way, so an agent replying after `respond`, or a reviewer typing in their
  clone while the authors hold the round, is no longer recorded as the other
  side.
- **Looking does not commit.** `open` by the reviewer of a returned round only
  checks it out. It used to record the reviewer as its author and commit,
  which forked the round from the authors' copy. `open --as` still lets one
  person play both parts.
- **Authors' comments reach the reviewer.** A comment made while the text had
  uncommitted edits (after any rejection) pointed at a snapshot on the
  authors' machine and showed as orphaned in the reviewer's clone after
  `respond`. Every handover now re-anchors such threads to its commit.
- **Sign-off edits are refused, not slipped in.** A hand edit by the reviewer
  during sign-off was committed by `close` or `return` as "author responses"
  and merged untracked. Both now refuse any change since the authors' handed
  over commit, committed or not (the preview says so); text a contest puts
  back is still sent.
- **Rejecting a deleted or renamed file works.** It failed as "not tracked";
  the restored file is now written and staged for the authors' commit.
- **Concurrent writers.** An editor's save while scrivmark works out a
  change is no longer overwritten (a save in the instant of replacement can
  still race it); the viewer's revision is taken before it reads anything,
  so a CLI change made meanwhile makes the page's edit stale instead of being
  overwritten; `--expect` on `accept`, `reject`, `resolve` and `keep` with
  several ids now covers every item, not only the first.
- Previews (`--dry-run`) say when it is the other side's turn.
- Smaller: record writes from round transitions are atomic; an accepted change
  undone by a merged author branch loses its old reason too; the viewer
  notices `open --as` without a restart.

## 0.6.0 (7 November 2026)

Rounds that go back and forth, and agents as authors.

- **Sign-off.** After deciding every change, the authors can hand the round
  back with `scrivmark respond` instead of closing it. The reviewer then
  reads their decisions and replies, answers, reopens threads, comments, and
  can `scrivmark contest c4 --because "..."` a decision (a rejected edit is
  put back for the authors to decide again). Then `scrivmark close`, or
  `scrivmark return` to send it back, as often as needed. A human author can
  still simply close from `returned`.
- **Agents named on what they do.** `--by` or `SCRIVMARK_AGENT` records which
  agent replied, decided, resolved or handed over, beside the side's
  initials, in the record, the viewer and the export.
- **Reasons with decisions.** `accept`/`reject --because "..."`, also from
  the viewer's decision popup, kept with the change and shown wherever it is
  listed.
- **What's new.** `scrivmark news` lists what the other side did in its last
  turn; the viewer marks new items and the Items panel filters them.
- **Read-only when it is not your turn.** Opening a round the other side
  holds (say, the reviewer looking at a returned round in their own clone)
  gives a read-only view; before, it would have acted as the other side.
- The agent guide has a section for agents who are themselves the authors,
  shaped by a trial round in which Claude and Codex were the authors: they
  caught a deliberate error among the reviewer's edits, rejected it with the
  reason, and reported what was awkward. From that trial:
  `respond --dry-run`; refusals come back as JSON under `--json`;
  `changes --json` gives each change's sentence (`context`) and line; a
  superseded change can carry a reason (`reject c4 --because ...`); `open` and
  `respond` say plainly what happened.
- `scrivmark status` says whose turn it is; initials skip "and" ("Claude and
  Codex" is CC).
- `scrivmark open --as NAME` now holds for that clone: the viewer and later
  commands there act as NAME until a new round is started.
- Every write from the viewer carries the revision of the page it was made
  on, so nothing made before a handover can land after it.
- Answered asks join the record at each handover, not only at return and
  close.
- Times are recorded to the millisecond, so acts and handovers in the same
  second keep their order; older records are read as before.

- The README is rewritten around what scrivmark now does, with new screenshots.
- Long entries in the Items panel no longer wrap under their type.

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
