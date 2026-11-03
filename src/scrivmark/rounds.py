"""Round lifecycle: start, return, open, close, over plain git, with GitHub
pull requests when `gh` is available."""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import config, gitutil, record
from .errors import ScrivError
from .review import BRANCH_PREFIX, Review, active, catalogue, drafts, is_managed, locate, rebase_anchors, threads
from .state import ensure_ignored

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


def _gh() -> bool:
    return shutil.which("gh") is not None


def _on_github(root: Path) -> bool:
    return gitutil.has_remote(root) and "github.com" in gitutil.git(root, "remote", "get-url", "origin")


def init(root: Path, files: list[str] | None = None) -> dict:
    found = config.detect(root)
    chosen = files or found["files"]
    if not chosen:
        raise ScrivError("No Markdown or Quarto files found; name them: `scrivmark init paper.md`")
    config.write(root, chosen, found["bibliography"], found["csl"])
    ensure_ignored(root)
    _commit(root, "scrivmark: add manuscript config", [config.CONFIG, ".gitignore"])
    return {"files": chosen, "bibliography": found["bibliography"], "csl": found["csl"]}


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


def return_round(root: Path, push: bool = True) -> dict:
    rv = active(root)
    if rv.status != "open":
        raise ScrivError(f"Round {rv.id} is {rv.status}, not open.")
    left = drafts(rv)
    if left:
        raise ScrivError(f"{len(left)} draft comment(s) still need keeping or dropping before you return the round.")
    _commit(root, f"scrivmark: reviewer edits for {rv.id}")
    returned = gitutil.rev(root, "HEAD")
    rv.rec["changes"] = catalogue(root, rv.ms, rv.rec["round"]["base"], returned)
    orphans = rebase_anchors(rv, returned)
    rv.rec["round"].update({"status": "returned", "returned": returned, "integrated": rv.rec["round"]["base"]})
    record.path(root, rv.id).write_text(record.dumps(rv.rec))
    _commit(root, f"scrivmark: return review round {rv.id}", [rv.record_rel])
    gitutil.git(root, "tag", "-f", TAG.format(id=rv.id, which="base"), rv.rec["round"]["base"])
    gitutil.git(root, "tag", "-f", TAG.format(id=rv.id, which="returned"), returned)
    pushed = pr = False
    if push and gitutil.has_remote(root):
        gitutil.git(root, "push", "-q", "-u", "origin", f"{BRANCH_PREFIX}{rv.id}")
        gitutil.git(root, "push", "-q", "-f", "origin", TAG.format(id=rv.id, which="base"), TAG.format(id=rv.id, which="returned"))
        pushed = True
        if _gh() and _on_github(root):
            body = (
                f"{rv.rec['round']['statement']}\n\n"
                f"{len(rv.rec['changes'])} changes and {len(rv.rec['threads'])} comments.\n\n"
                f"To work through them:\n\n```bash\nscrivmark open {rv.id}\n```\n"
            )
            existing = subprocess.run(["gh", "pr", "view", f"{BRANCH_PREFIX}{rv.id}"], cwd=root, capture_output=True)
            if existing.returncode == 0:
                subprocess.run(["gh", "pr", "ready", f"{BRANCH_PREFIX}{rv.id}"], cwd=root, capture_output=True)
                subprocess.run(["gh", "pr", "edit", f"{BRANCH_PREFIX}{rv.id}", "--body", body], cwd=root, capture_output=True)
            else:
                subprocess.run(
                    ["gh", "pr", "create", "--base", rv.rec["round"]["author_branch"], "--head",
                     f"{BRANCH_PREFIX}{rv.id}", "--title", f"Review round {rv.id}", "--body", body],
                    cwd=root, capture_output=True,
                )
            pr = True
    return {"round": rv.id, "returned": returned, "changes": len(rv.rec["changes"]),
            "threads": len(rv.rec["threads"]), "orphans": orphans, "pushed": pushed, "pr": pr}


def _author_tip(root: Path, branch: str) -> str:
    local = gitutil.rev(root, branch)
    remote = f"origin/{branch}"
    if gitutil.has_remote(root) and gitutil.ok(root, "rev-parse", "--verify", remote):
        r = gitutil.rev(root, remote)
        if gitutil.ok(root, "merge-base", "--is-ancestor", local, r):
            return r
    return local


