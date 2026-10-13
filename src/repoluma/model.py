"""Loading, validating, and normalizing a repoluma map.

The map is authored YAML (see ``docs/schema.md``). Validation is lenient by
design: it records diagnostics and still produces a normalized model so the
viewer can show a partial or broken map together with its problems. Only an
unreadable document is fatal.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")

NODE_KINDS = {
    "system": "The whole system or a deployable product.",
    "domain": "A broad area of purpose.",
    "capability": "Something the system lets someone accomplish.",
    "responsibility": "A coherent role performed by part of the implementation.",
    "component": "A concrete implementation unit (module, service, class group).",
    "contract": "An interface: inputs, outputs, guarantees, errors, assumptions.",
    "rule": "A business rule or invariant that must hold.",
    "resource": "Shared state, storage, queue, cache, or file.",
    "data": "An important data type or record.",
    "external": "A system outside this repository.",
    "actor": "A person or role interacting with the system.",
    "test": "A test suite or verification mechanism.",
    "area": "A neutral grouping when no stronger concept fits.",
}

# Relationship types. The arrow always points from `from` to `to`; what that means
# depends on the type. Data is preferred: if something moves, model the movement.
RELATIONSHIP_TYPES = {
    "data": "data moves from `from` to `to`; the label names the data",
    "error": "information about a failure or problem (an exception, an error value, a "
    "reported diagnostic) moves from where it is raised or found (`from`) to where it is "
    "handled or read (`to`); the label names it",
    "control": "`from` starts, calls or drives `to` (when no data movement says it)",
    "dependency": "`from` relies on, implements, is constrained or verified by `to`",
}
# Optional detail per type (for filtering and the inspector).
RELATIONSHIP_KINDS = {
    "data": ("event",),
    "error": (),
    "control": ("calls", "requests", "triggers", "delegates", "renders", "schedules"),
    "dependency": (
        "relies_on",
        "depends_on",
        "implements",
        "enforces",
        "validates",
        "configures",
        "tests",
        "documents",
    ),
}
KIND_TYPE = {k: t for t, kinds in RELATIONSHIP_KINDS.items() for k in kinds}
# schema_version 1 verbs: (type, kind, reversed). "A reads B" is data from B to A.
V1_KINDS = {
    "calls": ("control", "calls", False),
    "requests": ("control", "requests", False),
    "triggers": ("control", "triggers", False),
    "delegates": ("control", "delegates", False),
    "renders": ("control", "renders", False),
    "publishes": ("data", "event", False),
    "emits": ("data", "event", False),
    "subscribes": ("data", "event", True),
    "handles": ("data", "event", True),
    "reads": ("data", None, True),
    "writes": ("data", None, False),
    "stores": ("data", None, False),
    "transforms": ("data", None, False),
    "provides": ("data", None, False),
    "implements": ("dependency", "implements", False),
    "relies_on": ("dependency", "relies_on", False),
    "enforces": ("dependency", "enforces", False),
    "validates": ("dependency", "validates", False),
    "depends_on": ("dependency", "depends_on", False),
    "configures": ("dependency", "configures", False),
    "tests": ("dependency", "tests", False),
    "documents": ("dependency", "documents", False),
}

# How sure the author is of a claim (`certainty`; `evidence` in schema_version 1 and 2).
# This is about the claim, not about whether its references resolve or are fresh.
CERTAINTY_STATES = {
    "observed": "The author read the code and it does this (not executed or verified).",
    "inferred": "Interpretation drawn from source, tests, or docs.",
    "needs_review": "Plausible but not checked carefully; treat with caution.",
    "unknown": "Not established; placeholder for a known gap.",
    "planned": "Not implemented yet; describes intended behavior.",
}
EVIDENCE_STATES = CERTAINTY_STATES  # name used by schema_version 1 and 2

CONFIDENCE = ("low", "medium", "high")
STEP_KINDS = ("main", "alternative", "failure", "compensation")
REF_ROLES = ("implementation", "test", "config", "docs", "schema", "example", "entrypoint")
# Roles of a relationship's refs: where the data is made, handed over, and taken in.
PROVENANCE_ROLES = {
    "origin": "where the data is created, computed, assembled or configured",
    "transfer": "where it is handed over: the call's arguments, a return, a publish, a write",
    "receive": "where the other end takes it in: a parameter, the assignment of a result, a read",
}
SITE_ROLES = ("transfer", "receive")  # either one grounds an observed data or error line
CARRIES = ("data", "error")  # relationship types that move something, named by their label
# The legs of an exchange (relationships sharing an `exchange` id), seen from the caller.
EXCHANGE_LEGS = {
    "input": "what the caller supplies (data or control); from the caller to the callee",
    "result": "what is returned synchronously; from the callee back to the caller",
    "response": "what arrives later (callback, resolved promise, reply message); back to the caller",
    "error": "failure information returned instead of a result (an exception, an error "
    "value); a relationship of type error",
}
CONTRACT_FIELDS = ("inputs", "outputs", "guarantees", "errors", "assumptions")
FOCUS_MAX = 8  # focus anchors per ref

TOP_FIELDS = {
    "schema_version",
    "repository",
    "scope",
    "source",
    "nodes",
    "relationships",
    "flows",
}
REPOSITORY_FIELDS = {"name", "description"}
SCOPE_FIELDS = {"summary", "covered", "not_covered", "reviewed_revision", "reviewed_at"}
SOURCE_FIELDS = {"exclude", "ownership"}
OWNERSHIP_FIELDS = {"ignore", "require"}
NODE_FIELDS = {
    "id",
    "label",
    "kind",
    "parent",
    "summary",
    "details",
    "contract",
    "tags",
    "certainty",
    "evidence",
    "confidence",
    "refs",
    "owns",
    "notes",
}
REL_FIELDS = {
    "id",
    "from",
    "to",
    "type",
    "kind",
    "label",
    "summary",
    "exchange",
    "leg",
    "certainty",
    "evidence",
    "confidence",
    "refs",
    "notes",
}
FLOW_FIELDS = {
    "id", "label", "summary", "certainty", "evidence", "confidence", "steps", "tags", "notes"
}
STEP_FIELDS = {"id", "node", "label", "via", "after", "kind", "refs", "notes"}
REF_FIELDS = {"path", "symbol", "lines", "role", "note", "focus"}
GLOB_CHARS = set("*?[")


@dataclass
class Diagnostic:
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    location: str = ""  # JSON-path-like, e.g. nodes[3].parent
    line: int | None = None
    record: str | None = None  # id of the record concerned, when known
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def format(self, map_path: str = "map") -> str:
        where = f"{map_path}:{self.line}" if self.line else map_path
        rec = f" [{self.record}]" if self.record else ""
        loc = f" {self.location}:" if self.location else ""
        text = f"{self.severity:<7} {where}{rec}{loc} {self.message} ({self.code})"
        if self.hint:
            text += f"\n        hint: {self.hint}"
        return text


@dataclass
class LoadResult:
    model: dict[str, Any] | None
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


# --------------------------------------------------------------------------- YAML


class _LineDict(dict):
    """A dict that remembers the 1-based source line of its YAML mapping."""

    line: int | None = None


class _LineLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):  # libyaml when available
    """Safe loader that records line numbers on mappings (no arbitrary objects)."""


def _construct_mapping(loader: _LineLoader, node: yaml.MappingNode) -> _LineDict:
    loader.flatten_mapping(node)
    result = _LineDict()
    result.line = node.start_mark.line + 1
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, (str, int, float, bool)) and key is not None:
            raise yaml.constructor.ConstructorError(
                None, None, "mapping keys must be scalars", key_node.start_mark
            )
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)
# Keep timestamps/dates as plain strings: authors write revisions and dates as text.
_LineLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_yaml(text: str) -> tuple[Any, Diagnostic | None]:
    try:
        return yaml.load(text, Loader=_LineLoader), None
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        if mark is not None:
            line = mark.line + 1
        problem = getattr(exc, "problem", None) or str(exc)
        return None, Diagnostic(
            "error",
            "yaml-syntax",
            f"YAML could not be parsed: {problem}",
            line=line,
            hint="Fix indentation/quoting. Quote strings containing ': ' or starting with '{', "
            "'[', '*', '&', '!', '%', '@' or '`'. Inside inline {...} mappings, also quote "
            "values containing commas. Only the first syntax error is reported per run.",
        )


# --------------------------------------------------------------------- validator


def _line(obj: Any) -> int | None:
    return getattr(obj, "line", None)


def _suggest(value: str, options: Any) -> str | None:
    match = difflib.get_close_matches(str(value), list(options), n=1, cutoff=0.6)
    return match[0] if match else None


def parse_lines(value: Any) -> tuple[tuple[int, int] | None, str | None]:
    """Parse a ``lines`` value: 12, "12", "12-40", [12, 40]. Returns ((start, end), error)."""
    if value is None:
        return None, None
    start = end = None
    if isinstance(value, bool):
        return None, "lines must be a number, 'start-end', or [start, end]"
    if isinstance(value, int):
        start = end = value
    elif isinstance(value, str):
        m = re.fullmatch(r"\s*(\d+)\s*(?:[-–:]\s*(\d+)\s*)?", value)
        if not m:
            return None, f"cannot parse lines {value!r}; use '12-40'"
        start = int(m.group(1))
        end = int(m.group(2) or m.group(1))
    elif (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        start, end = value
    else:
        return None, "lines must be a number, 'start-end', or [start, end]"
    if start < 1 or end < start:
        return None, f"invalid line range {start}-{end}; lines are 1-based and start <= end"
    return (start, end), None


def parse_ref_shorthand(value: str) -> dict[str, Any]:
    """``path#Symbol``, ``path:12-40`` or plain ``path``."""
    if "#" in value:
        path, symbol = value.split("#", 1)
        return {"path": path, "symbol": symbol}
    m = re.fullmatch(r"(.+?):(\d+(?:-\d+)?)", value)
    if m:
        return {"path": m.group(1), "lines": m.group(2)}
    return {"path": value}


