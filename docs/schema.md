# Map format (schema version 1)

The authoritative, agent-facing description is the authoring guide
(`repoluma guide`, source: `src/repoluma/guide/AUTHORING.md`). The machine-readable
schema is printed by `repoluma schema` and is generated from the validator's own
constants (`repoluma.model.json_schema`), so the two cannot drift; a test validates
both bundled maps against it.

## Why YAML

Agents write YAML reliably, it allows comments, and it diffs line by line. It is parsed
with a `SafeLoader` subclass that only builds plain data and records each record's line
number, so diagnostics can say `map.yaml:42`. JSON is valid YAML and is accepted too.

## Separation of concerns

| concern | where it lives |
| --- | --- |
| Meaning (nodes, relationships, flows, contracts, evidence status) | authored in the map |
| Evidence (paths, symbols, line hints) | authored as references, *resolved* at read time |
| Presentation (positions, sizes, zoom, filters, selection) | computed by the viewer; never authored |
| Caches (resolutions, excerpts) | in memory only; nothing is written to the repository |

The map never contains source code, signatures, coordinates or call graphs.

## Identity

- Every node, relationship and flow has an id unique across all three record types,
  matching `^[a-z0-9][a-z0-9._-]{0,79}$`. Ids are independent of labels and positions.
- Relationship ids may be omitted (`from--type--to`, or `from--kind--to` when a kind is
  given, is derived) but explicit ids are recommended because flows reference them (`via`).
- Flow step ids are local to their flow (default `s1`, `s2`, ...).

## Containment versus relationships

`parent` forms a tree that decides *where* something is drawn. It is not a claim that
the system is a tree: cross-cutting concerns and many-to-many mappings are expressed
with typed relationships (data, control, dependency). The viewer never
draws containment as an edge.

## Relationship types and direction

Every relationship has a `type`, and its arrow always points from `from` to `to`:

- `data`: the data goes from `from` to `to`; `label` names the data (a warning if missing);
- `control`: `from` starts or drives `to`, for what no data movement expresses;
- `dependency`: `from` relies on, implements, is constrained or verified by `to`.

`kind` is an optional detail within a type (`event`; `calls`, `triggers`, ...;
`implements`, `enforces`, ...). A control or dependency `kind` alone implies its type.

Version 1 maps described relationships with verbs read as "from *verb* to" (`A reads B`).
They are still read, with a warning: each verb is mapped to a type, and `reads`,
`subscribes` and `handles` swap `from` and `to` so that every data arrow shows where the
data goes. Default ids are derived before the swap, so references from flows keep
working. A version 2 map that still uses a version 1 verb without `type` is read the same
way, with a `verb-kind` warning.

## Flows are drawn on relationships

A flow step is shown by highlighting the relationship it uses: `via`, which must touch
the step's node or a node containing it (or inside it), or else a relationship from where
the flow is to this step's node (exact ends first, then containing nodes, then map order).
The resolved id is the step's `link` in the model the viewer receives. A step without one
gets only its numbered badge, and `validate` warns (`step-without-relationship`,
`step-via-mismatch`).

