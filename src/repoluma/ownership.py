"""Which node owns each file: the map's ``owns`` patterns resolved against the repository.

Ownership is a partition, as in architecture-conformance tools and ``CODEOWNERS``: every
file that must be mapped has exactly one owning node, so "where is this file on the map?"
has one answer. Many nodes may still *cite* a file through ``refs``; that is evidence,
not ownership.

Rules (see ``docs/schema.md``):

* A pattern is an exact path, a glob (``*`` within a segment, ``**`` across segments,
  ``?``, ``[...]``) or a directory (``dir/``, the same as ``dir/**``).
* The most specific pattern wins: an exact path beats every glob; among globs, the one
  with more fixed leading segments, then more fixed characters. Equally specific patterns
  of different nodes are a conflict.
* ``path#Symbol`` gives part of a file to a node. The file's owner keeps everything else.
  A part inside another part (``Class.method`` inside ``Class``) wins over it.
* Code files must be owned, except tests, docs, vendored, generated and tooling files
  (``DEFAULT_UNREQUIRED``); ``source.ownership.ignore`` and ``.require`` adjust that.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from functools import lru_cache
from typing import Any

from .model import Diagnostic

# File extensions that count as code (they need an owner unless a default skips them).
CODE_EXTENSIONS = frozenset(
    {
        "py", "pyi", "pyx", "js", "mjs", "cjs", "jsx", "ts", "tsx", "mts", "cts", "vue", "svelte",
        "astro", "go", "rs", "java", "kt", "kts", "scala", "sc", "groovy", "c", "h", "cc", "cpp",
        "cxx", "hh", "hpp", "hxx", "ipp", "m", "mm", "cs", "fs", "fsx", "vb", "swift", "dart", "rb",
        "php", "pl", "pm", "lua", "r", "jl", "ex", "exs", "erl", "hrl", "hs", "ml", "mli", "clj",
        "cljs", "cljc", "elm", "nim", "zig", "sol", "sql", "sh", "bash", "zsh", "fish", "ps1", "bat",
        "cmd", "f", "f90", "f95", "f03", "for", "cu", "cuh", "proto", "graphql", "gql", "css", "scss",
        "sass", "less", "html", "htm",
    }
)

# Files that need no owner by default: tests, docs, vendored and generated code, tooling.
DEFAULT_UNREQUIRED = (
    "**/test/**",
    "**/tests/**",
    "**/__tests__/**",
    "**/spec/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/conftest.py",
    "**/*_test.go",
    "**/*.test.*",
    "**/*.spec.*",
    "**/*Test.java",
    "**/*Tests.java",
    "**/*Test.kt",
    "docs/**",
    "doc/**",
    "**/vendor/**",
    "**/third_party/**",
    "**/node_modules/**",
    "dist/**",
    "build/**",
    "**/*.min.js",
    "**/*.min.css",
    "**/*_pb2.py",
    "**/*_pb2_grpc.py",
    "**/*.pb.go",
    "**/*.generated.*",
    "**/generated/**",
    ".*/**",
)

MAX_LISTED_UNOWNED = 6
UNMAPPED = "~unmapped"  # the viewer's region for code without an owner (not a valid map id)  # files named per unowned-file diagnostic


@lru_cache(maxsize=4096)
def glob_regex(pattern: str) -> re.Pattern:
    """Compile an ownership glob. ``**`` spans directories; ``dir/`` means ``dir/**``."""
    if pattern.endswith("/"):
        pattern += "**"
    parts = pattern.split("/")
    out = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]*/)*")
            continue
        seg, j = [], 0
        while j < len(part):
            ch = part[j]
            if ch == "*":
                seg.append("[^/]*")
            elif ch == "?":
                seg.append("[^/]")
            elif ch == "[" and "]" in part[j + 2 :]:
                k = part.index("]", j + 2)
                body = part[j + 1 : k].replace("\\", "\\\\")
                seg.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                j = k
            else:
                seg.append(re.escape(ch))
            j += 1
        out.append("".join(seg) + ("" if last else "/"))
    return re.compile("^" + "".join(out) + "$")


