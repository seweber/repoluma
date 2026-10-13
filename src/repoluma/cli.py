"""Command-line interface: init, validate, where, serve, guide, schema, migrate, example, prompt."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import CURRENT_SCHEMA_VERSION, DEFAULT_MAP_PATH, __version__
from .workspace import Workspace

SKELETON = f"""\
# repoluma map: a domain-first description of this repository.
# Authoring guide:  repoluma guide      Schema:  repoluma schema
# Validate:         repoluma validate   View:    repoluma serve
schema_version: {CURRENT_SCHEMA_VERSION}

repository:
  name: {{name}}
  description: TODO one sentence on what this software is for.

scope:
  summary: Not described yet. Ask your coding agent to author this map (see `repoluma prompt`).
  not_covered:
    - Everything (the map has not been authored yet)

nodes: []
relationships: []
flows: []
"""

AGENT_PROMPT = """\
Please build a repoluma map of this repository.
1. Run `{cmd} guide` and follow those instructions exactly (they match the installed version).
2. Write the map to {map_path}, describing what the software does before how files are organized.
3. Run `{cmd} validate` and fix every error; re-run until it passes.
4. Tell me what you covered, what you left out, which claims need review, and which data lines you could not point at code.
"""


def _resource_text(*parts: str) -> str:
    node = resources.files("repoluma")
    for p in parts:
        node = node.joinpath(p)
    return node.read_text(encoding="utf-8")


def _find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    for d in (cwd, *cwd.parents):
        if (d / DEFAULT_MAP_PATH).is_file():
            return d
        if (d / ".git").exists():
            return d
    return cwd


def _workspace(args) -> Workspace:
    root = _find_root(args.root)
    return Workspace.open(root, args.map)


def _cmd_name() -> str:
    """How printed instructions should invoke repoluma (agents copy this verbatim)."""
    import os

    if os.environ.get("REPOLUMA_CMD"):
        return os.environ["REPOLUMA_CMD"]
    if "archive-v0" in sys.prefix.replace("\\", "/"):  # ephemeral environment created by uvx
        return "uvx repoluma"
    return "repoluma"


def cmd_init(args) -> int:
    ws = _workspace(args)
    cmd = _cmd_name()
    if ws.map_path.exists():
        print(f"A map already exists at {ws.map_display}; nothing was changed.")
    else:
        ws.map_path.parent.mkdir(parents=True, exist_ok=True)
        ws.map_path.write_text(SKELETON.format(name=ws.root.name), encoding="utf-8")
        print(f"Created {ws.map_display} (a placeholder; your coding agent fills it in).")
        gi = ws.map_path.parent / ".gitignore"
        if ws.map_path.parent.name == ".repoluma" and not gi.exists():
            gi.write_text(
                "# generated caches and local state; the map itself is tracked\ncache/\n",
                encoding="utf-8",
            )
    prompt = AGENT_PROMPT.format(cmd=cmd, map_path=ws.map_display)
    copied = None if args.no_copy or not sys.stdout.isatty() else copy_to_clipboard(prompt)
    print()
    if copied == "terminal":
        print("Next: give your coding agent this prompt (your terminal was asked to copy it to "
              "the clipboard; if pasting does not work, copy it from here):")
    elif copied:
        print(f"Next: give your coding agent this prompt (copied to the clipboard with {copied}):")
    else:
        print("Next: give your coding agent this prompt (copy it):")
    print()
    print(prompt)
    print(f"Then view the result with:  {cmd} serve")
    print(f"(Optional) export the guide as an agent skill:  {cmd} guide --write <path>")
    return 0


def copy_to_clipboard(text: str) -> str | None:
    """Put text on the clipboard; returns how (a tool's name, "terminal") or None.

    Uses the platform's clipboard command when there is one (pbcopy, clip.exe, wl-copy,
    xclip, xsel). Otherwise, on a terminal, it sends an OSC 52 sequence, which terminals
    that allow it (also over SSH and in containers) turn into a clipboard write; whether
    that happened cannot be checked.
    """
    import base64
    import os
    import subprocess

    tools = []
    if sys.platform == "darwin":
        tools.append(["pbcopy"])
    wsl = hasattr(os, "uname") and "microsoft" in os.uname().release.lower()
    if sys.platform == "win32" or wsl:
        tools.append(["clip.exe"])
    if os.environ.get("WAYLAND_DISPLAY"):
        tools.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        tools += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for tool in tools:
        if not shutil.which(tool[0]):
            continue
        data = text.encode("utf-16-le" if tool[0] == "clip.exe" else "utf-8")
        try:
            subprocess.run(tool, input=data, check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return tool[0]
        except (OSError, subprocess.SubprocessError):
            continue
    if sys.stdout.isatty():
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\033]52;c;{payload}\a")
        sys.stdout.flush()
        return "terminal"
    return None


def cmd_prompt(args) -> int:
    ws = _workspace(args)
    print(AGENT_PROMPT.format(cmd=_cmd_name(), map_path=ws.map_display))
    return 0


def cmd_validate(args) -> int:
    ws = _workspace(args)
    analysis = ws.analyze(with_git=not args.no_git, with_coverage=False)
    diags = analysis["diagnostics"]
    errors = [d for d in diags if d["severity"] == "error"]
    warnings = [d for d in diags if d["severity"] == "warning"]
    model = analysis["model"]
    if args.json:
        out = {
            "ok": not errors and not (args.strict and warnings),
            "map": analysis["map_path"],
            "counts": _counts(model),
            "grounding": _grounding(model, analysis) if model else None,
            "errors": len(errors),
            "warnings": len(warnings),
            "diagnostics": diags,
            "refs": analysis["refs"],
            "ownership": analysis.get("ownership"),
        }
        print(json.dumps(out, indent=2))
    else:
        from .model import Diagnostic

        shown = [d for d in diags if args.verbose or d["severity"] != "info"]
        for d in shown:
            print(
                Diagnostic(
                    **{
                        k: d.get(k)
                        for k in (
                            "severity",
                            "code",
                            "message",
                            "location",
                            "line",
                            "record",
                            "hint",
                        )
                    }
                ).format(analysis["map_path"])
            )
        if model is not None:
            c = _counts(model)
            refs = analysis["refs"]
            ok_refs = sum(
                1
                for r in refs.values()
                if r["status"] in ("resolved", "heuristic", "lines", "file", "moved")
            )
            own = (analysis.get("ownership") or {}).get("counts")
            owned = (
                f"; {own['owned_required']}/{own['required']} code files have an owner"
                if own
                else ""
            )
            print(
                f"{analysis['map_path']}: {c['nodes']} nodes, {c['relationships']} relationships"
                f" ({c['exchanges']} exchanges), {c['flows']} flows; {ok_refs}/{len(refs)} source"
                f" refs resolve{owned}"
            )
            g = _grounding(model, analysis)
            print(
                f"  {g['sited']}/{g['data']} data and error relationships cite a site with its focus text "
                f"found; {g['focus_found']}/{g['focus']} focus texts found"
                + (f"; Python definitions: {g['units']['mapped']} cited, {g['units']['coarse']} "
                   f"only in a broader ref, {g['units']['unmapped']} shown by no node"
                   if g["units"] else "")
            )
            infos = len(diags) - len(errors) - len(warnings)
            if infos and not args.verbose:
                print(f"({infos} informational notes hidden; use --verbose)")
        status = "FAILED" if errors or (args.strict and warnings) else "OK"
        print(f"{status}: {len(errors)} errors, {len(warnings)} warnings")
    if errors or (args.strict and warnings):
        return 1
    return 0


def cmd_where(args) -> int:
    from .ownership import owner_at

    ws = _workspace(args)
    rel, line = _repo_path(ws.root, args.path)
    analysis = ws.analyze(with_git=False, with_coverage=False)
    model = analysis["model"]
    if model is None:
        print(f"The map at {analysis['map_path']} cannot be read; run `repoluma validate`.")
        return 1
    own = analysis["ownership"] or {"files": {}}
    entry = own["files"].get(rel)
    by_id = {n["id"]: n for n in model["nodes"]}
    owner, part = owner_at(entry, line)
    cited = []
    for n in model["nodes"]:
        for r in n["refs"]:
            res = analysis["refs"].get(r["key"], {})
            if r["path"] != rel:
                continue
            if line is not None and res.get("start") and not res["start"] <= line <= res["end"]:
                continue
            span = f" L{res['start']}-{res['end']}" if res.get("start") else ""
            cited.append(f"{n['id']} ({r.get('symbol') or 'file'}{span})")
    # where the line is shown on the map: a citing node's pane, or its area's rest-of-file pane
    shown = None
    if line is not None and owner:
        for rp in own.get("rest", []):
            if rp["area"] == owner and rp["path"] == rel:
                seg = next((g for g in rp["segments"] if g["start"] <= line <= g["end"]), None)
                if seg and seg["kind"] == "shown":
                    shown = {"node": seg["node"], "ref": seg["ref"]}
                elif seg and seg["kind"] == "code":
                    shown = {"node": owner, "ref": f"{rel}#~rest"}
        if shown is None:
            spans = [
                (res["end"] - res["start"], n["id"], r["key"])
                for n in model["nodes"]
                for r in n["refs"]
                if r["path"] == rel
                and (res := analysis["refs"].get(r["key"], {})).get("start")
                and res["start"] <= line <= res["end"]
                and _chain_of(by_id, n["id"])[0] == owner
            ]
            if spans:
                _, nid, key = min(spans)
                shown = {"node": nid, "ref": key}
    result = {
        "path": rel,
        "line": line,
        "shown_in": shown,
        "owner": owner,
        "chain": _chain_of(by_id, owner),
        "pattern": entry["pattern"] if entry and not part else None,
        "part": part,
        "parts": entry["parts"] if entry else [],
        "required": entry["required"] if entry else None,
        "cited_by": cited,
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if owner else 1
    print(rel + (f":{line}" if line else ""))
    if owner:
        n = by_id[owner]
        print(f"  owner    {n['label']}  ({owner}, {n['kind']})")
        print(f"  on map   {' › '.join(by_id[a]['label'] for a in result['chain'])}")
        if n.get("summary"):
            print(f"  summary  {n['summary']}")
        if part:
            print(f"  by       owns {rel}#{part['symbol']} (lines {part['start']}-{part['end']})")
        else:
            print(f"  by       owns {entry['pattern']}")
        if shown:
            where = ("its rest-of-file pane" if shown["ref"].endswith("#~rest")
                     else f"shown in {by_id[shown['node']]['label']} ({shown['ref']})")
            print(f"  line     {where}")
    elif not (rel in own["files"] or (ws.root / rel).is_file()):
        print("  not a file in this repository (or excluded from display)")
        return 1
    else:
        need = entry["required"] if entry else False
        partly = " (only parts of it are owned)" if entry and entry["parts"] else ""
        print(f"  owner    none{partly}")
        print("  " + ("this code file should have an owner: add it to a node's 'owns'"
                      if need else "needs no owner (tests, docs, tooling or ignored files)"))
    others = [p for p in result["parts"] if p is not part]
    if others and line is None:
        print("  parts    " + "; ".join(
            f"{p['symbol']} (L{p['start']}-{p['end']}) → {p['node']}" for p in others))
    if cited:
        print("  cited by " + ", ".join(cited))
    print(f"  view     {_cmd_name()} serve, then open #file={rel}" + (f":{line}" if line else ""))
    return 0 if owner else 1


def _repo_path(root: Path, raw: str) -> tuple[str, int | None]:
    """Accept 'path', 'path:LINE', absolute paths and paths relative to the current directory."""
    import re

    line = None
    m = re.fullmatch(r"(.+?):(\d+)", raw)
    if m:
        raw, line = m.group(1), int(m.group(2))
    p = Path(raw)
    cand = p if p.is_absolute() else Path.cwd() / p
    try:
        return cand.resolve().relative_to(root).as_posix(), line
    except ValueError:
        return raw if p.is_absolute() else raw.replace("\\", "/").removeprefix("./"), line


def _chain_of(by_id, nid):
    chain = []
    while nid and nid in by_id and nid not in chain:
        chain.insert(0, nid)
        nid = by_id[nid]["parent"]
    return chain


def _counts(model):
    if not model:
        return {"nodes": 0, "relationships": 0, "exchanges": 0, "flows": 0}
    return {k: len(model[k]) for k in ("nodes", "relationships", "exchanges", "flows")}


def _grounding(model, analysis):
    """How much of the map's data movement points at code (not whether it is right)."""
    from .model import SITE_ROLES

    data = [r for r in model["relationships"] if r["type"] in ("data", "error")]
    anchors = [a for r in model["relationships"] for x in r["refs"] for a in x.get("anchors", [])]
    sited = [r for r in data if any(
        x["role"] in SITE_ROLES and any(a["status"] == "found" for a in x.get("anchors", []))
        for x in r["refs"])]
    units = ((analysis.get("ownership") or {}).get("units") or {}).get("counts")
    return {"data": len(data), "sited": len(sited), "focus": len(anchors),
            "focus_found": sum(a["status"] == "found" for a in anchors), "units": units}


