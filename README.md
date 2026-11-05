# scrivmark

> **Status:** early beta, developed in the open. Try it on the tutorial first; expect rough edges, and please report them.

**Track changes and comments for manuscripts written in Markdown, Quarto or LaTeX, without Word.**

You write your paper in plain text, in a git repository, next to its code and
data. scrivmark adds the part that has always sent people back to Word: a
supervisor or co-author reads the paper as nicely rendered prose, edits it and
leaves comments in the margin, and the author goes through each change and
comment and accepts, rejects or replies.

![The scrivmark viewer: a rendered manuscript with tracked changes and margin comments](docs/img/viewer.png)

- **Edits are real edits.** A reviewer's changes are made to the Markdown or
  LaTeX source itself, on a review branch. The redline you see is the difference from the
  author's version, so nothing is ever converted back from another format.
- **Comments sit beside the text, not in it.** Threads are kept in a review
  file next to the manuscript, anchored to the passage they are about, and
  they follow the passage as it is edited.
- **It reads like a paper.** Citations, figures, tables, equations and
  cross-references are rendered, in a viewer that runs on your own machine.
- **Your AI collaborator works alongside you.** Claude Code or Codex can do a
  first critical read and leave draft comments for you to keep or drop, look
  at whatever you have selected, or make the edits you ask for, all through
  the same review.
- **The record stays.** Every change, decision and comment thread is kept in
  the repository when the review is merged.

## LaTeX manuscripts

scrivmark reads `.tex` sources through pandoc's LaTeX reader, so a LaTeX
grant or paper gets the same viewer, redline, comments and rounds as a
Markdown one. `scrivmark init` finds the documents (each file with
`\documentclass` and `\begin{document}`, preferring those in a directory with
a `.latexmkrc`), follows `\input` and `\include`, and takes the bibliography
from `\bibliography{...}`.

- The preamble is not shown, but its `\newcommand` definitions are expanded,
  and selections across simple macros map back to the source.
- `%` comments show as grey "comment in the source" notes.
- `\ref`, `\eqref`, `\cref` and `\autoref` are numbered from the document's
  own labels.
- PDF figures are shown as PNGs (needs `pdftocairo`, from Poppler) and each
  `tikzpicture` is compiled on its own to SVG (needs `pdflatex`). Without the
  tools, those figures show as placeholders.
- The viewer is a faithful reading of the words, not of the page: build the
  PDF as usual to check layout and page limits.
- Body text on the same line as `\begin{document}` is not shown; start the
  body on the next line.
- Citation style: pandoc's default author-date unless `csl` in
  `scrivmark.toml` names a CSL file.

## Five-minute tutorial

You need `git`, `pandoc` (3.1 or later) and [`uv`](https://docs.astral.sh/uv/), on Linux, macOS or
Windows through WSL (native Windows is not supported).

```bash
uv tool install git+https://github.com/BWBrook/scrivmark
scrivmark tutorial
```

That creates a one-page practice manuscript with a few problems to fix. Follow
[its walk-through](src/scrivmark/tutorial/TUTORIAL.md): you review it, then
respond as its author, in about five minutes.

To let Claude Code or Codex help, install the agent guide once:

```bash
scrivmark skill --install
```

To update later, run the install again with `--reinstall`, then refresh the
agent guide and restart any running viewer:

```bash
uv tool install --reinstall git+https://github.com/BWBrook/scrivmark
scrivmark skill --install
```

`scrivmark --version` shows the release you have, `scrivmark doctor` says
whether a newer one is out, and [CHANGELOG.md](CHANGELOG.md) says what changed.

## See a finished round

[scrivmark-tutorial-example](https://github.com/BWBrook/scrivmark-tutorial-example)
is the tutorial manuscript after one complete round, on 3–4 November 2026.
Barry W. Brook reviewed it with Claude, which answered his asks as he read.
Claude then played the fictional author: it accepted 17 of the 19 tracked
changes, rejected 2 with reasons, and answered all 11 comments with replies
and revisions. Clone it to step through the round in the viewer. This is what
the author saw on opening it:

![The returned round in the author's view: tracked changes inline, the reviewer's comments in the margin](docs/img/example-round.png)

## On your own manuscript

```bash
scrivmark init paper.md # in the manuscript's repository: the sources, in reading order
scrivmark start         # reviewer: open a review round
scrivmark serve         # read, edit and comment in the viewer
scrivmark return        # hand it back; pushes and opens a pull request on GitHub
scrivmark open <round>  # author: work through the review in the same viewer
scrivmark close         # merge it, keeping the record in reviews/
```

`scrivmark doctor` checks your setup, and `scrivmark --help` lists every
command. Authors can ask for a review with `scrivmark request`.

The viewer listens on port 8765 (set another with `[viewer] port = 8800` in
`scrivmark.toml`, or `--port`) and keeps the same address across restarts. A
launcher that sets `$PORT`, such as the Claude desktop app's browser pane, is
obeyed. A minimal `.claude/launch.json`:

```json
{
  "version": "0.0.1",
  "configurations": [
    { "name": "scrivmark", "runtimeExecutable": "scrivmark",
      "runtimeArgs": ["serve"], "port": 8765, "autoPort": false }
  ]
}
```

`"autoPort": false` makes a busy port fail visibly instead of moving.
`scrivmark url` prints the full address.

## How it works

The design, and the decisions behind it, are in
[`docs/DESIGN.md`](docs/DESIGN.md), which also says what is built and what is
still planned. Its independent reviews are in [`docs/audits/`](docs/audits/). In short:

- a review round is a branch, `review/<round>`, cut from the author's branch;
- when the reviewer returns it, every difference is frozen into a numbered
  list of changes in `reviews/<round>.json`, and tags keep both versions;
- rejecting a change restores the original only if the text, and the text
  around it, are exactly as scrivmark expects; otherwise it refuses and
  changes nothing;
- the author's own edits never need deciding;
- closing merges the round with a merge commit, so the history shows the
  whole review.

Not yet built: rendering through Quarto itself, so `.qmd` files currently
render with pandoc and show no code outputs. Several reviewers in one round
and live co-editing are out of scope.

## How this work is done

Developed by Barry W. Brook with AI collaborators.

- **Claude (Opus 5.5, in Claude Code)** surveyed existing tools, ran the
  design interview with Barry, wrote the design and the implementation, and
  adjudicated the reviews.
- **Codex (`gpt-6.1-sol`)** carried out the independent adversarial reviews
  of the design and the code.
- **GPT-6 Pro** carried out an external review of the whole repository, commissioned by Barry.

## Citation

See [`CITATION.cff`](CITATION.cff).

## Licence

Code is released under the [MIT Licence](LICENSE). Text, figures and data created for this project are released under [CC BY-SA 4.0](LICENSE-CONTENT). The tutorial cites two published papers, whose bibliographic details come from Crossref.
