# Map format (schema version 3)

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
| Meaning (nodes, relationships, exchanges, flows, contracts, certainty) | authored in the map |
| Evidence (paths, symbols, line hints, focus text) | authored as references, *resolved* at read time |
| Presentation (positions, sizes, zoom, filters, selection) | computed by the viewer; never authored |
| Caches (resolutions, excerpts) | in memory only; nothing is written to the repository |

The map never contains source code, signatures, coordinates or call graphs.

## Identity

- Every node, relationship, exchange and flow has an id unique across all of them,
  matching `^[a-z0-9][a-z0-9._-]{0,79}$`. Ids are independent of labels and positions.
  An exchange id is written on its legs (`exchange: <id>`); there is no exchange record.
- Relationship ids may be omitted (`from--type--to`, or `from--kind--to` when a kind is
  given, is derived) but explicit ids are recommended because flows reference them (`via`).
- Flow step ids are local to their flow (default `s1`, `s2`, ...).

## Containment versus relationships

`parent` forms a tree that decides *where* something is drawn. It is not a claim that
the system is a tree: cross-cutting concerns and many-to-many mappings are expressed
with typed relationships (data, control, dependency). The viewer never
draws containment as an edge.

A node with children is a **container** (an explanatory group); a node without is an
**item** (an operation, resource, contract, rule, external system or actor). This is
derived from the tree, not authored, so it cannot contradict it. In schema version 3 a
relationship may not end on a container (`group-endpoint`, an error): containers explain,
items act. A data or control relationship to or from a `rule` is a warning
(`rule-endpoint`): rules constrain, through dependencies. At overview zoom a line is drawn
to the regions containing its ends (a projection); the model keeps the real ends, and the
viewer names them on hover and in the inspector. Version 2 maps keep their lines on
containers as written, with an informational `legacy-group-endpoint` note; nothing is
expanded into lines between children.

## Relationship types and direction

Every relationship has a `type`, and its arrow always points from `from` to `to`:

- `data`: the data goes from `from` to `to`; `label` names the data (a warning if missing);
- `error`: information about a failure or problem (an exception, an error value, a
  reported diagnostic) goes from where it is raised or found (`from`) to where it is
  handled or read (`to`); `label` names it. Drawn red and dashed, with a
  route of its own even where it shares its ends with a data line;
- `control`: `from` starts or drives `to`, for what no data movement expresses;
- `dependency`: `from` relies on, implements, is constrained or verified by `to`.

`kind` is an optional detail within a type (`event`; `calls`, `triggers`, ...;
`implements`, `enforces`, ...). A control or dependency `kind` alone implies its type.

### Exchanges

Relationships that share an `exchange` id are the legs of one exchange, each with a `leg`:
`input` (caller → callee, data or control), `result` (synchronous return) and `response`
(arrives later), both data, or `error` (failure information instead of a result, a
relationship of type `error`). The validator checks that an exchange has at most one input, that all legs connect
the same two nodes, and that what comes back runs from callee to caller
(`exchange-incoherent`); a `leg` without `exchange` is a warning. Pairing is only ever
explicit: opposite relationships without a shared exchange id stay unpaired. The model
lists `exchanges: [{id, caller, callee, legs}]` (legs in the order input, result,
response, error); exchanges are not drawn themselves, their legs are lines as before.

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
  imports (multi-line ones too, and imports inside a module-level `if TYPE_CHECKING:`), the module docstring, `__all__`, the `__main__` block,
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
- **Broad coverage.** For owned Python files, every definition (module-level functions,
  classes and assignments, and the methods of classes) is sorted into `mapped` (a node of
  its area cites it or a region inside it), `coarse` (it is only inside a broader ref of
  that area: its class, the whole file, a wide line range) or `unmapped`. Each broad ref is
  listed (`coarse-mapping`, info) with its members. With a known `scope.reviewed_revision`,
  definitions added since then that only a broad ref covers are `coarse-new-members`
  warnings (the file at the revision is read with `git show`; nothing is cached or written).
  Other languages get line coverage only and are listed as without inventory. The payload
  carries this as `ownership.units` (`counts`, `coarse`, `without_inventory`, `baseline`).
  These are measures of where code is shown, not of whether it is explained well.

## Certainty, location and freshness

Three independent signals, never merged into one score:

- **Certainty** (`certainty`, authored; `evidence` in versions 1 and 2): how sure the author
  is of the claim. `observed` means the author read the code; repoluma does not execute or
  verify anything.
- **Location** (resolved): whether a ref's symbol, lines and focus text were found, and how
  (syntax tree, text pattern, line range only).
- **Freshness** (git): whether the file changed since `scope.reviewed_revision`, and for a
  focus text in a changed file whether the same text was there at that revision.

In version 3 an `observed` data or error relationship needs a ref with role `transfer` or `receive`
(`observed-without-site`, a warning) and such a ref needs `focus` (`site-without-focus`,
a warning). Warnings fail `validate --strict`; they are not errors so that a map that is
incomplete in this respect stays usable while it is being grounded. Version 2 maps are not
checked for this.

## Reference resolution semantics

A reference is `{path, symbol?, lines?, role?, focus?, note?}` (or `path#symbol`, `path:a-b`).
On relationships, `role` may also be `transfer`, `receive` or `origin` (where the data is
handed over, taken in, or made).

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

5. With `focus` (text or a list of text): each text is searched in the resolved range with
   runs of whitespace collapsed (so a statement may span lines). Exactly one match is
   `found`, with its lines and start/end columns; several are `ambiguous` (with the lines);
   none is `missing` (the message names other lines of the file where the text occurs,
   which are never used instead); a ref whose range cannot be displayed makes its focus
   `unresolved`. Anything but `found` is an error (`focus-missing`, `focus-ambiguous`,
   `focus-unresolved`). The results are `anchors` on the ref in the model:
   `{text, status, start?, end?, cols?, candidates?, elsewhere?, message, since_review}`,
   where `since_review` is `unknown` (no reviewed revision), `unchanged` (the file did not
   change), `text_unchanged` (the file changed, the text was there at the revision) or
   `text_new`. A stable text does not prove that its surroundings kept their meaning.

Statuses `resolved`, `heuristic`, `lines`, `moved`, `file` are displayable; everything
else (`missing_file`, `missing_symbol`, `ambiguous`, `out_of_range`, `excluded`,
`binary`, `too_large`, `invalid_path`, `outside_root`, `not_a_file`) is reported and
never shown as code.

Source mode is the **current working tree** (including uncommitted changes); the top
bar shows HEAD and the number of uncommitted files. If `scope.reviewed_revision` is set,
references into files changed since that commit are marked "changed since review":
a reference that still resolves does not prove the description is still correct.

## Compatibility policy

- `schema_version` is an integer. This release writes version `3` and reads `1`, `2` and `3`.
  Version 2 maps are read with their old meaning (lines on containers allowed, `evidence`
  as certainty, no site requirement) and an informational `old-schema-version` note.
  `repoluma migrate` does the mechanical part of moving a map to version 3 into a new file
  (`--in-place` to overwrite): the version number and the `evidence` → `certainty` rename,
  keeping the text's comments and order, checked by parsing the result; it lists the
  decisions that need the code (lines on containers, data lines without sites, opposite
  lines that might be one exchange) instead of guessing them.
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
