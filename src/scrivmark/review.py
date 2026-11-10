"""Operations on one review round: the change catalogue, decisions, anchoring,
threads and drafts. Lifecycle transitions live in `rounds`."""

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config, gitutil, latex, record
from .errors import Conflict, ScrivError
from .state import LOCAL, atomic_write, local_dir, read_json, revision, transaction
from .textdiff import diff_units, projection

BRANCH_PREFIX = "review/"
CONTEXT = 32
NEIGHBOURHOOD = 40


def is_managed(path: str) -> bool:
    return path.startswith((record.REVIEWS + "/", ".scrivmark/")) or path in (".gitignore", config.CONFIG)


@dataclass
class Review:
    root: Path
    rec: dict
    ms: config.Manuscript
    after: str | None = None

    @property
    def id(self) -> str:
        return self.rec["round"]["id"]

    @property
    def status(self) -> str:
        return self.rec["round"]["status"]

    @property
    def record_rel(self) -> str:
        return f"{record.REVIEWS}/{self.id}.json"

    def watched(self) -> list[str]:
        files = (set(self.ms.sources) | {c["file"] for c in self.rec["changes"]}
                 | {c["old_file"] for c in self.rec["changes"] if c.get("old_file")} | {self.record_rel})
        return sorted(files)

    def guarded(self) -> list[str]:
        """Everything a human or agent sees before acting: the manuscript, the
        record, drafts and asks. A revision of these is what `--expect` checks."""
        return self.watched() + [self.drafts_rel, self.asks_rel]

    def rev(self) -> str:
        return revision(self.root, self.guarded())

    @property
    def drafts_rel(self) -> str:
        return str((local_dir(self.root, self.id) / "drafts.json").relative_to(self.root))

    @property
    def asks_rel(self) -> str:
        return str((local_dir(self.root, self.id) / "asks.json").relative_to(self.root))

    def text(self, rel: str) -> str | None:
        p = self.root / rel
        return p.read_text() if p.exists() else None

    def reload(self) -> None:
        self.rec = record.load(self.root, self.id)


LIVE = ("open", "returned", "responded")
OPEN = ("open",)
RETURNED = ("returned",)
RESPONDED = ("responded",)


def acting(rec: dict) -> str | None:
    """Whose turn it is: the reviewer while the round is open or the authors
    have responded, the authors while it is returned. The phase decides the
    role, not the git identity: agents often run as their human's account."""
    return {"open": "reviewer", "responded": "reviewer", "returned": "author"}.get(rec["round"]["status"])


def handovers(rec: dict) -> list[dict]:
    """Each time the round changed hands after the review: to the authors at
    return, back to the reviewer at each response, and so on."""
    rnd = rec["round"]
    if "handovers" in rnd:
        return rnd["handovers"]
    return [{"to": "author", "at": rnd["returned_at"]}] if rnd.get("returned_at") else []


def news_window(rec: dict) -> tuple[str | None, str | None]:
    """The other side's last turn, [start, end): what is new to whoever's turn
    it is now. The whole review is new to the authors at the first return."""
    hs = handovers(rec)
    if not hs:
        return None, None
    return (hs[-2]["at"] if len(hs) > 1 else None), hs[-1]["at"]


def _ts(at: str) -> datetime:
    """A recorded time; one with no zone (a date from a signed source comment)
    is read as local time."""
    t = datetime.fromisoformat(at)
    return t if t.tzinfo else t.astimezone()


def _within(at: str | None, window: tuple[str | None, str | None]) -> bool:
    """In (start, end]: a turn ends at its handover, and anything recorded at
    the same instant as a handover belongs to the turn it ended (older records
    keep times to the second)."""
    start, end = window
    if not at or end is None:
        return False
    t = _ts(at)
    return (start is None or t > _ts(start)) and t <= _ts(end)


def _agent(agent: str | None) -> dict:
    return {"agent": agent} if agent else {}


def me(root: Path) -> str | None:
    try:
        return config.identity(root).name
    except ScrivError:
        return None


def other_side(rec: dict, name: str | None) -> bool:
    """Whether `name` is recognisably the side whose turn it is not: the
    reviewer while the authors hold the round, or the authors while the
    reviewer signs off. Nobody is when one person plays both sides."""
    rnd = rec["round"]
    role, author = acting(rec), (rnd.get("author") or {}).get("name")
    if role is None or name is None or author == rnd["reviewer"]["name"]:
        return False
    if role == "author":
        return name == rnd["reviewer"]["name"]
    return name == author


def require_turn(rv: "Review") -> None:
    """Refuse to act for the other side: a round's record and branch are
    shared, so an act by the side whose turn it is not would be recorded as
    the other side's."""
    if other_side(rv.rec, me(rv.root)):
        if acting(rv.rec) == "author":
            raise ScrivError(f"Round {rv.id} is with the authors, and you are its reviewer, so it is read-only for "
                             f"you until they respond. Acting for the authors? `scrivmark open {rv.id} --as \"Name\"`.")
        raise ScrivError(f"Round {rv.id} is with the reviewer to sign off, so it is read-only for you until it "
                         "comes back.")


def who(rv: "Review") -> str:
    """The initials of the side whose turn it is, which every act records."""
    rnd = rv.rec["round"]
    if acting(rv.rec) == "reviewer":
        return rnd["reviewer"]["initials"]
    return (rnd["author"] or {"initials": config.identity(rv.root).initials})["initials"]


PHASE_ERROR = {
    OPEN: "Drafts belong to the reviewer, before the round is returned.",
    RETURNED: "Changes are decided by the authors while the round is returned to them; signing off, contest a "
              "decision instead.",
    RESPONDED: "That is for the reviewer, once the authors have responded (`scrivmark respond`).",
}


@contextmanager
def _locked(rv: Review, expect: str | None, allowed: tuple[str, ...]):
    """Take the lock, check `expect`, reload the record and only then check
    the round's phase: a request that waited for the lock while the round was
    returned or closed must not act on what it saw before."""
    with transaction(rv.root, expect, rv.guarded()) as writes:
        rv.reload()
        if rv.status not in allowed:
            raise ScrivError("This round is closed." if rv.status == "closed" else
                             PHASE_ERROR.get(allowed, f"Round {rv.id} is {rv.status}; that needs it {' or '.join(allowed)}."))
        require_turn(rv)
        yield writes
        for rel in writes:
            if rel != rv.record_rel and not rel.startswith(f"{LOCAL}/"):
                writable(rv, rel)
    rv.after = writes.rev