class _Validator:
    def __init__(self) -> None:
        self.diags: list[Diagnostic] = []
        self.version: Any = CURRENT_SCHEMA_VERSION

    def add(self, severity, code, message, location="", obj=None, record=None, hint=None):
        self.diags.append(Diagnostic(severity, code, message, location, _line(obj), record, hint))

    # -- helpers -------------------------------------------------------------
    def check_fields(self, obj: dict, allowed: set[str], location: str, record=None):
        for key in obj:
            if not isinstance(key, str):
                self.add("error", "bad-key", f"non-string key {key!r}", location, obj, record)
                continue
            if key in allowed or key.startswith("x-"):
                continue
            guess = _suggest(key, allowed)
            self.add(
                "warning",
                "unknown-field",
                f"unknown field '{key}' is ignored",
                f"{location}.{key}" if location else key,
                obj,
                record,
                hint=f"did you mean '{guess}'?"
                if guess
                else f"allowed: {', '.join(sorted(allowed))}; prefix custom fields with 'x-'",
            )

    def text(self, obj: dict, key: str, location: str, record=None, required=False) -> str | None:
        value = obj.get(key)
        if value is None:
            if required:
                self.add("error", "missing-field", f"'{key}' is required", location, obj, record)
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str):
            self.add("error", "bad-type", f"'{key}' must be text", f"{location}.{key}", obj, record)
            return None
        value = value.strip()
        if required and not value:
            self.add("error", "missing-field", f"'{key}' must not be empty", location, obj, record)
            return None
        return value

    def text_list(self, obj: dict, key: str, location: str, record=None) -> list[str]:
        value = obj.get(key)
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()]
        if not isinstance(value, list):
            self.add(
                "error",
                "bad-type",
                f"'{key}' must be a list of text",
                f"{location}.{key}",
                obj,
                record,
            )
            return []
        out = []
        for i, item in enumerate(value):
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                out.append(str(item).strip())
            else:
                self.add(
                    "error",
                    "bad-type",
                    f"'{key}[{i}]' must be text",
                    f"{location}.{key}[{i}]",
                    obj,
                    record,
                )
        return out

    def enum(self, obj, key, options, location, record=None, severity="warning") -> str | None:
        value = self.text(obj, key, location, record)
        if value is None:
            return None
        if value not in options:
            guess = _suggest(value, options)
            self.add(
                severity,
                f"unknown-{key}",
                f"unknown {key} '{value}'",
                f"{location}.{key}",
                obj,
                record,
                hint=(f"did you mean '{guess}'? " if guess else "")
                + f"known: {', '.join(options)}",
            )
        return value

    def ident(self, obj, location, record_kind) -> str | None:
        raw = obj.get("id")
        if raw is None:
            return None
        if not isinstance(raw, str) or not ID_RE.match(raw):
            self.add(
                "error",
                "bad-id",
                f"{record_kind} id {raw!r} is invalid",
                f"{location}.id",
                obj,
                hint="ids are lowercase letters, digits, '.', '_' or '-', start alphanumeric, "
                "max 80 chars, e.g. 'authorize-payment'",
            )
            return None
        return raw

    def refs(self, obj: dict, location: str, record=None, provenance=False) -> list[dict[str, Any]]:
        value = obj.get("refs")
        if value is None:
            return []
        if not isinstance(value, list):
            self.add("error", "bad-type", "'refs' must be a list", f"{location}.refs", obj, record)
            return []
        out = []
        for i, item in enumerate(value):
            loc = f"{location}.refs[{i}]"
            if isinstance(item, str):
                item_d = parse_ref_shorthand(item.strip())
                line_obj = obj
            elif isinstance(item, dict):
                self.check_fields(item, REF_FIELDS, loc, record)
                item_d = dict(item)
                line_obj = item
            else:
                self.add(
                    "error",
                    "bad-ref",
                    "a ref must be a mapping or 'path#Symbol' text",
                    loc,
                    obj,
                    record,
                )
                continue
            path = item_d.get("path")
            if not isinstance(path, str) or not path.strip():
                self.add(
                    "error",
                    "bad-ref",
                    "ref needs a repository-relative 'path'",
                    loc,
                    line_obj,
                    record,
                )
                continue
            path = path.strip()
            path_problem = check_relative_path(path)
            if path_problem:
                self.add(
                    "error",
                    "bad-ref-path",
                    f"ref path {path!r}: {path_problem}",
                    loc,
                    line_obj,
                    record,
                    hint="use a forward-slash path relative to the "
                    "repository root, e.g. 'src/app/service.py'",
                )
                continue
            symbol = item_d.get("symbol")
            if symbol is not None and (not isinstance(symbol, str) or not symbol.strip()):
                self.add("error", "bad-ref", "ref 'symbol' must be text", loc, line_obj, record)
                symbol = None
            lines, err = parse_lines(item_d.get("lines"))
            if err:
                self.add("error", "bad-ref-lines", err, f"{loc}.lines", line_obj, record)
            role = item_d.get("role")
            roles = REF_ROLES + tuple(PROVENANCE_ROLES) if provenance else REF_ROLES
            if role is not None and role not in roles:
                guess = _suggest(role, roles)
                where = "" if provenance or role not in PROVENANCE_ROLES else (
                    "; origin, transfer and receive describe where data moves and belong "
                    "on relationship refs"
                )
                self.add(
                    "warning",
                    "unknown-role",
                    f"unknown ref role '{role}'" + where,
                    f"{loc}.role",
                    line_obj,
                    record,
                    hint=f"did you mean '{guess}'?" if guess and not where
                    else f"known: {', '.join(roles)}",
                )
            focus = self.focus(item_d.get("focus"), f"{loc}.focus", line_obj, record)
            note = item_d.get("note")
            out.append(
                {
                    "path": path,
                    "symbol": symbol.strip() if isinstance(symbol, str) else None,
                    "lines": list(lines) if lines else None,
                    "role": role if isinstance(role, str) else None,
                    "focus": focus,
                    "note": str(note) if note is not None else None,
                    "location": loc,
                    "line": _line(line_obj),
                }
            )
        return out

    def focus(self, value, location, obj, record) -> list[str] | None:
        """``focus``: exact text (or several) inside the ref's range that the claim is about."""
        if value is None:
            return None
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items:
            self.add(
                "error", "bad-focus", "'focus' must be text or a list of text", location, obj,
                record,
            )
            return None
        out = []
        for i, item in enumerate(items):
            text = " ".join(str(item).split()) if isinstance(item, (str, int, float)) else ""
            if isinstance(item, bool) or not text:
                self.add(
                    "error",
                    "bad-focus",
                    f"focus[{i}] must be non-empty text copied from the source",
                    location,
                    obj,
                    record,
                )
                continue
            out.append(text)
        if len(out) > FOCUS_MAX:
            self.add(
                "warning",
                "too-many-focus",
                f"{len(out)} focus anchors in one ref; only the first {FOCUS_MAX} are used",
                location,
                obj,
                record,
                hint="split the evidence into several refs, one per site",
            )
            out = out[:FOCUS_MAX]
        return out or None

    def owns(self, obj: dict, location: str, record=None) -> list[dict[str, Any]]:
        """``owns``: files (exact paths, globs, ``dir/``) or ``path#Symbol`` parts of a file."""
        value = obj.get("owns")
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            self.add("error", "bad-type", "'owns' must be a list", f"{location}.owns", obj, record)
            return []
        out = []
        for i, item in enumerate(value):
            loc = f"{location}.owns[{i}]"
            if not isinstance(item, str) or not item.strip():
                self.add(
                    "error",
                    "bad-owns",
                    "an 'owns' entry must be a path, a glob, 'dir/' or 'path#Symbol'",
                    loc,
                    obj,
                    record,
                )
                continue
            pattern, _, symbol = item.strip().partition("#")
            symbol = symbol.strip() or None
            problem = check_pattern(pattern)
            if not problem and symbol and (GLOB_CHARS & set(pattern) or pattern.endswith("/")):
                problem = "a '#Symbol' part needs one exact file path, not a glob or directory"
            if problem:
                self.add(
                    "error",
                    "bad-owns",
                    f"owns {item.strip()!r}: {problem}",
                    loc,
                    obj,
                    record,
                    hint="use a repository-relative path ('src/app/service.py'), a glob "
                    "('src/app/**', 'src/*.py'), a directory ('src/app/') or "
                    "'src/app/util.py#Symbol' for part of a file",
                )
                continue
            out.append({"pattern": pattern, "symbol": symbol, "location": loc, "line": _line(obj)})
        return out

    def common(self, obj, location, record, provenance=False) -> dict[str, Any]:
        out = {}
        key = "certainty"
        if "evidence" in obj and "certainty" not in obj:
            key = "evidence"
            if self.version == CURRENT_SCHEMA_VERSION:
                self.add(
                    "warning",
                    "renamed-field",
                    "'evidence' is called 'certainty' in schema_version 3 (how sure the claim "
                    "is; the source evidence is 'refs')",
                    f"{location}.evidence",
                    obj,
                    record,
                    hint="rename 'evidence:' to 'certainty:'",
                )
        out["certainty"] = self.enum(obj, key, CERTAINTY_STATES, location, record)
        out["confidence"] = self.enum(obj, "confidence", CONFIDENCE, location, record)
        out["refs"] = self.refs(obj, location, record, provenance)
        out["notes"] = self.text(obj, "notes", location, record)
        return out


