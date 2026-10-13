# Authoring a repoluma map (guide for coding agents)

This guide ships with repoluma {{version}} and matches its schema (`schema_version: 2`).
Print it any time with `repoluma guide`; print the JSON Schema with `repoluma schema`.

A repoluma map is a YAML file (default `.repoluma/map.yaml`) that explains **what a
repository does** and links each claim to **evidence in the source**. People read it
in a zoomable viewer: the overview shows your top-level nodes, zooming in reveals the
nodes you nest inside them, and zooming further shows the referenced source code,
read live from the working tree. Your job is the meaning; repoluma handles layout,
code display, and reference checking.

## Workflow

1. **Understand before drawing.** Read the README, entry points, tests, configuration,
   and the main modules. Identify the system's purpose, its users/actors, its most
   important use cases, its vocabulary, and its boundaries (external systems, storage,
   queues, processes). Do not infer architecture from file names alone.
2. **Start small and useful.** First write 4–9 top-level nodes describing areas of
   purpose (capabilities or domains), the most important responsibilities under them,
   and **one end-to-end flow including a failure path**. Validate. Then add detail
   where it helps a reader answer real questions.
3. **Follow the data.** Model what moves through the system (requests, records, events,
   results) as data relationships labelled with the data, and describe contracts, rules,
   and shared resources: what an interface promises, what can fail, what must stay true,
   what is shared.
4. **Attach evidence.** Give leaf nodes `refs` to the code (and tests) that support
   them. Mark how sure you are with `evidence`.
5. **Give every code file a home, and every line of code a node.** With `owns` on the
   top-level nodes, each code file belongs to exactly one area; inside the area, every
   line of code must be shown by some node's `refs` (see "Ownership" below).
6. **Validate and repair**: run `repoluma validate` and fix every error. Use
   `repoluma validate --json` for machine-readable diagnostics. Treat warnings as
   likely mistakes (typos in field names, unknown kinds, outdated line hints).
7. **Look at it** if you can: `repoluma serve` prints a local URL.
8. **Report** to the user: what the map covers, what it omits (also record that in
   `scope.not_covered`), and which claims need review.

## The format

YAML tips: prefer block style (one field per line) for anything with prose. If you use
inline mappings like `{id: a, label: ...}`, quote any value that contains a comma or
`: ` (`label: "Declined: release stock, retry"`). Only the first YAML syntax error is
reported per validate run.

```yaml
schema_version: 2
repository:
  name: shop
  description: One sentence on what this software is for.
scope:
  summary: What this map covers and how deep.
  covered: [Checkout including declined payments]
  not_covered: [Admin back office, deployment]
  reviewed_revision: 3f2a1c9   # optional: commit the map was last checked against
source:
  exclude: [fixtures/large/*]  # optional extra exclusions (secrets are excluded by default)
  ownership:
    ignore: [scripts/**]       # optional: code files that need no owner (tests, docs are skipped anyway)
nodes:
  - id: payments               # stable id: lowercase, digits, . _ - ; never reuse for something else
    kind: capability
    label: Payments
    summary: Holds, collects and returns customer money through an external provider.
    owns: [shop/payments/]     # top-level nodes only: every file in this folder belongs to this area
  - id: authorize-payment
    parent: payments           # containment: where it lives on the map
    kind: contract
    label: Payment authorization
    summary: Hold funds for an order without collecting them.
    contract:
      inputs: [payment token, positive amount and currency]
      outputs: [Authorization id and amount]
      guarantees: [Never captures money]
      errors: [PaymentDeclined with the provider's reason]
      assumptions: [Provider answers synchronously]
    evidence: observed
    refs:
      - path: shop/payments.py
        symbol: PaymentService.authorize
      - path: tests/test_orders.py
        symbol: test_declined_payment_releases_reservation
        role: test
relationships:
  - id: order-authorizes         # data: from = where the data comes from, to = where it goes
    from: place-order
    to: authorize-payment
    type: data
    label: payment token and total   # the data itself, not a verb
    evidence: observed
  - id: authorization-returned   # the result of the same call is a second data line
    from: authorize-payment
    to: place-order
    type: data
    label: authorization
    evidence: observed
  - id: order-enforces-limits
    from: place-order
    to: cart-limits
    type: dependency
    kind: enforces
    evidence: observed
flows:
  - id: flow-place-order
    label: Place an order
    steps:
      - {id: reserve, node: reserve-stock, label: Reserve every line or fail}
      - {id: authorize, node: authorize-payment, label: Ask provider to hold funds, via: order-authorizes}
      - {id: declined, node: release-stock, kind: compensation, after: authorize, label: "Declined: release reservation"}
      - {id: confirm, node: event-bus, label: Order confirmed and announced}
```