def writable(rv: Review, rel: str) -> None:
    """A manuscript file may be written only if it is inside the repository
    and tracked by git (or was in the round's base: a deleted file being put
    back), checked at the moment of writing: an edit to anything else would
    never reach the review's record."""
    config.inside(rv.root, rel)
    if not (gitutil.ok(rv.root, "ls-files", "--error-unmatch", "--", rel)
            or gitutil.ok(rv.root, "cat-file", "-e", f"{rv.rec['round']['base']}:{rel}")):
        raise ScrivError(f"{rel} is not tracked by git, so an edit to it would not be recorded. Commit it first.")


def active(root: Path, round_id: str | None = None) -> Review:
    ms = config.load(root)
    if round_id is None:
        branch = gitutil.current_branch(root)
        if not branch.startswith(BRANCH_PREFIX):
            raise ScrivError(
                f"You are on branch {branch!r}, not a review round. "
                "Use `scrivmark start`, or `scrivmark open <round>`."
            )
        round_id = branch[len(BRANCH_PREFIX) :]
    return Review(root, record.load(root, round_id), ms)


# ---------------------------------------------------------------- catalogue


def _summary(status: str, path: str, old: str | None, a: bytes | None, b: bytes | None) -> str:
    if status == "A":
        return f"added {path}"
    if status == "D":
        return f"deleted {path}"
    if status == "R":
        return f"renamed {old} to {path}"
    if a is not None and b is not None and not gitutil.is_binary(a) and not gitutil.is_binary(b):
        la, lb = a.decode(errors="replace").splitlines(), b.decode(errors="replace").splitlines()
        return f"edited {path} ({len(lb) - len(la):+d} lines)"
    return f"replaced {path}"


def catalogue(root: Path, ms: config.Manuscript, base: str, returned: str) -> list[dict]:
    order = {f: i for i, f in enumerate(ms.files)}
    entries = sorted(
        (e for e in gitutil.name_status(root, base, returned) if not is_managed(e[1])),
        key=lambda e: (order.get(e[1], len(order)), e[1]),
    )
    changes: list[dict] = []
    for status, path, old in entries:
        if status == "M" and ms.is_prose(path):
            a = gitutil.show(root, base, path) or ""
            b = gitutil.show(root, returned, path) or ""
            for u in diff_units(a, b, latex.is_tex(path)):
                changes.append(
                    {"type": u.type, "file": path, "old_file": None, "base": u.old_text,
                     "reviewer": u.new_text, "r_range": list(u.new)}
                )
        else:
            a = gitutil.show_bytes(root, base, old or path)
            b = gitutil.show_bytes(root, returned, path)
            changes.append(
                {"type": "file", "file": path, "old_file": old, "status": status,
                 "base": "", "reviewer": _summary(status, path, old, a, b), "r_range": None}
            )
    for i, c in enumerate(changes, 1):
        c.update({"id": f"c{i}", "decision": None, "by": None, "at": None})
    return changes


# ---------------------------------------------------------------- change status


def _r_text(rv: Review, path: str) -> str:
    return gitutil.show(rv.root, rv.rec["round"]["returned"], path) or ""


