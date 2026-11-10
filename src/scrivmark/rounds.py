"""Round lifecycle: start, return, open, close, over plain git, with GitHub
pull requests when `gh` is available."""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import config, gitutil, record, review
from .errors import ScrivError
from .review import (BRANCH_PREFIX, Review, active, archive_asks, catalogue, drafts, is_managed, locate,
                     rebase_anchors, require_turn, signoff_edits, threads, undecide)
from .state import ensure_ignored, transaction

TAG = "scrivmark/{id}/{which}"


def _require_clean(root: Path, what: str) -> None:
    dirty = [p for p in gitutil.dirty_paths(root) if not is_managed(p)]
    if dirty:
        listing = "\n  ".join(dirty[:10])
        raise ScrivError(f"Commit or stash these changes before you {what}:\n  {listing}")


def _new_id(root: Path, initials: str) -> str:
    stem = f"{date.today().isoformat()}-{initials.lower()}"
    rid, n = stem, 1
    while gitutil.ok(root, "rev-parse", "--verify", f"refs/heads/{BRANCH_PREFIX}{rid}") or record.path(root, rid).exists() \
            or gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/tags/{TAG.format(id=rid, which='abandoned')}"):
        n += 1
        rid = f"{stem}-{n}"
    return rid


def _commit(root: Path, message: str, paths: list[str]) -> bool:
    """Commit exactly `paths`; anything else staged stays staged and out of it."""
    gitutil.git(root, "add", "-A", "--", *paths)
    if gitutil.ok(root, "diff", "--cached", "--quiet", "--", *paths):
        return False
    gitutil.git(root, "commit", "-q", "-m", message, "--only", "--", *paths)
    return True


def _commit_work(root: Path, message: str) -> bool:
    """Commit changes to tracked files; refuse if untracked files are lying
    around, so nothing unintended joins the review."""
    stray = [p for p in gitutil.untracked(root) if not is_managed(p)]
    if stray:
        listing = "\n  ".join(stray[:10])
        raise ScrivError(
            "These new files are not part of the repository yet:\n  " + listing +
            "\nRun `git add <file>` for any that belong to the manuscript, and delete or ignore the rest."
        )
    gitutil.git(root, "add", "-u")
    if gitutil.ok(root, "diff", "--cached", "--quiet"):
        return False
    gitutil.git(root, "commit", "-q", "-m", message)
    return True


def _gh() -> bool:
    return shutil.which("gh") is not None


def _on_github(root: Path) -> bool:
    return gitutil.has_remote(root) and "github.com" in gitutil.git(root, "remote", "get-url", "origin")


def init(root: Path, files: list[str] | None = None, write: bool = True) -> dict:
    """Write scrivmark.toml (and the .gitignore line) without committing them:
    `start` commits them on the round branch, so the author's branch only gets
    them when a round is merged. With no files named, a config that an earlier
    round's branch already carries is reused as it is."""
    if gitutil.has_remote(root):
        gitutil.git(root, "fetch", "-q", "origin", check=False)
    earlier = None if (root / config.CONFIG).exists() else config.from_rounds(root)
    found = earlier if earlier and not files else config.detect(root)
    chosen = files or found["files"]
    if not chosen:
        raise ScrivError("No Markdown, Quarto or LaTeX files found; name them: `scrivmark init paper.md`")
    chosen = [config.inside(root, f) for f in chosen]
    for b in found["bibliography"]:
        config.inside(root, b, "Bibliography")
    if found["csl"]:
        config.inside(root, found["csl"], "CSL style")
    missing = [f for f in chosen if not (root / f).exists()]
    if missing:
        raise ScrivError(f"These files do not exist: {', '.join(missing)}")
    info = {"files": chosen, "bibliography": found["bibliography"], "csl": found["csl"], "source": found["source"],
            "inline_references": config.inline_references(root, chosen) if not found["bibliography"] else None,
            "written": False}
    if earlier and files and earlier["files"] != chosen:
        info["differs_from"] = earlier["source"]
    if not write:
        return info
    if found is earlier:
        (root / config.CONFIG).write_text(earlier["text"])
    else:
        config.write(root, chosen, found["bibliography"], found["csl"])
    ensure_ignored(root)
    return {**info, "written": True}


def _yield_config(root: Path, ref: str) -> None:
    """Make way for a round branch that carries scrivmark.toml: git will not
    check it out over an uncommitted copy, even an identical one."""
    local = root / config.CONFIG
    if not local.exists() or gitutil.ok(root, "ls-files", "--error-unmatch", "--", config.CONFIG):
        return
    theirs = gitutil.show(root, ref, config.CONFIG)
    if theirs is None:
        return
    if local.read_text() != theirs:
        raise ScrivError(f"Your uncommitted {config.CONFIG} differs from the one {ref} carries. Move it aside "
                         "(or delete it) and open the round again; the round brings its own.")
    local.unlink()


def _restore_config(root: Path, ref: str) -> None:
    """After leaving a round branch for one without the config, put the round's
    scrivmark.toml back (uncommitted), so the next round can start."""
    if not (root / config.CONFIG).exists() and (text := gitutil.show(root, ref, config.CONFIG)) is not None:
        (root / config.CONFIG).write_text(text)
    ensure_ignored(root)