### Nodes

| field | meaning |
| --- | --- |
| `id` (required) | Stable identifier, unique across nodes, relationships and flows. Keep it when the label changes. |
| `label` | Short display name a reader understands on its own; see "Naming boxes". |
| `kind` | `system`, `domain`, `capability`, `responsibility`, `component`, `contract`, `rule`, `resource`, `data`, `external`, `actor`, `test`, `area`. Pick what helps a reader; `area` is a neutral grouping. |
| `parent` | Id of the containing node. Omit for top level. Containment must be a tree. |
| `summary` | One or two sentences: what it does or means (not how the code is laid out). |
| `details` | Optional extra explanation (a short paragraph). |
| `contract` | `inputs`, `outputs`, `guarantees`, `errors`, `assumptions` (lists of short text). |
| `evidence` | `observed` (you read the code and it does this), `inferred` (interpretation), `needs_review`, `unknown` (known gap), `planned` (not implemented). |
| `confidence` | Optional `low`/`medium`/`high`; an annotation, not a probability. |
| `refs` | Source evidence (see below). |
| `owns` | Top-level nodes only: files that belong to this area: paths, globs, folders (`dir/`) or `path#Symbol` (see "Ownership"). |
| `tags` | Free labels for filtering, e.g. `infrastructure`, `test`, `legacy`. |
| `notes` | Caveats, open questions. |

Prefix custom fields with `x-` (they are ignored, not errors).

### Choosing the hierarchy

- Top level: areas of **purpose** a newcomer would recognize (named as in "Naming boxes"; e.g. "Purchasing",
  "Simulation setup", "Plugin loading"), plus important external systems and actors.
  4–9 top-level nodes reads best; group further if there are more.
- Below that: responsibilities, contracts, rules, resources. Depth is free: a small
  area can go straight to evidence; a large one can nest several levels.
- Each level should be readable on its own. A node with more than ~12 children
  usually wants an intermediate grouping.
- **Containment is placement, not the only truth.** When one implementation serves
  several concepts, or a concern cuts across areas, place it once and connect it with
  relationships (data, or dependencies such as `implements`, `relies_on`). Do not
  duplicate nodes.
- Do not mirror the directory tree. If the code's organization *is* meaningful (e.g.
  a plugin boundary), say why in the summary.
- Size: a useful first map of a medium repository has roughly 25–60 nodes and 1–3
  flows. Grow it where readers need answers, not for completeness.
- External systems and libraries (`external`): model the ones whose behavior matters
  to understanding (payment provider, HTTP library, database). Give them refs only to
  *this repository's* code that touches them (the adapter, the configuration, the
  dependency declaration with `role: config`); never to vendored or installed code.

### Naming boxes (`label`)

Labels are read on boxes, in search results and breadcrumbs, and cut to their first
words when zoomed out. A good label lets someone who sees **only the label** guess what
is inside. Choose the concepts first (what is affected, what happens), then the words,
then the shortest phrase that keeps both.

Form by kind, so siblings read as a list:

| kind | form | good | weak |
| --- | --- | --- | --- |
| capability, domain, area, system | noun phrase for what a user or the system gets, in plain words | `Lending`, `Catalogue search`, `Zoomable map` | `Navigate meaning`, `Manage knowledge` |
| responsibility | verb + specific object (imperative), a strong verb | `Renew loans`, `Trace change impact`, `Reanalyze on file changes` | `Analysis`, `Handle loans`, `Review candidates` |
| rule | the rule itself, as a short name of the constraint | `Max 5 loans per member`, `Whole-order returns only` | `Loan limits`, `Partial returns` (feature or ban?) |
| data, resource | what it holds | `Member records`, `Stock levels` | `Store`, `Data` |
| component, contract, external, actor, test | the thing as people call it, with its role if the name alone is generic | `Card reader interface`, `Syntax highlighter`, `Librarian` | `Shell`, `Helpers`, `Engine` |

- **Specific, never a lone generic noun.** `Analysis`, `Layout`, `Cache`, `Lookup`,
  `Helpers`, `Core`, `Utilities` say nothing on their own: say of what or for what
  (`Fine calculation`, `DOM and math helpers`, `Code excerpt loader`).