def check_relative_path(path: str) -> str | None:
    if "\\" in path:
        return "use forward slashes"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return "must be relative to the repository root"
    parts = path.split("/")
    if any(p == ".." for p in parts):
        return "must not contain '..'"
    if any(p == "" for p in parts[:-1]) or path.endswith("/"):
        return "must name a file (no empty segments or trailing '/')"
    if "\x00" in path:
        return "must not contain NUL"
    return None


def check_pattern(pattern: str) -> str | None:
    """Problems with an ``owns`` pattern: like a path, but globs and a trailing '/' are fine."""
    if not pattern:
        return "empty pattern"
    if pattern in ("**", "**/", "*"):
        return "would own the whole repository; name the directories this node is about"
    return check_relative_path(pattern.removesuffix("/"))


def validate_document(doc: Any) -> LoadResult:
    """Validate a parsed YAML document and build the normalized model."""
    v = _Validator()
    if doc is None:
        v.add(
            "error",
            "empty",
            "the map is empty",
            hint="start from `repoluma init` or the example in `repoluma guide`",
        )
        return LoadResult(None, v.diags)
    if not isinstance(doc, dict):
        v.add("error", "bad-document", "the map must be a YAML mapping at the top level")
        return LoadResult(None, v.diags)

    v.check_fields(doc, TOP_FIELDS, "")
    v.version = doc.get("schema_version")

    version = doc.get("schema_version")
    if version is None:
        v.add(
            "error",
            "missing-schema-version",
            "'schema_version' is required",
            "schema_version",
            doc,
            hint=f"add `schema_version: {CURRENT_SCHEMA_VERSION}` at the top",
        )
    elif version == 1:
        v.add(
            "warning",
            "old-schema-version",
            "schema_version 1 is read with conversions: relationship verbs become data, "
            "control or dependency relationships, and 'reads', 'subscribes' and 'handles' "
            "are reversed so that arrows show where data goes",
            "schema_version",
            doc,
            hint=f"update to schema_version: {CURRENT_SCHEMA_VERSION}; `repoluma guide` explains "
            "relationship types",
        )
    elif version == 2:
        v.add(
            "info",
            "old-schema-version",
            "schema_version 2 is read as before: lines may still end on a container, "
            "'evidence' means certainty, and observed data relationships need no cited site",
            "schema_version",
            doc,
            hint=f"to migrate to schema_version {CURRENT_SCHEMA_VERSION}, follow 'Migrating from "
            "schema_version 2' in `repoluma guide`",
        )
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        newer = isinstance(version, int) and version > max(SUPPORTED_SCHEMA_VERSIONS)
        v.add(
            "error",
            "unsupported-schema-version",
            f"schema_version {version!r} is not supported by this repoluma "
            f"(supports {', '.join(map(str, SUPPORTED_SCHEMA_VERSIONS))})",
            "schema_version",
            doc,
            hint="upgrade repoluma" if newer else f"use schema_version: {CURRENT_SCHEMA_VERSION}",
        )

    repo = doc.get("repository") or {}
    if not isinstance(repo, dict):
        v.add("error", "bad-type", "'repository' must be a mapping", "repository", doc)
        repo = {}
    v.check_fields(repo, REPOSITORY_FIELDS, "repository")
    repository = {
        "name": v.text(repo, "name", "repository"),
        "description": v.text(repo, "description", "repository"),
    }

    scope_raw = doc.get("scope")
    if scope_raw is None:
        v.add(
            "warning",
            "missing-scope",
            "no 'scope' given; readers cannot tell what is modeled",
            "scope",
            doc,
            hint="add scope.summary and scope.not_covered",
        )
        scope_raw = {}
    if not isinstance(scope_raw, dict):
        v.add("error", "bad-type", "'scope' must be a mapping", "scope", doc)
        scope_raw = {}
    v.check_fields(scope_raw, SCOPE_FIELDS, "scope")
    scope = {
        "summary": v.text(scope_raw, "summary", "scope"),
        "covered": v.text_list(scope_raw, "covered", "scope"),
        "not_covered": v.text_list(scope_raw, "not_covered", "scope"),
        "reviewed_revision": v.text(scope_raw, "reviewed_revision", "scope"),
        "reviewed_at": v.text(scope_raw, "reviewed_at", "scope"),
    }

    source_raw = doc.get("source") or {}
    if not isinstance(source_raw, dict):
        v.add("error", "bad-type", "'source' must be a mapping", "source", doc)
        source_raw = {}
    v.check_fields(source_raw, SOURCE_FIELDS, "source")
    source = {"exclude": v.text_list(source_raw, "exclude", "source"), "ownership": {}}
    own_raw = source_raw.get("ownership") or {}
    if not isinstance(own_raw, dict):
        v.add("error", "bad-type", "'source.ownership' must be a mapping", "source.ownership", doc)
        own_raw = {}
    v.check_fields(own_raw, OWNERSHIP_FIELDS, "source.ownership")
    for key in sorted(OWNERSHIP_FIELDS):
        patterns = []
        for i, pat in enumerate(v.text_list(own_raw, key, "source.ownership")):
            problem = check_pattern(pat)
            if problem:
                v.add(
                    "error",
                    "bad-owns",
                    f"source.ownership.{key}[{i}] {pat!r}: {problem}",
                    f"source.ownership.{key}[{i}]",
                    own_raw,
                )
            else:
                patterns.append(pat)
        source["ownership"][key] = patterns

    ids: dict[str, str] = {}  # id -> location, shared namespace for nodes/relationships/flows

    def claim(ident: str, location: str, obj) -> bool:
        if ident in ids:
            v.add(
                "error",
                "duplicate-id",
                f"id '{ident}' is already used at {ids[ident]}",
                location,
                obj,
                ident,
                hint="ids must be unique across nodes, relationships "
                "and flows; keep the existing id stable and rename the new record",
            )
            return False
        ids[ident] = location
        return True

    # ---- nodes
    nodes: list[dict[str, Any]] = []
    raw_nodes = doc.get("nodes")
    if raw_nodes is None:
        raw_nodes = []
        v.add(
            "warning",
            "no-nodes",
            "the map has no nodes yet",
            "nodes",
            doc,
            hint="describe the system's main capabilities first",
        )
    if not isinstance(raw_nodes, list):
        v.add("error", "bad-type", "'nodes' must be a list", "nodes", doc)
        raw_nodes = []
    for i, raw in enumerate(raw_nodes):
        loc = f"nodes[{i}]"
        if not isinstance(raw, dict):
            v.add("error", "bad-type", "each node must be a mapping", loc, raw_nodes)
            continue
        ident = v.ident(raw, loc, "node")
        if ident is None:
            if "id" not in raw:
                v.add(
                    "error",
                    "missing-field",
                    "'id' is required",
                    loc,
                    raw,
                    hint="give the node a stable kebab-case id",
                )
            continue
        if not claim(ident, loc, raw):
            continue
        v.check_fields(raw, NODE_FIELDS, loc, ident)
        label = v.text(raw, "label", loc, ident) or ident
        if "label" not in raw:
            v.add("warning", "missing-label", "node has no label; showing its id", loc, raw, ident)
        kind = v.enum(raw, "kind", NODE_KINDS, loc, ident) or "area"
        contract = None
        if raw.get("contract") is not None:
            c = raw["contract"]
            if not isinstance(c, dict):
                v.add(
                    "error",
                    "bad-type",
                    "'contract' must be a mapping",
                    f"{loc}.contract",
                    raw,
                    ident,
                )
            else:
                v.check_fields(c, set(CONTRACT_FIELDS), f"{loc}.contract", ident)
                contract = {f: v.text_list(c, f, f"{loc}.contract", ident) for f in CONTRACT_FIELDS}
        node = {
            "id": ident,
            "label": label,
            "kind": kind,
            "parent": v.text(raw, "parent", loc, ident),
            "summary": v.text(raw, "summary", loc, ident),
            "details": v.text(raw, "details", loc, ident),
            "contract": contract,
            "tags": v.text_list(raw, "tags", loc, ident),
            "line": _line(raw),
            "location": loc,
        }
        node.update(v.common(raw, loc, ident))
        node["owns"] = v.owns(raw, loc, ident)
        nodes.append(node)

    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        p = n["parent"]
        if p is None:
            continue
        if p not in by_id:
            guess = _suggest(p, by_id)
            v.add(
                "error",
                "unknown-parent",
                f"parent '{p}' does not exist; placed at top level",
                f"{n['location']}.parent",
                None,
                n["id"],
                hint=f"did you mean '{guess}'?"
                if guess
                else "define the parent node or remove 'parent'",
            )
            v.diags[-1].line = n["line"]
            n["parent"] = None
        elif p == n["id"]:
            v.add(
                "error",
                "self-parent",
                "a node cannot be its own parent",
                n["location"],
                None,
                n["id"],
            )
            v.diags[-1].line = n["line"]
            n["parent"] = None
    # containment cycles
    for n in nodes:
        seen = []
        cur = n
        while cur["parent"] is not None:
            if cur["id"] in seen:
                break
            seen.append(cur["id"])
            cur = by_id[cur["parent"]]
            if cur["id"] == n["id"]:
                chain = " -> ".join(seen + [n["id"]])
                v.add(
                    "error",
                    "containment-cycle",
                    f"containment cycle: {chain}; '{n['id']}' placed at top level",
                    n["location"] + ".parent",
                    None,
                    n["id"],
                    hint="containment must be a tree; express other links as relationships",
                )
                v.diags[-1].line = n["line"]
                n["parent"] = None
                break

    children: dict[str | None, list[str]] = {}
    for n in nodes:
        children.setdefault(n["parent"], []).append(n["id"])
    for n in nodes:
        n["children"] = children.get(n["id"], [])

    def depth(nid: str) -> int:
        d = 0
        cur = by_id[nid]
        while cur["parent"] is not None:
            d += 1
            cur = by_id[cur["parent"]]
        return d

    # files belong to the outermost boxes (areas); inner boxes show code through refs
    for n in nodes:
        if n["owns"] and n["parent"] is not None:
            area = by_id[n["parent"]]
            while area["parent"] is not None:
                area = by_id[area["parent"]]
            v.add(
                "error",
                "owns-not-top-level",
                f"'owns' belongs on top-level nodes; counted for '{area['id']}' for now",
                f"{n['location']}.owns",
                None,
                n["id"],
                hint=f"move these patterns to '{area['id']}' (the area this node is in); "
                "inner nodes show their code with refs, and the rest of each file is shown "
                "in its area",
            )
            v.diags[-1].line = n["line"]
            area["owns"] = area["owns"] + n["owns"]
            n["owns"] = []
    for n in nodes:
        n["depth"] = depth(n["id"])
        if n["kind"] == "contract" and not n["contract"]:
            v.add(
                "info",
                "contract-without-terms",
                "contract node has no 'contract' terms",
                n["location"],
                None,
                n["id"],
                hint="describe inputs/outputs/guarantees/errors so readers can judge changes",
            )
            v.diags[-1].line = n["line"]

    # ---- relationships
    relationships: list[dict[str, Any]] = []
    raw_rels = doc.get("relationships") or []
    if not isinstance(raw_rels, list):
        v.add("error", "bad-type", "'relationships' must be a list", "relationships", doc)
        raw_rels = []
    for i, raw in enumerate(raw_rels):
        loc = f"relationships[{i}]"
        if not isinstance(raw, dict):
            v.add("error", "bad-type", "each relationship must be a mapping", loc, raw_rels)
            continue
        ident = v.ident(raw, loc, "relationship")
        if ident is None and "id" in raw:
            continue
        v.check_fields(raw, REL_FIELDS, loc, ident)
        src = v.text(raw, "from", loc, ident, required=True)
        dst = v.text(raw, "to", loc, ident, required=True)
        typ, kind, reverse, default_kind = _rel_type(v, raw, loc, ident, version)
        if ident is None and src and dst and default_kind:
            ident = f"{src}--{default_kind}--{dst}"
        if ident is None:
            continue
        if not claim(ident, loc, raw):
            continue
        ok = True
        for end_key, end in (("from", src), ("to", dst)):
            if end is None:
                ok = False
            elif end not in by_id:
                guess = _suggest(end, by_id)
                v.add(
                    "error",
                    "unknown-endpoint",
                    f"'{end_key}: {end}' is not a node id; relationship ignored",
                    f"{loc}.{end_key}",
                    raw,
                    ident,
                    hint=f"did you mean '{guess}'?" if guess else "define that node first",
                )
                ok = False
        if not ok or not typ:
            continue
        if reverse:  # schema_version 1 "A reads B": the data goes from B to A
            src, dst = dst, src
        if src == dst:
            v.add(
                "info", "self-relationship", "relationship from a node to itself", loc, raw, ident
            )
        rel = {
            "id": ident,
            "from": src,
            "to": dst,
            "type": typ,
            "kind": kind,
            "label": v.text(raw, "label", loc, ident),
            "summary": v.text(raw, "summary", loc, ident),
            "exchange": None,
            "leg": None,
            "line": _line(raw),
            "location": loc,
        }
        exchange = raw.get("exchange")
        if exchange is not None:
            if not isinstance(exchange, str) or not ID_RE.match(exchange):
                v.add(
                    "error",
                    "bad-id",
                    f"exchange id {exchange!r} is invalid",
                    f"{loc}.exchange",
                    raw,
                    ident,
                    hint="an exchange id is like any id, e.g. 'order-payment-authorization'",
                )
            else:
                rel["exchange"] = exchange
        leg = v.enum(raw, "leg", EXCHANGE_LEGS, loc, ident, severity="error")
        rel["leg"] = leg if leg in EXCHANGE_LEGS else None
        rel.update(v.common(raw, loc, ident, provenance=True))
        relationships.append(rel)
    rel_by_id = {r["id"]: r for r in relationships}
    exchanges = _check_connections(v, relationships, by_id, ids, version)

    # ---- flows
    flows: list[dict[str, Any]] = []
    raw_flows = doc.get("flows") or []
    if not isinstance(raw_flows, list):
        v.add("error", "bad-type", "'flows' must be a list", "flows", doc)
        raw_flows = []
    for i, raw in enumerate(raw_flows):
        loc = f"flows[{i}]"
        if not isinstance(raw, dict):
            v.add("error", "bad-type", "each flow must be a mapping", loc, raw_flows)
            continue
        ident = v.ident(raw, loc, "flow")
        if ident is None:
            if "id" not in raw:
                v.add("error", "missing-field", "'id' is required", loc, raw)
            continue
        if not claim(ident, loc, raw):
            continue
        v.check_fields(raw, FLOW_FIELDS, loc, ident)
        steps_raw = raw.get("steps") or []
        if not isinstance(steps_raw, list) or not steps_raw:
            v.add(
                "error",
                "flow-without-steps",
                "a flow needs a non-empty 'steps' list",
                loc,
                raw,
                ident,
            )
            steps_raw = steps_raw if isinstance(steps_raw, list) else []
        steps = []
        step_ids: set[str] = set()
        prev_main = None
        for j, s in enumerate(steps_raw):
            sloc = f"{loc}.steps[{j}]"
            if not isinstance(s, dict):
                v.add("error", "bad-type", "each step must be a mapping", sloc, raw, ident)
                continue
            v.check_fields(s, STEP_FIELDS, sloc, ident)
            sid = s.get("id")
            if sid is None:
                sid = f"s{j + 1}"
            elif not isinstance(sid, str) or not ID_RE.match(sid):
                v.add("error", "bad-id", f"step id {sid!r} is invalid", f"{sloc}.id", s, ident)
                sid = f"s{j + 1}"
            if sid in step_ids:
                v.add(
                    "error",
                    "duplicate-step",
                    f"step id '{sid}' repeats within the flow",
                    sloc,
                    s,
                    ident,
                )
                sid = f"{sid}-{j + 1}"
            step_ids.add(sid)
            node_id = v.text(s, "node", sloc, ident, required=True)
            if node_id and node_id not in by_id:
                guess = _suggest(node_id, by_id)
                v.add(
                    "error",
                    "unknown-step-node",
                    f"step node '{node_id}' does not exist",
                    f"{sloc}.node",
                    s,
                    ident,
                    hint=f"did you mean '{guess}'?" if guess else None,
                )
                node_id = None
            via = v.text(s, "via", sloc, ident)
            if via and via not in rel_by_id:
                guess = _suggest(via, rel_by_id)
                v.add(
                    "warning",
                    "unknown-step-via",
                    f"step 'via: {via}' is not a relationship id",
                    f"{sloc}.via",
                    s,
                    ident,
                    hint=f"did you mean '{guess}'?" if guess else None,
                )
                via = None
            kind = v.enum(s, "kind", STEP_KINDS, sloc, ident) or "main"
            after = v.text(s, "after", sloc, ident)
            if after is not None and after not in step_ids:
                v.add(
                    "error",
                    "unknown-step-after",
                    f"'after: {after}' must name an earlier step of this flow",
                    f"{sloc}.after",
                    s,
                    ident,
                    hint="earlier steps: " + (", ".join(sorted(step_ids - {sid})) or "none"),
                )
                after = None
            if after is None and j > 0:
                after = prev_main
            label = v.text(s, "label", sloc, ident)
            if not label:
                v.add(
                    "warning",
                    "step-without-label",
                    "step has no label",
                    sloc,
                    s,
                    ident,
                    hint="say what happens in domain language",
                )
            steps.append(
                {
                    "id": sid,
                    "node": node_id,
                    "label": label or "",
                    "via": via,
                    "after": after,
                    "kind": kind,
                    "refs": v.refs(s, sloc, ident),
                    "notes": v.text(s, "notes", sloc, ident),
                    "line": _line(s),
                }
            )
            if kind in ("main", "alternative"):
                prev_main = sid
        _link_steps(steps, by_id, relationships, rel_by_id, v, loc, ident)
        flow = {
            "id": ident,
            "label": v.text(raw, "label", loc, ident) or ident,
            "summary": v.text(raw, "summary", loc, ident),
            "tags": v.text_list(raw, "tags", loc, ident),
            "steps": steps,
            "line": _line(raw),
            "location": loc,
        }
        flow.update(v.common(raw, loc, ident))
        flows.append(flow)

    model = {
        "schema_version": version,
        "repository": repository,
        "scope": scope,
        "source": source,
        "nodes": nodes,
        "relationships": relationships,
        "exchanges": exchanges,
        "flows": flows,
    }
    return LoadResult(model, v.diags)