def start(root: Path, author_branch: str | None = None, statement: str | None = None,
          reviewer: config.Identity | None = None) -> Review:
    ms = config.load(root)
    _require_clean(root, "start a review")
    config.acting_as(root, None)
    tracked = set(gitutil.git(root, "ls-files", "-z", "--", *ms.sources).split("\0"))
    untracked = [f for f in ms.sources if f not in tracked]
    if untracked:
        raise ScrivError(f"These sources are not tracked by git, so edits to them would not be recorded: "
                         f"{', '.join(untracked)}. Commit them (or stop ignoring them) first.")
    me = reviewer or config.identity()
    branch = author_branch or gitutil.current_branch(root)
    if branch == "HEAD" or not gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
        raise ScrivError(f"{branch!r} is not a local branch, so the reviewed work would have nowhere to merge. "
                         "Check out the author's branch (or name it with --author-branch) first.")
    if branch.startswith(BRANCH_PREFIX):
        raise ScrivError(f"{branch} is already a review branch; check out the author's branch first.")
    base = gitutil.rev(root, f"refs/heads/{branch}")
    rid = _new_id(root, me.initials)
    gitutil.git(root, "checkout", "-q", "-b", f"{BRANCH_PREFIX}{rid}", base)
    stmt = statement or config.default_statement(me)
    rec = record.blank(rid, {"name": me.name, "initials": me.initials}, branch, base, stmt)
    record.save(root, rec)
    _commit(root, f"scrivmark: start review round {rid}",
            [str(record.path(root, rid).relative_to(root)), *(f for f in (config.CONFIG, ".gitignore")
                                                              if (root / f).exists())])
    return Review(root, rec, ms)


def request(root: Path, reviewer_name: str, reviewer_initials: str, github_user: str | None = None) -> Review:
    rv = start(root, reviewer=config.Identity(reviewer_name, reviewer_initials),
               statement=f"Review requested from {reviewer_name}.")
    if gitutil.has_remote(root):
        gitutil.git(root, "push", "-q", "-u", "origin", f"{BRANCH_PREFIX}{rv.id}")
        if github_user and _gh() and _on_github(root):
            subprocess.run(
                ["gh", "pr", "create", "--draft", "--base", rv.rec["round"]["author_branch"],
                 "--head", f"{BRANCH_PREFIX}{rv.id}", "--title", f"Review request: {rv.id}",
                 "--reviewer", github_user,
                 "--body", f"Review requested. Open it with `scrivmark open {rv.id}`."],
                cwd=root, check=False,
            )
    gitutil.git(root, "checkout", "-q", rv.rec["round"]["author_branch"])
    _restore_config(root, f"{BRANCH_PREFIX}{rv.id}")
    return rv


