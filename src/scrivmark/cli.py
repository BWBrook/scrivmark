import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import config, gitutil, review, rounds
from .errors import ScrivError
from .state import ensure_ignored, local_dir, read_json, revision

app = typer.Typer(
    help="Manuscript review on Markdown and Quarto sources. Start with `scrivmark tutorial`.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

Json = Annotated[bool, typer.Option("--json", help="Machine-readable output for agents.")]
Expect = Annotated[Optional[str], typer.Option("--expect", help="Refuse if the review has changed since this revision.")]


def _root() -> Path:
    return gitutil.root(Path.cwd())


def _emit(data, as_json: bool, human: str | None = None) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    elif human is not None:
        typer.echo(human)


def _who(rv: review.Review) -> str:
    rnd = rv.rec["round"]
    if rnd["status"] == "open":
        return rnd["reviewer"]["initials"]
    return (rnd["author"] or {"initials": config.identity().initials})["initials"]


def main() -> None:
    try:
        app()
    except ScrivError as e:
        typer.secho(str(e), fg="red", err=True)
        sys.exit(1)


# ------------------------------------------------------------------ setup


@app.command()
def init(files: Annotated[Optional[list[str]], typer.Argument(help="Manuscript files in reading order.")] = None,
         as_json: Json = False):
    """Set up a repository for scrivmark: writes and commits scrivmark.toml."""
    info = rounds.init(_root(), files)
    _emit(info, as_json, f"Manuscript: {', '.join(info['files'])}\nBibliography: {', '.join(info['bibliography']) or 'none'}\n"
          "Wrote and committed scrivmark.toml. Edit it to change the file order.")


@app.command()
def whoami(name: Annotated[Optional[str], typer.Option()] = None,
           initials: Annotated[Optional[str], typer.Option()] = None,
           statement: Annotated[Optional[str], typer.Option(help="Default workflow statement for your rounds.")] = None,
           as_json: Json = False):
    """Show or set the name and initials scrivmark signs your work with."""
    if name:
        config.set_identity(name, initials or config.initials_of(name), statement)
    me = config.identity()
    _emit(me.__dict__, as_json, f"{me.name} ({me.initials})" + (f"\nStatement: {me.statement}" if me.statement else ""))


@app.command()
def doctor(as_json: Json = False):
    """Check tools, configuration and identity."""
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("git", shutil.which("git"))
    pandoc = shutil.which("pandoc")
    ver = subprocess.run(["pandoc", "--version"], capture_output=True, text=True).stdout.split("\n")[0] if pandoc else ""
    check("pandoc ≥ 3.1", pandoc and _ver(ver.split()[-1]) >= (3, 1), ver)
    check("gh (optional, for pull requests)", shutil.which("gh"), "not required")
    try:
        me = config.identity()
        check("identity", True, f"{me.name} ({me.initials})")
    except ScrivError as e:
        check("identity", False, str(e))
    try:
        root = _root()
        ms = config.load(root)
        check("scrivmark.toml", True, ", ".join(ms.files))
        for b in ms.bibliography:
            check(f"bibliography {b}", (root / b).exists())
        if any(f.endswith(".qmd") for f in ms.files):
            check("quarto (for .qmd)", shutil.which("quarto"), "code outputs need Quarto's committed _freeze/")
    except ScrivError as e:
        check("repository", False, str(e))
    _emit(checks, as_json, "\n".join(f"{'✓' if c['ok'] else '✗'} {c['check']}  {c['detail']}" for c in checks))


def _ver(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split(".")[:2])
    except ValueError:
        return (0,)


@app.command()
def skill(install: Annotated[bool, typer.Option(help="Copy the guide into ~/.claude/skills and ~/.codex/skills.")] = False):
    """Show or install the agent guide that teaches Claude and Codex to use scrivmark."""
    from importlib.resources import files

    src = files("scrivmark") / "skill" / "SKILL.md"
    if not install:
        typer.echo(src.read_text())
        return
    done = []
    for home in (Path.home() / ".claude", Path.home() / ".codex"):
        if home.exists():
            dest = home / "skills" / "scrivmark" / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text())
            done.append(str(dest))
    typer.echo("Installed:\n  " + "\n  ".join(done) if done else "Found neither ~/.claude nor ~/.codex.")