def _check_connections(v, relationships, by_id, ids, version) -> list[dict[str, Any]]:
    """Endpoint rules and exchanges. Returns the exchanges: ``{id, caller, callee, legs}``.

    Containers (nodes with children) explain; they do not send, receive or call. In
    schema_version 3 a relationship ending on a container is an error; older maps keep
    their meaning and are shown as they were, with a note (``legacy-group-endpoint``).
    An exchange is the relationships that share an ``exchange`` id: one ``input`` from the
    caller to the callee and the ``result``, ``response`` or ``error`` that come back.
    """
    v3 = version == CURRENT_SCHEMA_VERSION
    for r in relationships:
        r["group_endpoint"] = [e for e in (r["from"], r["to"]) if by_id[e]["children"]]
        if r["group_endpoint"]:
            names = " and ".join(f"'{e}'" for e in r["group_endpoint"])
            leaves = sorted(c for e in r["group_endpoint"] for c in by_id[e]["children"])
            v.add(
                "error" if v3 else "info",
                "group-endpoint" if v3 else "legacy-group-endpoint",
                f"{names} {'contains' if len(r['group_endpoint']) == 1 else 'contain'} other "
                "nodes: a container explains, it does not send, receive or call"
                + ("" if v3 else "; shown as written (schema_version 2)"),
                r["location"],
                None,
                r["id"],
                hint="end the relationship on the item inside that does the work (children: "
                + ", ".join(leaves[:6]) + ("…" if len(leaves) > 6 else "")
                + "); do not draw one line per child unless each really takes part",
            )
            v.diags[-1].line = r["line"]
        if v3 and r["type"] in ("data", "error", "control"):
            rules = [e for e in (r["from"], r["to"]) if by_id[e]["kind"] == "rule"]
            if rules:
                v.add(
                    "warning",
                    "rule-endpoint",
                    f"'{rules[0]}' is a rule: a rule constrains, it does not send or receive "
                    f"{'data' if r['type'] == 'data' else 'calls'}",
                    r["location"],
                    None,
                    r["id"],
                    hint="use a dependency (kind: enforces) from the operation that enforces it",
                )
                v.diags[-1].line = r["line"]
        if v3 and r["type"] in CARRIES and r["certainty"] == "observed":
            sites = [x for x in r["refs"] if x["role"] in SITE_ROLES]
            if not sites:
                v.add(
                    "warning",
                    "observed-without-site",
                    f"an observed {r['type']} relationship needs a ref to where it is handed "
                    "over or taken in (role: transfer or receive, with focus)",
                    r["location"],
                    None,
                    r["id"],
                    hint="cite the call, return, publish or write with `role: transfer` and "
                    "`focus: <text copied from that line>`; if no such site is in this "
                    "repository, say so in notes and lower `certainty` to inferred",
                )
                v.diags[-1].line = r["line"]
        if v3:
            for x in r["refs"]:
                if x["role"] in PROVENANCE_ROLES and not x["focus"]:
                    v.add(
                        "warning",
                        "site-without-focus",
                        f"ref {x['path']}{'#' + x['symbol'] if x['symbol'] else ''} is a "
                        f"{x['role']} site without 'focus': the whole range would be shown as "
                        "the evidence",
                        x["location"],
                        None,
                        r["id"],
                        hint="add `focus:` with the exact text of the statement or expression "
                        "(copied from the source; it must occur once in the ref's range)",
                    )
                    v.diags[-1].line = x["line"]
        if r["leg"] and not r["exchange"]:
            v.add(
                "warning",
                "leg-without-exchange",
                f"'leg: {r['leg']}' without 'exchange': nothing pairs this relationship",
                r["location"] + ".leg",
                None,
                r["id"],
                hint="give every leg of one call the same `exchange: <id>`",
            )
            v.diags[-1].line = r["line"]

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in relationships:
        if r["exchange"]:
            groups.setdefault(r["exchange"], []).append(r)
    exchanges = []
    for xid, legs in groups.items():
        first = legs[0]

        def fail(message, hint, rel=first, xid=xid):
            v.add("error", "exchange-incoherent", f"exchange '{xid}': {message}",
                  rel["location"] + ".exchange", None, rel["id"], hint=hint)
            v.diags[-1].line = rel["line"]

        if xid in ids:
            fail(f"'{xid}' is also the id of the record at {ids[xid]}; an exchange needs an id "
                 "of its own", "exchange ids share the namespace of nodes, relationships and "
                 "flows: name the exchange after the call, e.g. "
                 f"'{xid}-call'")
            continue
        for r in legs:
            if not r["leg"]:
                fail(f"'{r['id']}' does not say which leg it is", "add `leg: input` for what "
                     "the caller supplies, `leg: result` (or response, error) for what comes "
                     "back", r)
        inputs = [r for r in legs if r["leg"] == "input"]
        back = [r for r in legs if r["leg"] in ("result", "response")]
        errors = [r for r in legs if r["leg"] == "error"]
        if len(inputs) > 1:
            fail(f"{len(inputs)} input legs ({', '.join(r['id'] for r in inputs)})",
                 "one exchange is one request; give a second call its own exchange id",
                 inputs[1])
        ends = {frozenset((r["from"], r["to"])) for r in legs}
        if len(ends) > 1:
            fail("its legs connect different nodes", "all legs of an exchange run between "
                 "the same two nodes, in either direction")
            continue
        caller = inputs[0]["from"] if inputs else ((back + errors)[0]["to"] if back + errors
                                                     else first["from"])
        callee = first["to"] if first["from"] == caller else first["from"]
        for r in inputs:
            if r["type"] not in ("data", "control"):
                fail(f"input '{r['id']}' is a {r['type']} relationship",
                     "an input is data the caller supplies (or a control call)", r)
        for r in back + errors:
            want = "error" if r["leg"] == "error" else "data"
            if r["type"] != want:
                fail(f"'{r['id']}' ({r['leg']}) must be of type {want}",
                     "a result or response is data coming back; an error leg is `type: error`",
                     r)
            elif r["from"] != callee or r["to"] != caller:
                fail(f"'{r['id']}' ({r['leg']}) runs from '{r['from']}' to '{r['to']}', but "
                     f"what comes back goes from '{callee}' to '{caller}'",
                     "swap from and to, or check which leg this is", r)
        if len(legs) == 1:
            v.add("info", "single-leg-exchange", f"exchange '{xid}' has only one leg",
                  first["location"], None, first["id"],
                  hint="fine for a call whose result does not matter; otherwise add the leg "
                  "that comes back")
            v.diags[-1].line = first["line"]
        order = list(EXCHANGE_LEGS)
        legs_sorted = sorted(legs, key=lambda r: order.index(r["leg"]) if r["leg"] in order else 9)
        exchanges.append({"id": xid, "caller": caller, "callee": callee,
                          "legs": [r["id"] for r in legs_sorted]})
    return exchanges


