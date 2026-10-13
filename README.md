# repoluma

**A domain-first, zoomable map of a codebase, written by your coding agent and backed by
live source.**

repoluma shows what a piece of software *does* before showing how its files are
organized. Your coding agent writes a small structured description of the repository
(`.repoluma/map.yaml`): capabilities, responsibilities, contracts, rules, shared
resources, flows (including failure paths), and references into the source. repoluma
validates that description against the real code and serves a local, zoomable map:

- **Overview**: the system's areas of purpose, with relationships summarized between them.
- **Zoom in**: responsibilities, contracts (inputs, outputs, guarantees, errors), rules,
  and the relationships between them appear as they become large enough to read.
- **Zoom further**: the referenced implementation and tests appear *inside the map*,
  read live from your working tree, with line numbers, freshness, and resolution status.
- **Follow the data**: relationships are data (labelled with what moves, arrow from source
  to destination), control, or dependency, explained by a legend on the map.
- **Find every file's place**: each code file belongs to exactly one area of the map
  (`owns` on top-level nodes), each area names its files, and every line of code must be
  shown by a node of its area (`validate` fails on code no node explains; the viewer
  flags it).
  From any file (the Files tab, a code pane's file name, `#file=path:line`, or
  `repoluma where path:line`) you reach its area and the pane showing each line. Code
  files without an owner show up as "not yet mapped".
- **Follow a flow** step by step across responsibilities, including what happens on failure.
- **Stay honest**: unresolved references, uncertain claims, unmodeled directories, and
  hidden-by-filter counts are shown, not hidden.
- **Talk to your agent**: stable ids, view links, and a "copy for agent" summary of any selection.

There is no embedded LLM, no API key, and no build step for users. The viewer never
executes or imports the repository it shows.

## Quick start

```sh
cd your-repository
uvx repoluma init        # creates .repoluma/map.yaml (placeholder) and prints a prompt
# give the printed prompt to your coding agent; it runs `uvx repoluma guide` and `validate`
uvx repoluma serve       # opens the viewer on http://127.0.0.1:<port>/
```

`serve` works before a map exists: the page explains the next step and loads the map as
soon as the agent writes a valid one. Edits to the map or to referenced files appear in
the open page within about two seconds.

Want to look first? `uvx repoluma example ./repoluma-example && uvx repoluma serve --root ./repoluma-example`
opens a small fictional shop with a complete map.

> `uvx repoluma` assumes a published release. Before that, use a checkout:
> `uvx --from /path/to/repoluma repoluma serve` (or see [development](docs/development.md)).

## Commands

| command | what it does |
| --- | --- |
| `repoluma init` | Create a placeholder map if none exists (never overwrites) and print an agent prompt. |
| `repoluma prompt` | Print the agent prompt again. |
| `repoluma guide` | Print the authoring guide for agents (matches this version). `--format skill --write DIR` exports it as a `SKILL.md` for agents that use skills. Nothing is installed into agent configuration automatically. |
| `repoluma schema` | Print the JSON Schema of the map format. |
| `repoluma validate` | Check the map, resolve every source reference, and check that every code file has exactly one owner. `--json` for machine-readable diagnostics, `--strict` to fail on warnings. Exit status 1 on errors. |
| `repoluma where PATH[:LINE]` | Which area owns a file (or one line of it), which pane shows that line, and which nodes cite it. `--json`; exit status 1 if it has no owner. |
| `repoluma serve` | Serve the viewer on loopback. `--root`, `--map`, `--port`, `--no-open`. |
| `repoluma example DIR` | Copy the example repository with its map. |

## Using the viewer

| input | effect |
| --- | --- |
| wheel / pinch | zoom around the pointer |
| drag, arrow keys | pan (zoom level is kept, so neighbouring regions show the same depth) |
| click / double-click | select and inspect / zoom to it |
| click a code pane | activate it: the wheel scrolls code and text is selectable; `Esc` releases |
| `/` | search labels, ids, summaries, file paths |
| `0`, `b`, `u` | whole map, back, select the containing item |
| `n` / `p` | next / previous step of the active flow |
| `c` | copy the selection as text for a coding agent |

The left panel has an **Outline** (keyboard-accessible tree), **Files** (every file and
the node it belongs to), **Flows**, **Layers** (relationship types, node kinds, tags,
uncertainty highlighting, legend), and **Coverage** (diagnostics, reference status,
changed-since-review, claims without evidence, files without an owner). Clicking a file,
or the file name above a code pane, shows the whole file and which pane on the map shows each line. The URL always encodes the current view, selection,
flow step, and filters.

## Documentation

- [Map format and compatibility policy](docs/schema.md); the agent-facing guide is
  `repoluma guide` ([source](src/repoluma/guide/AUTHORING.md))
- [Design decisions](docs/decisions.md): what was chosen, what was tried, and when to revisit
- [Development](docs/development.md): running the checkout on its own map, tests, refresh
- [Performance](docs/performance.md): measured limits and how to reproduce them
- [Security model](docs/security.md)
- [Evaluation notes](docs/evaluation.md): an external agent mapping `psf/requests`, and what changed

## Status and limitations

Alpha (0.1). Known limitations:

- Symbol resolution is exact for Python. Other languages use disclosed text heuristics
  (good for common definition syntax, not a parser), plus Markdown headings and TOML tables.
- Layout is automatic and connection-driven; there is no manual positioning yet. Dense
  regions still have line crossings (see `docs/decisions.md` for measurements).
- One map file per repository; very large maps work (see performance) but are not split
  into multiple files.
- No proposed-change overlay, visual editing, or chat integration yet.
- Accessibility: the map canvas is mirrored by the keyboard-navigable outline and
  inspector; the canvas itself is not screen-reader accessible.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
