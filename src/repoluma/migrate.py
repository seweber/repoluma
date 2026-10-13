"""Mechanical part of moving a map from schema_version 2 to 3.

What can be done without reading the code is done here, keeping the file's comments,
order and formatting: ``schema_version`` becomes 3 and ``evidence:`` becomes
``certainty:``. The result is parsed again and compared with the original so that
nothing else changed. Everything that needs judgment is reported, not guessed:

* relationships that end on a container (which item inside does the work?);
* observed data relationships without a cited transfer or receive site;
* opposite data relationships between the same two nodes, which *may* be one exchange
  (only the author can tell: same endpoints do not make a request and its result).

Nothing is written over the map unless ``--in-place`` is given.
"""

from __future__ import annotations

import re
from typing import Any

from .model import SITE_ROLES, parse_yaml, validate_document

# `evidence:` as a mapping key: at the start of a line (block style, possibly after "- ")
# or after "{" / "," inside a flow mapping. Values that merely contain the word are kept.
_KEY = re.compile(r"(?P<pre>(?:^[ \t]*(?:-[ \t]+)?)|[{,][ \t]*)evidence(?P<post>[ \t]*:)", re.MULTILINE)
_VERSION = re.compile(r"^(schema_version[ \t]*:[ \t]*)2\b", re.MULTILINE)


def migrate_text(text: str) -> tuple[str | None, list[str], list[str]]:
    """Returns (new text or None, notes on what changed, decisions left to the author)."""
    doc, err = parse_yaml(text)
    if err:
        return None, [], [f"the map cannot be parsed: {err.message} (line {err.line})"]
    if not isinstance(doc, dict) or doc.get("schema_version") != 2:
        version = doc.get("schema_version") if isinstance(doc, dict) else None
        return None, [], [f"only schema_version 2 maps are migrated (this one has {version!r})"]
    new, n_version = _VERSION.subn(r"\g<1>3", text, count=1)
    new, n_renamed = _KEY.subn(r"\g<pre>certainty\g<post>", new)
    notes = [f"schema_version: 2 -> 3 ({n_version} change)",
             f"evidence -> certainty ({n_renamed} fields)"]
    problem = _compare(doc, parse_yaml(new)[0])
    if problem:
        why = (f"the text rewrite would change more than intended ({problem}); "
               "rename 'evidence:' to 'certainty:' by hand")
        return None, notes, [why]
    return new, notes, _decisions(doc)


def _rename(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {("certainty" if k == "evidence" else k): _rename(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rename(x) for x in obj]
    return obj


def _compare(old: Any, new: Any) -> str | None:
    expected = _rename(old)
    expected["schema_version"] = 3
    if expected == new:
        return None
    return "the parsed result differs from a plain rename"


def _decisions(doc: dict[str, Any]) -> list[str]:
    result = validate_document(doc)
    model = result.model
    if model is None:
        return ["the map has errors that prevent a model; run `repoluma validate` first"]
    out = []
    by_id = {n["id"]: n for n in model["nodes"]}
    for r in model["relationships"]:
        if r.get("group_endpoint"):
            ends = ", ".join(r["group_endpoint"])
            out.append(f"{r['id']} (line {r['line']}): ends on the container {ends}; end it on "
                       "the item inside that does the work, or remove it. Do not draw one line "
                       "per child unless each takes part")
    for r in model["relationships"]:
        if r["type"] in ("data", "error") and r["certainty"] == "observed" and not any(
            x["role"] in SITE_ROLES for x in r["refs"]
        ):
            out.append(f"{r['id']} (line {r['line']}): observed data without a cited site; add a "
                       "ref with role transfer/receive and focus, or lower certainty to inferred "
                       "and say why in notes")
    seen = set()
    for r in model["relationships"]:
        if r["type"] != "data":
            continue
        for s in model["relationships"]:
            if s is r or s["type"] != "data" or (s["id"], r["id"]) in seen:
                continue
            if s["from"] == r["to"] and s["to"] == r["from"]:
                seen.add((r["id"], s["id"]))
                a, b = by_id[r["from"]]["label"], by_id[r["to"]]["label"]
                out.append(f"{r['id']} / {s['id']}: opposite data lines between {a} and {b}; if "
                           "one is what a call returns for the other, give both the same "
                           "`exchange` with `leg: input` / `leg: result` (check the code: same "
                           "endpoints alone do not make an exchange)")
    return out