def cmd_serve(args) -> int:
    from .server import serve

    ws = _workspace(args)
    serve(ws, host=args.host, port=args.port, open_browser=not args.no_open, verbose=args.verbose)
    return 0


def cmd_guide(args) -> int:
    text = _resource_text("guide", "AUTHORING.md").replace("{{version}}", __version__)
    if args.format == "skill":
        text = (
            "---\nname: repoluma-map\ndescription: Author or update the repoluma map "
            "(.repoluma/map.yaml), a domain-first description of this repository with source "
            "references. Use when asked to create, update, repair or validate the repoluma map.\n"
            "---\n\n" + text
        )
    if args.write:
        dest = Path(args.write)
        if dest.is_dir():
            dest = dest / ("SKILL.md" if args.format == "skill" else "REPOLUMA_GUIDE.md")
        if dest.exists() and not args.force:
            print(f"{dest} exists; not overwriting (use --force).", file=sys.stderr)
            return 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print(f"wrote {dest} (repoluma {__version__}); review it before pointing an agent at it.")
        return 0
    sys.stdout.write(text)
    return 0


def cmd_migrate(args) -> int:
    from .migrate import migrate_text

    ws = _workspace(args)
    try:
        text = ws.map_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {ws.map_display}: {exc}", file=sys.stderr)
        return 1
    new, notes, decisions = migrate_text(text)
    for n in notes:
        print(f"  changed  {n}")
    if new is None:
        for d in decisions:
            print(f"  cannot migrate: {d}", file=sys.stderr)
        return 1
    if args.in_place:
        dest = ws.map_path
    else:
        dest = Path(args.output) if args.output else ws.map_path.with_suffix(".v3.yaml")
        if dest.exists() and not args.force:
            print(f"{dest} exists; not overwriting (use --force, or --output PATH).",
                  file=sys.stderr)
            return 1
    dest.write_text(new, encoding="utf-8")
    print(f"wrote {dest}" + ("" if args.in_place else f" (your map {ws.map_display} is unchanged)"))
    if decisions:
        print(f"\n{len(decisions)} decisions need you (read the code; nothing was guessed):")
        for d in decisions:
            print(f"  - {d}")
    print("\nNext: make these decisions, then run `repoluma validate` on the result. "
          "`repoluma guide` explains schema_version 3 ('Migrating from schema_version 2').")
    return 0