def _rel_type(v, raw, loc, ident, version):
    """Type, detail kind, whether from/to are reversed, and the kind used in a default id.

    schema_version 1 named relationships with verbs ("A reads B"); they are converted so
    that every arrow points from `from` to `to` in the sense of its type. Version 2 maps
    give `type`; a control or dependency `kind` alone implies it.
    """
    typ = v.text(raw, "type", loc, ident)
    kind = v.text(raw, "kind", loc, ident)
    if version == 1 or (typ is None and kind in V1_KINDS and kind not in KIND_TYPE):
        if kind is None:
            v.add("error", "missing-field", "'kind' is required", loc, raw, ident)
            return None, None, False, None
        if kind not in V1_KINDS:
            guess = _suggest(kind, V1_KINDS)
            v.add(
                "warning",
                "unknown-kind",
                f"unknown relationship kind '{kind}'",
                f"{loc}.kind",
                raw,
                ident,
                hint=(f"did you mean '{guess}'? " if guess else "")
                + "known: "
                + ", ".join(V1_KINDS),
            )
            return "control", kind, False, kind
        if version != 1:
            v.add(
                "warning",
                "verb-kind",
                f"'kind: {kind}' is a schema_version 1 verb; read as a "
                f"{V1_KINDS[kind][0]} relationship"
                + (" with from and to swapped" if V1_KINDS[kind][2] else ""),
                f"{loc}.kind",
                raw,
                ident,
                hint="give `type` (data, control or dependency); for data, `from` is where the "
                "data comes from and `label` names the data",
            )
        t, k, rev = V1_KINDS[kind]
        return t, k, rev, kind
    if typ is None:
        if kind in KIND_TYPE:
            return KIND_TYPE[kind], kind, False, kind
        guess = _suggest(kind, {**KIND_TYPE, **V1_KINDS}) if kind else None
        v.add(
            "error",
            "missing-field",
            "'type' is required: " + "; ".join(f"{t} ({m})" for t, m in RELATIONSHIP_TYPES.items()),
            loc,
            raw,
            ident,
            hint=(f"'kind: {kind}' is unknown; did you mean '{guess}'? " if guess else "")
            + "prefer data: if something moves, model the movement",
        )
        return None, None, False, None
    if typ not in RELATIONSHIP_TYPES:
        guess = _suggest(typ, RELATIONSHIP_TYPES)
        v.add(
            "error",
            "unknown-type",
            f"unknown relationship type '{typ}'; relationship ignored",
            f"{loc}.type",
            raw,
            ident,
            hint=(f"did you mean '{guess}'? " if guess else "")
            + "types: data, control, dependency",
        )
        return None, None, False, None
    if kind is not None and kind not in RELATIONSHIP_KINDS[typ]:
        known = ", ".join(RELATIONSHIP_KINDS[typ]) or "none"
        v.add(
            "warning",
            "unknown-kind",
            f"'{kind}' is not a known kind of {typ} relationship",
            f"{loc}.kind",
            raw,
            ident,
            hint=f"known for {typ}: {known}; or omit `kind`",
        )
    if typ in CARRIES and not v.text(raw, "label", loc, ident):
        v.add(
            "warning",
            "data-without-label",
            f"a {typ} relationship should name the {typ} it carries",
            loc,
            raw,
            ident,
            hint="set `label` to what moves, e.g. `label: order total` or "
            "`label: decline reason`",
        )
    return typ, kind, False, kind or typ


