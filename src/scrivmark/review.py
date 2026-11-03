"""Operations on one review round: the change catalogue, decisions, anchoring,
threads and drafts. Lifecycle transitions live in `rounds`."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config, gitutil, record
from .errors import Conflict, ScrivError
from .state import local_dir, read_json, revision, transaction
from .textdiff import diff_units, projection

BRANCH_PREFIX = "review/"
CONTEXT = 32


def is_managed(path: str) -> bool:
    return path.startswith((record.REVIEWS + "/", ".scrivmark/")) or path == ".gitignore"


@dataclass
class Review:
    root: Path
    rec: dict
    ms: config.Manuscript

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
        files = set(self.ms.files) | {c["file"] for c in self.rec["changes"]} | {self.record_rel}
        return sorted(files)

    def rev(self) -> str:
        return revision(self.root, self.watched() + [self.drafts_rel])

    @property
    def drafts_rel(self) -> str:
        return str((local_dir(self.root, self.id) / "drafts.json").relative_to(self.root))

    def text(self, rel: str) -> str | None:
        p = self.root / rel
        return p.read_text() if p.exists() else None

    def reload(self) -> None:
        self.rec = record.load(self.root, self.id)


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
            for u in diff_units(a, b):
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


def locate(rv: Review, change: dict) -> tuple[int, int] | None:
    """Where a text or block change now sits in the working file, or None if
    the author has since changed it."""
    cur = rv.text(change["file"])
    if cur is None:
        return None
    pr = projection(_r_text(rv, change["file"]), cur)
    s, e = change["r_range"]
    if s == e:
        p = pr.point(s)
        return None if p is None else (p, p)
    r = pr.range(s, e)
    if r is None or cur[r[0] : r[1]] != change["reviewer"]:
        return None
    return r


def _file_live(rv: Review, c: dict) -> bool:
    returned = rv.rec["round"]["returned"]
    cur = (rv.root / c["file"]).read_bytes() if (rv.root / c["file"]).exists() else None
    if c["status"] == "D":
        return cur is None
    r_bytes = gitutil.show_bytes(rv.root, returned, c["file"])
    if c["status"] == "R" and (rv.root / c["old_file"]).exists():
        return False
    return cur == r_bytes


def change_status(rv: Review, c: dict) -> str:
    if c["decision"]:
        return c["decision"]
    live = _file_live(rv, c) if c["type"] == "file" else locate(rv, c) is not None
    return "pending" if live else "superseded"


def changes(rv: Review) -> list[dict]:
    return [dict(c, state=change_status(rv, c)) for c in rv.rec["changes"]]


def _find_change(rv: Review, cid: str) -> dict:
    for c in rv.rec["changes"]:
        if c["id"] == cid:
            return c
    raise ScrivError(f"No change {cid} in round {rv.id}")


def decide(rv: Review, cid: str, decision: str, who: str, expect: str | None = None) -> dict:
    if rv.status != "returned":
        raise ScrivError("Changes are decided after the reviewer returns the round.")
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        rv.reload()
        c = _find_change(rv, cid)
        if c["decision"]:
            raise ScrivError(f"{cid} is already {c['decision']}")
        state = change_status(rv, c)
        if state == "superseded":
            raise Conflict(f"{cid} has been edited since the review, so it needs no decision.")
        if decision == "rejected":
            writes.update(_reject_writes(rv, c))
        c.update({"decision": decision, "by": who, "at": record.now()})
        writes[rv.record_rel] = record.dumps(rv.rec)
    return c


def _reject_writes(rv: Review, c: dict) -> dict:
    if c["type"] == "file":
        base = rv.rec["round"]["base"]
        old = gitutil.show_bytes(rv.root, base, c["old_file"] or c["file"])
        if c["status"] == "A":
            return {c["file"]: None}
        if c["status"] == "R":
            return {c["file"]: None, c["old_file"]: old}
        return {c["file"]: old}
    where = locate(rv, c)
    if where is None:
        raise Conflict(f"{c['id']} could not be located exactly; nothing was changed.")
    cur = rv.text(c["file"])
    s, e = where
    return {c["file"]: cur[:s] + c["base"] + cur[e:]}


# ---------------------------------------------------------------- anchoring


def _snapshot(rv: Review, text: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    p = local_dir(rv.root, rv.id) / "snapshots" / h
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
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


def resolve(rv: Review, a: dict) -> dict:
    """Locate an anchor in the current text: {'state': 'ok', 'range': [s, e]} or
    {'state': 'orphan', 'candidates': [...]}."""
    cur = rv.text(a["file"])
    if cur is None:
        return {"state": "orphan", "candidates": []}
    old = _text_at(rv, a["file"], a["rev"])
    if old is not None:
        pr = projection(old, cur)
        s, e = a["range"]
        r = pr.range(s, e)
        if r and cur[r[0] : r[1]] == a["exact"]:
            return {"state": "ok", "range": list(r)}
        left = pr.range(max(0, s - 16), s)
        right = pr.range(e, min(len(old), e + 16))
        if left and right and left[1] <= right[0]:
            return {"state": "ok", "range": [left[1], right[0]], "edited": True}
    hits = _occurrences(cur, a["exact"])
    if len(hits) == 1:
        return {"state": "ok", "range": [hits[0], hits[0] + len(a["exact"])]}
    if hits:
        scored = sorted(((_context_score(cur, i, a), i) for i in hits), reverse=True)
        full = len(a["prefix"]) + len(a["suffix"])
        if scored[0][0] == full and scored[1][0] < full:
            i = scored[0][1]
            return {"state": "ok", "range": [i, i + len(a["exact"])]}
    return {"state": "orphan", "candidates": [[i, i + len(a["exact"])] for i in hits]}


# ---------------------------------------------------------------- threads


def _thread(rv: Review, tid: str) -> dict:
    for t in rv.rec["threads"]:
        if t["id"] == tid:
            return t
    raise ScrivError(f"No thread {tid} in round {rv.id}")


def threads(rv: Review) -> list[dict]:
    return [dict(t, where=resolve(rv, t["anchor"])) for t in rv.rec["threads"]]


def comment(rv: Review, anchor: dict, body: str, who: str, expect: str | None = None) -> dict:
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        rv.reload()
        t = {
            "id": record.new_id("t"), "status": "open", "anchor": anchor,
            "messages": [{"id": record.new_id("m"), "by": who, "at": record.now(), "body": body}],
        }
        rv.rec["threads"].append(t)
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def reply(rv: Review, tid: str, body: str, who: str, expect: str | None = None) -> dict:
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        rv.reload()
        t = _thread(rv, tid)
        t["messages"].append({"id": record.new_id("m"), "by": who, "at": record.now(), "body": body})
        t["status"] = "open"
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def set_thread(rv: Review, tid: str, status: str, expect: str | None = None) -> dict:
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        rv.reload()
        t = _thread(rv, tid)
        t["status"] = status
        writes[rv.record_rel] = record.dumps(rv.rec)
    return t


def rebase_anchors(rv: Review, commit: str) -> list[str]:
    """Re-express every resolvable anchor against `commit`; return orphan ids."""
    orphans = []
    for t in rv.rec["threads"]:
        where = resolve(rv, t["anchor"])
        if where["state"] != "ok":
            orphans.append(t["id"])
            continue
        s, e = where["range"]
        text = rv.text(t["anchor"]["file"])
        t["anchor"].update({
            "rev": f"git:{commit}", "range": [s, e], "exact": text[s:e],
            "prefix": text[max(0, s - CONTEXT) : s], "suffix": text[e : e + CONTEXT],
        })
    return orphans


# ---------------------------------------------------------------- drafts


def drafts(rv: Review) -> list[dict]:
    return [dict(d, where=resolve(rv, d["anchor"])) for d in read_json(rv.root / rv.drafts_rel, [])]


def add_draft(rv: Review, anchor: dict, body: str, wording: str | None, expect: str | None = None) -> dict:
    if rv.status != "open":
        raise ScrivError("Drafts belong to the reviewer, before the round is returned.")
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        ds = read_json(rv.root / rv.drafts_rel, [])
        d = {"id": record.new_id("d"), "anchor": anchor, "body": body, "wording": wording, "at": record.now()}
        ds.append(d)
        writes[rv.drafts_rel] = json.dumps(ds, indent=2, ensure_ascii=False)
    return d


def triage(rv: Review, did: str, keep: bool, who: str, body: str | None = None,
           wording: str | None = None, expect: str | None = None) -> dict | None:
    """Keep a draft as the reviewer's own (applying any wording as a real edit),
    or drop it."""
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
        rv.reload()
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
        anchor = d["anchor"]
        if wording is not None:
            where = resolve(rv, anchor)
            if where["state"] != "ok":
                raise Conflict("The draft's passage has changed; its wording cannot be applied.")
            s, e = where["range"]
            cur = rv.text(anchor["file"])
            new = cur[:s] + wording + cur[e:]
            writes[anchor["file"]] = new
            anchor = {
                "file": anchor["file"], "rev": _snapshot(rv, new), "range": [s, s + len(wording)],
                "exact": wording, "prefix": new[max(0, s - CONTEXT) : s],
                "suffix": new[s + len(wording) : s + len(wording) + CONTEXT],
            }
        t = None
        if body and body.strip():
            t = {
                "id": record.new_id("t"), "status": "open", "anchor": anchor,
                "messages": [{"id": record.new_id("m"), "by": who, "at": record.now(), "body": body}],
            }
            rv.rec["threads"].append(t)
            writes[rv.record_rel] = record.dumps(rv.rec)
    return t


# ---------------------------------------------------------------- direct edits


def replace_lines(rv: Review, rel: str, first: int, last: int, new_text: str, expect: str | None = None) -> None:
    """Replace lines first..last (1-based, inclusive) of a manuscript file."""
    if rv.status == "closed":
        raise ScrivError("This round is closed.")
    if rel not in rv.ms.files:
        raise ScrivError(f"{rel} is not a manuscript file")
    with transaction(rv.root, expect, rv.watched() + [rv.drafts_rel]) as writes:
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
    }