def is_glob(pattern: str) -> bool:
    return pattern.endswith("/") or any(c in pattern for c in "*?[")


def literal_prefix(pattern: str) -> str:
    """The fixed directory prefix of a pattern ('' when it starts with a wildcard)."""
    if pattern.endswith("/"):
        return pattern
    fixed = []
    for part in pattern.split("/")[:-1]:
        if any(c in part for c in "*?["):
            break
        fixed.append(part + "/")
    return "".join(fixed)


def specificity(pattern: str) -> tuple[int, int, int]:
    """Higher wins: exact paths, then more fixed leading segments, then fixed characters."""
    if not is_glob(pattern):
        return (2, pattern.count("/") + 1, len(pattern))
    p = pattern + "**" if pattern.endswith("/") else pattern
    segments = 0
    for part in p.split("/"):
        if any(c in part for c in "*?["):
            break
        segments += 1
    fixed = len(re.sub(r"\*+|\?|\[[^\]]*\]", "", p))
    return (1, segments, fixed)


def matches_any(path: str, patterns) -> bool:
    return any(glob_regex(p).match(path) for p in patterns)


def is_code(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return "." in name[1:] and name.rsplit(".", 1)[-1].lower() in CODE_EXTENSIONS


def is_required(path: str, ignore=(), require=()) -> bool:
    """Whether a file must have an owner: code files, minus the default and stated skips."""
    if require and matches_any(path, require):
        return True
    if not is_code(path):
        return False
    return not matches_any(path, DEFAULT_UNREQUIRED) and not matches_any(path, ignore)


def compute_ownership(model: dict[str, Any], files: list[str], repo, truncated=False):
    """Resolve every node's ``owns`` against the listed files.

    Returns ``(payload, diagnostics)``. The payload lists every owned file, every file that
    must be owned but is not, and every unowned file the map cites, with the owner, the
    deciding pattern, and the parts of the file other nodes own.
    """
    diags: list[Diagnostic] = []
    nodes = model["nodes"]
    by_id = {n["id"]: n for n in nodes}
    settings = model["source"].get("ownership") or {}
    ignore, require = settings.get("ignore", []), settings.get("require", [])
    file_set = set(files)
    dirs = {f.rsplit("/", 1)[0] + "/" for f in files if "/" in f}

    # --- file-level patterns: exact paths by lookup, globs filtered by their fixed prefix
    exact: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    globs: list[tuple[str, dict, re.Pattern, str, tuple]] = []
    hits: dict[int, int] = {}
    for n in nodes:
        for entry in n.get("owns", []):
            if entry["symbol"]:
                continue
            pat = entry["pattern"]
            hits[id(entry)] = 0
            if not is_glob(pat) and pat not in file_set and pat + "/" in dirs:
                pat = pat + "/"  # a directory written without its slash
            if is_glob(pat):
                globs.append((n["id"], entry, glob_regex(pat), literal_prefix(pat), specificity(pat)))
            else:
                exact[pat].append((n["id"], entry))

    owned: dict[str, dict[str, Any]] = {}
    conflicts: dict[tuple, list[str]] = defaultdict(list)
    for path in files:
        cands = [(specificity(path), nid, e) for nid, e in exact.get(path, [])]
        if not cands:
            cands = [(spec, nid, e) for nid, e, rx, pre, spec in globs
                     if path.startswith(pre) and rx.match(path)]
        if not cands:
            continue
        best = max(c[0] for c in cands)
        top = [c for c in cands if c[0] == best]
        for c in cands:
            hits[id(c[2])] += 1
        if len({c[1] for c in top}) > 1:
            conflicts[tuple(sorted((c[1], c[2]["pattern"]) for c in top))].append(path)
        # deterministic meanwhile: the first claimant in id order
        _, nid, entry = min(top, key=lambda c: c[1])
        owned[path] = {"owner": nid, "pattern": entry["pattern"], "parts": []}

    for claim, paths in conflicts.items():
        names = " and ".join(f"'{nid}' ({pat})" for nid, pat in claim)
        nid, pat = claim[0]
        node = by_id[nid]
        diags.append(
            Diagnostic(
                "error",
                "ownership-conflict",
                f"{_count(paths, 'file')} {_names(paths)} claimed equally by {names}; "
                f"shown under '{nid}' for now",
                node["location"] + ".owns",
                node["line"],
                nid,
                hint="a file has exactly one owner: give one node a more specific pattern "
                "(an exact path beats a glob, 'src/app/**' beats 'src/**'), or remove one claim",
            )
        )
    for n in nodes:
        for entry in n.get("owns", []):
            if not entry["symbol"] and hits.get(id(entry)) == 0:
                excluded = not is_glob(entry["pattern"]) and repo.is_excluded(entry["pattern"])
                diags.append(
                    Diagnostic(
                        "error" if excluded else "warning",
                        "owns-excluded" if excluded else "owns-matches-nothing",
                        f"owns {entry['pattern']!r} "
                        + ("is excluded from display" if excluded else "matches no file")
                        + (" (the file list was truncated)" if truncated else ""),
                        entry["location"],
                        entry["line"],
                        n["id"],
                        hint=None if excluded else "check the path (repository-relative, forward "
                        "slashes); directories need a trailing '/' or '/**'",
                    )
                )

    # --- parts of files (path#Symbol)
    seen_parts: dict[tuple[str, str], str] = {}
    for n in nodes:
        for entry in n.get("owns", []):
            if not entry["symbol"]:
                continue
            path, symbol = entry["pattern"], entry["symbol"]
            key = f"{path}#{symbol}"
            res = repo.resolve({"path": path, "symbol": symbol})
            if res.status not in ("resolved", "heuristic"):
                diags.append(
                    Diagnostic(
                        "error",
                        f"owns-{res.status.replace('_', '-')}",
                        f"owns {key!r}: {res.message}",
                        entry["location"],
                        entry["line"],
                        n["id"],
                        hint="name a symbol defined in that file (qualified, e.g. Class.method)",
                    )
                )
                continue
            if (path, symbol) in seen_parts:
                other = seen_parts[(path, symbol)]
                diags.append(
                    Diagnostic(
                        "error",
                        "ownership-conflict",
                        f"owns {key!r} is also claimed by '{other}'",
                        entry["location"],
                        entry["line"],
                        n["id"],
                        hint="a part of a file has one owner; cite it from other nodes with refs",
                    )
                )
                continue
            seen_parts[(path, symbol)] = n["id"]
            rec = owned.get(path)
            if rec is None:
                rec = owned[path] = {"owner": None, "pattern": None, "parts": []}
            rec["parts"].append(
                {"node": n["id"], "symbol": symbol, "start": res.start, "end": res.end,
                 "method": res.method}
            )
    for rec in owned.values():
        # outer parts first, so that a reader (and the viewer) can let inner ones win
        rec["parts"].sort(key=lambda p: (p["start"], -p["end"]))

    # --- what must be owned
    cited: dict[str, set[str]] = defaultdict(set)
    for n in nodes:
        for r in n.get("refs", []):
            cited[r["path"]].add(n["id"])
    required_count = owned_required = 0
    unowned: list[str] = []
    out_files: dict[str, dict[str, Any]] = {}
    for path in files:
        req = is_required(path, ignore, require)
        rec = owned.get(path)
        whole = rec is not None and rec["owner"] is not None
        if req:
            required_count += 1
            if whole:
                owned_required += 1
            else:
                unowned.append(path)
        if rec is not None or req or path in cited:
            base = rec or {"owner": None, "pattern": None, "parts": []}
            out_files[path] = {**base, "required": req}

    # one diagnostic per directory, so a fresh map does not drown in warnings
    by_dir: dict[str, list[str]] = defaultdict(list)
    for path in unowned:
        by_dir[path.rsplit("/", 1)[0] + "/" if "/" in path else "(root)"].append(path)
    chains = {nid: _chain(by_id, nid) for nid in by_id}
    for d, paths in sorted(by_dir.items()):
        partial = [p for p in paths if p in owned]
        suggestion = _suggest_owner(paths, cited, chains, by_id)
        diags.append(
            Diagnostic(
                "warning",
                "unowned-file",
                f"{_count(paths, 'code file')} in {d} {'has' if len(paths) == 1 else 'have'} no "
                f"owner: {_names(paths)}"
                + (f" ({len(partial)} only partly owned)" if partial else ""),
                "nodes",
                None,
                None,
                hint=suggestion
                or "add the files to the 'owns' of the node they belong to (a directory "
                "pattern such as 'src/app/' covers many), or list them under "
                "source.ownership.ignore if they need no place on the map",
            )
        )

    payload = {
        "files": out_files,
        "unowned": unowned,
        "counts": {
            "files": len(files),
            "required": required_count,
            "owned_required": owned_required,
            "owned": sum(1 for r in owned.values() if r["owner"] is not None),
        },
        "truncated": truncated,
    }
    return payload, diags


def _chain(by_id, nid):
    chain = []
    cur = by_id.get(nid)
    while cur is not None and cur["id"] not in chain:
        chain.insert(0, cur["id"])
        cur = by_id.get(cur["parent"]) if cur["parent"] else None
    return chain


def _suggest_owner(paths, cited, chains, by_id) -> str | None:
    """Where the map already points at these files, suggest the node that covers those places."""
    hints = []
    for path in paths[:MAX_LISTED_UNOWNED]:
        citing = sorted(cited.get(path, ()))
        if not citing:
            continue
        areas = sorted({chains[c][0] for c in citing})
        if len(areas) == 1:
            hints.append(f"{path} is cited by {', '.join(citing)}: consider owns on '{areas[0]}'")
        else:
            hints.append(f"{path} is cited from the areas {', '.join(areas)}: give it to the "
                         "one it mainly serves, or split it with path#Symbol")
    return "; ".join(hints) or None


def _count(items, word):
    return f"{len(items)} {word}{'' if len(items) == 1 else 's'}"


def _names(paths):
    shown = ", ".join(p.rsplit("/", 1)[-1] for p in paths[:MAX_LISTED_UNOWNED])
    return shown + (f" and {len(paths) - MAX_LISTED_UNOWNED} more" if len(paths) > MAX_LISTED_UNOWNED
                    else "")


def owner_at(entry: dict[str, Any] | None, line: int | None = None) -> tuple[str | None, dict | None]:
    """Owner of a file (or of one line in it): the innermost part containing the line wins."""
    if entry is None:
        return None, None
    if line is not None:
        inner = None
        for part in entry["parts"]:
            if part["start"] <= line <= part["end"] and (
                inner is None or part["end"] - part["start"] <= inner["end"] - inner["start"]
            ):
                inner = part
        if inner is not None:
            return inner["node"], inner
    return entry["owner"], None


DISPLAYED = ("resolved", "heuristic", "lines", "moved", "file")


GLUE_RE = re.compile(
    r"^\s*(?:$|#|//|/\*|\*|<!--|-->|import\s|from\s+\S+\s+import\s|export\s+(?:\*|\{[^}]*\})\s+from\s|"
    r"#include|#pragma|using\s|package\s|use\s|require\(|(?:const|let|var)\s+\w+\s*=\s*require\(|"
    r"@import|@charset|[\"']use strict[\"']|[}\])]+[;,]?\s*$)"
)


def glue_lines(path: str, lines: list[str]) -> set[int]:
    """Lines that need no node: blank lines, comments, imports (also inside a module-level
    ``if TYPE_CHECKING:``), the module docstring, ``__all__`` and the
    ``if __name__ == "__main__"`` block. Everything else is code that some node must show."""
    glue = {i for i, t in enumerate(lines, 1) if GLUE_RE.match(t)}
    if not path.endswith((".py", ".pyi")):
        # multi-line imports: import {\n  a,\n  b,\n} from "./x.js";
        start = None
        for i, t in enumerate(lines, 1):
            if start is None and re.match(r"^\s*(?:import|export)\s*\{[^}]*$", t):
                start = i
            if start is not None:
                glue.add(i)
                if re.search(r"\}\s*from\s*[\"'][^\"']+[\"'];?\s*$", t):
                    start = None
        return glue
    try:
        tree = ast.parse("\n".join(lines) + "\n")
    except (SyntaxError, ValueError):
        return glue
    for k, node in enumerate(tree.body):
        doc = k == 0 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
            and isinstance(node.value.value, str)
        names = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
        main = isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
        typing_only = isinstance(node, ast.If) and ast.unparse(node.test).endswith("TYPE_CHECKING") \
            and not node.orelse and all(isinstance(b, (ast.Import, ast.ImportFrom)) for b in node.body)
        if doc or main or typing_only or names == ["__all__"] or isinstance(node, (ast.Import, ast.ImportFrom)):
            glue.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return glue


def class_headers(path: str, lines: list[str]) -> list[tuple[int, int, int, int]]:
    """Python classes as (header start, header end, body start, body end): the header is
    the decorators, the ``class`` line and the docstring."""
    if not path.endswith((".py", ".pyi")):
        # brace languages: the `class X {` line, its body up to the matching brace
        from .sources import _block_end

        out = []
        for i, t in enumerate(lines):
            if re.match(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+\w+[^{]*\{\s*$", t):
                end = _block_end(lines, i, len(lines))
                if end > i + 1:
                    out.append((i + 1, i + 1, i + 2, end))
        return out
    try:
        tree = ast.parse("\n".join(lines) + "\n")
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.body:
            continue
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        first = node.body[0]
        doc = isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant)
        body = node.body[1:] if doc and len(node.body) > 1 else node.body
        h1 = (first.end_lineno or first.lineno) if doc else node.lineno
        out.append((start, max(h1, body[0].lineno - 1), body[0].lineno, node.end_lineno or node.lineno))
    return out


def rest_of_files(model: dict[str, Any], ownership: dict[str, Any], refs: dict[str, Any], repo):
    """Where each line of each owned file is shown, and the code no node shows.

    Every line of code in an owned file must be shown by a node of its area (a ref to a
    symbol, a class or the whole file); only glue (see ``glue_lines``) needs none. Code that
    no node shows is an ``unexplained-code`` error, and the viewer shows it in a flagged
    pane of the area. Returns ``(entries, diagnostics)``: one entry per (area, file) with
    such code, ``{area, path, total, segments}``, where a segment is ``{kind: code|glue|
    shown|other, start, end}`` plus ``node`` and ``ref`` (the citing pane) for ``shown``
    and the owning area (or None) for ``other``. Also records ``lines`` (the file's length)
    on each file entry. Code that needs an owner and has none is listed under ``~unmapped``.
    """
    by_id = {n["id"]: n for n in model["nodes"]}
    cites = _cites(model, refs)
    out, diags = [], []
    for path, rec in sorted(ownership["files"].items()):
        if rec["owner"] is None and not rec["parts"] and not rec["required"]:
            continue
        try:
            ft = repo.read(path)
        except Exception as exc:  # noqa: BLE001 - binary, too large, gone: no pane, say why
            rec["unreadable"] = getattr(exc, "message", str(exc))
            continue
        lines = ft.lines
        total = len(lines)
        rec["lines"] = total
        if not is_code(path):
            continue  # documents and data: shown where cited and in the file view
        glue = glue_lines(path, lines)
        fallback = UNMAPPED if rec["required"] else None
        owner = [None] + [owner_at(rec, i)[0] or fallback for i in range(1, total + 1)]
        for area in sorted({a for a in owner[1:] if a}):
            reason: list[tuple] = [()] * (total + 1)
            for i in range(1, total + 1):
                if owner[i] != area:
                    reason[i] = ("other", owner[i])
                    continue
                # the smallest citation containing the line (a method inside a cited class)
                shown = min((c for c in cites[path] if c[0] == area and c[3] <= i <= c[4]),
                            key=lambda c: c[4] - c[3], default=None)
                reason[i] = ("shown", shown[1], shown[2]) if shown else \
                    ("glue",) if i in glue else ("code",)
            # a class's header is context for its members: when any member is shown, the
            # header needs no node of its own
            for h0, h1, b0, b1 in class_headers(path, lines):
                if any(reason[i][0] == "shown" for i in range(b0, b1 + 1)):
                    for i in range(h0, h1 + 1):
                        if reason[i] == ("code",):
                            reason[i] = ("glue",)
            open_lines = [i for i in range(1, total + 1) if reason[i] == ("code",)]
            if not open_lines:
                continue
            # glue right next to unexplained code is shown with it (a comment above a
            # function, the blank line between two), not folded away
            for step in (1, -1):
                rng = range(1, total + 1) if step == 1 else range(total, 0, -1)
                for i in rng:
                    j = i - step
                    if reason[i] == ("glue",) and 1 <= j <= total and reason[j] == ("code",):
                        reason[i] = ("code",)
            kept = []
            for i in range(1, total + 1):
                if kept and kept[-1][0] == reason[i]:
                    kept[-1][2] = i
                else:
                    kept.append([reason[i], i, i])
            segments = []
            for (r, a, b) in kept:
                d = {"kind": r[0], "start": a, "end": b}
                if r[0] == "shown":
                    d.update(node=r[1], ref=r[2])
                elif r[0] == "other":
                    d["node"] = r[1]
                segments.append(d)
            out.append({"area": area, "path": path, "total": total, "segments": segments})
            if area != UNMAPPED:
                diags.append(_unexplained(by_id[area], path, open_lines, segments, repo, ft))
    return out, diags


def _cites(model: dict[str, Any], refs: dict[str, Any]):
    """Displayable node refs per file: (area, node, ref key, start, end)."""
    by_id = {n["id"]: n for n in model["nodes"]}
    area_of = {nid: _chain(by_id, nid)[0] for nid in by_id}
    cites: dict[str, list[tuple[str, str, str, int, int]]] = defaultdict(list)
    for n in model["nodes"]:
        for r in n["refs"]:
            res = refs.get(r.get("key") or "", {})
            if res.get("status") in DISPLAYED and res.get("start"):
                cites[r["path"]].append((area_of[n["id"]], n["id"], r["key"], res["start"], res["end"]))
    return cites


def python_units(text_lines: list[str]) -> list[tuple[str, int, int]] | None:
    """The definitions a Python file accounts for: module-level functions, classes and
    assignments, and the methods of classes, as (qualified name, start, end). None when the
    file does not parse. Statements inside functions are part of their function."""
    try:
        tree = ast.parse("\n".join(text_lines) + "\n")
    except (SyntaxError, ValueError):
        return None
    out = []

    def span(node):
        return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]), \
            node.end_lineno or node.lineno

    def visit(body, prefix, in_class):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append((prefix + node.name, *span(node)))
                if isinstance(node, ast.ClassDef):
                    visit(node.body, prefix + node.name + ".", True)
            elif not in_class and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id != "__all__":
                        out.append((t.id, *span(node)))
            elif not in_class and isinstance(node, (ast.If, ast.Try)) and not (
                isinstance(node, ast.If) and "__name__" in ast.unparse(node.test)
            ):
                visit(node.body, prefix, False)
                for h in getattr(node, "handlers", []):
                    visit(h.body, prefix, False)
                visit(node.orelse, prefix, False)

    visit(tree.body, "", False)
    return out


def mapping_units(model, ownership, refs, repo, reviewed_lines=None):
    """How each definition of an owned Python file is accounted for by its area's nodes.

    * ``mapped``: a node cites the definition itself (or a region inside it);
    * ``coarse``: it is only inside a broader ref (its class, the whole file, a wide line
      range): accounted for, but no node says anything about it in particular;
    * ``unmapped``: no node of its area shows it (``unexplained-code`` reports these).

    None of these says that a description is right. ``reviewed_lines(path)`` gives the file
    at ``scope.reviewed_revision`` (or None); definitions added since then that only a coarse
    ref covers are reported, because a broad ref silently covers new behavior.
    Returns ``(payload, diagnostics)``.
    """
    by_id = {n["id"]: n for n in model["nodes"]}
    cites = _cites(model, refs)
    counts = {"mapped": 0, "coarse": 0, "unmapped": 0}
    coarse: dict[tuple[str, str], dict[str, Any]] = {}
    files_without_inventory = []
    diags: list[Diagnostic] = []
    for path, rec in sorted(ownership["files"].items()):
        if not is_code(path) or (rec["owner"] is None and not rec["parts"]):
            continue
        if not path.endswith((".py", ".pyi")):
            files_without_inventory.append(path)
            continue
        try:
            ft = repo.read(path)
        except Exception:  # noqa: BLE001, S112 - unreadable files are reported elsewhere
            continue
        units = python_units(ft.lines)
        if units is None:
            files_without_inventory.append(path)
            continue
        old_names = None
        if reviewed_lines is not None:
            old = reviewed_lines(path)
            if old is not None:
                old_units = python_units(old)
                old_names = {u[0] for u in old_units} if old_units is not None else None
        for name, a, b in units:
            area = owner_at(rec, a)[0]
            if area is None:
                continue
            mine = [c for c in cites[path] if c[0] == area]
            if any((c[3] >= a and c[4] <= b) for c in mine):
                counts["mapped"] += 1
                continue
            around = [c for c in mine if c[3] <= a and c[4] >= b]
            if not around:
                counts["unmapped"] += 1
                continue
            counts["coarse"] += 1
            c = min(around, key=lambda c: c[4] - c[3])
            entry = coarse.setdefault((c[1], c[2]), {"node": c[1], "ref": c[2], "path": path,
                                                     "members": [], "new": []})
            entry["members"].append(name)
            if old_names is not None and name not in old_names:
                entry["new"].append(name)
    for e in coarse.values():
        # members of a coarsely cited class are named once, by their class
        e["members"] = [m for m in e["members"]
                        if not any(m.startswith(o + ".") for o in e["members"])]
        node = by_id[e["node"]]
        shown = ", ".join(e["members"][:6]) + ("…" if len(e["members"]) > 6 else "")
        diags.append(Diagnostic(
            "info",
            "coarse-mapping",
            f"'{e['node']}' accounts for {len(e['members'])} definitions of {e['path']} only "
            f"through its ref {e['ref']}: {shown}",
            node["location"] + ".refs",
            node["line"],
            e["node"],
            hint="fine for a cohesive module or class; if a member does something a reader "
            "should know, give it its own ref or node",
        ))
        if e["new"]:
            diags.append(Diagnostic(
                "warning",
                "coarse-new-members",
                f"{len(e['new'])} definitions added to {e['path']} since the reviewed revision "
                f"are covered only by '{e['node']}' through {e['ref']}: "
                + ", ".join(e["new"][:6]) + ("…" if len(e["new"]) > 6 else ""),
                node["location"] + ".refs",
                node["line"],
                e["node"],
                hint="read the new code; describe it (own ref or node) or confirm that the "
                "broad ref still says what it does, then update scope.reviewed_revision",
            ))
    payload = {
        "counts": counts,
        "coarse": list(coarse.values()),
        "without_inventory": files_without_inventory,
        "baseline": reviewed_lines is not None,
    }
    return payload, diags


def _unexplained(area: dict, path: str, open_lines: list[int], segments, repo, ft) -> Diagnostic:
    """An error naming the code no node shows, and a node that shows code next to it."""
    names: list[str] = []
    index = repo._python_index(path, ft) if path.endswith((".py", ".pyi")) else None
    for i in open_lines:
        name = f"line {i}"
        if index:
            spans = [(b - a, n) for n, ss in index.items() for a, b in ss if a <= i <= b]
            if spans:
                name = min(spans)[1]
        elif index is None:
            name = _enclosing_name(ft.lines, i) or name
        if not names or names[-1] != name:
            names.append(name)
    uniq = []
    for n in dict.fromkeys(n for n in names if not n.startswith("line ")):
        if not any(n.startswith(u + ".") for u in uniq):  # Order.id is part of Order
            uniq.append(n)
    lines_only = [n for n in names if n.startswith("line ")]
    shown = uniq[:6] + (["…"] if len(uniq) > 6 else [])
    what = ", ".join(shown) if shown else ""
    if lines_only:
        runs = _runs([int(n.split()[1]) for n in lines_only])
        what = (what + "; " if what else "") + "lines " + ", ".join(runs[:5]) + ("…" if len(runs) > 5 else "")
    near = [g["node"] for g in segments if g["kind"] == "shown"]
    hint = "show it from the node it belongs to with refs: a symbol (path#Name), a whole class, " \
        "or the whole file (path only)"
    if near:
        hint += f"; nodes showing code nearby: {', '.join(dict.fromkeys(near[:4]))}"
    hint += ". Imports, docstrings and comments need no node. Code nothing needs: delete it."
    return Diagnostic(
        "error",
        "unexplained-code",
        f"{path}: {len(open_lines)} lines of code that no node in '{area['id']}' shows: {what}",
        area["location"] + ".owns",
        area["line"],
        area["id"],
        hint=hint,
    )


_JS_DEF = re.compile(
    r"^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?\s+|class\s+|(?:const|let|var)\s+)"
    r"([A-Za-z_$][\w$]*)"
)
_JS_METHOD = re.compile(
    r"^(\s+)(?:static\s+|async\s+|get\s+|set\s+)*([A-Za-z_$][\w$]*)\s*(?:\([^)]*\)\s*\{|=\s*(?:\(|debounce|async))"
)


def _enclosing_name(lines: list[str], i: int) -> str | None:
    """For languages without a parser: the definition a line belongs to, by indentation
    (``Class.method`` for methods), or None at the top level."""
    own = len(lines[i - 1]) - len(lines[i - 1].lstrip())
    names: list[str] = []
    limit = own + 1
    for j in range(i, 0, -1):
        text = lines[j - 1]
        if not text.strip():
            continue
        ind = len(text) - len(text.lstrip())
        if ind >= limit:
            continue
        m = _JS_DEF.match(text) or (_JS_METHOD.match(text) if ind else None)
        if m and m.group(2) not in ("if", "for", "while", "switch", "catch", "return"):
            names.insert(0, m.group(2))
            limit = ind
            if ind == 0:
                break
        elif ind < limit:
            limit = ind + 1 if names else limit
    return ".".join(names) or None


def _runs(nums: list[int]) -> list[str]:
    out = []
    for n in nums:
        if out and out[-1][1] == n - 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [f"{a}" if a == b else f"{a}-{b}" for a, b in out]