def _link_steps(steps, by_id, relationships, rel_by_id, v, loc, ident) -> None:
    """Give every step the relationship it is drawn along: step["link"] = rel id or None.

    A flow only highlights relationships that exist in the map. `via` names the
    relationship a step uses; it must touch the step's node (or a node containing it or
    inside it) and continue from where the flow is: the previous step's node, or the other
    end of the relationship that step was drawn along (a coordinator calling one service
    after another). Otherwise the flow jumps, and the nodes it skips are not part of it;
    only an actor (outside the system) can start something anew.
    Without `via`, a relationship from where the flow is to this step's node is used: the
    previous step's node first, then the closest fit (exact ends before containing ones),
    then map order.
    """

    def ancestry(nid: str) -> dict[str, int]:
        out, cur, d = {}, by_id.get(nid), 0
        while cur is not None:
            out[cur["id"]] = d
            cur = by_id.get(cur["parent"]) if cur["parent"] else None
            d += 1
        return out

    def distance(end: str, node: str) -> int | None:
        """How far relationship end `end` is from `node` in the containment tree, if related."""
        up = ancestry(node)
        if end in up:
            return up[end]
        down = ancestry(end)
        return down.get(node)

    def between(rel: dict, a: str, b: str) -> int | None:
        fits = []
        for x, y in ((a, b), (b, a)):
            dx, dy = distance(rel["from"], x), distance(rel["to"], y)
            if dx is not None and dy is not None:
                fits.append(dx + dy)
        return min(fits) if fits else None

    by_sid = {s["id"]: s for s in steps}
    usable = [
        r for r in relationships if r["from"] in by_id and r["to"] in by_id and r["from"] != r["to"]
    ]
    def touches(rel: dict, nid: str) -> bool:
        return distance(rel["from"], nid) is not None or distance(rel["to"], nid) is not None

    for j, s in enumerate(steps):
        s["link"] = None
        node = s["node"]
        if not node:
            continue
        sloc = f"{loc}.steps[{j}]"
        prev = by_sid.get(s["after"]) if s["after"] else None
        # where the flow is when this step starts: the previous step's node, then the other
        # end of the relationship that step was drawn along
        here: list[str] = []
        if prev is not None and prev["node"]:
            here.append(prev["node"])
            if prev["link"]:
                pr = rel_by_id[prev["link"]]
                here += [e for e in (pr["from"], pr["to"]) if e not in here]
        if s["via"]:
            rel = rel_by_id[s["via"]]
            if rel["from"] not in by_id or rel["to"] not in by_id or not touches(rel, node):
                v.add(
                    "warning",
                    "step-via-mismatch",
                    f"'via: {s['via']}' does not touch this step's node '{node}'",
                    f"{sloc}.via",
                    None,
                    ident,
                    hint="name a relationship of this step's node (or of a node containing it)",
                )
                v.diags[-1].line = s["line"]
                continue
            s["link"] = s["via"]
            by_actor = any(by_id[e]["kind"] == "actor" for e in (rel["from"], rel["to"]))
            if here and not by_actor and not any(touches(rel, h) for h in here):
                v.add(
                    "warning",
                    "step-via-gap",
                    f"'via: {s['via']}' does not continue from step '{prev['id']}' on "
                    f"'{prev['node']}': the flow jumps over the nodes in between",
                    f"{sloc}.via",
                    None,
                    ident,
                    hint=f"add a step for each node the flow passes between '{prev['node']}' and "
                    f"'{node}' (only an actor can start something anew)",
                )
                v.diags[-1].line = s["line"]
            continue
        if not here or here[0] == node:
            continue
        for h in here:
            fits = [(f, i, r) for i, r in enumerate(usable) if (f := between(r, h, node)) is not None]
            if fits:
                s["link"] = min(fits, key=lambda x: (x[0], x[1]))[2]["id"]
                break
        if s["link"]:
            continue
        v.add(
            "warning",
            "step-without-relationship",
            f"step '{s['id']}' follows '{prev['id']}' on '{prev['node']}', but no relationship "
            f"leads to '{node}'; the flow cannot be drawn here",
            sloc,
            None,
            ident,
            hint="name the relationship this step uses with 'via' (add it if it is missing)",
        )
        v.diags[-1].line = s["line"]