def expected(rv: Review, path: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """The returned text with every recorded rejection applied: what the file
    would hold if the author had changed nothing else. Also each change's
    range within it."""
    r = _r_text(rv, path)
    units = sorted((c for c in rv.rec["changes"] if c["file"] == path and c["type"] != "file"),
                   key=lambda c: tuple(c["r_range"]))
    parts, ranges, pos, length = [], {}, 0, 0
    for c in units:
        s, e = c["r_range"]
        parts.append(r[pos:s])
        length += s - pos
        piece = c["base"] if c["decision"] == "rejected" else r[s:e]
        parts.append(piece)
        ranges[c["id"]] = (length, length + len(piece))
        length += len(piece)
        pos = e
    parts.append(r[pos:])
    return "".join(parts), ranges


def target(rv: Review, change: dict) -> dict:
    """Where a text or block change now sits, and how sure scrivmark is:

    exact      the change and its surroundings are as scrivmark left them;
    shifted    the change is intact but the text around it was edited;
    ambiguous  the change is intact but its passage is repeated, so which
               copy is the original cannot be established;
    gone       the author has edited or deleted the change itself.
    """
    cur = rv.text(change["file"])
    if cur is None:
        return {"state": "gone"}
    exp, ranges = expected(rv, change["file"])
    es, ee = ranges[change["id"]]
    piece = exp[es:ee]
    if cur == exp:
        return {"state": "exact", "range": (es, ee)}
    pr = projection(exp, cur)
    r = pr.range(es, ee) if es < ee else (lambda p: None if p is None else (p, p))(pr.point(es))
    if r is None or cur[r[0] : r[1]] != piece:
        return {"state": "gone"}
    left, right = min(NEIGHBOURHOOD, es, r[0]), min(NEIGHBOURHOOD, len(exp) - ee, len(cur) - r[1])
    window = exp[es - left : ee + right]
    if cur[r[0] - left : r[1] + right] != window:
        return {"state": "shifted", "range": r}
    in_exp, in_cur = _occurrences(exp, window), _occurrences(cur, window)
    if len(in_exp) > 1 and pr.range(min(in_exp), max(in_exp) + len(window)) is None:
        return {"state": "ambiguous", "range": r}
    if len(in_cur) > 1:
        return {"state": "ambiguous", "range": r}
    return {"state": "exact", "range": r}


def locate(rv: Review, change: dict) -> tuple[int, int] | None:
    """Where a change now sits in the working file, or None if the author has
    since changed it."""
    t = target(rv, change)
    return None if t["state"] == "gone" else t["range"]


def _file_live(rv: Review, c: dict) -> bool:
    returned = rv.rec["round"]["returned"]
    cur = (rv.root / c["file"]).read_bytes() if (rv.root / c["file"]).exists() else None
    if c["status"] == "D":
        return cur is None
    r_bytes = gitutil.show_bytes(rv.root, returned, c["file"])
    if r_bytes is None or (c["status"] == "R" and (rv.root / c["old_file"]).exists()):
        return False
    return cur == r_bytes


def change_status(rv: Review, c: dict) -> str:
    if c["decision"]:
        return c["decision"]
    live = _file_live(rv, c) if c["type"] == "file" else target(rv, c)["state"] != "gone"
    return "pending" if live else "superseded"


def changes(rv: Review) -> list[dict]:
    """Each change with its state and, for text, the sentence around it as the
    reviewer left it, the change marked «»."""
    out = []
    returned = rv.rec["round"].get("returned")
    for c in rv.rec["changes"]:
        x = dict(c, state=change_status(rv, c))
        if returned and c["type"] != "file" and c.get("r_range"):
            text = gitutil.show(rv.root, returned, c["file"]) or ""
            s, e = c["r_range"]
            x["line"] = text.count("\n", 0, s) + 1
            x["context"] = _context(text, s, e, None, span=70)["before"]
        out.append(x)
    return out


def _find_change(rv: Review, cid: str) -> dict:
    for c in rv.rec["changes"]:
        if c["id"] == cid:
            return c
    raise ScrivError(f"No change {cid} in round {rv.id}")


def decide(rv: Review, cid: str, decision: str, who: str, expect: str | None = None,
           because: str | None = None, agent: str | None = None) -> dict:
    with _locked(rv, expect, RETURNED) as writes:
        c = _find_change(rv, cid)
        if c["decision"]:
            raise ScrivError(f"{cid} is already {c['decision']}")
        state = change_status(rv, c)
        if state == "superseded":
            if because and because.strip():
                c.update({"because": because.strip(), "by": who, "at": record.now(), **_agent(agent)})
                writes[rv.record_rel] = record.dumps(rv.rec)
                return c
            raise Conflict(f"{cid} has been edited since the review, so it needs no decision. "
                           "Add --because to record why you rewrote it.")
        if decision == "rejected":
            writes.update(_reject_writes(rv, c))
        c.update({"decision": decision, "by": who, "at": record.now()})
        c.pop("because", None)
        c.pop("agent", None)
        if because and because.strip():
            c["because"] = because.strip()
        c.update(_agent(agent))
        writes[rv.record_rel] = record.dumps(rv.rec)
    if decision == "rejected" and c["type"] == "file" and c["status"] in ("D", "R"):
        gitutil.git(rv.root, "add", "--", c["old_file"] or c["file"])
    return c


def _reject_writes(rv: Review, c: dict) -> dict:
    if c["type"] == "file":
        base = rv.rec["round"]["base"]
        old = gitutil.show_bytes(rv.root, base, c["old_file"] or c["file"])
        if c["status"] == "A":
            return {c["file"]: None}
        if old is None:
            raise Conflict(f"The original of {c['old_file'] or c['file']} could not be read from git; nothing was changed.")
        if c["status"] == "R":
            return {c["file"]: None, c["old_file"]: old}
        return {c["file"]: old}
    t = target(rv, c)
    if t["state"] != "exact":
        why = {
            "gone": "it has been edited since the review",
            "shifted": "the text around it has been edited since the review",
            "ambiguous": "the passage is repeated, so scrivmark cannot be sure which copy is the reviewer's",
        }[t["state"]]
        raise Conflict(f"{c['id']} was not rejected because {why}. Nothing was changed. Edit the passage yourself "
                       f"instead; the change then becomes superseded, and `scrivmark reject {c['id']} --because \"...\"` "
                       "records your reason with it.")
    cur = rv.text(c["file"])
    s, e = t["range"]
    return {c["file"]: cur[:s] + c["base"] + cur[e:]}


def undo(rv: Review, cid: str, expect: str | None = None) -> dict:
    """Reopen a decision. Undoing a rejection puts the reviewer's text back,
    only where scrivmark's own restoration is still exactly in place."""
    with _locked(rv, expect, RETURNED) as writes:
        c = _find_change(rv, cid)
        if not c["decision"]:
            raise ScrivError(f"{cid} has no decision to undo.")
        _reopen(rv, c, writes)
        writes[rv.record_rel] = record.dumps(rv.rec)
    return c


def _reopen(rv: Review, c: dict, writes: dict) -> None:
    """Make a decided change undecided again, putting the reviewer's text back
    after a rejection (only where scrivmark's restoration is exactly in place)."""
    if c["decision"] == "rejected":
        if c["type"] == "file":
            raise Conflict("Reopening a whole-file rejection is not supported; restore the file with git.")
        t = target(rv, c)
        if t["state"] != "exact":
            raise Conflict(f"The restored text for {c['id']} has been edited since, so the rejection cannot be reopened safely.")
        cur = rv.text(c["file"])
        s, e = t["range"]
        writes[c["file"]] = cur[:s] + c["reviewer"] + cur[e:]
    undecide(c)


def undecide(c: dict) -> None:
    c.update({"decision": None, "by": None, "at": None})
    c.pop("because", None)
    c.pop("agent", None)


def contest(rv: Review, cid: str, who: str, because: str, expect: str | None = None,
            agent: str | None = None) -> dict:
    """The reviewer, signing off, sends a decision back to the authors to
    reconsider, with a reason. The change is undecided again (a rejected edit
    is put back) and the contest stays in its history."""
    if not because or not because.strip():
        raise ScrivError("Say why the decision should be reconsidered (--because).")
    with _locked(rv, expect, RESPONDED) as writes:
        c = _find_change(rv, cid)
        if c["decision"] not in ("accepted", "rejected"):
            raise ScrivError(f"{cid} has no decision to contest.")
        was = {"decision": c["decision"], "by": c["by"], "at": c["at"],
               **({"because": c["because"]} if c.get("because") else {}), **_agent(c.get("agent"))}
        if c["decision"] == "rejected" and c["file"] in signoff_edits(rv):
            raise Conflict(f"{c['file']} has been edited by hand during sign-off, so the reviewer's text was not "
                           "put back. Undo that edit (`git restore`) first.")
        _reopen(rv, c, writes)
        if c["file"] in writes:
            base = _signoff_base(rv)
            put_back = read_json(rv.root / _put_back_rel(rv), {})
            files = put_back.get("files", {}) if put_back.get("commit") == base else {}
            files[c["file"]] = hashlib.sha256(writes[c["file"]].encode()).hexdigest()
            writes[_put_back_rel(rv)] = json.dumps({"commit": base, "files": files})
        c.setdefault("contested", []).append({"by": who, "at": record.now(), "because": because.strip(),
                                              "was": was, **_agent(agent)})
        writes[rv.record_rel] = record.dumps(rv.rec)
    return c


def _put_back_rel(rv: Review) -> str:
    return str((local_dir(rv.root, rv.id) / "contested.json").relative_to(rv.root))


def _signoff_base(rv: Review) -> str:
    """The commit the authors handed over at their last response (HEAD for
    records from before handovers named their commit)."""
    hs = handovers(rv.rec)
    return hs[-1].get("commit", "HEAD") if hs and hs[-1]["to"] == "reviewer" else "HEAD"


def signoff_edits(rv: Review) -> list[str]:
    """Files changed since the authors handed the round over, committed or
    not, other than by a contest in this turn putting the reviewer's text
    back. While the reviewer signs off the text is the authors': such an
    edit would reach them, or the merge, untracked."""
    base = _signoff_base(rv)
    put_back = read_json(rv.root / _put_back_rel(rv), {})
    allowed = put_back.get("files", {}) if put_back.get("commit") == base else {}
    changed = gitutil._nul(gitutil.git(rv.root, "diff", "--name-only", "-z", "--no-renames", base)) + gitutil.untracked(rv.root)
    out = []
    for rel in dict.fromkeys(changed):
        f = rv.root / rel
        if not is_managed(rel) and (not f.exists() or allowed.get(rel) != hashlib.sha256(f.read_bytes()).hexdigest()):
            out.append(rel)
    return out


# ---------------------------------------------------------------- anchoring


def _snapshot(rv: Review, text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    p = local_dir(rv.root, rv.id) / "snapshots" / h
    if not p.exists():
        atomic_write(p, text)
    return f"snap:{h}"


def _rev_for(rv: Review, rel: str, text: str) -> str:
    head = gitutil.rev(rv.root, "HEAD")
    if gitutil.show(rv.root, head, rel) == text:
        return f"git:{head}"
    return _snapshot(rv, text)


def _text_at(rv: Review, rel: str, rev: str) -> str | None:
    kind, _, ref = rev.partition(":")
    if kind == "git":
        return gitutil.show(rv.root, ref, rel)
    p = local_dir(rv.root, rv.id) / "snapshots" / ref
    return p.read_text() if p.exists() else None


def anchor_at(rv: Review, rel: str, s: int, e: int) -> dict:
    text = rv.text(rel)
    if text is None:
        raise ScrivError(f"{rel} does not exist")
    if not (0 <= s <= e <= len(text)):
        raise ScrivError("Anchor range is outside the file")
    return {
        "file": rel, "rev": _rev_for(rv, rel, text), "range": [s, e], "exact": text[s:e],
        "prefix": text[max(0, s - CONTEXT) : s], "suffix": text[e : e + CONTEXT],
    }


def _ws_pattern(text: str) -> str:
    return r"\s+".join(re.escape(part) for part in text.split())


def anchor_quote(rv: Review, rel: str, quote: str, prefix: str = "", suffix: str = "") -> dict:
    """Anchor to quoted source text. Whitespace in the quote matches any run of
    whitespace, so a phrase quoted from rendered prose finds its wrapped source."""
    text = rv.text(rel)
    if text is None:
        raise ScrivError(f"{rel} does not exist")
    if not quote.strip():
        raise ScrivError("The quote is empty")
    pat = re.compile(
        _ws_pattern(quote)
    )
    hits = [m.span() for m in pat.finditer(text)]
    if prefix.strip():
        pre = re.compile(_ws_pattern(prefix) + r"\s*$")
        hits = [h for h in hits if pre.search(text[max(0, h[0] - 4 * len(prefix) - 20) : h[0]])]
    if suffix.strip():
        suf = re.compile(r"^\s*" + _ws_pattern(suffix))
        hits = [h for h in hits if suf.match(text[h[1] :])]
    if not hits:
        raise ScrivError(f"The quoted text does not occur in {rel}")
    if len(hits) > 1:
        raise Conflict(f"The quote occurs {len(hits)} times in {rel}; add --prefix or --suffix to choose one.")
    return anchor_at(rv, rel, *hits[0])


def _occurrences(text: str, needle: str) -> list[int]:
    out, i = [], text.find(needle)
    while needle and i != -1:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def _context_score(text: str, i: int, a: dict) -> int:
    pre, suf = a["prefix"], a["suffix"]
    before, after = text[max(0, i - len(pre)) : i], text[i + len(a["exact"]) : i + len(a["exact"]) + len(suf)]
    left = next((k for k in range(len(pre), -1, -1) if before.endswith(pre[len(pre) - k :])), 0)
    right = next((k for k in range(len(suf), -1, -1) if after.startswith(suf[:k])), 0)
    return left + right


def _lost_twin(old: str, a: dict, pr) -> bool:
    """The passage had a twin when it was anchored (the same text with the
    same stored context, or the same whole lines) and some copy of it did not
    survive. Which copy is left is then the diff's guess, not evidence."""
    s, e = a["range"]
    full = len(a["prefix"]) + len(a["suffix"])
    twins = [i for i in _occurrences(old, a["exact"]) if i != s and _context_score(old, i, a) == full]
    if a["exact"] and twins and any(pr.range(i, i + e - s) is None for i in [s, *twins]):
        return True
    ls = old.rfind("\n", 0, s) + 1
    le = old.find("\n", e)
    le = len(old) if le < 0 else le
    block = old[ls:le]
    if not block.strip():
        return False
    i = old.find(block)
    while i >= 0:
        whole = (i == 0 or old[i - 1] == "\n") and (i + len(block) == len(old) or old[i + len(block)] == "\n")
        if i != ls and whole and pr.range(i + s - ls, i + e - ls) is None:
            return True
        i = old.find(block, i + 1)
    return False


def resolve(rv: Review, a: dict, cur: str | None = None) -> dict:
    """Locate an anchor in the current text.

    ok, exact   the passage is where it was, unchanged;
    ok, edited  its surroundings are intact and the passage itself was edited,
                or it was rewritten and solid context survives close by on
                both sides (the thread follows the new text);
    ok, removed it was deleted outright where context survives on both sides;
                the range is the empty point where it was;
    ok, moved   it is no longer where it was, but occurs once elsewhere with
                its full original context;
    orphan      none of these holds. A lookalike passage elsewhere is listed
                as a candidate, never adopted.

    `cur` stands in for the file's current text, to ask what an edit would do.
    """
    cur = rv.text(a["file"]) if cur is None else cur
    if cur is None:
        return {"state": "orphan", "candidates": []}
    old = _text_at(rv, a["file"], a["rev"])
    hits = _occurrences(cur, a["exact"])
    full = len(a["prefix"]) + len(a["suffix"])
    unsure = {"state": "orphan", "why": "ambiguous", "candidates": [[i, i + len(a["exact"])] for i in hits]}
    if old is not None:
        pr = projection(old, cur)
        s, e = a["range"]
        if _lost_twin(old, a, pr):
            return unsure
        r = pr.range(s, e)
        if r and cur[r[0] : r[1]] == a["exact"]:
            return {"state": "ok", "how": "exact", "range": list(r)}
        left = pr.range(max(0, s - 16), s)
        right = pr.range(e, min(len(old), e + 16))
        if left and right and left[1] <= right[0]:
            return {"state": "ok", "how": "edited", "edited": True, "range": [left[1], right[0]]}
        near = pr.around(s, e)
        if near and near[0] < near[1]:
            return {"state": "ok", "how": "edited", "edited": True, "range": list(near)}
    else:
        near = None
    whole = [i for i in hits if _context_score(cur, i, a) == full]
    twins = old is not None and sum(1 for i in _occurrences(old, a["exact"]) if _context_score(old, i, a) == full) > 1
    if len(whole) == 1 and a["exact"] and twins:
        return unsure
    if len(whole) == 1 and a["exact"]:
        i = whole[0]
        return {"state": "ok", "how": "moved", "range": [i, i + len(a["exact"])]}
    if near and a["exact"]:
        return {"state": "ok", "how": "removed", "edited": True, "range": list(near)}
    return {"state": "orphan", "candidates": [[i, i + len(a["exact"])] for i in hits]}


# ---------------------------------------------------------------- threads


def _thread(rv: Review, tid: str) -> dict:
    for t in rv.rec["threads"]:
        if t["id"] == tid:
            return t
    raise ScrivError(f"No thread {tid} in round {rv.id}")


def threads(rv: Review) -> list[dict]:
    return [dict(t, where=resolve(rv, t["anchor"])) for t in rv.rec["threads"]]


def comment(rv: Review, anchor: dict, body: str, who: str, expect: str | None = None,
            agent: str | None = None) -> dict:
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    with _locked(rv, expect, LIVE) as writes:
        t = {
            "id": record.new_id("t"), "status": "open", "anchor": anchor,
            "messages": [{"id": record.new_id("m"), "by": who, "at": record.now(), "body": body, **_agent(agent)}],
        }
        rv.rec["threads"].append(t)
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def add_many(rv: Review, items: list[dict], who: str, expect: str | None = None) -> list[dict]:
    """Add several comments and drafts at once, all or none. Each item has an
    `anchor` and `body`, and for drafts `draft: true` and optional `wording`."""
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    if any(i.get("draft") for i in items) and rv.status != "open":
        raise ScrivError("Drafts belong to the reviewer, before the round is returned.")
    out = []
    phase = OPEN if any(i.get("draft") for i in items) else LIVE
    with _locked(rv, expect, phase) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        for i in items:
            if i.get("draft"):
                d = {"id": record.new_id("d"), "anchor": i["anchor"], "body": i["body"], "wording": i.get("wording"),
                     "at": record.now(), "by": i.get("by")}
                ds.append(d)
                out.append(d)
            else:
                t = {"id": record.new_id("t"), "status": "open", "anchor": i["anchor"],
                     "messages": [{"id": record.new_id("m"), "by": i.get("by", who), "at": i.get("at") or record.now(),
                                   "body": i["body"], **_agent(i.get("agent"))}]}
                if i.get("at"):
                    t["messages"][0]["imported_at"] = record.now()
                rv.rec["threads"].append(t)
                out.append(t)
        writes[rv.record_rel] = record.dumps(rv.rec)
        if any(i.get("draft") for i in items):
            writes[rv.drafts_rel] = json.dumps(ds, indent=2, ensure_ascii=False)
    return out


HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
SIGNED = re.compile(r"^\s*([A-Z][\w.' -]{0,40}?),\s*(\d{4}-\d{2}-\d{2})\b")


def source_comments(rv: Review, importer: str) -> list[dict]:
    """HTML comments in the Markdown sources that no thread is anchored to yet,
    as items for add_many. A comment that opens "Name, YYYY-MM-DD" is signed by
    that name; others by the importer."""
    taken = {(t["anchor"]["file"], t["anchor"]["exact"]) for t in rv.rec["threads"]}
    out = []
    for rel in rv.ms.files:
        if latex.is_tex(rel):
            continue
        text = rv.text(rel) or ""
        for m in HTML_COMMENT.finditer(text):
            body = m.group(1).strip()
            if body and (rel, m.group(0)) not in taken:
                signed = SIGNED.match(body)
                out.append({"anchor": anchor_at(rv, rel, m.start(), m.end()),
                            "by": signed.group(1).strip() if signed else importer,
                            "at": signed.group(2) if signed else None,
                            "body": f"{body}\n\n(Imported by {importer} from a comment in the source.)",
                            "line": text.count("\n", 0, m.start()) + 1})
    return out


def reply(rv: Review, tid: str, body: str, who: str, expect: str | None = None,
          agent: str | None = None) -> dict:
    with _locked(rv, expect, LIVE) as writes:
        t = _thread(rv, tid)
        t["messages"].append({"id": record.new_id("m"), "by": who, "at": record.now(), "body": body, **_agent(agent)})
        t["status"] = "open"
        t.pop("resolved_by", None)
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def can_edit(rv: Review, m: dict, who: str) -> bool:
    """A message can be changed by whoever wrote it until the other side has
    seen it: during the turn in which it was written, by the side whose turn
    it is. The reviewer's first comments until the return, the authors'
    replies until they respond or close, and so on, turn by turn."""
    rnd = rv.rec["round"]
    role = acting(rv.rec)
    if role is None or m.get("by") != who:
        return False
    if who != (rnd["reviewer"]["initials"] if role == "reviewer" else (rnd["author"] or {}).get("initials")):
        return False
    hs = handovers(rv.rec)
    return not hs or _ts(m["at"]) > _ts(hs[-1]["at"])


def edit_message(rv: Review, tid: str, mid: str, body: str, who: str, expect: str | None = None) -> dict:
    """Change the text of one of your own messages before the other side has
    seen it. If an agent drafted it, the drafted text stays in its history."""
    if not body.strip():
        raise ScrivError("A comment needs some text.")
    with _locked(rv, expect, LIVE) as writes:
        t = _thread(rv, tid)
        m = next((x for x in t["messages"] if x["id"] == mid), None)
        if m is None:
            raise ScrivError(f"No message {mid} in thread {tid}")
        if not can_edit(rv, m, who):
            raise ScrivError("Only your own messages can be edited, and only until the other side has seen them "
                             "(until the round next changes hands).")
        if m.get("drafted_by"):
            m.setdefault("draft_history", []).append(
                {"body": m["body"], "at": m.get("edited_at") or m["at"], "by": who if m.get("edited_at") else m["drafted_by"]})
        m["body"] = body
        m["edited_at"] = record.now()
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def set_thread(rv: Review, tid: str, status: str, expect: str | None = None, who: str | None = None,
               agent: str | None = None) -> dict:
    with _locked(rv, expect, LIVE) as writes:
        t = _thread(rv, tid)
        t["status"] = status
        if status == "resolved" and who:
            t["resolved_by"] = {"by": who, "at": record.now(), **_agent(agent)}
        elif status == "open":
            t.pop("resolved_by", None)
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def news(rv: Review) -> dict:
    """What the other side did in its last turn, for whoever's turn it is."""
    window = news_window(rv.rec)
    out = {"turn": acting(rv.rec), "since": window[0], "until": window[1], "messages": [], "resolved": [],
           "decisions": [], "contested": []}
    if window[1] is None:
        return out
    for t in rv.rec["threads"]:
        for m in t["messages"]:
            if _within(m["at"], window):
                out["messages"].append({"thread": t["id"], "message": m["id"], "by": m["by"], **_agent(m.get("agent")),
                                        "at": m["at"], "body": m["body"], "quote": t["anchor"]["exact"],
                                        "new_thread": m is t["messages"][0]})
        rb = t.get("resolved_by")
        if rb and _within(rb["at"], window):
            out["resolved"].append({"thread": t["id"], **rb})
    for c in rv.rec["changes"]:
        if (c.get("decision") in ("accepted", "rejected") or c.get("because")) and _within(c.get("at"), window):
            out["decisions"].append({"change": c["id"], "decision": c.get("decision") or "superseded",
                                     "by": c["by"], "at": c["at"],
                                     **({"because": c["because"]} if c.get("because") else {}), **_agent(c.get("agent"))})
        for k in c.get("contested", []):
            if _within(k["at"], window):
                out["contested"].append({"change": c["id"], **k})
    return out


def reattach(rv: Review, tid: str, anchor: dict, expect: str | None = None) -> dict:
    """Give a thread a new passage, by hand, keeping what it was first about."""
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    with _locked(rv, expect, LIVE) as writes:
        t = _thread(rv, tid)
        t["anchor"] = dict(anchor, was=t["anchor"].get("was") or t["anchor"].get("removed") or t["anchor"]["exact"])
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def rebase_anchors(rv: Review, commit: str, snapshots_only: bool = False) -> list[str]:
    """Re-express every resolvable anchor against `commit`; return orphan ids.
    With `snapshots_only`, only anchors to a local snapshot, which no other
    clone has; the rest keep the text they were written against."""
    orphans = []
    for t in rv.rec["threads"]:
        if snapshots_only and not t["anchor"]["rev"].startswith("snap:"):
            continue
        where = resolve(rv, t["anchor"])
        if where["state"] != "ok":
            orphans.append(t["id"])
            continue
        s, e = where["range"]
        text = rv.text(t["anchor"]["file"])
        if where["how"] == "removed":
            t["anchor"]["removed"] = t["anchor"].get("removed") or t["anchor"]["exact"]
        t["anchor"].update({
            "rev": f"git:{commit}", "range": [s, e], "exact": text[s:e],
            "prefix": text[max(0, s - CONTEXT) : s], "suffix": text[e : e + CONTEXT],
        })
    return orphans


# ---------------------------------------------------------------- drafts


def agents(rv: Review, also: str | None = None) -> list[str]:
    """Every agent named in the round so far: drafting, commenting, deciding
    or handing over (plus `also`), in order of first appearance."""
    rec = rv.rec
    names = [h.get("agent") for h in rec["round"].get("handovers") or []]
    names += [c.get("agent") for c in rec["changes"]]
    names += [e.get("by") for e in rec.get("drafted_edits") or []]
    for t in rec["threads"]:
        names.append((t.get("resolved_by") or {}).get("agent"))
        for m in t["messages"]:
            names += [m.get("agent"), m.get("drafted_by")]
    names += [d.get("by") for d in drafts(rv)] + [also]
    return list(dict.fromkeys(n for n in names if n))


def unnamed_agents(rv: Review, statement: str, also: str | None = None) -> list[str]:
    """Agents in the round that the statement does not mention by name."""
    flat = " ".join(statement.split()).casefold()
    return [a for a in agents(rv, also) if " ".join(a.split()).casefold() not in flat]


def drafts(rv: Review) -> list[dict]:
    return [dict(d, where=resolve(rv, d["anchor"])) for d in read_json(rv.root / rv.drafts_rel, [])]


def add_draft(rv: Review, anchor: dict, body: str, wording: str | None, expect: str | None = None,
              by: str | None = None) -> dict:
    if rv.status != "open":
        raise ScrivError("Drafts belong to the reviewer, before the round is returned.")
    with _locked(rv, expect, OPEN) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        d = {"id": record.new_id("d"), "anchor": anchor, "body": body, "wording": wording, "at": record.now(), "by": by}
        ds.append(d)
        writes[rv.drafts_rel] = json.dumps(ds, indent=2, ensure_ascii=False)
    return d


def _context(text: str, s: int, e: int, wording: str | None, span: int = 90) -> dict:
    """The passage in its surroundings, before and after the wording, with the
    changed part marked «»."""
    a = max(0, s - span)
    a = text.rfind(" ", 0, a + 1) + 1 if a else 0
    z = min(len(text), e + span)
    space = text.find(" ", z)
    z = len(text) if space < 0 else space
    pre, post = text[a:s], text[e:z]
    lead, tail = ("…" if a else ""), ("…" if z < len(text) else "")
    flat = lambda x: " ".join(x.split())  # noqa: E731
    out = {"before": flat(f"{lead}{pre}«{text[s:e]}»{post}{tail}")}
    if wording is not None:
        out["after"] = flat(f"{lead}{pre}«{wording}»{post}{tail}")
    return out


def _fate(rv: Review, anchor: dict, text: str, s: int, e: int, wording: str) -> str:
    """What applying `wording` over [s, e) would do to another anchor there."""
    w = resolve(rv, anchor, text[:s] + wording + text[e:])
    if w["state"] != "ok":
        return "would lose its passage"
    return {"exact": "stays anchored, unchanged", "moved": "stays anchored",
            "edited": "stays anchored, on the edited text", "removed": "stays anchored where the text was removed"}[w["how"]]


def preflight(rv: Review, items: list[dict]) -> list[dict]:
    """For a batch about to be added: where each item lands, its wording in
    context, and what it overlaps (other items, drafts, threads, comments in
    the source). Overlaps are advisory; for an item with wording, each overlap
    says what keeping it would do to that thread or draft."""
    taken = []
    for d in drafts(rv):
        if d["where"]["state"] == "ok":
            taken.append((d["anchor"]["file"], *d["where"]["range"],
                          f"draft {d['id']}" + (f" by {d['by']}" if d.get("by") else ""), d))
    for t in threads(rv):
        if t["where"]["state"] == "ok" and t["status"] == "open":
            taken.append((t["anchor"]["file"], *t["where"]["range"], f"thread {t['id']}", t))
    for rel in rv.ms.files:
        for m in HTML_COMMENT.finditer(rv.text(rel) or ""):
            taken.append((rel, m.start(), m.end(), "a comment in the source", None))
    out = []
    spans = []
    for n, i in enumerate(items, 1):
        a = i["anchor"]
        s, e = a["range"]
        text = rv.text(a["file"]) or ""
        wording = i.get("wording")
        hits = []
        for f, x, y, label, other in taken:
            if f != a["file"] or not (s < y and x < e):
                continue
            if wording is not None and other is not None:
                fate = ("both propose wording; keep all applies only the first" if other.get("wording") is not None
                        else _fate(rv, other["anchor"], text, s, e, wording))
                label = f"{label} ({fate})"
            hits.append(label)
        for m, (f, x, y, w) in spans:
            if f == a["file"] and s < y and x < e:
                hits.append(f"item {m}" + (" (both propose wording; keep all applies only the first)"
                                           if wording is not None and w is not None else ""))
        spans.append((n, (a["file"], s, e, wording)))
        out.append({"item": n, "file": a["file"], "line": text.count("\n", 0, s) + 1, "exact": a["exact"],
                    "draft": bool(i.get("draft")), "by": i.get("by"), "wording": wording,
                    **_context(text, s, e, wording), "overlaps": hits})
    return out


def revise_draft(rv: Review, did: str, body: str | None = None, wording: str | None = None,
                 clear_wording: bool = False, by: str | None = None, expect: str | None = None) -> dict:
    """Change a draft's body or wording in place. It keeps its author; the
    earlier version goes to its history, which is kept in the record if the
    draft is kept."""
    if rv.status != "open":
        raise ScrivError("Drafts belong to the reviewer, before the round is returned.")
    with _locked(rv, expect, OPEN) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        d = next((x for x in ds if x["id"] == did), None)
        if d is None:
            raise ScrivError(f"No draft {did}")
        d.setdefault("history", []).append({"body": d["body"], "wording": d["wording"], "at": d.get("revised_at") or d["at"],
                                            **({"by": d["revised_by"]} if d.get("revised_by") else {})})
        if body is not None:
            d["body"] = body
        if clear_wording:
            d["wording"] = None
        elif wording is not None:
            d["wording"] = wording
        d["revised_at"] = record.now()
        if by:
            d["revised_by"] = by
        else:
            d.pop("revised_by", None)
        writes[rv.drafts_rel] = json.dumps(ds, indent=2, ensure_ascii=False)
    return d


def keep_all(rv: Review, who: str, expect: str | None = None) -> dict:
    """Keep every draft, in document order, in one transaction. A draft whose
    wording can no longer be applied (its passage changed, or an earlier draft
    in this batch rewrote part of it) stays for the reviewer to edit or drop."""
    order = {f: i for i, f in enumerate(rv.ms.sources)}
    refusal = "The draft's passage has been edited since it was written, so its wording was not applied."
    with _locked(rv, expect, OPEN) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        placed = []
        for d in ds:
            w = resolve(rv, d["anchor"])
            pos = w["range"] if w["state"] == "ok" else [0, 0]
            placed.append((order.get(d["anchor"]["file"], len(order)), pos[0], pos[1], d, w))
        placed.sort(key=lambda x: x[:3])
        kept, left, edits, reach = [], [], {}, {}
        for _, s, e, d, w in placed:
            rel = d["anchor"]["file"]
            if d["wording"] is not None:
                cur = rv.text(rel)
                if w["state"] != "ok" or w["how"] == "edited" or cur[s:e] != d["anchor"]["exact"]:
                    left.append({"id": d["id"], "reason": refusal})
                    continue
                if s < reach.get(rel, 0):
                    left.append({"id": d["id"], "reason": "It overlaps a draft kept just before it, so its wording "
                                 "was not applied."})
                    continue
                reach[rel] = e
                edits.setdefault(rel, []).append((s, e, d))
            else:
                _keep(rv, d, d["anchor"], who, None, writes)
            kept.append(d["id"])
        for rel, items in edits.items():
            cur, out, shift, spans = rv.text(rel), [], 0, []
            pos = 0
            for s, e, d in items:
                out += [cur[pos:s], d["wording"]]
                spans.append((s + shift, s + shift + len(d["wording"]), d))
                shift += len(d["wording"]) - (e - s)
                pos = e
            new = "".join(out) + cur[pos:]
            writes[rel] = new
            snap = _snapshot(rv, new)
            for s, e, d in spans:
                anchor = {"file": rel, "rev": snap, "range": [s, e], "exact": new[s:e],
                          "prefix": new[max(0, s - CONTEXT) : s], "suffix": new[e : e + CONTEXT]}
                _keep(rv, d, anchor, who, d["anchor"]["exact"], writes)
        done = set(kept)
        writes[rv.drafts_rel] = json.dumps([d for d in ds if d["id"] not in done], indent=2, ensure_ascii=False)
        if kept:
            writes[rv.record_rel] = record.dumps(rv.rec)
    return {"kept": kept, "left": left}


def _keep(rv: Review, d: dict, anchor: dict, who: str, was: str | None, writes: dict,
          body: str | None = None, wording: str | None = None) -> dict | None:
    """Record a kept draft: its applied wording (when `was` is given) and its
    comment as a thread. The caller writes the record."""
    body = d["body"] if body is None else body
    wording = d["wording"] if wording is None else wording
    if was is not None and d.get("by"):
        rv.rec.setdefault("drafted_edits", []).append(
            {"by": d["by"], "file": anchor["file"], "was": was, "wording": wording, "at": record.now(),
             **({"history": d["history"]} if d.get("history") else {})})
    if not (body and body.strip()):
        return None
    t = {
        "id": record.new_id("t"), "status": "open", "anchor": anchor,
        "messages": [{"id": record.new_id("m"), "by": who, "at": record.now(), "body": body}],
    }
    if d.get("by"):
        t["messages"][0]["drafted_by"] = d["by"]
    if d.get("history"):
        t["messages"][0]["draft_history"] = d["history"]
    rv.rec["threads"].append(t)
    return t


def _apply_wording(rv: Review, anchor: dict, wording: str, writes: dict, refusal: str) -> dict:
    """Replace the anchored text with `wording` as a real edit, only while that
    text is exactly as quoted; returns the anchor of the new text."""
    where = resolve(rv, anchor)
    cur = rv.text(anchor["file"])
    if where["state"] != "ok" or where["how"] == "edited" or cur[where["range"][0] : where["range"][1]] != anchor["exact"]:
        raise Conflict(refusal)
    s, e = where["range"]
    new = cur[:s] + wording + cur[e:]
    writes[anchor["file"]] = new
    return {
        "file": anchor["file"], "rev": _snapshot(rv, new), "range": [s, s + len(wording)],
        "exact": wording, "prefix": new[max(0, s - CONTEXT) : s],
        "suffix": new[s + len(wording) : s + len(wording) + CONTEXT],
    }


def triage(rv: Review, did: str, keep: bool, who: str, body: str | None = None,
           wording: str | None = None, expect: str | None = None) -> dict | None:
    """Keep a draft as the reviewer's own (applying any wording as a real edit),
    or drop it."""
    with _locked(rv, expect, OPEN) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        d = next((x for x in ds if x["id"] == did), None)
        if d is None:
            raise ScrivError(f"No draft {did}")
        ds.remove(d)
        writes[rv.drafts_rel] = json.dumps(ds, indent=2, ensure_ascii=False)
        if not keep:
            return None
        body = d["body"] if body is None else body
        wording = d["wording"] if wording is None else wording
        anchor, was = d["anchor"], None
        if wording is not None:
            was = anchor["exact"]
            anchor = _apply_wording(rv, anchor, wording, writes, "The draft's passage has been edited since it "
                                    "was written, so its wording was not applied. Edit the draft, or drop it.")
        t = _keep(rv, d, anchor, who, was, writes, body, wording)
        if t is not None or was is not None:
            writes[rv.record_rel] = record.dumps(rv.rec)
    return t


# ---------------------------------------------------------------- asks
# Questions the human queues for their agent from the viewer. Like drafts they
# are local to this machine and never reach the record or the other side.


def _asks_path(rv: Review) -> Path:
    return local_dir(rv.root, rv.id) / "asks.json"


def asks(rv: Review, include_done: bool = False) -> list[dict]:
    items = read_json(_asks_path(rv), [])
    return [dict(a, where=resolve(rv, a["anchor"])) for a in items if include_done or not a.get("done")]


def add_ask(rv: Review, anchor: dict, question: str, who: str, expect: str | None = None) -> dict:
    with _locked(rv, expect, LIVE) as writes:
        items = read_json(_asks_path(rv), [])
        item = {"id": record.new_id("q"), "anchor": anchor, "question": question, "by": who, "answer": None,
                "at": record.now(), "done": False}
        items.append(item)
        writes[str(_asks_path(rv).relative_to(rv.root))] = json.dumps(items, indent=2, ensure_ascii=False)
    return item


def update_ask(rv: Review, qid: str, answer: str | None = None, done: bool | None = None,
               wording: str | None = None, by: str | None = None, expect: str | None = None) -> dict:
    with _locked(rv, expect, LIVE) as writes:
        items = read_json(_asks_path(rv), [])
        item = next((a for a in items if a["id"] == qid), None)
        if item is None:
            raise ScrivError(f"No ask {qid}")
        if answer is not None:
            item.update({"answer": answer, "answered_at": record.now(), "answered_by": by, "wording": wording})
        if done is not None:
            item["done"] = done
        writes[str(_asks_path(rv).relative_to(rv.root))] = json.dumps(items, indent=2, ensure_ascii=False)
    return item


def apply_ask(rv: Review, qid: str, expect: str | None = None) -> dict:
    """Make an answer's proposed wording a real edit. The ask stays open, now
    anchored to the new text, so it can still become a comment."""
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    with _locked(rv, expect, ("open", "returned")) as writes:
        items = read_json(_asks_path(rv), [])
        item = next((a for a in items if a["id"] == qid), None)
        if item is None:
            raise ScrivError(f"No ask {qid}")
        if item.get("wording") is None or item.get("applied"):
            raise ScrivError(f"Ask {qid} has no wording to apply.")
        anchor = _apply_wording(rv, item["anchor"], item["wording"], writes, "The passage has been edited since "
                                "the answer was written, so its wording was not applied. Edit the text yourself.")
        item.update({"applied": record.now(), "anchor": anchor, "asked_on": item["anchor"]["exact"]})
        writes[str(_asks_path(rv).relative_to(rv.root))] = json.dumps(items, indent=2, ensure_ascii=False)
    return item


def archive_asks(rv: Review) -> int:
    """Answered asks join the round record, apart from the comment threads, so
    the human-AI exchange behind a review is kept with it. Returns how many."""
    kept = {a["id"]: a for a in rv.rec.get("asks", [])}
    kept.update({a["id"]: a for a in read_json(_asks_path(rv), []) if a.get("answer")})
    if kept:
        rv.rec["asks"] = sorted(kept.values(), key=lambda a: a["at"])
    return len(kept)


# ---------------------------------------------------------------- direct edits


def replace_lines(rv: Review, rel: str, first: int, last: int, new_text: str, file_rev: str | None = None) -> None:
    """Replace lines first..last (1-based, inclusive) of a manuscript file,
    if the file is still at `file_rev`."""
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    if rel not in rv.ms.sources:
        raise ScrivError(f"{rel} is not a manuscript file")
    with transaction(rv.root, file_rev, [rel]) as writes:
        rv.reload()
        if rv.status not in ("open", "returned"):
            raise ScrivError("This round is closed." if rv.status == "closed" else
                             "The text is the authors' while you sign off; comment, or contest a decision, instead.")
        require_turn(rv)
        writable(rv, rel)
        lines = rv.text(rel).splitlines(keepends=True)
        if not (1 <= first <= last <= len(lines)):
            raise ScrivError("Those lines are no longer in the file; reload.")
        tail = "\n" if lines[last - 1].endswith("\n") and not new_text.endswith("\n") else ""
        lines[first - 1 : last] = [new_text + tail]
        writes[rel] = "".join(lines)


def summary(rv: Review) -> dict:
    cs = changes(rv) if rv.status != "open" else []
    ts = threads(rv)
    count = lambda xs, k, v: sum(1 for x in xs if x[k] == v)  # noqa: E731
    return {
        "round": rv.rec["round"],
        "rev": rv.rev(),
        "changes": {s: count(cs, "state", s) for s in ("pending", "accepted", "rejected", "superseded")},
        "threads": {"open": count(ts, "status", "open"), "resolved": count(ts, "status", "resolved"),
                    "orphaned": sum(1 for t in ts if t["where"]["state"] != "ok")},
        "drafts": len(drafts(rv)) if rv.status == "open" else 0,
        "uncommitted": gitutil.dirty_paths(rv.root, (".scrivmark/",)),
    }
