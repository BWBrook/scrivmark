import json
import secrets
from datetime import datetime
from pathlib import Path

import jsonschema

from .errors import ScrivError

REVIEWS = "reviews"

_person = {
    "type": "object",
    "required": ["name", "initials"],
    "properties": {"name": {"type": "string"}, "initials": {"type": "string"}},
}
_range = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}

SCHEMA = {
    "type": "object",
    "required": ["scrivmark", "round", "changes", "threads"],
    "properties": {
        "scrivmark": {"const": 1},
        "round": {
            "type": "object",
            "required": ["id", "reviewer", "author_branch", "base", "status", "statement"],
            "properties": {
                "id": {"type": "string"},
                "reviewer": _person,
                "author": {"anyOf": [_person, {"type": "null"}]},
                "author_branch": {"type": "string"},
                "base": {"type": "string"},
                "returned": {"type": ["string", "null"]},
                "returned_at": {"type": "string"},
                "integrated": {"type": ["string", "null"]},
                "closed": {"type": ["string", "null"]},
                "status": {"enum": ["open", "returned", "responded", "closed"]},
                "responded_at": {"type": "string"},
                "handovers": {"type": "array", "items": {
                    "type": "object", "required": ["to", "at"],
                    "properties": {"to": {"enum": ["author", "reviewer"]}, "at": {"type": "string"},
                                   "by": {"type": "string"}, "agent": {"type": "string"}}}},
                "statement": {"type": "string"},
                "forced": {"type": "boolean"},
            },
        },
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "file", "base", "reviewer", "decision"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"enum": ["text", "block", "file"]},
                    "file": {"type": "string"},
                    "old_file": {"type": ["string", "null"]},
                    "status": {"type": "string"},
                    "base": {"type": "string"},
                    "reviewer": {"type": "string"},
                    "r_range": {"anyOf": [_range, {"type": "null"}]},
                    "decision": {"enum": [None, "accepted", "rejected", "superseded"]},
                    "by": {"type": ["string", "null"]},
                    "at": {"type": ["string", "null"]},
                    "because": {"type": "string"},
                    "agent": {"type": "string"},
                    "contested": {"type": "array", "items": {
                        "type": "object", "required": ["by", "at", "because", "was"],
                        "properties": {"by": {"type": "string"}, "at": {"type": "string"},
                                       "because": {"type": "string"}, "was": {"type": "object"},
                                       "agent": {"type": "string"}}}},
                },
            },
        },
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "status", "anchor", "messages"],
                "properties": {
                    "id": {"type": "string"},
                    "status": {"enum": ["open", "resolved"]},
                    "resolved_by": {"type": "object", "required": ["by", "at"],
                                    "properties": {"by": {"type": "string"}, "at": {"type": "string"},
                                                   "agent": {"type": "string"}}},
                    "anchor": {
                        "type": "object",
                        "required": ["file", "rev", "range", "exact", "prefix", "suffix"],
                        "properties": {
                            "file": {"type": "string"},
                            "rev": {"type": "string"},
                            "range": _range,
                            "exact": {"type": "string"},
                            "prefix": {"type": "string"},
                            "suffix": {"type": "string"},
                        },
                    },
                    "messages": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["id", "by", "at", "body"],
                            "properties": {
                                "id": {"type": "string"},
                                "by": {"type": "string"},
                                "at": {"type": "string"},
                                "body": {"type": "string"},
                                "imported_at": {"type": "string"},
                                "edited_at": {"type": "string"},
                                "agent": {"type": "string"},
                                "drafted_by": {"type": "string"},
                                "draft_history": {"type": "array", "items": {"type": "object", "properties": {
                    "body": {"type": "string"}, "wording": {"type": ["string", "null"]}, "at": {"type": "string"},
                    "by": {"type": "string"}}}},
                            },
                        },
                    },
                },
            },
        },
        "drafted_edits": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["by", "file", "was", "wording", "at"],
                "properties": {"by": {"type": "string"}, "file": {"type": "string"}, "was": {"type": "string"},
                               "wording": {"type": "string"}, "at": {"type": "string"},
                               "history": {"type": "array", "items": {"type": "object", "properties": {
                    "body": {"type": "string"}, "wording": {"type": ["string", "null"]}, "at": {"type": "string"},
                    "by": {"type": "string"}}}}},
            },
        },
        "asks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "anchor", "question", "answer", "at"],
                "properties": {
                    "id": {"type": "string"},
                    "anchor": {"type": "object", "required": ["file", "exact"]},
                    "question": {"type": "string"},
                    "by": {"type": ["string", "null"]},
                    "answer": {"type": "string"},
                    "answered_by": {"type": ["string", "null"]},
                    "wording": {"type": ["string", "null"]},
                    "applied": {"type": ["string", "null"]},
                    "done": {"type": "boolean"},
                },
            },
        },
    },
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def path(root: Path, round_id: str) -> Path:
    return root / REVIEWS / f"{round_id}.json"


def validate(rec: dict) -> dict:
    try:
        jsonschema.validate(rec, SCHEMA)
    except jsonschema.ValidationError as e:
        raise ScrivError(f"Review record is invalid: {e.message} at {list(e.absolute_path)}") from e
    return rec


def load(root: Path, round_id: str) -> dict:
    p = path(root, round_id)
    if not p.exists():
        raise ScrivError(f"No review record {p.relative_to(root)}")
    return validate(json.loads(p.read_text()))


def dumps(rec: dict) -> str:
    return json.dumps(validate(rec), indent=2, ensure_ascii=False) + "\n"


def blank(round_id: str, reviewer: dict, author_branch: str, base: str, statement: str) -> dict:
    return {
        "scrivmark": 1,
        "round": {
            "id": round_id,
            "reviewer": reviewer,
            "author": None,
            "author_branch": author_branch,
            "base": base,
            "returned": None,
            "integrated": None,
            "closed": None,
            "status": "open",
            "statement": statement,
        },
        "changes": [],
        "threads": [],
    }