*Where the flow is* when a step starts: the previous step's node, then the other end of
the relationship that step was drawn on (so each step of a coordinator calling one
service after another can be drawn on the coordinator's call). A step's `via` must touch
one of these, unless one of its ends is an `actor` (actors start things anew); otherwise
the flow jumps over the nodes in between, which the viewer then dims although the flow
passes through them, and `validate` warns (`step-via-gap`). Flows add no lines and do not
influence layout. These are warnings, so maps that were valid stay valid.

## Ownership: one home per file

`refs` are evidence (many nodes may cite the same code); `owns` is placement: every code
file has exactly one owning area (a top-level node), so a file found anywhere leads to one
place on the map, and every line of it is shown inside that area.

- `owns` is accepted on top-level nodes only; on an inner node it is an error
  (`owns-not-top-level`) and is counted for the node's area meanwhile.

- An `owns` entry is an exact path, a folder (`dir/`, or `dir` when that names a
  folder), a glob (`*` within a segment, `**` across segments, `?`, `[...]`), or
  `path#Symbol` for part of one file. Paths follow the reference rules (relative, `/`,
  no `..`); `**` alone is refused.
- A file's area is decided by the **most specific** matching pattern: exact paths beat
  globs; among globs, more fixed leading segments, then more fixed characters. Equal
  specificity from different nodes is `ownership-conflict` (an error); the file is shown
  under the first of them in id order meanwhile.
- `path#Symbol` is resolved like a reference symbol. The part belongs to that node; the
  file's owner keeps the rest; the innermost part containing a line wins. The same part
  claimed twice is a conflict; a missing symbol is an error (`owns-missing-symbol`).
- A file *needs* an owner if its extension is a code language and it is not a test, doc,
  vendored, generated or hidden-tooling file (`repoluma.ownership.DEFAULT_UNREQUIRED`),
  minus `source.ownership.ignore`, plus `source.ownership.require`. Files that need an
  owner and have none are `unowned-file` warnings, grouped per folder, with a suggested
  owner where refs point at them (the deepest node containing every citing node).
- The file list is `git ls-files --cached --others --exclude-standard` (or a walk outside
  git), minus `source.exclude` and the default exclusions; ownership is recomputed when
  files are added or removed.
- **Coverage of code.** Every line of a code file must be shown by a node of the area
  that owns it: inside the range of a displayable ref of that node or a node within it
  (citations from other areas do not count). Glue needs none: blank lines, comments,
  imports (multi-line ones too), the module docstring, `__all__`, the `__main__` block,
  lines of only closing brackets, and a class's header (decorators, `class` line,
  docstring) when a member of the class is shown. Anything else is an `unexplained-code`
  error, one per area and file, naming the symbols (Python syntax tree; enclosing
  definitions by text pattern elsewhere) and the nodes showing code nearby.
- **Display.** For each area and file with unexplained code, `ownership.rest` lists
  segments covering the file: `code` (unexplained, with glue next to it), `glue`, `shown`
  (with the node and ref whose pane shows it; the smallest such ref wins) and `other`
  (another area's part, or no owner). The viewer draws it as a dashed "not yet explained"
  pane in the area. Code that needs an owner and has none is listed under `~unmapped`,
  drawn as "Not yet mapped". Non-code files are owned but not checked for coverage.
- The analysis payload carries `ownership.files` (owner, deciding pattern, parts, whether
  an owner is required, line count) for owned files, files that need an owner, and cited
  files, plus `ownership.unowned`, `ownership.rest` and counts. Within schema version 2
  these are additive fields.

## Reference resolution semantics

A reference is `{path, symbol?, lines?, role?, note?}` (or `path#symbol`, `path:a-b`).

1. `path` is repository-relative with `/` separators; absolute paths, `..`, and
   backslashes are rejected at validation time. At read time the path is resolved with
   symlinks and must stay inside the root and outside the exclusion list.
2. With a `symbol`:
   - `.py`/`.pyi`: exact qualified-name lookup in the syntax tree (`Class.method`,
     `function`, `CONSTANT`; decorators included in the range). No fuzzy matching; a
     "did you mean" suggestion may be offered in the message.
   - Markdown: heading text; TOML: table name.
   - Anything else: definition-like text patterns, then brace or indentation matching to
     find the end. Reported as `heuristic`.
   - Several matches: `ambiguous` unless exactly one overlaps the `lines` hint.
   - A single match outside the `lines` hint: `moved` (still displayed; hint is stale).
3. With only `lines`: displayed as `lines` (identity not verified).
4. With neither: the whole file (`file`).

Statuses `resolved`, `heuristic`, `lines`, `moved`, `file` are displayable; everything
else (`missing_file`, `missing_symbol`, `ambiguous`, `out_of_range`, `excluded`,
`binary`, `too_large`, `invalid_path`, `outside_root`, `not_a_file`) is reported and
never shown as code.

Source mode is the **current working tree** (including uncommitted changes); the top
bar shows HEAD and the number of uncommitted files. If `scope.reviewed_revision` is set,
references into files changed since that commit are marked "changed since review":
a reference that still resolves does not prove the description is still correct.

## Compatibility policy

- `schema_version` is an integer. This release writes version `2` and reads `1` and `2`.
- Within a schema version, changes are additive only: new optional fields, new
  recommended kinds. Older maps stay valid; unknown kinds and fields produce warnings,
  not errors, so a map written for a newer minor release still opens.
- A change that would make existing valid maps invalid or change their meaning requires
  a new `schema_version`. repoluma will keep reading the previous version for at least
  one minor release and `validate` will explain the migration.
- A map with a newer `schema_version` than the installed repoluma supports is refused
  with an "upgrade repoluma" hint rather than misread.
- The guide printed by `repoluma guide` always matches the installed version, which
  is why the init prompt tells agents to read it instead of a copied file.