def cmd_schema(args) -> int:
    from .model import json_schema

    print(json.dumps(json_schema(), indent=2))
    return 0


def cmd_example(args) -> int:
    dest = Path(args.dest)
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty; choose another directory.", file=sys.stderr)
        return 1
    src = resources.files("repoluma").joinpath("examples", "shop")
    with resources.as_file(src) as src_path:
        shutil.copytree(
            src_path, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
        )
    cmd = _cmd_name()
    print(f"Copied the example repository (a small fictional shop) to {dest}")
    print(f"Explore it with:  {cmd} serve --root {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="repoluma",
        description="A domain-first, zoomable map of a codebase. Typical first run:\n"
        "  repoluma init      create a placeholder map and print a prompt for your coding agent\n"
        "  repoluma validate  check the map and its source references\n"
        "  repoluma serve     open the viewer (works before a map exists, too)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"repoluma {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    def common(sp):
        sp.add_argument(
            "--root",
            help="repository root (default: nearest ancestor with "
            f"{DEFAULT_MAP_PATH} or .git, else the current directory)",
        )
        sp.add_argument("--map", help=f"map file (default: <root>/{DEFAULT_MAP_PATH})")

    sp = sub.add_parser("init", help="create a placeholder map and print the agent prompt")
    common(sp)
    sp.add_argument("--no-copy", action="store_true",
                    help="do not copy the prompt to the clipboard (it is copied only on a terminal)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("validate", help="validate the map and resolve its source references")
    common(sp)
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--strict", action="store_true", help="treat warnings as errors")
    sp.add_argument("--no-git", action="store_true", help="skip git change-since-review checks")
    sp.add_argument("-v", "--verbose", action="store_true", help="also show informational notes")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("where", help="show which part of the map a file (or line) belongs to")
    common(sp)
    sp.add_argument("path", help="a file, optionally with :LINE (e.g. src/app/util.py:42)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_where)

    sp = sub.add_parser("serve", help="serve the interactive viewer on localhost")
    common(sp)
    sp.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback only)")
    sp.add_argument("--port", type=int, default=0, help="port (default: a free port)")
    sp.add_argument("--no-open", action="store_true", help="do not open a browser")
    sp.add_argument("-v", "--verbose", action="store_true", help="log requests")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("guide", help="print the authoring guide for coding agents")
    sp.add_argument(
        "--format", choices=["md", "skill"], default="md", help="'skill' adds SKILL.md front matter"
    )
    sp.add_argument("--write", metavar="PATH", help="write to a file or directory instead")
    sp.add_argument("--force", action="store_true", help="overwrite an existing file")
    sp.set_defaults(func=cmd_guide)

    sp = sub.add_parser(
        "migrate",
        help=f"rewrite a schema_version 2 map for version {CURRENT_SCHEMA_VERSION} (to a new file)",
    )
    common(sp)
    sp.add_argument("--output", metavar="PATH", help="where to write (default: <map>.v3.yaml)")
    sp.add_argument("--in-place", action="store_true", help="overwrite the map itself")
    sp.add_argument("--force", action="store_true", help="overwrite an existing output file")
    sp.set_defaults(func=cmd_migrate)

    sp = sub.add_parser("schema", help="print the JSON Schema of the map format")
    sp.set_defaults(func=cmd_schema)

    sp = sub.add_parser("prompt", help="print a prompt to give your coding agent")
    common(sp)
    sp.set_defaults(func=cmd_prompt)

    sp = sub.add_parser("example", help="copy a small example repository with a map")
    sp.add_argument("dest", help="destination directory")
    sp.set_defaults(func=cmd_example)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