- **The reader's words.** What users and maintainers call it; no internal metaphors,
  slang, abbreviations or code names (unless the box is that public thing).
- **Readable alone, without repeating the parent.** `Other languages` means nothing in a
  search result (`Match non-Python definitions` does); inside `Payments`, `Refund payment`
  is fine but `Payments refund processing` is not needed.
- **Front-load the distinguishing word**; **no adverbs or qualities** (`safely`, `fast`:
  put them in the summary); **not a button text** (`Copy for agent` names a click, not a
  part).
- **Short:** 1–4 words, about 30 characters; top level 1–3 words. A label that needs more
  usually describes *how* (move that to the summary) or covers two nodes.
- **Check the list.** Read all labels as a flat list: can two be confused (`Map
  validation` / `Check map structure` is fine, `Map validation` / `Check map consistency`
  was read as "checks against the code")? Can one be read as its opposite? Rename until not.

### Relationships

`from` and `to` are node ids, and the arrow always points from `from` to `to`. What that
means depends on the relationship's `type`:

| type | `from` → `to` | `label` |
| --- | --- | --- |
| `data` | where the data comes from → where it goes | **the data itself**: `order total`, `order.confirmed`, `map YAML` |
| `control` | who starts it → what is started | optional; say how (`on decline`, `every 1.5 s`) |
| `dependency` | what depends → what it relies on | optional |

**Prefer data.** Most of a system can be understood by following its data, so if
something moves, model the movement:

- A call that sends something and gets a result back is two data relationships, one per
  direction, each labelled with what it carries (`place-order → pricing: cart lines`,
  `pricing → place-order: order total`). Model only the directions a reader needs.
- Reading is data flowing *out of* the store: `inventory-store → reserve-stock:
  available stock`. Writing is data flowing in. Events go from the publisher to the
  bus and from the bus to each subscriber (`kind: event`).
- Use `control` only where no data movement says what happens: a scheduler starting a
  job, a user starting a checkout, change detection triggering re-analysis. When both
  matter in a complicated case, give both a control and a data relationship.
- Use `dependency` for what a reader needs to judge changes but that does not move data:
  `implements` an interface, `enforces` a rule, `relies_on` a guarantee, `configures`,
  `tests`, `documents`.

Optional `kind` adds detail: `event` for data; `calls`, `requests`, `triggers`,
`delegates`, `renders`, `schedules` for control; `relies_on`, `depends_on`, `implements`,
`enforces`, `validates`, `configures`, `tests`, `documents` for dependency. Model the
relationships a reader needs to reason about the system; do not enumerate every call.
`id` is optional (default `from--type--to`, or `from--kind--to` with a kind) but
recommended so flows can reference it.

Maps written for `schema_version: 1` used verbs (`A reads B`) and still open: they are
converted (data relationships are turned around where needed) with a warning. Rewrite
them in this form when you next edit the map.

### Flows

A flow is an important use case traced across nodes. Steps run in order; a step with
`after: <step id>` branches from an earlier step. `kind` is `main` (default),
`alternative`, `failure`, or `compensation`. Include at least one failure path for
the main flow when the code has one.

The viewer draws a flow only on relationships that exist in the map: each step
highlights the relationship it uses. Set `via` to that relationship's id. It must touch
the step's node (or a node containing it), in either direction, and continue from where
the flow is: the previous step's node, or the other end of the relationship the previous
step used. So when a coordinator calls one service after another, each step's `via` is
the coordinator's call. Do not jump over nodes the work passes through (map file →
parser → checker needs a step on the parser); only a relationship with an `actor` may
start something anew. Without `via`, a relationship from where the flow is to this step's
node is used if there is one. If the code makes a move that no relationship describes,
add the relationship; do not leave the step unconnected.

- Without `after`, a step follows the most recent `main` or `alternative` step.
  `failure` and `compensation` steps do not advance the main path, so the next main
  step continues from where the failure branched off. Give `after` explicitly on
  every branch to make intent obvious.
- Point a step at the node where the thing *happens*: a failure that is detected in
  validation points at the validating node; a failure that surfaces as an exception to
  the caller points at the node that raises it (or a contract/error-model node that
  describes the error).

### Source references (`refs`)

