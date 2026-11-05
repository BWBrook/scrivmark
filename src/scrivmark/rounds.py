"""Round lifecycle: start, return, open, close, over plain git, with GitHub
pull requests when `gh` is available."""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import config, gitutil, record
from .errors import ScrivError
from .review import BRANCH_PREFIX, Review, active, archive_asks, catalogue, drafts, is_managed, locate, rebase_anchors, threads
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
    while gitutil.ok(root, "rev-parse", "--verify", f"refs/heads/{BRANCH_PREFIX}{rid}") or record.path(root, rid).exists():
        n += 1
        rid = f"{stem}-{n}"
    return rid


def _commit(root: Path, message: str, paths: list[str] | None = None) -> bool:
    gitutil.git(root, "add", "-A", *(["--", *paths] if paths else []))
    if gitutil.ok(root, "diff", "--cached", "--quiet"):
        return False
    gitutil.git(root, "commit", "-q", "-m", message)
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
    found = config.detect(root)
    chosen = files or found["files"]
    if not chosen:
        raise ScrivError("No Markdown, Quarto or LaTeX files found; name them: `scrivmark init paper.md`")
    missing = [f for f in chosen if not (root / f).exists()]
    if missing:
        raise ScrivError(f"These files do not exist: {', '.join(missing)}")
    if not write:
        return {"files": chosen, "bibliography": found["bibliography"], "csl": found["csl"],
                "source": found["source"], "written": False}
    if not gitutil.ok(root, "diff", "--cached", "--quiet"):
        raise ScrivError("Other changes are staged in git. Commit or unstage them first, so the setup commit holds only scrivmark's files.")
    config.write(root, chosen, found["bibliography"], found["csl"])
    ensure_ignored(root)
    _commit(root, "scrivmark: add manuscript config", [config.CONFIG, ".gitignore"])
    return {"files": chosen, "bibliography": found["bibliography"], "csl": found["csl"], "written": True}


def start(root: Path, author_branch: str | None = None, statement: str | None = None,
          reviewer: config.Identity | None = None) -> Review:
    ms = config.load(root)
    _require_clean(root, "start a review")
    me = reviewer or config.identity()
    branch = author_branch or gitutil.current_branch(root)
    if branch.startswith(BRANCH_PREFIX):
        raise ScrivError(f"{branch} is already a review branch; check out the author's branch first.")
    base = gitutil.rev(root, branch)
    rid = _new_id(root, me.initials)
    gitutil.git(root, "checkout", "-q", "-b", f"{BRANCH_PREFIX}{rid}", base)
    stmt = statement or config.default_statement(me)
    rec = record.blank(rid, {"name": me.name, "initials": me.initials}, branch, base, stmt)
    p = record.path(root, rid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(record.dumps(rec))
    _commit(root, f"scrivmark: start review round {rid}", [str(p.relative_to(root))])
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
    return rv


def return_round(root: Path, push: bool = True, statement: str | None = None) -> dict:
    """Return a round, or finish publishing one already returned."""
    with transaction(root):
        rv = active(root)
        if rv.status == "returned" and statement is not None:
            raise ScrivError("This round is already returned; its statement can no longer change.")
        if rv.status == "returned":
            info = {"round": rv.id, "returned": rv.rec["round"]["returned"], "changes": len(rv.rec["changes"]),
                    "threads": len(rv.rec["threads"]), "orphans": [], "resumed": True}
        else:
            info = _return_locked(root, statement)
    return _publish_return(root, info, push)


def _return_locked(root: Path, statement: str | None = None) -> dict:
    rv = active(root)
    if rv.status != "open":
        raise ScrivError(f"Round {rv.id} is {rv.status}, not open.")
    if statement is not None and not statement.strip():
        raise ScrivError("The statement needs some text.")
    left = drafts(rv)
    if left:
        raise ScrivError(f"{len(left)} draft comment(s) still need keeping or dropping before you return the round.")
    _commit_work(root, f"scrivmark: reviewer edits for {rv.id}")
    returned = gitutil.rev(root, "HEAD")
    rv.rec["changes"] = catalogue(root, rv.ms, rv.rec["round"]["base"], returned)
    orphans = rebase_anchors(rv, returned)
    rv.rec["round"].update({"status": "returned", "returned": returned, "returned_at": record.now(),
                           "integrated": rv.rec["round"]["base"]})
    if statement is not None:
        rv.rec["round"]["statement"] = statement.strip()
    archive_asks(rv)
    record.path(root, rv.id).write_text(record.dumps(rv.rec))
    _commit(root, f"scrivmark: return review round {rv.id}", [rv.record_rel])
    gitutil.git(root, "tag", "-f", TAG.format(id=rv.id, which="base"), rv.rec["round"]["base"])
    gitutil.git(root, "tag", "-f", TAG.format(id=rv.id, which="returned"), returned)
    return {"round": rv.id, "returned": returned, "changes": len(rv.rec["changes"]),
            "threads": len(rv.rec["threads"]), "orphans": orphans, "pushed": False, "pr": False}


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
            c.update({"decision": None, "by": None, "at": None})
            invalidated.append(c["id"])
    rv.rec["round"]["integrated"] = tip
    record.path(rv.root, rv.id).write_text(record.dumps(rv.rec))
    _commit(rv.root, f"scrivmark: record integrated author branch for {rv.id}", [rv.record_rel])
    return {"merged": merged, "invalidated": invalidated}


def open_round(root: Path, round_id: str) -> dict:
    branch = f"{BRANCH_PREFIX}{round_id}"
    if gitutil.has_remote(root):
        gitutil.git(root, "fetch", "-q", "--tags", "origin", check=False)
    if gitutil.current_branch(root) != branch:
        _require_clean(root, "open a round")
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
    rv = active(root)
    result = {"round": rv.id, "status": rv.status, "merged": False, "invalidated": []}
    if rv.status == "returned":
        if rv.rec["round"]["author"] is None:
            me = config.identity()
            rv.rec["round"]["author"] = {"name": me.name, "initials": me.initials}
            record.path(root, rv.id).write_text(record.dumps(rv.rec))
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
        elif rv.status == "returned":
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

    _commit_work(root, f"scrivmark: author responses for {rv.id}")
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
    record.path(root, rv.id).write_text(record.dumps(rv.rec))
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