@app.command()
def tutorial(target: Annotated[Path, typer.Argument(help="Folder to create.")] = Path("scrivmark-tutorial")):
    """Create a one-page practice manuscript in its own git repository."""
    from importlib.resources import files

    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise ScrivError(f"{target} already exists and is not empty.")
    src = files("scrivmark") / "tutorial"
    shutil.copytree(str(src), target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    g = lambda *a: subprocess.run(["git", *a], cwd=target, check=True, capture_output=True)  # noqa: E731
    ensure_ignored(target)
    g("init", "-q", "-b", "main")
    g("add", "-A")
    g("-c", "user.name=Sam Student", "-c", "user.email=sam@example.org", "commit", "-q",
      "--author", "Sam Student <sam@example.org>", "-m", "Draft of the wombat burrow note")
    typer.echo(f"Practice manuscript ready in {target}\n\nNext:\n  cd {target}\n  scrivmark start\n  scrivmark serve\n\n"
               "The walk-through is in TUTORIAL.md in that folder, and at\n"
               "https://github.com/BWBrook/scrivmark/blob/main/src/scrivmark/tutorial/TUTORIAL.md")


# ------------------------------------------------------------------ lifecycle


@app.command()
def start(author_branch: Annotated[Optional[str], typer.Option(help="Branch to review (default: current).")] = None,
          statement: Annotated[Optional[str], typer.Option(help="How this review was done, shown once to the author.")] = None,
          as_json: Json = False):
    """Start a review round (reviewer)."""
    rv = rounds.start(_root(), author_branch, statement)
    _emit(rv.rec["round"], as_json, f"Started round {rv.id} on branch review/{rv.id}.\n"
          "Edit the Markdown and add comments; run `scrivmark serve` to see the rendered review.")


@app.command()
def request(reviewer: Annotated[str, typer.Option(help="Reviewer's name.")],
            initials: Annotated[Optional[str], typer.Option()] = None,
            github: Annotated[Optional[str], typer.Option(help="Reviewer's GitHub user, to notify.")] = None,
            as_json: Json = False):
    """Ask someone to review the current version (author)."""
    rv = rounds.request(_root(), reviewer, initials or config.initials_of(reviewer), github)
    _emit(rv.rec["round"], as_json, f"Requested round {rv.id} from {reviewer}. They run `scrivmark open {rv.id}`.")


@app.command("return")
def return_(push: Annotated[bool, typer.Option(help="Push the round to origin if there is one.")] = True,
            as_json: Json = False):
    """Hand the review back to the author (reviewer)."""
    info = rounds.return_round(_root(), push)
    msg = f"Returned round {info['round']}: {info['changes']} changes, {info['threads']} comments."
    if info["orphans"]:
        msg += f"\nWarning: {len(info['orphans'])} comment(s) lost their passage: {', '.join(info['orphans'])}"
    msg += "\nPushed to origin." if info["pushed"] else "\nNot pushed (no remote, or --no-push)."
    _emit(info, as_json, msg)


@app.command("open")
def open_(round_id: Annotated[str, typer.Argument(help="Round id, e.g. 2026-11-03-bwb.")],
          as_: Annotated[Optional[str], typer.Option("--as", help="Your name, if not your usual identity.")] = None,
          as_json: Json = False):
    """Check out a round to work on it (either side)."""
    root = _root()
    if as_:
        os.environ["SCRIVMARK_AS"] = as_
    info = rounds.open_round(root, round_id)
    msg = f"Round {info['round']} is {info['status']}."
    if info["merged"]:
        msg += "\nBrought in newer work from the author branch."
    if info["invalidated"]:
        msg += f"\nThese accepted changes were disturbed by that merge and are now undecided: {', '.join(info['invalidated'])}"
    _emit(info, as_json, msg + "\nRun `scrivmark serve` to work through it.")


@app.command()
def close(force: Annotated[bool, typer.Option(help="Close even with undecided changes or open comments.")] = False,
          push: Annotated[bool, typer.Option()] = True, as_json: Json = False):
    """Finish the round and merge it into the author branch (author)."""
    info = rounds.close_round(_root(), force, push)
    d = info["decisions"]
    _emit(info, as_json, f"Closed round {info['round']} and merged it into {info['merged_into']}: "
          f"{d['accepted']} accepted, {d['rejected']} rejected, {d['superseded']} superseded by your edits.\n"
          f"The review record stays in reviews/{info['round']}.json.")


@app.command()
def status(as_json: Json = False):
    """Summarise the current round."""
    rv = review.active(_root())
    s = review.summary(rv)
    r, c, t = s["round"], s["changes"], s["threads"]
    human = (f"Round {r['id']} ({r['status']}), reviewer {r['reviewer']['name']}\n"
             f"Changes: {c['pending']} pending, {c['accepted']} accepted, {c['rejected']} rejected, {c['superseded']} superseded\n"
             f"Comments: {t['open']} open, {t['resolved']} resolved, {t['orphaned']} orphaned\n"
             f"Drafts: {s['drafts']}\nRevision: {s['rev']}")
    _emit(s, as_json, human)


@app.command("rounds")
def rounds_(as_json: Json = False):
    """List review rounds in this repository and on origin."""
    rs = rounds.list_rounds(_root())
    _emit(rs, as_json, "\n".join(f"{r['id']}  {r['status']}  ({r['where']})" for r in rs) or "No rounds yet.")


# ------------------------------------------------------------------ changes


@app.command()
def changes(undecided: Annotated[bool, typer.Option(help="Only pending changes.")] = False, as_json: Json = False):
    """List the reviewer's changes and their state."""
    rv = review.active(_root())
    if rv.status == "open":
        raise ScrivError("The change list is frozen when the reviewer returns the round.")
    cs = [c for c in review.changes(rv) if not undecided or c["state"] == "pending"]
    lines = []
    for c in cs:
        if c["type"] == "file":
            lines.append(f"{c['id']:>4}  {c['state']:<10} {c['reviewer']}")
        else:
            lines.append(f"{c['id']:>4}  {c['state']:<10} {c['file']}: {_short(c['base'])!r} → {_short(c['reviewer'])!r}")
    _emit(cs, as_json, "\n".join(lines) or "No changes.")


def _short(s: str, n: int = 60) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


All = Annotated[bool, typer.Option("--all", help="Every change still pending.")]


@app.command()
def accept(change_ids: Annotated[Optional[list[str]], typer.Argument()] = None, all_: All = False,
           expect: Expect = None, as_json: Json = False):
    """Accept changes (author)."""
    _decide(change_ids or [], all_, "accepted", expect, as_json)


@app.command()
def reject(change_ids: Annotated[Optional[list[str]], typer.Argument()] = None, all_: All = False,
           expect: Expect = None, as_json: Json = False):
    """Reject changes, restoring your original text (author)."""
    _decide(change_ids or [], all_, "rejected", expect, as_json)


def _decide(ids: list[str], all_: bool, decision: str, expect: str | None, as_json: bool) -> None:
    rv = review.active(_root())
    states = {c["id"]: c["state"] for c in review.changes(rv)}
    if all_:
        ids = [cid for cid, st in states.items() if st == "pending"]
    unknown = [cid for cid in ids if cid not in states]
    if unknown:
        raise ScrivError(f"No such change: {', '.join(unknown)}. `scrivmark changes` lists them.")
    if not ids:
        raise ScrivError("Name the changes, e.g. `scrivmark accept c1 c3`, or use --all.")
    done = [review.decide(rv, cid, decision, _who(rv), expect if i == 0 else None) for i, cid in enumerate(ids)]
    _emit(done, as_json, "\n".join(f"{c['id']} {decision}" for c in done))


# ------------------------------------------------------------------ comments


@app.command("threads")
def threads_(open_only: Annotated[bool, typer.Option("--open", help="Only open threads.")] = False,
             as_json: Json = False):
    """List comment threads."""
    rv = review.active(_root())
    ts = [t for t in review.threads(rv) if not open_only or t["status"] == "open"]
    lines = []
    for t in ts:
        where = "ORPHANED " if t["where"]["state"] != "ok" else ""
        lines.append(f"{t['id']}  {t['status']}  {where}{t['anchor']['file']}: {_short(t['anchor']['exact'], 50)!r}")
        lines += [f"    {m['by']}: {m['body']}" for m in t["messages"]]
    _emit(ts, as_json, "\n".join(lines) or "No comments.")


def _anchor(rv, file: str, quote: str | None, prefix: str, suffix: str, lines: str | None) -> dict:
    if lines:
        a, _, b = lines.partition("-")
        text = rv.text(file)
        if text is None:
            raise ScrivError(f"{file} does not exist")
        ls = text.splitlines(keepends=True)
        first, last = int(a), int(b or a)
        s = sum(len(x) for x in ls[: first - 1])
        e = sum(len(x) for x in ls[:last]) - (1 if ls[last - 1].endswith("\n") else 0)
        return review.anchor_at(rv, file, s, e)
    if not quote:
        raise ScrivError("Give --quote TEXT or --lines A-B to say where the comment goes.")
    return review.anchor_quote(rv, file, quote, prefix, suffix)


@app.command()
def comment(file: str,
            body: Annotated[str, typer.Option(help="The comment.")],
            quote: Annotated[Optional[str], typer.Option(help="Exact source text the comment is about.")] = None,
            prefix: Annotated[str, typer.Option(help="Text just before the quote, to pick one occurrence.")] = "",
            suffix: Annotated[str, typer.Option(help="Text just after the quote.")] = "",
            lines: Annotated[Optional[str], typer.Option(help="Anchor to whole source lines, e.g. 12-14.")] = None,
            draft: Annotated[bool, typer.Option(help="A draft for the reviewer to keep or drop (agents).")] = False,
            wording: Annotated[Optional[str], typer.Option(help="With --draft: replacement text to apply if kept.")] = None,
            expect: Expect = None, as_json: Json = False):
    """Add a comment anchored to source text."""
    rv = review.active(_root())
    anchor = _anchor(rv, file, quote, prefix, suffix, lines)
    if draft:
        d = review.add_draft(rv, anchor, body, wording, expect)
        _emit(d, as_json, f"Draft {d['id']} added.")
    else:
        if wording is not None:
            raise ScrivError("--wording applies to drafts; to change the text, edit the file.")
        t = review.comment(rv, anchor, body, _who(rv), expect)
        _emit(t, as_json, f"Comment {t['id']} added.")


@app.command()
def reply(thread_id: str, body: Annotated[str, typer.Option()], expect: Expect = None, as_json: Json = False):
    """Reply to a thread."""
    rv = review.active(_root())
    t = review.reply(rv, thread_id, body, _who(rv), expect)
    _emit(t, as_json, f"Replied on {t['id']}.")


@app.command()
def resolve(thread_ids: list[str], expect: Expect = None, as_json: Json = False):
    """Mark threads resolved."""
    rv = review.active(_root())
    done = [review.set_thread(rv, tid, "resolved", expect if i == 0 else None) for i, tid in enumerate(thread_ids)]
    _emit(done, as_json, "\n".join(f"{t['id']} resolved" for t in done))


@app.command()
def reopen(thread_id: str, as_json: Json = False):
    """Reopen a resolved thread."""
    rv = review.active(_root())
    t = review.set_thread(rv, thread_id, "open")
    _emit(t, as_json, f"{t['id']} reopened")


@app.command("drafts")
def drafts_(as_json: Json = False):
    """List draft comments awaiting the reviewer's triage."""
    rv = review.active(_root())
    ds = review.drafts(rv)
    lines = []
    for d in ds:
        lines.append(f"{d['id']}  {d['anchor']['file']}: {_short(d['anchor']['exact'], 50)!r}\n    {d['body']}")
        if d["wording"] is not None:
            lines.append(f"    wording → {d['wording']!r}")
    _emit(ds, as_json, "\n".join(lines) or "No drafts.")


@app.command()
def keep(draft_ids: list[str], as_json: Json = False):
    """Keep drafts as your own comments, applying any proposed wording."""
    rv = review.active(_root())
    out = [review.triage(rv, did, True, _who(rv)) for did in draft_ids]
    _emit(out, as_json, "\n".join(f"kept {d}" for d in draft_ids))


@app.command()
def drop(draft_ids: list[str], as_json: Json = False):
    """Drop drafts."""
    rv = review.active(_root())
    for did in draft_ids:
        review.triage(rv, did, False, _who(rv))
    _emit(draft_ids, as_json, "\n".join(f"dropped {d}" for d in draft_ids))


# ------------------------------------------------------------------ viewer bridge


@app.command()
def selection(as_json: Json = False):
    """What the human has selected in the viewer."""
    rv = review.active(_root())
    sel = read_json(local_dir(rv.root, rv.id) / "selection.json", None)
    if sel is None:
        _emit(None, as_json, "Nothing is selected in the viewer.")
        return
    stale = rv.text(sel["file"]) is None or revision(rv.root, [sel["file"]]) != sel["file_rev"]
    sel["stale"] = stale
    _emit(sel, as_json, f"{sel['file']} lines {sel['lines'][0]}-{sel['lines'][1]}" + (" (STALE: file changed)" if stale else "")
          + f"\n{sel['exact']}")


@app.command()
def asks(done: Annotated[Optional[str], typer.Option(help="Mark an ask answered.")] = None, as_json: Json = False):
    """Questions the human queued from the viewer's Ask button."""
    rv = review.active(_root())
    p = local_dir(rv.root, rv.id) / "asks.json"
    items = read_json(p, [])
    if done:
        items = [dict(a, done=True) if a["id"] == done else a for a in items]
        p.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    pending = [a for a in items if not a.get("done")]
    _emit(pending, as_json, "\n".join(f"{a['id']}  {a['file']}: {_short(a['exact'], 50)!r}\n    {a['question']}" for a in pending)
          or "No open asks.")


@app.command()
def serve(port: Annotated[int, typer.Option()] = 0,
          open_browser: Annotated[bool, typer.Option("--browser/--no-browser", help="Open a browser too.")] = False):
    """Run the local viewer and print its address."""
    from .server import run

    run(_root(), port, open_browser)


if __name__ == "__main__":
    main()