```yaml
refs:
  - path: src/payments/service.py      # repository-relative, forward slashes
    symbol: PaymentService.authorize   # recommended
    lines: 40-62                       # optional hint
    role: implementation               # implementation|test|config|docs|schema|example|entrypoint
    note: why this is evidence         # optional
  - "README.md#Returns"                # shorthand: path#symbol (Markdown: a heading)
  - "config/app.toml:10-20"            # shorthand: path:lines
```

How repoluma resolves them (`repoluma validate` reports the result of each):

- **Python** (`.py`): `symbol` is the qualified name (`Class.method`, `function`,
  `MODULE_CONSTANT`), located with Python's syntax tree. Exact match only.
  `@overload` stubs are ignored when an implementation exists. Conditionally defined
  duplicates (e.g. under `if`/`try`) are ambiguous: add a `lines` hint.
- **Other languages**: `symbol` is found with definition-like text patterns
  (`function name`, `class Name`, `fn name`, `func (r T) Name`, `name(...) {`).
  Use `Outer.inner` to search inside a container. This is approximate: the viewer
  marks it as such. If it is ambiguous, add a `lines` hint that overlaps the right one.
- **Markdown**: `symbol` is a heading text; **TOML**: a table name.
- `lines` alone works but is fragile; prefer a symbol when one exists.
- A whole file: give only `path` (keep this for small files).
- If `lines` and `symbol` disagree, the symbol wins and the ref is reported as moved:
  update or drop the hint.

Never copy source code into the map. Never reference secrets (`.env`, keys); they are
excluded and reported as errors.

### Ownership (`owns`)

`refs` say *which code supports a claim*; many nodes may cite the same file. `owns` says
*which area a file belongs to*: every code file has **exactly one** owner, and owners are
**top-level nodes** (the areas of the overview). A reader who meets a file in an editor,
a diff or a stack trace uses this to find its area; the viewer names each area's files on
its box.

**Every line of code in an owned file must be shown by a node of its area**, through that
node's `refs`: a symbol (`path#Name`), a whole class, or the whole file (`path` alone).
Code that no node shows is an error (`unexplained-code`): the map would not say what it
does. The viewer still shows it, in a dashed "not yet explained" pane in the area, until
you fix the map. Exempt ("glue"): blank lines, comments, imports, module docstrings,
`__all__`, an `if __name__ == "__main__"` block, closing brackets, and a class's header
when one of its members is shown. Code that nothing needs is better deleted.

How to fix `unexplained-code`: cite the code from the node it belongs to (the hint names
nodes that show code nearby); a new node when it does something no node describes; a
whole class or file where its parts are not worth separate claims (a module of small
helpers can be one node "Shared helpers").

```yaml
  - id: payments                            # a top-level node
    owns: [shop/payments/]                  # a folder: everything in it...
  - id: purchasing
    owns:
      - shop/payments/checkout_client.py    # ...except what a more specific pattern claims
      - shop/orders.py
  - id: shared
    owns:
      - shop/money.py
      - shop/cli.py#serve_command          # part of a file: this symbol belongs here
```

- `owns` on an inner node is an error (`owns-not-top-level`); it is counted for its area
  until you move it there.
- Patterns: an exact path, a folder (`src/app/`), or a glob (`*` within a folder name,
  `**` across folders, `?`, `[abc]`). They are repository-relative with forward slashes.
- **The most specific pattern wins**: an exact path beats any glob; among globs, the one
  with more fixed leading folders wins, then the one with more fixed characters. Two
  equally specific claims by different areas are an error (`ownership-conflict`).
- **Which files need an owner**: code files (by extension). Tests, docs, vendored and
  generated code, and hidden tooling folders (`.github/`, ...) need none; adjust with
  `source.ownership.ignore` (no owner needed) and `source.ownership.require` (owner
  needed, e.g. `[docs/**]`). Non-code files (README, configuration) may still be owned.
- **A file used by many areas** (`utils.py`, `money.py`): give it to an area of its own
  (named for what it holds, e.g. "Money and date types" or "Text formatting helpers", with a
  child node per cohesive part that cites it)
  rather than to the first user. Its users cite it with `refs`; no relationship from
  every user is needed.
- **A file that serves several areas** (a command-line module with one function per
  feature): give the file to the area it mainly belongs to and the parts to the other
  areas with `path#Symbol`. Parts use the same symbol lookup as `refs`; a part inside
  another part (`Class.method` inside `Class`) wins over it. The file's owner keeps
  everything not given away (imports, helpers). Split only where parts clearly belong to
  another area, not to make files look small.