def abandon(root: Path, force: bool = False) -> dict:
    """Give up a round that has not been returned, and go back to the author
    branch. Its last state is committed and tagged scrivmark/<id>/abandoned,
    so nothing is lost; kept comments or edits need `force`."""
    with transaction(root):
        rv = active(root)
        if rv.status != "open":
            raise ScrivError(f"Round {rv.id} is {rv.status}: the authors have it, so abandoning it here would leave "
                             "them a round nobody finishes. Close it instead (`scrivmark close --force` is recorded).")
        require_turn(rv)
        rnd = rv.rec["round"]
        edited = sorted({e[1] for e in gitutil.name_status(root, rnd["base"], _working_tree(root))
                         if not is_managed(e[1])})
        kept, left = len(rv.rec["threads"]), len(drafts(rv))
        if (kept or edited) and not force:
            raise ScrivError(f"Round {rv.id} holds {kept} kept comment(s) and edits to {len(edited)} file(s). "
                             "`scrivmark abandon --force` sets them aside (tagged, so git can bring them back).")
        _commit_work(root, f"scrivmark: abandon review round {rv.id}")
        _commit(root, f"scrivmark: abandon review round {rv.id}", [rv.record_rel])
        tag = TAG.format(id=rv.id, which="abandoned")
        tip = gitutil.rev(root, "HEAD")
        if not gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/tags/{tag}"):
            gitutil.git(root, "tag", tag, tip)
        elif gitutil.rev(root, f"refs/tags/{tag}^{{commit}}") != tip:
            raise ScrivError(f"Tag {tag} already names another commit; check it with git before abandoning again.")
        gitutil.git(root, "checkout", "-q", rnd["author_branch"])
        _restore_config(root, tip)
        gitutil.git(root, "branch", "-q", "-D", f"{BRANCH_PREFIX}{rv.id}")
    return {"round": rv.id, "tag": tag, "commit": tip, "branch": rnd["author_branch"], "threads": kept,
            "drafts": left, "files": edited,
            "on_origin": gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{BRANCH_PREFIX}{rv.id}")}


def return_round(root: Path, push: bool = True, statement: str | None = None, agent: str | None = None) -> dict:
    """Return a round, or finish publishing one already returned. From a round
    the authors have responded to, send it back to them."""
    with transaction(root):
        rv = active(root)
        sending_back = rv.status == "responded"
        if sending_back:
            require_turn(rv)
            if statement is not None:
                raise ScrivError("The statement was set when the round was first returned.")
            info = _hand_over(root, rv, "author", agent)
    if sending_back:
        return _publish_handover(root, info, push)
    with transaction(root):
        rv = active(root)
        if rv.status == "returned" and statement is not None:
            raise ScrivError("This round is already returned; its statement can no longer change.")
        if rv.status == "returned":
            _seal_return(root, rv)
            info = {"round": rv.id, "returned": rv.rec["round"]["returned"], "changes": len(rv.rec["changes"]),
                    "threads": len(rv.rec["threads"]), "orphans": [], "resumed": True}
        else:
            info = _return_locked(root, statement, agent)
    return _publish_return(root, info, push)


def _return_locked(root: Path, statement: str | None = None, agent: str | None = None) -> dict:
    rv = active(root)
    if rv.status != "open":
        raise ScrivError(f"Round {rv.id} is {rv.status}, not open.")
    require_turn(rv)
    if statement is not None and not statement.strip():
        raise ScrivError("The statement needs some text.")
    left = drafts(rv)
    if left:
        raise ScrivError(f"{len(left)} draft comment(s) still need keeping or dropping before you return the round.")
    _commit_work(root, f"scrivmark: reviewer edits for {rv.id}")
    returned = gitutil.rev(root, "HEAD")
    rv.rec["changes"] = catalogue(root, rv.ms, rv.rec["round"]["base"], returned)
    orphans = rebase_anchors(rv, returned)
    at = record.now()
    rv.rec["round"].update({"status": "returned", "returned": returned, "returned_at": at,
                           "integrated": rv.rec["round"]["base"],
                           "handovers": [{"to": "author", "at": at, "by": rv.rec["round"]["reviewer"]["initials"],
                                          **({"agent": agent} if agent else {})}]})
    if statement is not None:
        rv.rec["round"]["statement"] = statement.strip()
    archive_asks(rv)
    record.save(root, rv.rec)
    _seal_return(root, rv)
    return {"round": rv.id, "returned": returned, "changes": len(rv.rec["changes"]),
            "threads": len(rv.rec["threads"]), "orphans": orphans, "pushed": False, "pr": False}


def _seal_return(root: Path, rv: Review) -> None:
    """The local half of a return, safe to repeat: the returned record is
    committed and both snapshot tags name the commits it records. A retry after
    a failure part-way finishes it without recomputing anything."""
    if rv.record_rel in gitutil.dirty_paths(root):
        _commit(root, f"scrivmark: return review round {rv.id}", [rv.record_rel])
    for which in ("base", "returned"):
        tag, sha = TAG.format(id=rv.id, which=which), rv.rec["round"][which]
        if gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/tags/{tag}"):
            if gitutil.rev(root, tag) != sha:
                raise ScrivError(f"Tag {tag} points at another commit than this round records ({sha[:10]}); "
                                 "check it with git before returning again.")
        else:
            gitutil.git(root, "tag", tag, sha)


def respond(root: Path, push: bool = True, agent: str | None = None) -> dict:
    """The authors hand the round back to the reviewer to sign off: every
    change decided, their replies made, their own edits committed."""
    from .review import changes as list_changes

    with transaction(root):
        rv = active(root)
        if rv.status == "responded" and (rv.rec["round"].get("handovers") or [{}])[-1].get("to") == "reviewer":
            if rv.record_rel in gitutil.dirty_paths(root):
                _commit(root, f"scrivmark: {rv.id} back to the reviewer", [rv.record_rel])
            info = _handover_info(rv, "reviewer")
            return _publish_handover(root, info, push)
        if rv.status != "returned":
            raise ScrivError(f"Round {rv.id} is {rv.status}; the authors respond to a returned round.")
        require_turn(rv)
        _commit_work(root, f"scrivmark: author responses for {rv.id}")
        _require_clean(root, "bring in the latest author branch")
        integrate(rv)
        rv.reload()
        pending = [c["id"] for c in list_changes(rv) if c["state"] == "pending"]
        if pending:
            raise ScrivError(f"{len(pending)} change(s) still need accepting or rejecting first: {', '.join(pending)}")
        info = _hand_over(root, rv, "reviewer", agent)
    return _publish_handover(root, info, push)


def _hand_over(root: Path, rv: Review, to: str, agent: str | None) -> dict:
    """Pass a returned round between the authors and the reviewer, recorded."""

    rnd = rv.rec["round"]
    by = rnd["author"]["initials"] if to == "reviewer" and rnd["author"] else rnd["reviewer"]["initials"]
    if to == "author":
        _refuse_signoff_edits(rv)
    _commit_work(root, f"scrivmark: {'author responses' if to == 'reviewer' else 'contested changes'} for {rv.id}")
    head = gitutil.rev(root, "HEAD")
    rebase_anchors(rv, head, snapshots_only=True)
    at = record.now()
    hs = list(rnd.get("handovers") or ([{"to": "author", "at": rnd["returned_at"]}] if rnd.get("returned_at") else []))
    hs.append({"to": to, "at": at, "by": by, "commit": head, **({"agent": agent} if agent else {})})
    rnd.update({"status": "responded" if to == "reviewer" else "returned", "handovers": hs})
    if to == "reviewer":
        rnd["responded_at"] = at
    archive_asks(rv)
    record.save(root, rv.rec)
    _commit(root, f"scrivmark: {rv.id} {'back to the reviewer' if to == 'reviewer' else 'back to the authors'}",
            [rv.record_rel])
    return _handover_info(rv, to)


def _refuse_signoff_edits(rv: Review) -> None:
    edits = signoff_edits(rv)
    if edits:
        raise ScrivError("These files were edited during sign-off:\n  " + "\n  ".join(edits[:10]) +
                         "\nWhile you sign off the text is the authors', so these edits would reach them (or the "
                         "merge) untracked. Undo them (`git restore <file>`) and comment instead; an edit of your own "
                         "belongs in a new round.")


def _handover_info(rv: Review, to: str) -> dict:
    from .review import changes as list_changes

    cs = list_changes(rv)
    ts = threads(rv)
    return {"round": rv.id, "to": to, "turn": len(rv.rec["round"].get("handovers") or []),
            "decided": {k: sum(1 for c in cs if c["state"] == k) for k in ("accepted", "rejected", "superseded")},
            "pending": [c["id"] for c in cs if c["state"] == "pending"],
            "open_threads": [t["id"] for t in ts if t["status"] == "open"]}


def _publish_handover(root: Path, info: dict, push: bool) -> dict:
    rv = active(root)
    branch = f"{BRANCH_PREFIX}{rv.id}"
    out = dict(info, pushed=False, pr=False)
    if not (push and gitutil.has_remote(root)):
        return out
    gitutil.git(root, "push", "-q", "-u", "origin", branch)
    out["pushed"] = True
    if _gh() and _on_github(root) and _run(root, "gh", "pr", "view", branch):
        d = info["decided"]
        note = (f"Back to the reviewer: {d['accepted']} accepted, {d['rejected']} rejected, "
                f"{len(info['open_threads'])} comment thread(s) still open. `scrivmark open {rv.id}` to sign off."
                if info["to"] == "reviewer" else
                f"Back to the authors: {len(info['pending'])} change(s) to decide again, "
                f"{len(info['open_threads'])} comment thread(s) open. `scrivmark open {rv.id}` to continue.")
        out["pr"] = _run(root, "gh", "pr", "comment", branch, "--body", note)
    return out


def _run(root: Path, *cmd: str) -> bool:
    return subprocess.run(list(cmd), cwd=root, capture_output=True).returncode == 0


def _publish_return(root: Path, info: dict, push: bool) -> dict:
    rv = active(root)
    branch = f"{BRANCH_PREFIX}{rv.id}"
    out = dict(info, pushed=False, pr=False, pr_error=None)
    if not (push and gitutil.has_remote(root)):
        return out
    tags = [TAG.format(id=rv.id, which=w) for w in ("base", "returned")]
    gitutil.git(root, "push", "-q", "-u", "origin", branch)
    gitutil.git(root, "push", "-q", "-f", "origin", *tags)
    out["pushed"] = True
    if _gh() and _on_github(root):
        body = (
            f"{rv.rec['round']['statement']}\n\n"
            f"{len(rv.rec['changes'])} changes and {len(rv.rec['threads'])} comments.\n\n"
            f"To work through them:\n\n```bash\nscrivmark open {rv.id}\n```\n"
        )
        if _run(root, "gh", "pr", "view", branch):
            _run(root, "gh", "pr", "ready", branch)
            out["pr"] = _run(root, "gh", "pr", "edit", branch, "--body", body)
        else:
            out["pr"] = _run(root, "gh", "pr", "create", "--base", rv.rec["round"]["author_branch"], "--head",
                             branch, "--title", f"Review round {rv.id}", "--body", body)
        if not out["pr"]:
            out["pr_error"] = "The branch is pushed, but the pull request could not be created or updated; run `scrivmark return` again, or open it on GitHub."
    return out


def _author_tip(root: Path, branch: str) -> str:
    local = gitutil.rev(root, branch)
    remote = f"origin/{branch}"
    if gitutil.has_remote(root) and gitutil.ok(root, "rev-parse", "--verify", remote):
        r = gitutil.rev(root, remote)
        if gitutil.ok(root, "merge-base", "--is-ancestor", local, r):
            return r
    return local


def integrate(rv: Review) -> dict:
    """Bring in the author branch if it has moved (merging it, or noting a merge
    already completed by hand), then clear decisions on accepted changes that
    the merge disturbed."""
    rnd = rv.rec["round"]
    tip = _author_tip(rv.root, rnd["author_branch"])
    if tip == rnd["integrated"]:
        return {"merged": False, "invalidated": []}
    merged = False
    if not gitutil.ok(rv.root, "merge-base", "--is-ancestor", tip, "HEAD"):
        proc = subprocess.run(["git", "merge", "--no-edit", "-q", tip], cwd=rv.root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ScrivError(
                f"Merging {rnd['author_branch']} into the round hit conflicts. Resolve them in the source, "
                f"commit, then run the command again.\n{proc.stdout}{proc.stderr}"
            )
        merged = True
    rv.reload()
    invalidated = []
    for c in rv.rec["changes"]:
        if c["decision"] == "accepted" and c["type"] != "file" and locate(rv, c) is None:
            undecide(c)
            invalidated.append(c["id"])
    rv.rec["round"]["integrated"] = tip
    record.save(rv.root, rv.rec)
    _commit(rv.root, f"scrivmark: record integrated author branch for {rv.id}", [rv.record_rel])
    return {"merged": merged, "invalidated": invalidated}


def open_round(root: Path, round_id: str, claim: bool = False) -> dict:
    """Check out a round, up to date with origin. The side whose turn it is
    takes it up (the first author to open a returned round is recorded as its
    author, and newer author-branch work is merged in); the other side only
    looks, and nothing is committed. `claim` takes the authors' part even as
    the reviewer, for one person playing both sides."""
    branch = f"{BRANCH_PREFIX}{round_id}"
    if gitutil.has_remote(root):
        gitutil.git(root, "fetch", "-q", "--tags", "origin", check=False)
    if gitutil.current_branch(root) != branch:
        _require_clean(root, "open a round")
        _yield_config(root, branch if gitutil.ok(root, "rev-parse", "--verify", f"refs/heads/{branch}")
                      else f"origin/{branch}")
        if gitutil.ok(root, "rev-parse", "--verify", f"refs/heads/{branch}"):
            gitutil.git(root, "checkout", "-q", branch)
        elif gitutil.ok(root, "rev-parse", "--verify", f"origin/{branch}"):
            gitutil.git(root, "checkout", "-q", "-b", branch, "--track", f"origin/{branch}")
        else:
            raise ScrivError(f"No round {round_id} here or on origin.")
    remote = f"origin/{branch}"
    if gitutil.has_remote(root) and gitutil.ok(root, "rev-parse", "--verify", remote) \
            and not gitutil.ok(root, "merge-base", "--is-ancestor", remote, "HEAD"):
        _require_clean(root, "update the round from origin")
        proc = subprocess.run(["git", "merge", "-q", "--ff-only", remote], cwd=root, capture_output=True, text=True)
        if proc.returncode != 0:
            raise ScrivError(f"The round here and on origin have diverged; settle it with git first.\n{proc.stderr}")
    with transaction(root):
        rv = active(root)
        result = {"round": rv.id, "status": rv.status, "merged": False, "invalidated": [],
                  "waiting": review.other_side(rv.rec, review.me(root)) and not claim}
        if rv.status == "returned" and not result["waiting"]:
            if rv.rec["round"]["author"] is None:
                me = config.identity(root)
                rv.rec["round"]["author"] = {"name": me.name, "initials": me.initials}
                record.save(root, rv.rec)
                _commit(root, f"scrivmark: {me.initials} opens review round {rv.id}", [rv.record_rel])
            _require_clean(root, "bring in the latest author branch")
            result.update(integrate(rv))
    return result


def close_round(root: Path, force: bool = False, push: bool = True, round_id: str | None = None) -> dict:
    """Close a returned round and merge it. Rerun after any failure, from the
    round branch or (naming the round) from the author branch, to finish."""
    with transaction(root):
        rv = active(root, round_id)
        if rv.status == "closed":
            if rv.record_rel in gitutil.dirty_paths(root):
                _commit(root, f"scrivmark: close review round {rv.id}", [rv.record_rel])
            info = {"round": rv.id, "forced": bool(rv.rec["round"].get("forced")), "invalidated": []}
        elif rv.status in ("returned", "responded"):
            require_turn(rv)
            info = _close_record(root, rv, force)
        else:
            raise ScrivError(f"Round {rv.id} is {rv.status}; only a returned round can be closed.")
    rv = active(root, rv.id)
    info["merged_into"] = _merge_closed(root, rv, push)
    info["decisions"] = {k: sum(1 for c in rv.rec["changes"] if c["decision"] == k)
                         for k in ("accepted", "rejected", "superseded")}
    return info


def _close_record(root: Path, rv: Review, force: bool) -> dict:
    from .review import changes as list_changes

    if rv.status == "responded":
        _refuse_signoff_edits(rv)
    _commit_work(root, f"scrivmark: {'author responses' if rv.status == 'returned' else 'contested changes'} for {rv.id}")
    integration = integrate(rv)
    cs = list_changes(rv)
    pending = [c["id"] for c in cs if c["state"] == "pending"]
    open_threads = [t["id"] for t in threads(rv) if t["status"] == "open"]
    if (pending or open_threads) and not force:
        parts = []
        if pending:
            parts.append(f"{len(pending)} change(s) still need accepting or rejecting: {', '.join(pending)}")
        if open_threads:
            parts.append(f"{len(open_threads)} comment thread(s) are still open: {', '.join(open_threads)}")
        raise ScrivError("Not ready to close.\n" + "\n".join(parts) + "\nUse --force to close anyway (recorded).")
    for c, live in zip(rv.rec["changes"], cs):
        if not c["decision"] and live["state"] == "superseded":
            c["decision"] = "superseded"
    rv.rec["round"].update({"status": "closed", "closed": record.now()})
    if force and (pending or open_threads):
        rv.rec["round"]["forced"] = True
    archive_asks(rv)
    record.save(root, rv.rec)
    _commit(root, f"scrivmark: close review round {rv.id}", [rv.record_rel])
    return {"round": rv.id, "forced": bool(rv.rec["round"].get("forced")), "invalidated": integration["invalidated"]}


def _merge_closed(root: Path, rv: Review, push: bool) -> str:
    """Merge a closed round into its author branch and publish it. Each step
    checks whether it has already happened, so a rerun finishes the job."""
    target = rv.rec["round"]["author_branch"]
    branch = f"{BRANCH_PREFIX}{rv.id}"
    head = gitutil.rev(root, branch)
    remote = push and gitutil.has_remote(root)
    if not gitutil.ok(root, "merge-base", "--is-ancestor", head, target):
        tip = _author_tip(root, target)
        if tip != rv.rec["round"]["integrated"] and not gitutil.ok(root, "merge-base", "--is-ancestor", tip, head):
            raise ScrivError(f"{target} has moved since the round was closed. Merge {target} into {branch}, "
                             "then run `scrivmark close` again.")
        if remote:
            gitutil.git(root, "push", "-q", "origin", branch)
        if remote and _gh() and _on_github(root) and _run(root, "gh", "pr", "view", branch):
            _run(root, "gh", "pr", "ready", branch)
            proc = subprocess.run(["gh", "pr", "merge", branch, "--merge", "--match-head-commit", head],
                                  cwd=root, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ScrivError(f"GitHub refused the merge. Fix the cause, then run `scrivmark close` again.\n{proc.stderr}")
            gitutil.git(root, "checkout", "-q", target)
            gitutil.git(root, "pull", "-q", "--ff-only", "origin", target)
        else:
            gitutil.git(root, "checkout", "-q", target)
            gitutil.git(root, "merge", "-q", "--no-ff", branch, "-m", f"Merge review round {rv.id}")
    elif gitutil.current_branch(root) != target:
        gitutil.git(root, "checkout", "-q", target)
    if remote:
        published = gitutil.ok(root, "rev-parse", "--verify", f"origin/{target}") and \
            gitutil.ok(root, "merge-base", "--is-ancestor", target, f"origin/{target}")
        if not published:
            proc = subprocess.run(["git", "push", "-q", "origin", target], cwd=root, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ScrivError(f"Merged locally, but pushing {target} failed. Fix the cause, then run "
                                 f"`scrivmark close {rv.id}` again.\n{proc.stderr}")
    return target


def list_rounds(root: Path) -> list[dict]:
    out = []
    for p in sorted((root / record.REVIEWS).glob("*.json")):
        try:
            rec = record.load(root, p.stem)
            out.append({"id": rec["round"]["id"], "status": rec["round"]["status"], "where": "this branch"})
        except ScrivError:
            continue
    seen = {r["id"] for r in out}
    for line in gitutil.git(root, "branch", "-a", "--format=%(refname:short)").splitlines():
        name = line.split("/", 1)[1] if line.startswith("origin/") else line
        if name.startswith(BRANCH_PREFIX) and name[len(BRANCH_PREFIX):] not in seen:
            rid = name[len(BRANCH_PREFIX):]
            rec_text = gitutil.show(root, line, f"{record.REVIEWS}/{rid}.json")
            status = "unknown"
            if rec_text:
                import json
                status = json.loads(rec_text)["round"]["status"]
            out.append({"id": rid, "status": status, "where": line})
            seen.add(rid)
    return out


# ---------------------------------------------------------------- preview


def _check(kind: str, ok: bool, text: str, **extra) -> dict:
    return {"kind": kind, "ok": ok, "text": text, **extra}


def _working_tree(root: Path) -> str:
    """The tree `_commit_work` would commit (index plus tracked-file edits),
    built in a throwaway copy of the index; no ref, file or index of the
    repository changes (it adds loose objects, which git prunes)."""
    import os
    import tempfile

    real = Path(gitutil.git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    with tempfile.TemporaryDirectory() as tmp:
        idx = Path(tmp) / "index"
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))
        steps = [["add", "-u"], ["write-tree"]]
        if real.exists():
            shutil.copy(real, idx)
        else:
            steps.insert(0, ["read-tree", "HEAD"])
        for args in steps:
            proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=env)
            if proc.returncode != 0:
                raise ScrivError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
        return proc.stdout.strip()


def _publication(root: Path, rv: Review, branch: str) -> dict:
    remote = gitutil.has_remote(root)
    on_github = remote and _on_github(root)
    pub = {"remote": gitutil.git(root, "remote", "get-url", "origin").strip() if remote else None,
           "branch": branch, "github": bool(on_github), "gh": _gh(), "pr": None}
    if on_github and pub["gh"]:
        try:
            pub["pr"] = subprocess.run(["gh", "pr", "view", branch], cwd=root, capture_output=True,
                                       timeout=20).returncode == 0
        except subprocess.TimeoutExpired:
            pub["pr"] = None
    return pub


def _stray(root: Path) -> list[str]:
    return [p for p in gitutil.untracked(root) if not is_managed(p)]


def _ids(xs: list[str]) -> str:
    return ", ".join(xs[:8]) + (f" and {len(xs) - 8} more" if len(xs) > 8 else "")


def preflight(root: Path, round_id: str | None = None, statement: str | None = None, push: bool = True,
              force: bool = False, step: str | None = None, agent: str | None = None) -> dict:
    """What the next transition of the round would do, with the options the
    command would be given. It changes no file, ref, tag or index; computing
    the change count may leave a few unreferenced git objects, which git
    prunes in its own time.

    Returns {"transition": "return"|"close"|None, "ready": bool, "round",
    "checks": [{kind, ok, text}], "blockers": [{kind, text, ...}],
    "notes": [...], "will": [...], ...} plus transition-specific fields."""
    rv = active(root, round_id)
    rv.rec["round"]
    base = {"round": rv.id, "status": rv.status}
    if rv.status == "closed":
        return {**base, "transition": None, "ready": False, "checks": [], "blockers": [],
                "notes": ["closed"], "will": []}
    if rv.status == "open":
        out = _preflight_return(root, rv, statement, push, agent)
    elif rv.status == "returned" and step == "respond":
        out = _preflight_respond(root, rv, push)
    elif rv.status == "responded" and step == "return":
        out = _preflight_send_back(root, rv, push)
    else:
        out = _preflight_close(root, rv, push)
        if force:
            for c in out["checks"]:
                if not c["ok"] and c.get("forceable"):
                    c["ok"] = True
                    out["notes"].append(f"--force closes anyway, and the record says so: {c['text']}")
    if review.other_side(rv.rec, review.me(root)):
        out["checks"].insert(0, _check("turn", False, "It is the other side's turn; this is read-only for you"))
    out["blockers"] = [{k: v for k, v in c.items() if k != "ok"} for c in out["checks"] if not c["ok"]]
    out["ready"] = not out["blockers"]
    return {**base, **out}


def _preflight_return(root: Path, rv: Review, restated: str | None = None, push: bool = True,
                      agent: str | None = None) -> dict:
    rnd = rv.rec["round"]
    ms = rv.ms
    checks, notes = [], []
    left = drafts(rv)
    ids = [d["id"] for d in left]
    checks.append(_check("drafts", not left,
                         f"{len(left)} draft comment(s) still to keep or drop: {_ids(ids)}" if left
                         else "No drafts waiting", count=len(left), ids=ids))
    stray = _stray(root)
    checks.append(_check("untracked", not stray,
                         f"{len(stray)} new file(s) not in git (`git add` or delete them): {_ids(stray)}" if stray
                         else "No stray untracked files", files=stray))
    statement = rnd["statement"] if restated is None else restated
    checks.append(_check("statement", bool(statement.strip()), "Statement is set" if statement.strip()
                         else "The statement is empty"))
    if unnamed := review.unnamed_agents(rv, statement, agent):
        notes.append(f"The statement does not name {', '.join(unnamed)}, who worked on this round. If it should, "
                     "add --statement '...' to restate it (it cannot change after the return)")
    tree = _working_tree(root)
    entries = [e for e in gitutil.name_status(root, rnd["base"], tree) if not is_managed(e[1])]
    files = sorted({e[1] for e in entries})
    cs = catalogue(root, ms, rnd["base"], tree)
    ts = threads(rv)
    lost = [t["id"] for t in ts if t["where"]["state"] != "ok"]
    ambiguous = [t["id"] for t in ts if t["where"]["state"] != "ok" and t["where"].get("why") == "ambiguous"]
    if lost:
        notes.append(f"{len(lost)} comment(s) have lost their passage ({len(ambiguous)} ambiguous): {_ids(lost)}")
    if not cs:
        notes.append("No edits will be recorded; only comments go back")
    pub = _publication(root, rv, f"{BRANCH_PREFIX}{rv.id}")
    if not push:
        pub["remote"] = None
    will = [f"Commit reviewer edits ({len(files)} file(s)) and record {len(cs)} change(s), {len(ts)} comment thread(s)",
            f"Tag scrivmark/{rv.id}/base and /returned"]
    if pub["remote"]:
        will.append(f"Push {pub['branch']} to origin ({pub['remote']})")
        if pub["github"] and pub["gh"]:
            will.append("Update the pull request" if pub["pr"] else "Open a pull request")
        elif pub["github"]:
            notes.append("`gh` is not installed, so no pull request will be made")
    else:
        will.append("Nothing is pushed" + (" (--no-push)" if not push else " (no remote)"))
    return {"transition": "return", "checks": checks, "notes": notes, "will": will,
            "files": files, "changes": len(cs), "threads": len(ts), "lost_comments": lost,
            "statement": statement, "publication": pub}


def _preflight_send_back(root: Path, rv: Review, push: bool = True) -> dict:
    from .review import changes as list_changes
    cs = list_changes(rv)
    pending = [c["id"] for c in cs if c["state"] == "pending"]
    open_threads = [t["id"] for t in threads(rv) if t["status"] == "open"]
    notes = []
    if pending:
        notes.append(f"{len(pending)} contested change(s) go back for the authors to decide again: {_ids(pending)}")
    if open_threads:
        notes.append(f"{len(open_threads)} comment thread(s) open: {_ids(open_threads)}")
    edits = signoff_edits(rv)
    checks = [_check("signoff_edits", not edits, f"{len(edits)} file(s) edited by hand during sign-off, which would "
                     f"reach the authors untracked (`git restore` them): {_ids(edits)}" if edits
                     else "No untracked edits", files=edits)]
    pub = _publication(root, rv, f"{BRANCH_PREFIX}{rv.id}")
    if not push:
        pub["remote"] = None
    will = ["Hand the round back to the authors",
            f"Push to origin ({pub['remote']}) and note it on the pull request" if pub["remote"]
            else "Nothing is pushed" + (" (--no-push)" if not push else " (no remote)")]
    return {"transition": "return", "checks": checks, "notes": notes, "will": will, "pending": pending,
            "open_threads": open_threads, "publication": pub}


def _preflight_respond(root: Path, rv: Review, push: bool = True) -> dict:
    from .review import changes as list_changes
    cs = list_changes(rv)
    pending = [c["id"] for c in cs if c["state"] == "pending"]
    open_threads = [t["id"] for t in threads(rv) if t["status"] == "open"]
    stray = _stray(root)
    checks = [
        _check("pending", not pending, f"{len(pending)} change(s) still need accepting or rejecting: {_ids(pending)}"
               if pending else "Every change is decided", ids=pending),
        _check("untracked", not stray, f"{len(stray)} new file(s) not in git (`git add` or delete them): {_ids(stray)}"
               if stray else "No stray untracked files", files=stray),
    ]
    notes = []
    if open_threads:
        notes.append(f"{len(open_threads)} comment thread(s) will stay open for the reviewer: {_ids(open_threads)}")
    uncommitted = [p for p in gitutil.dirty_paths(root) if not is_managed(p)]
    if uncommitted:
        notes.append(f"{len(uncommitted)} uncommitted edit(s) will be committed as author responses")
    pub = _publication(root, rv, f"{BRANCH_PREFIX}{rv.id}")
    if not push:
        pub["remote"] = None
    will = ["Commit your edits as author responses", "Bring in the author branch if it has moved",
            "Hand the round to the reviewer to sign off"]
    will.append(f"Push to origin ({pub['remote']}) and note it on the pull request" if pub["remote"]
                else "Nothing is pushed" + (" (--no-push)" if not push else " (no remote)"))
    return {"transition": "respond", "checks": checks, "notes": notes, "will": will,
            "open_threads": open_threads, "publication": pub}


def _preflight_close(root: Path, rv: Review, push: bool = True) -> dict:
    rnd = rv.rec["round"]
    from .review import changes as list_changes
    checks, notes = [], []
    cs = list_changes(rv)
    pending = [c["id"] for c in cs if c["state"] == "pending"]
    open_threads = [t["id"] for t in threads(rv) if t["status"] == "open"]
    checks.append(_check("pending", not pending,
                         f"{len(pending)} change(s) still need accepting or rejecting: {_ids(pending)}" if pending
                         else "Every change is decided", ids=pending, forceable=True))
    checks.append(_check("open_threads", not open_threads,
                         f"{len(open_threads)} comment thread(s) still open: {_ids(open_threads)}" if open_threads
                         else "Every comment thread is settled", ids=open_threads, forceable=True))
    stray = _stray(root)
    checks.append(_check("untracked", not stray,
                         f"{len(stray)} new file(s) not in git (`git add` or delete them): {_ids(stray)}" if stray
                         else "No stray untracked files", files=stray))
    target = rnd["author_branch"]
    moved = False
    if gitutil.ok(root, "rev-parse", "--verify", "-q", f"refs/heads/{target}"):
        moved = _author_tip(root, target) != rnd["integrated"]
    else:
        notes.append(f"Author branch {target} does not exist locally")
    if moved:
        notes.append(f"{target} has moved since the round; closing merges it in first, and may undecide "
                     "accepted changes it disturbs (the counts above are before that merge)")
    if pending or open_threads:
        notes.append("`scrivmark close --force` closes anyway and records that it was forced")
    if rv.status == "returned":
        notes.append("Or hand it back to the reviewer to sign off: `scrivmark respond`"
                     + (f" (once {len(pending)} change(s) are decided)" if pending else ""))
    else:
        notes.append("You are signing off. Close it, or send it back to the authors with `scrivmark return`"
                     + (f" ({len(pending)} contested change(s) are theirs to decide again)" if pending else ""))
    decisions = {k: sum(1 for c in cs if c["state"] == k) for k in ("accepted", "rejected", "superseded")}
    if rv.status == "responded":
        edits = signoff_edits(rv)
        checks.append(_check("signoff_edits", not edits, f"{len(edits)} file(s) edited by hand during sign-off, "
                             f"which would be merged untracked (`git restore` them): {_ids(edits)}" if edits
                             else "No untracked edits", files=edits))
    else:
        uncommitted = [p for p in gitutil.dirty_paths(root) if not is_managed(p)]
        if uncommitted:
            notes.append(f"{len(uncommitted)} uncommitted edit(s) will be committed as author responses")
    pub = _publication(root, rv, f"{BRANCH_PREFIX}{rv.id}")
    if not push:
        pub["remote"] = None
    will = ["Record the decisions and close the round",
            f"Merge {BRANCH_PREFIX}{rv.id} into {target}"]
    if pub["remote"]:
        will.append(f"Push to origin ({pub['remote']})" + (" and merge the pull request"
                    if pub["github"] and pub["gh"] and pub["pr"] else ""))
    else:
        will.append("Nothing is pushed" + (" (--no-push)" if not push else " (no remote)"))
    return {"transition": "close", "checks": checks, "notes": notes, "will": will,
            "decisions": decisions, "pending": pending, "open_threads": open_threads,
            "author_moved": moved, "merge_target": target,
            "push_target": f"origin/{target}" if pub["remote"] else None, "publication": pub}
