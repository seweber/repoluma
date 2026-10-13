"""Combines a map file and a repository into what the viewer and validator need."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import DEFAULT_MAP_PATH, __version__
from .model import Diagnostic, LoadResult, iter_refs, load_map, ref_key
from .ownership import compute_ownership, rest_of_files
from .sources import DISPLAYABLE, Repository, coverage, git_state, list_files

REF_SEVERITY = {
    "missing_file": "error",
    "missing_symbol": "error",
    "invalid_path": "error",
    "outside_root": "error",
    "not_a_file": "error",
    "out_of_range": "error",
    "excluded": "error",
    "ambiguous": "error",
    "binary": "warning",
    "too_large": "warning",
    "moved": "warning",
    "lines": "info",
    "heuristic": "info",
}


@dataclass
class Workspace:
    root: Path
    map_path: Path

    @classmethod
    def open(cls, root: str | Path = ".", map_path: str | Path | None = None) -> Workspace:
        root_p = Path(root).resolve()
        mp = Path(map_path) if map_path else root_p / DEFAULT_MAP_PATH
        if not mp.is_absolute():
            mp = (Path.cwd() / mp).resolve()
        return cls(root_p, mp)

    @property
    def map_display(self) -> str:
        try:
            return self.map_path.relative_to(self.root).as_posix()
        except ValueError:
            return str(self.map_path)

    def load(self) -> tuple[LoadResult, Repository]:
        result = load_map(self.map_path)
        excludes = result.model["source"]["exclude"] if result.model else []
        return result, Repository(self.root, excludes)

    def analyze(self, with_git: bool = True, with_coverage: bool = True) -> dict[str, Any]:
        """Validate the map, resolve every reference, and gather repository context."""
        started = time.perf_counter()
        result, repo = self.load()
        diags: list[Diagnostic] = list(result.diagnostics)
        model = result.model
        refs: dict[str, Any] = {}
        git: dict[str, Any] = {"is_repo": False}
        cov = None
        ownership = None
        if model is not None:
            if with_git:
                git = git_state(self.root, model["scope"]["reviewed_revision"])
            changed = set(git.get("reviewed", {}).get("changed", []))
            for owner_kind, owner_id, ref in iter_refs(model):
                key = ref_key(ref)
                ref["key"] = key
                if key not in refs:
                    res = repo.resolve(ref)
                    if git.get("reviewed", {}).get("known"):
                        res.changed_since_review = ref["path"] in changed
                    refs[key] = res.to_dict()
                status = refs[key]["status"]
                sev = REF_SEVERITY.get(status)
                if sev:
                    diags.append(
                        Diagnostic(
                            sev,
                            f"ref-{status.replace('_', '-')}",
                            f"{owner_kind} ref {key}: {refs[key]['message']}",
                            ref["location"],
                            ref.get("line"),
                            owner_id,
                            hint=_ref_hint(status),
                        )
                    )
            if git.get("reviewed") and not git["reviewed"].get("known"):
                diags.append(
                    Diagnostic(
                        "warning",
                        "unknown-reviewed-revision",
                        f"scope.reviewed_revision {git['reviewed']['revision']!r} is not a commit in "
                        "this repository; change-since-review cannot be computed",
                        "scope",
                    )
                )
            files, truncated = list_files(self.root, repo)
            ownership, own_diags = compute_ownership(model, files, repo, truncated)
            ownership["rest"], rest_diags = rest_of_files(model, ownership, refs, repo)
            diags.extend(own_diags + rest_diags)
            if with_coverage:
                cov = {
                    "by_dir": coverage(ownership["files"]),
                    "files": len(files),
                    "truncated": truncated,
                }
        return {
            "version": __version__,
            "root_name": self.root.name,
            "map_path": self.map_display,
            "map_exists": self.map_path.exists(),
            "model": model,
            "diagnostics": [d.to_dict() for d in diags],
            "refs": refs,
            "git": git,
            "coverage": cov,
            "ownership": ownership,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def watch_tokens(self, ref_paths: list[str], repo: Repository) -> dict[str, str]:
        tokens = {"__map__": _stat(self.map_path)}
        for p in ref_paths:
            tokens[p] = repo.stat_token(p)
        return tokens


def _stat(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "missing"


def _ref_hint(status: str) -> str | None:
    return {
        "missing_file": "the file moved or was deleted: find its new path or remove the ref",
        "missing_symbol": "the symbol was renamed or moved: update 'symbol' (use the qualified "
        "name, e.g. Class.method)",
        "ambiguous": "add a 'lines' hint that overlaps the intended definition",
        "moved": "update 'lines' to the symbol's current location, or drop 'lines'",
        "lines": None,
        "excluded": "excluded files are never displayed; reference a non-secret file",
        "out_of_range": "the file got shorter: update 'lines' or add a symbol",
    }.get(status)


def is_displayable(status: str) -> bool:
    return status in DISPLAYABLE