- Never give the same file (or part) to two areas. Many nodes citing it is fine.
- `repoluma validate` reports code files without an owner (`unowned-file`, with a
  suggested area where `refs` already point at them), patterns that match nothing, and
  conflicts. `repoluma where path:line` prints a file's area and the pane that shows
  the line. Non-code files (documents, data) may be owned too; they are shown where cited.

### Evidence honesty

- `observed` only when you read the referenced code and it does what you claim.
- Use `inferred` for interpretations (intent, business meaning), `needs_review` when
  unsure, `unknown` for gaps you know about, `planned` for intended-but-missing behavior.
- A node without `refs` that is not `planned`/`unknown` shows up as "claim without
  evidence" in the viewer. That is acceptable for high-level groupings; for leaves,
  add a ref or say why in `notes`.
- Put things you deliberately left out into `scope.not_covered`. An honest gap is
  more useful than a confident guess.

## Updating an existing map

- Keep ids stable. Rename labels freely; change ids only when the meaning changed.
- After code changes, run `repoluma validate`: moved/renamed symbols show up as
  `ref-missing-symbol` or `ref-moved` with hints. Fix references, then re-read the
  affected descriptions; a reference that still resolves does not prove the
  description is still right.
- Set `scope.reviewed_revision` (`git rev-parse HEAD`) only when you have checked the
  descriptions against that commit, including on a first map you wrote from reading
  the code. The viewer then flags nodes whose referenced files changed since. Do not
  bump it after merely fixing references.
- New, moved or renamed files: run `repoluma validate`; files without an owner and
  patterns that no longer match are reported. Give new files their place in `owns`.
- Edit only the affected regions; keep the file ordered (parents before children,
  related nodes together) so diffs stay readable.

## Diagnostics you may see

Severities: `error` (fails validation), `warning` (likely mistake; fails with
`--strict`), `info` (notes such as heuristic resolution; shown with `--verbose`).

| code | fix |
| --- | --- |
| `unknown-field` | Typo or unsupported field; follow the "did you mean" hint or prefix with `x-`. |
| `duplicate-id` | Ids are shared across nodes, relationships and flows; rename the new record. |
| `unknown-parent`, `unknown-endpoint`, `unknown-step-node` | The referenced id does not exist (see hint). |
| `containment-cycle` | `parent` links form a loop; containment must be a tree. |
| `missing-field` (type), `unknown-type` | Give `type: data`, `control` or `dependency`. |
| `data-without-label` | Set `label` to the data the relationship carries. |
| `verb-kind`, `old-schema-version` | A `schema_version: 1` verb was converted; write `type` and point data from source to destination. |
| `step-without-relationship` | A flow step has no relationship to be drawn on; set `via`, adding the relationship if the map lacks it. |
| `step-via-mismatch` | `via` names a relationship that does not touch the step's node; name the one this step uses. |
| `step-via-gap` | The step does not continue from the previous one (its node, or the other end of its relationship): add steps for the nodes in between. |
| `ref-missing-file` | Path wrong or file moved/deleted. |
| `ref-missing-symbol` | Symbol renamed/moved; use the qualified name (hint may suggest one). |
| `ref-ambiguous` | Several definitions match; add a `lines` hint. |
| `ref-moved` | `lines` hint no longer overlaps the symbol; update or drop it. |
| `ref-excluded` | File is excluded (secrets, generated, `source.exclude`). |
| `yaml-syntax` | Quote strings containing `: `, commas inside `{...}`, or starting with special characters. |
| `contract-without-terms` (info) | A `contract` node without `contract:` terms; describe inputs/outputs/guarantees/errors. |
| `unowned-file` | Code files nobody owns: add them to a node's `owns` (see the suggested owner), or to `source.ownership.ignore`. |
| `ownership-conflict` | Two nodes claim a file (or part) equally: make one pattern more specific, or drop one claim. |
| `owns-matches-nothing` | The pattern names no file: fix the path; folders need a trailing `/`. |
| `owns-not-top-level` | Move the patterns to the top-level node this node is in. |
| `unexplained-code` | Code in an owned file that no node of its area shows: cite it (symbol, class or whole file) from the node it belongs to, add a node, or delete dead code. |
| `bad-owns`, `owns-missing-symbol`, `owns-excluded` | Malformed pattern, unknown symbol, or an excluded file in `owns`. |
| `ref-heuristic`, `ref-lines` (info) | Resolved approximately; check the range in the viewer, prefer a symbol. |