def load_map(path: Path) -> LoadResult:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return LoadResult(
            None,
            [
                Diagnostic(
                    "error",
                    "missing-map",
                    f"no map at {path}",
                    hint="run `repoluma init` to create one",
                )
            ],
        )
    except (OSError, UnicodeDecodeError) as exc:
        return LoadResult(None, [Diagnostic("error", "unreadable-map", f"cannot read map: {exc}")])
    doc, err = parse_yaml(text)
    if err:
        return LoadResult(None, [err])
    return validate_document(doc)


def iter_refs(model: dict[str, Any]):
    """Yield (owner_kind, owner_id, ref) for every source ref in the model."""
    for n in model["nodes"]:
        for r in n["refs"]:
            yield "node", n["id"], r
    for r in model["relationships"]:
        for ref in r["refs"]:
            yield "relationship", r["id"], ref
    for f in model["flows"]:
        for ref in f["refs"]:
            yield "flow", f["id"], ref
        for s in f["steps"]:
            for ref in s["refs"]:
                yield "step", f"{f['id']}/{s['id']}", ref


def ref_key(ref: dict[str, Any]) -> str:
    key = ref["path"]
    if ref.get("symbol"):
        key += "#" + ref["symbol"]
    if ref.get("lines"):
        key += ":{}-{}".format(*ref["lines"])
    return key