def integrate(rv: Review) -> dict:
    """Merge the author branch into the round if it has moved; decisions on
    changes the merge disturbed are cleared."""
    rnd = rv.rec["round"]
    tip = _author_tip(rv.root, rnd["author_branch"])
    if tip == rnd["integrated"] or gitutil.ok(rv.root, "merge-base", "--is-ancestor", tip, "HEAD"):
        if tip != rnd["integrated"]:
            rnd["integrated"] = tip
            record.path(rv.root, rv.id).write_text(record.dumps(rv.rec))
            _commit(rv.root, f"scrivmark: record integrated author branch for {rv.id}", [rv.record_rel])
        return {"merged": False, "invalidated": []}
    proc = subprocess.run(["git", "merge", "--no-edit", "-q", tip], cwd=rv.root, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ScrivError(
            f"Merging {rnd['author_branch']} into the round hit conflicts. Resolve them in the source, "
            f"commit, then run the command again.\n{proc.stdout}{proc.stderr}"
        )
    rv.reload()
    invalidated = []
    for c in rv.rec["changes"]:
        if c["decision"] == "accepted" and c["type"] != "file" and locate(rv, c) is None:
            c.update({"decision": None, "by": None, "at": None})
            invalidated.append(c["id"])
    rv.rec["round"]["integrated"] = tip
    record.path(rv.root, rv.id).write_text(record.dumps(rv.rec))
    _commit(rv.root, f"scrivmark: record integrated author branch for {rv.id}", [rv.record_rel])
    return {"merged": True, "invalidated": invalidated}


def open_round(root: Path, round_id: str) -> dict:
    branch = f"{BRANCH_PREFIX}{round_id}"
    if gitutil.has_remote(root):
        gitutil.git(root, "fetch", "-q", "--tags", "origin", check=False)
    if gitutil.current_branch(root) != branch:
        _require_clean(root, "open a round")
        if gitutil.ok(root, "rev-parse", "--verify", f"refs/heads/{branch}"):
            gitutil.git(root, "checkout", "-q", branch)
            if gitutil.ok(root, "rev-parse", "--verify", f"origin/{branch}"):
                gitutil.git(root, "merge", "-q", "--ff-only", f"origin/{branch}", check=False)
        elif gitutil.ok(root, "rev-parse", "--verify", f"origin/{branch}"):
            gitutil.git(root, "checkout", "-q", "-b", branch, "--track", f"origin/{branch}")
        else:
            raise ScrivError(f"No round {round_id} here or on origin.")
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


def close_round(root: Path, force: bool = False, push: bool = True) -> dict:
    from .review import changes as list_changes

    rv = active(root)
    if rv.status != "returned":
        raise ScrivError(f"Round {rv.id} is {rv.status}; only a returned round can be closed.")
    _commit(root, f"scrivmark: author responses for {rv.id}")
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
    record.path(root, rv.id).write_text(record.dumps(rv.rec))
    _commit(root, f"scrivmark: close review round {rv.id}", [rv.record_rel])
    head = gitutil.rev(root, "HEAD")
    target = rv.rec["round"]["author_branch"]
    tip = _author_tip(root, target)
    if tip != rv.rec["round"]["integrated"]:
        raise ScrivError(f"{target} moved while closing; run `scrivmark close` again.")
    branch = f"{BRANCH_PREFIX}{rv.id}"
    merged_remotely = False
    if push and gitutil.has_remote(root):
        gitutil.git(root, "push", "-q", "origin", branch)
        if _gh() and _on_github(root) and subprocess.run(["gh", "pr", "view", branch], cwd=root, capture_output=True).returncode == 0:
            subprocess.run(["gh", "pr", "ready", branch], cwd=root, capture_output=True)
            gitutil.git(root, "checkout", "-q", target)
            proc = subprocess.run(
                ["gh", "pr", "merge", branch, "--merge", "--match-head-commit", head],
                cwd=root, capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise ScrivError(f"GitHub refused the merge:\n{proc.stderr}")
            gitutil.git(root, "pull", "-q", "--ff-only", "origin", target)
            merged_remotely = True
    if not merged_remotely:
        gitutil.git(root, "checkout", "-q", target)
        gitutil.git(root, "merge", "-q", "--no-ff", branch, "-m", f"Merge review round {rv.id}")
        if push and gitutil.has_remote(root):
            gitutil.git(root, "push", "-q", "origin", target)
    return {"round": rv.id, "merged_into": target, "forced": bool(rv.rec["round"].get("forced")),
            "invalidated": integration["invalidated"],
            "decisions": {k: sum(1 for c in rv.rec["changes"] if c["decision"] == k)
                          for k in ("accepted", "rejected", "superseded")}}


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
