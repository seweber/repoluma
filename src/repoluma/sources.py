"""Read-only access to repository source.

Everything shown as implementation in the viewer comes through this module. It
confines access to the repository root (after symlink resolution), applies
exclusions for secrets and generated files, refuses binary and oversized files,
and resolves map references (path + symbol + line hint) to concrete line ranges.

Nothing here imports or executes repository code. Python symbols are located
with ``ast.parse`` (parsing only); other languages use a disclosed text
heuristic. Git is invoked read-only with hooks and fsmonitor disabled.
"""

from __future__ import annotations

import ast
import bisect
import fnmatch
import functools
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import check_relative_path

MAX_FILE_BYTES = 2_000_000
MAX_LINES_PER_REQUEST = 2000
HEURISTIC_MAX_SPAN = 5000

# Never served, regardless of the map. Patterns match the repo-relative path or any segment.
DEFAULT_EXCLUDES = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.sqlite",
    "*.db",
    ".repoluma/cache",
)

# Statuses: resolved/heuristic/lines/moved are displayable; the rest are not.
DISPLAYABLE = {"resolved", "heuristic", "lines", "moved", "file"}


class SourceError(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class FileText:
    lines: list[str]
    mtime_ns: int
    size: int


@dataclass
class Resolution:
    status: str
    message: str
    start: int | None = None
    end: int | None = None
    total_lines: int | None = None
    method: str | None = None  # python-ast | text-heuristic | lines | file
    candidates: list[list[int]] | None = None
    changed_since_review: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class Repository:
    def __init__(self, root: Path, extra_excludes: list[str] | tuple[str, ...] = ()):
        self.root = Path(root).resolve()
        self.excludes = tuple(DEFAULT_EXCLUDES) + tuple(extra_excludes)
        self._cache: dict[str, tuple[int, int, FileText]] = {}
        self._ast_cache: dict[str, tuple[int, dict[str, list[tuple[int, int]]]]] = {}
        self._norm_cache: dict[str, tuple[int, Normalized]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- confinement
    def is_excluded(self, rel: str) -> bool:
        parts = rel.split("/")
        for pat in self.excludes:
            pat = pat.rstrip("/")
            if fnmatch.fnmatchcase(rel, pat) or fnmatch.fnmatchcase(rel, pat + "/*"):
                return True
            if "/" not in pat and any(fnmatch.fnmatchcase(p, pat) for p in parts):
                return True
        return False

    def safe_path(self, rel: str) -> Path:
        problem = check_relative_path(rel)
        if problem:
            raise SourceError("invalid_path", problem)
        if self.is_excluded(rel):
            raise SourceError("excluded", f"{rel} is excluded from display")
        candidate = self.root / rel
        try:
            real = candidate.resolve(strict=True)
        except FileNotFoundError:
            raise SourceError("missing_file", f"{rel} does not exist") from None
        except (OSError, RuntimeError) as exc:
            raise SourceError("invalid_path", f"cannot resolve {rel}: {exc}") from None
        if real != self.root and self.root not in real.parents:
            raise SourceError("outside_root", f"{rel} resolves outside the repository")
        real_rel = real.relative_to(self.root).as_posix()
        if real_rel != rel and self.is_excluded(real_rel):
            raise SourceError("excluded", f"{rel} points to an excluded file")
        if not real.is_file():
            raise SourceError("not_a_file", f"{rel} is not a regular file")
        return real

    def read(self, rel: str) -> FileText:
        path = self.safe_path(rel)
        st = path.stat()
        with self._lock:
            hit = self._cache.get(rel)
            if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
                return hit[2]
        if st.st_size > MAX_FILE_BYTES:
            raise SourceError("too_large", f"{rel} is {st.st_size} bytes (limit {MAX_FILE_BYTES})")
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            raise SourceError("binary", f"{rel} looks binary")
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        ft = FileText(lines, st.st_mtime_ns, st.st_size)
        with self._lock:
            if len(self._cache) > 512:
                self._cache.clear()
                self._ast_cache.clear()
            self._cache[rel] = (st.st_mtime_ns, st.st_size, ft)
        return ft

    def stat_token(self, rel: str) -> str:
        """Cheap change token for a path (used by refresh polling)."""
        try:
            st = (self.root / rel).stat()
            return f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            return "missing"

    def excerpt(self, rel: str, start: int, end: int) -> dict[str, Any]:
        ft = self.read(rel)
        total = len(ft.lines)
        start = max(1, start)
        end = min(total, end, start + MAX_LINES_PER_REQUEST - 1)
        return {
            "path": rel,
            "start": start,
            "end": end,
            "total_lines": total,
            "lines": ft.lines[start - 1 : end] if total else [],
            "token": f"{ft.mtime_ns}:{ft.size}",
        }

    # -------------------------------------------------------------- resolution
    def resolve(self, ref: dict[str, Any]) -> Resolution:
        rel = ref["path"]
        try:
            ft = self.read(rel)
        except SourceError as exc:
            return Resolution(exc.status, exc.message)
        total = len(ft.lines)
        symbol = ref.get("symbol")
        hint = tuple(ref["lines"]) if ref.get("lines") else None

        if not symbol:
            if hint:
                if hint[0] > total:
                    return Resolution(
                        "out_of_range",
                        f"lines {hint[0]}-{hint[1]} but file has {total} lines",
                        total_lines=total,
                    )
                end = min(hint[1], total)
                note = "line range only: identity is not verified; add a symbol if one exists"
                if hint[1] > total:
                    note = f"range clipped to {total} lines; " + note
                return Resolution("lines", note, hint[0], end, total, "lines")
            return Resolution("file", "whole file", 1, max(total, 1), total, "file")

        if rel.endswith((".py", ".pyi")):
            spans, method = self._python_spans(rel, ft, symbol), "python-ast"
            if spans is None:  # syntax error: fall back honestly
                spans, method = _heuristic_spans(ft.lines, symbol, rel), "text-heuristic"
        else:
            spans, method = _heuristic_spans(ft.lines, symbol, rel), "text-heuristic"

        if not spans:
            hint_text = ""
            if method == "python-ast":
                guess = self._python_suggest(rel, ft, symbol)
                if guess:
                    hint_text = f"; did you mean '{guess}'?"
            return Resolution(
                "missing_symbol",
                f"symbol '{symbol}' not found in {rel} ({method}){hint_text}",
                total_lines=total,
                method=method,
            )

        chosen = spans
        if len(spans) > 1 and hint:
            overlapping = [s for s in spans if s[0] <= hint[1] and s[1] >= hint[0]]
            if len(overlapping) == 1:
                chosen = overlapping
        if len(chosen) > 1:
            return Resolution(
                "ambiguous",
                f"symbol '{symbol}' matches {len(chosen)} places in "
                f"{rel}; add a 'lines' hint or a qualified name",
                total_lines=total,
                method=method,
                candidates=[list(s) for s in chosen],
            )
        start, end = chosen[0]
        status = "resolved" if method == "python-ast" else "heuristic"
        message = (
            "located by Python syntax tree"
            if status == "resolved"
            else "located by text pattern (no parser for this language); verify the range"
        )
        if hint and not (start <= hint[1] and end >= hint[0]):
            status = "moved"
            message = (
                f"symbol found at {start}-{end}, outside the stated lines "
                f"{hint[0]}-{hint[1]}; update the hint"
            )
        return Resolution(status, message, start, end, total, method)

    def resolve_focus(self, ref: dict[str, Any], res: Resolution) -> list[dict[str, Any]]:
        """Locate each ``focus`` text of a ref inside the ref's resolved range.

        A focus must occur exactly once in the range (whitespace normalized, so a statement
        may span lines). No match and several matches are reported as such, never resolved
        to the first one or to a similar text; when a missing focus occurs elsewhere in the
        file, the message says where, without moving the evidence there.
        """
        focus = ref.get("focus") or []
        if not focus:
            return []
        if res.status not in DISPLAYABLE or not res.start:
            return [{"text": t, "status": "unresolved", "message": "the ref's range is not "
                     f"available ({res.status}), so the focus cannot be located"} for t in focus]
        try:
            ft = self.read(ref["path"])
        except SourceError as exc:  # changed or removed since the ref was resolved
            return [{"text": t, "status": "unresolved", "message": exc.message} for t in focus]
        norm = self._normalized(ref["path"], ft)
        anchors = []
        for text in focus:
            every = [(norm.pos(a), norm.pos(b - 1)) for a, b in _find_all(norm.text, text)]
            hits = [(p, q) for p, q in every if p[0] >= res.start and q[0] <= res.end]
            if len(hits) == 1:
                (l0, c0), (l1, c1) = hits[0]
                anchors.append({"text": text, "status": "found", "start": l0, "end": l1,
                                "cols": [c0, c1 + 1], "message": "found once in the ref's range"})
            elif hits:
                lines = sorted({p[0] for p, _ in hits})
                where = (f"on line {lines[0]}" if len(lines) == 1
                         else f"on lines {', '.join(map(str, lines[:6]))}")
                anchors.append({"text": text, "status": "ambiguous", "candidates": lines,
                                "message": f"occurs {len(hits)} times in the ref's range {where};"
                                " lengthen the focus text (unquoted YAML text ends at ' #', which "
                                "can shorten it)"})
            else:
                elsewhere = sorted({p[0] for p, _ in every})
                msg = "not found in the ref's range"
                if elsewhere:
                    msg += (f"; the text occurs elsewhere in the file (line "
                            f"{', '.join(map(str, elsewhere[:6]))}): check the symbol or lines")
                anchors.append({"text": text, "status": "missing", "message": msg,
                                "elsewhere": elsewhere[:6] or None})
        return anchors

    def _normalized(self, rel: str, ft: FileText) -> Normalized:
        hit = self._norm_cache.get(rel)
        if hit and hit[0] == ft.mtime_ns:
            return hit[1]
        norm = Normalized(ft.lines)
        with self._lock:
            if len(self._norm_cache) > 512:
                self._norm_cache.clear()
            self._norm_cache[rel] = (ft.mtime_ns, norm)
        return norm

    def _python_index(self, rel: str, ft: FileText) -> dict[str, list[tuple[int, int]]] | None:
        hit = self._ast_cache.get(rel)
        if hit and hit[0] == ft.mtime_ns:
            return hit[1]
        try:
            tree = ast.parse("\n".join(ft.lines) + "\n", filename=rel)
        except (SyntaxError, ValueError):
            return None
        index: dict[str, list[tuple[int, int]]] = {}

        def visit(body, prefix):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = prefix + node.name
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                    span = (start, node.end_lineno or node.lineno)
                    if _is_overload(node):
                        overloads.setdefault(name, []).append(span)
                    else:
                        index.setdefault(name, []).append(span)
                    visit(node.body, name + ".")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            index.setdefault(prefix + t.id, []).append(
                                (node.lineno, node.end_lineno or node.lineno)
                            )
                elif isinstance(node, (ast.If, ast.Try)) and not prefix:
                    # module-level conditional definitions are real alternatives
                    visit(node.body, prefix)
                    for h in getattr(node, "handlers", []):
                        visit(h.body, prefix)
                    visit(node.orelse, prefix)

        overloads: dict[str, list[tuple[int, int]]] = {}
        visit(tree.body, "")
        # typing.overload stubs only count when there is no implementation (e.g. .pyi files)
        for name, spans in overloads.items():
            index.setdefault(name, spans)
        self._ast_cache[rel] = (ft.mtime_ns, index)
        return index

    def _python_spans(self, rel, ft, symbol):
        index = self._python_index(rel, ft)
        if index is None:
            return None
        return sorted(set(index.get(symbol, [])))

    def _python_suggest(self, rel, ft, symbol):
        index = self._python_index(rel, ft) or {}
        tail = symbol.split(".")[-1]
        suffix = [k for k in index if k.split(".")[-1] == tail]
        if len(suffix) == 1:
            return suffix[0]
        import difflib

        m = difflib.get_close_matches(symbol, list(index), n=1, cutoff=0.7)
        return m[0] if m else None


def _is_overload(node) -> bool:
    for d in getattr(node, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        if (isinstance(target, ast.Name) and target.id == "overload") or (
            isinstance(target, ast.Attribute) and target.attr == "overload"
        ):
            return True
    return False


class Normalized:
    """A file's text with every run of whitespace (line breaks included) collapsed to one
    space, and the way back from an offset in it to (line, column) in the file."""

    _TOKEN = re.compile(r"\S+")

    def __init__(self, lines: list[str]):
        parts: list[str] = []
        self._offsets: list[int] = []  # offset of each token in the text
        self._where: list[tuple[int, int]] = []  # (line, column) of each token
        offset = 0
        for n, line in enumerate(lines, 1):
            for m in self._TOKEN.finditer(line):
                self._offsets.append(offset)
                self._where.append((n, m.start()))
                parts.append(m.group())
                offset += len(m.group()) + 1
        self.text = " ".join(parts)

    def pos(self, k: int) -> tuple[int, int]:
        i = bisect.bisect_right(self._offsets, k) - 1
        line, col = self._where[i]
        return line, col + (k - self._offsets[i])


def _find_all(haystack: str, needle: str) -> list[tuple[int, int]]:
    """All (possibly overlapping) occurrences as (start, end) offsets."""
    hits, i = [], haystack.find(needle)
    while i >= 0:
        hits.append((i, i + len(needle)))
        i = haystack.find(needle, i + 1)
    return hits


# ------------------------------------------------------------ text heuristic

_DEF_PREFIX = (
    r"(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+|internal\s+|static\s+|"
    r"abstract\s+|final\s+|override\s+|async\s+|pub(?:\([a-z]+\))?\s+|unsafe\s+|inline\s+|"
    r"virtual\s+|readonly\s+|sealed\s+|open\s+|data\s+|suspend\s+)*"
)
_DEF_KEYWORDS = (
    r"(?:function\*?|class|interface|type|enum|struct|trait|impl|fn|func|def|const|let|var|"
    r"module|namespace|record|object|union|macro_rules!|sub|proc)"
)


@functools.lru_cache(maxsize=4096)
def _definition_patterns(name: str) -> list[re.Pattern]:
    n = re.escape(name)
    return [
        re.compile(rf"^\s*{_DEF_PREFIX}{_DEF_KEYWORDS}\s+{n}\b"),
        re.compile(rf"^\s*func\s+\([^)]*\)\s*{n}\s*[\[(]"),  # Go methods
        re.compile(rf"^\s*{_DEF_PREFIX}(?:get\s+|set\s+|\*\s*)?{n}\s*(?:<[^>]*>)?\s*\([^;]*$"),
        re.compile(rf"^\s*(?:export\s+)?(?:const|let|var)\s+{n}\s*(?::[^=]+)?="),
        re.compile(
            rf"^\s*{n}\s*[:=]\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|[a-zA-Z_]\w*\s*=>)"
        ),
        re.compile(rf"^\s*(?:[\w:<>,*&\[\]]+\s+)+\**{n}\s*\([^;]*$"),  # C/C++/Java/C# with types
    ]


_CONTROL_WORDS = {"if", "for", "while", "switch", "return", "catch", "else", "do", "new", "await"}


def _heuristic_spans(lines: list[str], symbol: str, rel: str) -> list[tuple[int, int]]:
    if rel.endswith((".md", ".markdown", ".rst", ".txt")):
        return _markdown_spans(lines, symbol)
    if rel.endswith(".toml"):
        return _toml_spans(lines, symbol)
    parts = [p for p in re.split(r"\.|::|#", symbol) if p]
    if not parts:
        return []
    window = (1, len(lines))
    spans: list[tuple[int, int]] = []
    for depth, name in enumerate(parts):
        spans = _find_definitions(lines, name, window, skip_first=depth > 0)
        if depth < len(parts) - 1:
            if len(spans) != 1:
                return [] if not spans else spans
            window = spans[0]
    return spans


def _find_definitions(lines, name, window, skip_first=False):
    pats = _definition_patterns(name)
    found = []
    lo, hi = window
    for i in range(lo - 1, hi):
        line = lines[i]
        stripped = line.lstrip()
        if stripped.startswith(("//", "#", "*", "/*", "--", ";")) and not stripped.startswith("#["):
            continue
        first = re.match(r"\s*(\w+)", line)
        if first and first.group(1) in _CONTROL_WORDS:
            continue
        if any(p.search(line) for p in pats):
            start = i + 1
            if skip_first and start == lo:
                continue  # the container's own header line
            found.append((start, _block_end(lines, i, hi)))
    return found


def _block_end(lines: list[str], i: int, hi: int) -> int:
    """End line (1-based) of the block starting at index i: braces, else indentation."""
    limit = min(hi, i + HEURISTIC_MAX_SPAN)
    depth = 0
    opened = False
    for j in range(i, limit):
        code = re.sub(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)", '""', lines[j])
        code = re.sub(r"//.*$", "", code)
        for ch in code:
            if ch == "{":
                depth += 1
                opened = True
            elif ch == "}":
                depth -= 1
        if opened and depth <= 0:
            return j + 1
        if not opened and j > i + 2:
            break
        if not opened and j == i and code.rstrip().endswith(";"):
            return j + 1
    # indentation-based (Python-like, YAML, Ruby/Elixir with end keywords approximate)
    base = len(lines[i]) - len(lines[i].lstrip())
    end = i
    for j in range(i + 1, limit):
        s = lines[j]
        if not s.strip():
            continue
        indent = len(s) - len(s.lstrip())
        if indent <= base:
            if s.strip() in ("end", "}", "};", "fi", "done", "esac"):
                end = j
            break
        end = j
    return end + 1


def _markdown_spans(lines, heading):
    target = heading.strip().lower()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m and m.group(2).strip().lower() == target:
            level = len(m.group(1))
            end = len(lines)
            for j in range(i + 1, len(lines)):
                m2 = re.match(r"^(#{1,6})\s", lines[j])
                if m2 and len(m2.group(1)) <= level:
                    end = j
                    break
            while end > i + 1 and not lines[end - 1].strip():
                end -= 1
            out.append((i + 1, max(i + 1, end)))
    return out


def _toml_spans(lines, table):
    target = table.strip().strip("[]")
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\[\[?([^\]]+)\]\]?\s*$", line)
        if m and m.group(1).strip() == target:
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^\s*\[", lines[j]):
                    end = j
                    break
            while end > i + 1 and not lines[end - 1].strip():
                end -= 1
            out.append((i + 1, end))
    return out


# --------------------------------------------------------------------- git


def _git(root: Path, *args: str, timeout: float = 5.0) -> str | None:
    cmd = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "diff.external=",
        "--no-optional-locks",
        "-C",
        str(root),
        *args,
    ]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_state(root: Path, reviewed_revision: str | None = None) -> dict[str, Any]:
    """Working-tree context: HEAD, dirty files, and files changed since the reviewed revision."""
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if not inside or inside.strip() != "true":
        return {"is_repo": False}
    head = (_git(root, "rev-parse", "--verify", "-q", "HEAD") or "").strip() or None
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    prefix = (_git(root, "rev-parse", "--show-prefix") or "").strip()
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", ".") or ""
    dirty = []
    for entry in status.split("\x00"):
        if len(entry) > 3:
            path = entry[3:]
            if prefix and path.startswith(prefix):
                path = path[len(prefix) :]
            if path:
                dirty.append(path)
    state: dict[str, Any] = {
        "is_repo": True,
        "head": head,
        "branch": branch,
        "dirty": sorted(set(dirty)),
    }
    if reviewed_revision:
        rev_ok = _git(root, "rev-parse", "--verify", "-q", reviewed_revision + "^{commit}")
        if not rev_ok:
            state["reviewed"] = {"revision": reviewed_revision, "known": False}
        else:
            diff = _git(root, "diff", "--name-only", "--relative", "-z", reviewed_revision, "--")
            changed = [p for p in (diff or "").split("\x00") if p]
            changed += [p for p in dirty if p not in changed]
            state["reviewed"] = {
                "revision": reviewed_revision,
                "known": True,
                "changed": sorted(set(changed)),
            }
    return state


def file_at_revision(root: Path, rev: str, rel: str) -> str | None:
    """A file's text at a commit (read-only ``git show``), or None if it did not exist."""
    if check_relative_path(rel):
        return None
    out = _git(root, "show", f"{rev}:./{rel}", timeout=10.0)
    if out is None or "\x00" in out[:8192]:
        return None
    return out


def list_files(root: Path, repo: Repository, limit: int = 50_000) -> tuple[list[str], bool]:
    """Repository files (git-tracked + untracked-not-ignored when available)."""
    out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    files: list[str] = []
    if out is not None:
        files = [p for p in out.split("\x00") if p]
    else:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            dirnames[:] = [
                d
                for d in dirnames
                if not repo.is_excluded(d if rel_dir == "." else f"{rel_dir}/{d}")
            ]
            for f in filenames:
                files.append(f if rel_dir == "." else f"{rel_dir}/{f}")
                if len(files) >= limit:
                    break
            if len(files) >= limit:
                break
    files = [f for f in files if not repo.is_excluded(f)]
    truncated = len(files) > limit
    return sorted(files)[:limit], truncated


def coverage(files: dict[str, dict[str, Any]], depth: int = 2) -> list[dict[str, Any]]:
    """Per-directory counts of files that must have an owner, and of those that have one."""
    groups: dict[str, list[int]] = {}
    for f, rec in files.items():
        if not rec["required"]:
            continue
        parts = f.split("/")
        key = "/".join(parts[: min(depth, len(parts) - 1)]) or "(root files)"
        g = groups.setdefault(key, [0, 0])
        g[0] += 1
        if rec["owner"] is not None:
            g[1] += 1
    return [
        {"dir": k, "files": v[0], "owned": v[1]}
        for k, v in sorted(groups.items(), key=lambda kv: kv[0])
    ]
