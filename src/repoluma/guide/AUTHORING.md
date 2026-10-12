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
5. **Validate and repair**: run `repoluma validate` and fix every error. Use
   `repoluma validate --json` for machine-readable diagnostics. Treat warnings as
   likely mistakes (typos in field names, unknown kinds, outdated line hints).
6. **Look at it** if you can: `repoluma serve` prints a local URL.
7. **Report** to the user: what the map covers, what it omits (also record that in
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
nodes:
  - id: payments               # stable id: lowercase, digits, . _ - ; never reuse for something else
    kind: capability
    label: Payments
    summary: Holds, collects and returns customer money through an external provider.
  - id: authorize-payment
    parent: payments           # containment: where it lives on the map
    kind: contract
    label: Authorize payment
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
| `label` | Short display name in the system's own language. |
| `kind` | `system`, `domain`, `capability`, `responsibility`, `component`, `contract`, `rule`, `resource`, `data`, `external`, `actor`, `test`, `area`. Pick what helps a reader; `area` is a neutral grouping. |
| `parent` | Id of the containing node. Omit for top level. Containment must be a tree. |
| `summary` | One or two sentences: what it does or means (not how the code is laid out). |
| `details` | Optional extra explanation (a short paragraph). |
| `contract` | `inputs`, `outputs`, `guarantees`, `errors`, `assumptions` (lists of short text). |
| `evidence` | `observed` (you read the code and it does this), `inferred` (interpretation), `needs_review`, `unknown` (known gap), `planned` (not implemented). |
| `confidence` | Optional `low`/`medium`/`high`; an annotation, not a probability. |
| `refs` | Source evidence (see below). |
| `tags` | Free labels for filtering, e.g. `infrastructure`, `test`, `legacy`. |
| `notes` | Caveats, open questions. |

Prefix custom fields with `x-` (they are ignored, not errors).

### Choosing the hierarchy

- Top level: areas of **purpose** a newcomer would recognize (e.g. "Purchasing",
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
the step's node (or a node containing it), in either direction, and need not start at
the previous step: when a coordinator calls one service after another, each step's
`via` is the coordinator's call. Without `via`, a relationship between the previous
step's node and this one is used if there is one. If the code makes a move that no
relationship describes, add the relationship; do not leave the step unconnected.

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
| `ref-missing-file` | Path wrong or file moved/deleted. |
| `ref-missing-symbol` | Symbol renamed/moved; use the qualified name (hint may suggest one). |
| `ref-ambiguous` | Several definitions match; add a `lines` hint. |
| `ref-moved` | `lines` hint no longer overlaps the symbol; update or drop it. |
| `ref-excluded` | File is excluded (secrets, generated, `source.exclude`). |
| `yaml-syntax` | Quote strings containing `: `, commas inside `{...}`, or starting with special characters. |
| `contract-without-terms` (info) | A `contract` node without `contract:` terms; describe inputs/outputs/guarantees/errors. |
| `ref-heuristic`, `ref-lines` (info) | Resolved approximately; check the range in the viewer, prefer a symbol. |
