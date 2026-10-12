"""Command-line interface: init, validate, serve, guide, schema, example, prompt."""

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
4. Tell me what you covered, what you left out, and which claims need review.
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
    print()
    print("Next: give your coding agent this prompt (copy it):")
    print()
    print(_indent(AGENT_PROMPT.format(cmd=cmd, map_path=ws.map_display)))
    print(f"Then view the result with:  {cmd} serve")
    print(f"(Optional) export the guide as an agent skill:  {cmd} guide --write <path>")
    return 0


def _indent(text: str) -> str:
    return "\n".join("    " + line if line else "" for line in text.splitlines()) + "\n"


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
            "errors": len(errors),
            "warnings": len(warnings),
            "diagnostics": diags,
            "refs": analysis["refs"],
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
            print(
                f"{analysis['map_path']}: {c['nodes']} nodes, {c['relationships']} relationships,"
                f" {c['flows']} flows; {ok_refs}/{len(refs)} source refs resolve"
            )
            infos = len(diags) - len(errors) - len(warnings)
            if infos and not args.verbose:
                print(f"({infos} informational notes hidden; use --verbose)")
        status = "FAILED" if errors or (args.strict and warnings) else "OK"
        print(f"{status}: {len(errors)} errors, {len(warnings)} warnings")
    if errors or (args.strict and warnings):
        return 1
    return 0


def _counts(model):
    if not model:
        return {"nodes": 0, "relationships": 0, "flows": 0}
    return {k: len(model[k]) for k in ("nodes", "relationships", "flows")}


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
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("validate", help="validate the map and resolve its source references")
    common(sp)
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--strict", action="store_true", help="treat warnings as errors")
    sp.add_argument("--no-git", action="store_true", help="skip git change-since-review checks")
    sp.add_argument("-v", "--verbose", action="store_true", help="also show informational notes")
    sp.set_defaults(func=cmd_validate)

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