def json_schema() -> dict[str, Any]:
    """JSON Schema (draft 2020-12) for the map, generated from the validator's constants.

    The validator in this module is authoritative (it also checks referential
    integrity and resolves source references); the schema serves editors and agents.
    """
    text = {"type": "string"}
    text_list = {"oneOf": [text, {"type": "array", "items": {"type": ["string", "number"]}}]}
    ident = {"type": "string", "pattern": ID_RE.pattern}
    ext = {"^x-": {}}

    def enum_or_text(values):
        return {"anyOf": [{"enum": list(values)}, text]}

    ref = {
        "oneOf": [
            {"type": "string", "description": "path, path#symbol, or path:start-end"},
            {
                "type": "object",
                "required": ["path"],
                "additionalProperties": False,
                "patternProperties": ext,
                "properties": {
                    "path": text,
                    "symbol": text,
                    "lines": {
                        "oneOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "pattern": r"^\s*\d+\s*(-\s*\d+\s*)?$"},
                            {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                        ]
                    },
                    "role": {
                        "enum": list(REF_ROLES) + list(PROVENANCE_ROLES),
                        "description": "On relationships: "
                        + "; ".join(f"{k}: {m}" for k, m in PROVENANCE_ROLES.items()),
                    },
                    "focus": {
                        "description": "Exact source text (or several) inside the ref's range "
                        "that the claim is about; each must occur exactly once there "
                        "(whitespace is normalized).",
                        "oneOf": [text, {"type": "array", "items": text, "minItems": 1}],
                    },
                    "note": text,
                },
            },
        ]
    }
    common = {
        "certainty": {
            "enum": list(CERTAINTY_STATES),
            "description": "How sure the claim is: "
            + "; ".join(f"{k}: {m}" for k, m in CERTAINTY_STATES.items()),
        },
        "confidence": {"enum": list(CONFIDENCE)},
        "refs": {"type": "array", "items": ref},
        "notes": text,
    }
    node = {
        "type": "object",
        "required": ["id"],
        "additionalProperties": False,
        "patternProperties": ext,
        "properties": {
            "id": ident,
            "label": text,
            "kind": enum_or_text(NODE_KINDS),
            "parent": ident,
            "summary": text,
            "details": text,
            "contract": {
                "type": "object",
                "additionalProperties": False,
                "properties": {f: text_list for f in CONTRACT_FIELDS},
            },
            "tags": text_list,
            "owns": {
                "description": "Top-level nodes only. Files this area owns: exact paths, "
                "globs ('src/app/**'), directories ('src/app/') or part of a file "
                "('path#Symbol'). Every code file has exactly one owner; the most specific "
                "pattern wins.",
                "oneOf": [text, {"type": "array", "items": text}],
            },
            **common,
        },
    }
    rel = {
        "type": "object",
        "required": ["from", "to"],
        "anyOf": [{"required": ["type"]}, {"required": ["kind"]}],
        "additionalProperties": False,
        "patternProperties": ext,
        "properties": {
            "id": ident,
            "from": ident,
            "to": ident,
            "type": {
                "enum": list(RELATIONSHIP_TYPES),
                "description": "; ".join(f"{t}: {m}" for t, m in RELATIONSHIP_TYPES.items()),
            },
            "kind": enum_or_text(KIND_TYPE),
            "label": text,
            "summary": text,
            "exchange": {
                **ident,
                "description": "Id shared by the legs of one exchange (a call and what comes "
                "back); not drawn itself",
            },
            "leg": {
                "enum": list(EXCHANGE_LEGS),
                "description": "; ".join(f"{k}: {m}" for k, m in EXCHANGE_LEGS.items()),
            },
            **common,
        },
    }
    step = {
        "type": "object",
        "required": ["node"],
        "additionalProperties": False,
        "patternProperties": ext,
        "properties": {
            "id": ident,
            "node": ident,
            "label": text,
            "via": ident,
            "after": ident,
            "kind": {"enum": list(STEP_KINDS)},
            "refs": common["refs"],
            "notes": text,
        },
    }
    flow = {
        "type": "object",
        "required": ["id", "steps"],
        "additionalProperties": False,
        "patternProperties": ext,
        "properties": {
            "id": ident,
            "label": text,
            "summary": text,
            "tags": text_list,
            "steps": {"type": "array", "items": step, "minItems": 1},
            **common,
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:repoluma:map:v{CURRENT_SCHEMA_VERSION}",
        "title": "repoluma map",
        "description": "Domain-first description of a repository with source references. "
        "Run `repoluma validate` for referential integrity and reference resolution.",
        "type": "object",
        "required": ["schema_version"],
        "additionalProperties": False,
        "patternProperties": ext,
        "properties": {
            "schema_version": {"const": CURRENT_SCHEMA_VERSION},
            "repository": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"name": text, "description": text},
            },
            "scope": {
                "type": "object",
                "additionalProperties": False,
                "patternProperties": ext,
                "properties": {
                    "summary": text,
                    "covered": text_list,
                    "not_covered": text_list,
                    "reviewed_revision": text,
                    "reviewed_at": text,
                },
            },
            "source": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "exclude": text_list,
                    "ownership": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {"ignore": text_list, "require": text_list},
                    },
                },
            },
            "nodes": {"type": "array", "items": node},
            "relationships": {"type": "array", "items": rel},
            "flows": {"type": "array", "items": flow},
        },
    }
