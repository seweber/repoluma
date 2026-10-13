# Authoring a repoluma map (guide for coding agents)

This guide ships with repoluma {{version}} and matches its schema (`schema_version: 3`).
Print it any time with `repoluma guide`; print the JSON Schema with `repoluma schema`.

A repoluma map is a YAML file (default `.repoluma/map.yaml`) that explains **what a
repository does** and links each claim to **the code that supports it**. People read it
in a zoomable viewer: the overview shows your top-level nodes, zooming in reveals the
nodes you nest inside them, and zooming further shows the referenced source code, read
live from the working tree. Clicking a line (or its label) shows what moves along it and
the code where it is handed over and taken in, with the text you pointed at highlighted.
Your job is the meaning; repoluma handles layout, code display and reference checking.

Three things are kept apart, and you should keep them apart too:

- **Certainty** (`certainty`): how sure *you* are of a claim. `observed` means you read the
  code and it does this; repoluma never runs or verifies anything.
- **Location**: whether a cited symbol and focus text were found exactly. A found text
  proves where to look, not that your explanation is right.
- **Freshness**: whether the cited files changed since `scope.reviewed_revision`.

## Workflow

Work from the code, and expect to revise the map as you understand more.

1. **Inventory.** Read the README, entry points, tests and configuration. List the
   in-scope code (`git ls-files`), the external boundaries (services, users, storage,
   queues) and what is out of scope (tests, generated code, tooling). Do not infer
   architecture from file names alone.
2. **Follow representative entry points.** For the most important use cases, trace the
   calls and the data: what each step receives, what it hands on, what comes back, and
   what happens on failure. Note the exact statements as you go; you will cite them.
3. **Choose the items** (the nodes that do work or hold state): operations (a function, a
   method, a cohesive set of them, or a meaningful region), resources (stores, queues,
   shared state), contracts and rules, external systems and actors. Give each its `refs`.
4. **Group them** into explanatory containers by purpose (capabilities, domains, areas).
   Revise the groups when your understanding changes; do not mirror directories.
5. **Draw the data with its evidence.** Write each data relationship together with the
   refs where the data is handed over and taken in (`role` + `focus`). Pair a request
   with what comes back (`exchange`) only where the code does that.
6. **Keep the rules, contracts and failure paths**: what an interface promises, what must
   stay true, what happens when a call fails (flows with failure and compensation steps).
7. **Validate and repair.** `repoluma validate` (`--json` for machines) until there are no
   errors; treat warnings as likely mistakes. Disclose what you could not ground.
8. **Read it as a reader would.** `repoluma serve`, then: pick a few labels and check that
   the highlighted code says what the label claims; follow a flow including its failure
   path; look at Coverage for code covered only broadly. Validation passing is not the
   end.
9. **Report** to the user: what the map covers, what it omits (also in
   `scope.not_covered`), which claims are inferred or need review, and which code is only
   covered broadly.

## The format

YAML tips: prefer block style (one field per line) for anything with prose or source
text. Quote values that contain `: `, ` #`, or start with `{`, `[`, `*`, `&`, `!`, `%`,
`@`, `` ` `` or a quote; inside inline `{...}` or `[...]` also quote values with commas.
For `focus`, single quotes are safest (`focus: 'raise Error("x")'`; a `'` inside is
written `''`). Only the first YAML syntax error is reported per validate run.

```yaml
schema_version: 3
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
    ignore: [scripts/**]       # optional: code files that need no owner
nodes:
  - id: payments               # a container: it explains, lines never end on it
    kind: capability
    label: Payments
    summary: Holds, collects and returns customer money through an external provider.
    owns: [shop/payments.py]   # top-level nodes only: files that belong to this area
  - id: authorize-payment      # an item: it does work, lines end on it
    parent: payments
    kind: contract
    label: Payment authorization
    summary: Hold funds for an order without collecting them.
    contract:
      inputs: [payment token, positive amount and currency]
      outputs: [Authorization id and amount]
      errors: [PaymentDeclined with the provider's reason]
    certainty: observed
    refs:
      - path: shop/payments.py
        symbol: PaymentService.authorize
      - path: tests/test_orders.py
        symbol: test_declined_payment_releases_reservation
        role: test
  - id: purchasing
    kind: capability
    label: Ordering
    owns: [shop/orders.py]
  - id: place-order
    parent: purchasing
    kind: responsibility
    label: Place order
    certainty: observed
    refs: [shop/orders.py#OrderService.place_order]
relationships:
  - id: order-authorizes           # data: from = where it comes from, to = where it goes
    from: place-order
    to: authorize-payment
    type: data
    label: payment token and total # the data, in the reader's words
    certainty: observed
    exchange: payment-authorization
    leg: input                     # what the caller supplies
    refs:
      - path: shop/orders.py
        symbol: OrderService.place_order
        role: transfer             # where it is handed over
        focus: self.payments.authorize(payment_token, total)
  - id: authorization-returned
    from: authorize-payment
    to: place-order
    type: data
    label: authorization
    certainty: observed
    exchange: payment-authorization
    leg: result                    # what comes back
    refs:
      - path: shop/payments.py
        symbol: PaymentService.authorize
        role: transfer
        focus: 'return Authorization(id=response["id"], amount=amount)'
      - path: shop/orders.py
        symbol: OrderService.place_order
        role: receive              # where the caller takes it in
        focus: authorization = self.payments.authorize
flows:
  - id: flow-place-order
    label: Place an order
    steps:
      - {id: authorize, node: authorize-payment, label: Ask provider to hold funds, via: order-authorizes}
      - {id: approved, node: place-order, label: The authorization comes back, via: authorization-returned}
```

### Nodes

| field | meaning |
| --- | --- |
| `id` (required) | Stable identifier, unique across nodes, relationships, exchanges and flows. Keep it when the label changes. |
| `label` | Short display name a reader understands on its own; see "Naming boxes". |
| `kind` | `system`, `domain`, `capability`, `responsibility`, `component`, `contract`, `rule`, `resource`, `data`, `external`, `actor`, `test`, `area`. Pick what helps a reader; `area` is a neutral grouping. |
| `parent` | Id of the containing node. Omit for top level. Containment must be a tree. |
| `summary` | One or two sentences: what it does or means (not how the code is laid out). |
| `details` | Optional extra explanation (a short paragraph). |
| `contract` | `inputs`, `outputs`, `guarantees`, `errors`, `assumptions` (lists of short text). |
| `certainty` | `observed` (you read the code and it does this), `inferred` (interpretation), `needs_review`, `unknown` (known gap), `planned` (not implemented). Was `evidence` in schema_version 2. |
| `confidence` | Optional `low`/`medium`/`high`; an annotation, not a probability. |
| `refs` | The code this node stands for, plus tests and docs that support it (see below). |
| `owns` | Top-level nodes only: files that belong to this area: paths, globs, folders (`dir/`) or `path#Symbol` (see "Ownership"). |
| `tags` | Free labels for filtering, e.g. `infrastructure`, `test`, `legacy`. |
| `notes` | Caveats, open questions. |

Prefix custom fields with `x-` (they are ignored, not errors).

### Containers explain, items act

A node that contains other nodes is a **container**: it explains a purpose and gives the
overview its shape. It does not send, receive or call anything, so **no relationship may
end on a container** (`group-endpoint`, an error). Lines connect the **items** that do the
work or hold the state, however deep they sit. At overview zoom a line is drawn up to the
regions that contain its ends; that is a projection, and hovering or clicking it names the
real ends.

- An item does not need to be at any particular depth. An external system, an actor or a
  store at the top level is an item; do not give it children to make it look "inner".
- If an operation is worth splitting, turn it into a container whose children are the
  sub-operations, and move its lines to them. Do not keep a line on the container "for the
  whole", and do not draw the same work twice (once whole, once in parts).
- Each item has one place in the tree; several nodes may cite the same code, but the code
  is *represented* by one node (see "Code accountability").
- A `rule` constrains; it does not send or receive data. Connect it with a dependency
  (`kind: enforces`) from the operation that enforces it (`rule-endpoint` warns otherwise).

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
- Containers explain, items act: see "Containers explain, items act" above.
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
| `data` | where the data comes from → where it goes | **the data itself**, as a reader understands it: `order total`, `order.confirmed`, `configured report request` |
| `error` | where a failure or problem is raised or found → where it is handled or read | **what it says**: `decline reason`, `out of stock (SKU)`, `validation errors with hints` |
| `control` | who starts it → what is started | optional; say how (`on decline`, `every 1.5 s`) |
| `dependency` | what depends → what it relies on | optional |

**Prefer data.** Most of a system can be understood by following its data, so if
something moves, model the movement:

- A call that sends something and gets something back is an **exchange** (below): one
  data line per direction, each labelled with what it carries.
- Reading is data flowing *out of* the store (`inventory-store → reserve-stock: available
  quantities`); writing is data flowing in. Events go from the publisher to the bus and
  from the bus to each subscriber (`kind: event`); nothing comes back, so draw nothing back.
- Information about something going wrong is its own type, `error` (drawn red and
  dashed): an exception or error value from where it is raised to where it is caught, and
  reported problems such as validation diagnostics or error reports from where they are
  found to where they are read. Use it for the error leg of an exchange and for any failure
  or problem report a reader should be able to follow; it is cited like data (`transfer`
  at the `raise` or where the report is produced, `receive` where it is handled).
- Use `control` only where no data movement says what happens: a scheduler starting a
  job, change detection triggering re-analysis.
- Use `dependency` for what a reader needs to judge changes but that does not move data:
  `implements` an interface, `enforces` a rule, `relies_on` a guarantee, `configures`,
  `tests`, `documents`.

Optional `kind` adds detail: `event` for data; `calls`, `requests`, `triggers`,
`delegates`, `renders`, `schedules` for control; `relies_on`, `depends_on`, `implements`,
`enforces`, `validates`, `configures`, `tests`, `documents` for dependency. Model the
relationships a reader needs to reason about the system; do not draw a line per call, and
do not add a control line next to every data exchange. `id` is optional (default
`from--type--to`) but give one: flows, links and pasted view links use it.

### Exchanges: a request and what comes back

Give the lines of one call the same `exchange` id and say which `leg` each is:

| `leg` | direction | meaning |
| --- | --- | --- |
| `input` | caller → callee | what the caller supplies (data, or a control call whose result matters) |
| `result` | callee → caller | what is returned synchronously |
| `response` | callee → caller | what arrives later: a callback, a resolved promise, a reply message |
| `error` | callee → caller | what comes back instead of a result: an exception, an error value (a relationship of `type: error`) |

- **Pair only what the code pairs.** Opposite lines between the same two nodes are not an
  exchange because they look like one; the validator never pairs lines by direction or
  label. A second call with a different meaning gets its own exchange id.
- **No quota.** An event, a write, a fire-and-forget command or a call whose result is
  ignored has no result leg. Never invent a return value or an acknowledgement.
- **An error leg is optional.** Draw it when the failure carries information the caller
  acts on (`decline reason`); a failure the flow already explains needs no extra line.
- A future or promise returned immediately (`result`) and the value it later resolves to
  (`response`) are different legs.
- **Errors that pass through.** Draw an error leg where the error is raised and where it
  is handled (caught, translated, cleaned up after). A layer that only lets it propagate
  gets no error leg of its own; say in its summary that errors pass through. Cite the
  `raise` as the transfer and the `except` (or the caller's handling) as the receive.
- **Resumed generators and coroutines.** Starting a generator and later resuming it with
  `send`/`throw` are separate exchanges (each has one input); the value a resumed generator
  produces is that exchange's `result`.
- An exchange has at most one `input`, all its legs connect the same two nodes, what
  comes back runs from the callee to the caller, results and responses are `type: data` and
  error legs `type: error` (`exchange-incoherent` otherwise). Exchange
  ids share the namespace of the other ids. The exchange itself is not drawn; its legs are.

### Where data moves: cited sites

A data line's `refs` say where in the code the movement happens. Give each such ref a
`role` and a `focus`:

| `role` | what to cite |
| --- | --- |
| `transfer` | where the data is handed over: the call with its arguments, the `return`, the `publish`, the write |
| `receive` | where the other end takes it in: the parameter, the assignment of a call's result, the handler, the read |
| `origin` | where it is made: the assignment, computation, setter calls or upstream result it comes from |

`focus` is **text copied exactly from the code** inside the ref's symbol (or `lines`, or
the whole file), one string or a list. Each must occur **exactly once** in that range;
whitespace is normalized, so a statement may span lines. Choose the shortest text that is
unique and says what you mean (`self.payments.authorize(payment_token, total)`, not just
`authorize(`). The viewer highlights it inside the surrounding code.

- In schema_version 3, an `observed` data or error line needs at least one `transfer` or `receive`
  ref (`observed-without-site`, a warning), and a site ref needs `focus`
  (`site-without-focus`). If the site is not in this repository (an external service
  builds the response, an HTTP handler is missing), cite what *is* here, say what is
  missing in `notes`, and use `inferred` or `needs_review`. Do not cite a whole function
  as if it were the site.
- A focus that is not found, or found more than once, is an error (`focus-missing`,
  `focus-ambiguous`); it is never resolved to the first match or a similar text. When the
  text exists elsewhere in the file, the message says where; check whether the code moved
  or your symbol is wrong, and re-read the claim, not just the text.
- A found text proves location, not data flow. You still have to have read the code.
- The receiving side of a result (`authorization = self.payments.authorize`) does not show
  how the callee built it; cite the callee's `return` as the result's `transfer`.
- The label names the data as a reader understands it; the focus names the actual
  variables and expressions.
- A write into a store needs no `receive`: the assignment (`self.reserved[sku] = ...`) is
  the `transfer`, and the store is the line's target. A read from a store is cited where
  the value is taken (`receive`), plus the store's accessor as `origin` if it computes
  something.
- If only one end is in this repository (a response built by an external service), cite
  that end. `observed` is right when you read that code and it shows the data arriving;
  say in `notes` that the other end is outside.
- `focus` works on any ref and is checked everywhere, but the viewer highlights it only in
  a line's evidence; on node refs prefer a precise `symbol`.

### Worked examples

The first examples are from the bundled shop (`repoluma example`); the others from a
small fixture in repoluma's tests (`tests/fixtures/provenance`), because the shop has no
such code. Both maps validate without warnings.

**A call and its result** (shop): see `order-authorizes` and `authorization-returned`
above. The input cites the call; the result cites the callee's `return` and the caller's
assignment. They share the call line, which is fine: evidence may overlap.

**A failure and its compensation** (shop). The decline is an error leg of the same
exchange; releasing the stock is a separate one-way line, and the flow puts them in order:

```yaml
# from: src/repoluma/examples/shop
relationships:
  - id: payment-declined
    from: authorize-payment
    to: place-order
    type: error
    label: decline reason
    certainty: observed
    exchange: payment-authorization
    leg: error
    summary: Raised instead of a result when the provider does not approve; no authorization exists on this path.
    refs:
      - path: shop/payments.py
        symbol: PaymentService.authorize
        role: transfer
        focus: 'raise PaymentDeclined(response.get("reason", "declined"))'
      - path: shop/orders.py
        symbol: OrderService.place_order
        role: receive
        focus: except PaymentDeclined as exc
  - id: order-releases
    from: place-order
    to: release-stock
    type: data
    label: reservation (on decline)
    certainty: observed
    summary: Only on the declined path, so that nothing stays held; the caller then gets a CheckoutError.
    refs:
      - path: shop/orders.py
        symbol: OrderService.place_order
        role: transfer
        focus: self.inventory.release(reservation)
      - path: shop/inventory.py
        symbol: Inventory.release
        role: receive
        focus: 'def release(self, reservation: Reservation) -> None:'
```

**A one-way event** (shop): `order-publishes` cites the `publish` call; the bus hands the
payload to each subscriber (`emails-subscribe`). Nothing is drawn back to the publisher.

**A configured object** (fixture). Several plain setters configure a request that is then
handed to a renderer. Do not draw a line per setter: draw one transfer of the configured
object, cite each setting as an `origin`, and say what is only conditional. Passing an
object passes a reference; say so if anything could change it afterwards:

```yaml
# from: tests/fixtures/provenance
relationships:
  - id: request-to-render
    from: build-report
    to: render-report
    type: data
    label: configured report request
    summary: Title, period, columns (refunds only for detailed reports) and currency, all set before the call.
    certainty: observed
    exchange: render-call
    leg: input
    notes: The request object is passed by reference; nothing changes it after render is called.
    refs:
      - path: reports/service.py
        symbol: monthly_report
        role: origin
        focus:
          - 'request = ReportRequest().set_title(f"Monthly report {month.name}")'
          - request.set_period(month.first, month.last)
          - request.add_column("revenue")
          - 'if detailed: request.add_column("refunds")'
      - path: reports/service.py
        symbol: monthly_report
        role: transfer
        focus: text = render(request)
      - path: reports/render.py
        symbol: render
        role: receive
        focus:
          - 'header = f"{request.title} ({request.currency})"'
          - 'f"{request.period[0]} to {request.period[1]}" if request.period'
          - '", ".join(request.columns)'
```

**A setter that is more than configuration** (fixture). `set_currency` rejects unknown
codes and writes to an audit log. Summarizing it inside "configured report request" would
hide both, so it is an operation of its own, with an input, an error leg and a write:

```yaml
# from: tests/fixtures/provenance
relationships:
  - id: currency-requested
    from: build-report
    to: change-currency
    type: data
    label: user's currency code
    certainty: observed
    exchange: currency-change
    leg: input
    refs:
      - path: reports/service.py
        symbol: monthly_report
        role: transfer
        focus: request.set_currency(user.currency, audit)
  - id: currency-rejected
    from: change-currency
    to: build-report
    type: error
    label: unsupported currency
    certainty: observed
    exchange: currency-change
    leg: error
    summary: Raised before anything is recorded or changed; monthly_report does not catch it.
    refs:
      - path: reports/request.py
        symbol: ReportRequest.set_currency
        role: transfer
        focus: 'raise ValueError(f"unsupported currency {code}")'
  - id: currency-audited
    from: change-currency
    to: audit-log
    type: data
    label: currency change
    certainty: observed
    refs:
      - path: reports/request.py
        symbol: ReportRequest.set_currency
        role: transfer
        focus: audit.record("currency", code)
      - path: reports/audit.py
        symbol: AuditLog.record
        role: receive
        focus: self.entries.append((field, value))
```

**Shared mutable state** (fixture). A module-level dictionary is a `resource`; writing
and reading it are two one-way lines, and what a reader gets depends on what was written
last. Say that; do not draw a line from the writer to the reader:

```yaml
# from: tests/fixtures/provenance
relationships:
  - id: report-remembered
    from: build-report
    to: recent-reports
    type: data
    label: report text per user
    certainty: observed
    refs:
      - path: reports/service.py
        symbol: monthly_report
        role: transfer
        focus: RECENT[user.name] = text
  - id: last-report-read
    from: recent-reports
    to: read-last-report
    type: data
    label: last report text
    certainty: observed
    summary: Whatever monthly_report stored last for the user; None before the first report.
    refs:
      - path: reports/service.py
        symbol: last_report
        role: receive
        focus: return RECENT.get(user.name)
```

**Something the repository does not contain** (shop). The checkout form posts to an HTTP
endpoint that is not in the repository. The line stays, with what is here as evidence and
the gap stated; it is not upgraded to `observed`:

```yaml
# from: src/repoluma/examples/shop
relationships:
  - id: form-submits-order
    from: checkout-form
    to: place-order
    type: data
    label: order request (POST /api/orders)
    certainty: needs_review
    exchange: checkout-request
    leg: input
    notes: The HTTP handler between the form and place_order is not in this repository, so it is not known how the request becomes the arguments of place_order.
    refs:
      - path: web/checkout.ts
        symbol: submitCheckout
        role: transfer
        focus: 'body: JSON.stringify({ lines, paymentToken })'
```

**Evidence that moved or became ambiguous.** After an edit, `validate` may report
`focus-missing` ("not found in the ref's range; the text occurs elsewhere in the file
(line 88)") or `focus-ambiguous` ("occurs 2 times"). Re-read the code: if the call moved
into another function, cite that function (and check that the claim still holds there); if
the text now occurs twice, lengthen the focus; if the behavior changed, change the claim.
Never "fix" a focus by pointing it at whatever text happens to match.

**Mutable values in general.** Cite the state as it is when the data moves. Later
mutation, aliases, callbacks and conditional branches can mean the receiver sees something
else: say so in `summary` or `notes`, lower `certainty` where you cannot tell, and never
label a value that only exists on one branch as if it always did.

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
- A step can use any leg of an exchange: the input for "ask", the result for "comes back",
  the error leg for a failure step. Give each failure step its own line where there is one.
- A flow may visit the same node again with another `via` (a coordinator calling one
  service after another); each step names the line it uses.
- A side effect between two calls (a write to an audit log while configuring) is a
  branch: give it `after:` the step it belongs to, and give the next main step `after:`
  the same step, so the main path does not appear to continue from the side effect.
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
                                       # (on relationships: transfer|receive|origin)
    focus: self.provider.authorize(    # optional: exact text inside the range (see above)
    note: why this is evidence         # optional
  - "README.md#Returns"                # shorthand: path#symbol (Markdown: a heading)
  - "config/app.toml:10-20"            # shorthand: path:lines
```

How repoluma resolves them (`repoluma validate` reports the result of each):

- **Python** (`.py`): `symbol` is the qualified name (`Class.method`, `function`,
  `MODULE_CONSTANT`, a nested function as `outer.inner`, a class attribute as
  `Class.field`), located with Python's syntax tree. Exact match only.
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
you fix the map. Exempt ("glue"): blank lines, comments, imports (also inside a module-level
`if TYPE_CHECKING:`), module docstrings, `__all__`, an `if __name__ == "__main__"` block,
closing brackets, and a class's header when one of its members is shown. Code that nothing needs is better deleted.

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

### Certainty honesty

- `observed` only when you read the referenced code and it does what you claim; for data
  lines, also cite where it happens.
- Use `inferred` for interpretations (intent, business meaning), `needs_review` when
  unsure, `unknown` for gaps you know about, `planned` for intended-but-missing behavior.
- A leaf without `refs` that is not `planned`/`unknown` shows up as "claim without
  evidence" in the viewer. Acceptable for containers; for items, add a ref or say why in
  `notes`.
- External systems and actors are outside the repository: cite only this repository's code
  that touches them, and never invent their implementation. That code (the adapter, the
  interface class) is *represented* by an item in the area that owns its file (say,
  "Payment provider interface" inside Payments); the external node's refs to it are only
  evidence. Draw the line between the two.
- Dependencies need no `certainty` unless it is in doubt; when you state one, the same
  meanings apply.
- Put things you deliberately left out into `scope.not_covered`. An honest gap is more
  useful than a confident guess.

## Code accountability

Three different things, which you should not mix up:

1. **Placement** (`owns`): which area a file belongs to. One owner per file.
2. **Representation** (an item's `refs` to code of its own area): which node stands for a
   piece of code. Every line of code of an owned file must be represented by a node of its
   area (`unexplained-code` otherwise).
3. **Evidence** (refs on relationships, refs with `role: test` or `docs`, citations of code
   that another area owns): support for a claim. Many-to-many; it never accounts for code.

Complete accountability does not need a box per statement or a line per call. A node may
stand for a cohesive class or module with one ref (a record or dataclass is cited as its
class; its fields are not counted as definitions), but that is **broad coverage**: the
viewer and `validate --verbose` list the definitions it covers without saying anything
about them (`coarse-mapping`), and when `scope.reviewed_revision` is set, definitions added
since then inside a broad ref are a warning (`coarse-new-members`), so a whole-file ref
cannot silently make new behavior look reviewed. Give a definition its own ref (or node)
when it does something a reader should know: a separate effect (I/O, an event, a write
to shared state), a check that can fail, or anything another line's evidence points into.
A class whose methods all serve the one purpose its node's summary states is fine as one
ref. A node whose summary says "check map
structure" should not cite the whole module as its implementation; put the module-wide ref
on a node that is honestly about the whole module.

Only Python files have a definition inventory; for other languages repoluma checks lines
only and says so. None of these counts says that a description is right.

## Updating an existing map

- Keep ids stable. Rename labels freely; change ids only when the meaning changed. Flows,
  pasted links and other records refer to ids; a renamed id shows "no longer in the map"
  for old links.
- After code changes, run `repoluma validate`: moved symbols show up as
  `ref-missing-symbol` or `ref-moved`, moved statements as `focus-missing`. Fix the
  references, then re-read the affected descriptions: a reference that still resolves does
  not prove the description is still right.
- **New code makes an old map incomplete** even when every old reference resolves: new
  files without an owner, new code that no node shows, and new definitions inside broad
  refs are reported. Describe them.
- Set `scope.reviewed_revision` (`git rev-parse HEAD`) only when you have checked the
  descriptions against that commit. The viewer then flags what changed since, and new
  definitions inside broad refs become warnings. Do not bump it after merely fixing
  references.
- Edit only the affected regions; keep the file ordered (parents before children,
  related nodes together) so diffs stay readable.

## Migrating from schema_version 2

Version 2 maps still open unchanged (lines may end on containers, `evidence` means
certainty, data lines need no cited site). To move a map to version 3:

1. `repoluma migrate` writes `<map>.v3.yaml` next to the map (`--in-place` overwrites the
   map; nothing else is changed). It sets `schema_version: 3` and renames `evidence:` to
   `certainty:`, keeping comments and order, and checks that nothing else changed.
2. It then lists the decisions it cannot make, because they need the code:
   - lines that end on a container: find the item inside that does the work and move the
     end there; draw several lines only if several items really take part;
   - observed data lines without a cited site: add `transfer` / `receive` refs with
     `focus`, or lower `certainty` and say why;
   - opposite data lines between the same nodes: give them one `exchange` only if one is
     what the call returns for the other.
3. Keep every id, label, summary, contract, rule, flow, uncertainty and note that is still
   true; add, do not regenerate. If an id must change, update the flows that use it.
4. Broad refs (whole files, whole classes) that were fine before are now listed as broad
   coverage. Do not split them blindly; split them where a reader needs to know what a part
   does, and leave a broad ref on a node that is honestly about the whole.
5. `repoluma validate`, then look at the result in the viewer.

## Before you finish

- [ ] `repoluma validate` reports no errors; every warning is fixed or explained to the user.
- [ ] Every line ends on an item, never on a container.
- [ ] Every observed data line cites where it happens, with focus text that says what the label claims.
- [ ] Exchanges pair only what the code pairs; nothing is invented to complete a pair.
- [ ] Failure paths that matter have a flow step (and an error leg where the caller gets information).
- [ ] Gaps (missing code, external systems, unclear behavior) are in `notes`, `certainty` and `scope.not_covered`.
- [ ] You looked at the map: a few labels clicked, a flow followed, broad coverage reviewed.

## Repair loop

Run `repoluma validate --json`, take the first error, fix its cause (the `hint` says how),
re-run. Fix structure first (YAML, ids, parents, endpoints), then references and focus
text, then ownership and coverage, then warnings. When a fix needs knowledge you do not
have, lower the certainty and say so instead of guessing.

## Diagnostics you may see

Severities: `error` (fails validation), `warning` (likely mistake; fails with
`--strict`), `info` (notes such as heuristic resolution; shown with `--verbose`).

| code | fix |
| --- | --- |
| `unknown-field` | Typo or unsupported field; follow the "did you mean" hint or prefix with `x-`. |
| `renamed-field` | `evidence` is called `certainty` in schema_version 3. |
| `duplicate-id` | Ids are shared across nodes, relationships, exchanges and flows; rename the new record. |
| `unknown-parent`, `unknown-endpoint`, `unknown-step-node` | The referenced id does not exist (see hint). |
| `containment-cycle` | `parent` links form a loop; containment must be a tree. |
| `group-endpoint` | A line ends on a container; end it on the item inside that does the work. (`legacy-group-endpoint`: the same in a version 2 map, shown as written.) |
| `rule-endpoint` | Data, error or control to or from a rule; use a dependency (`enforces`) instead. |
| `missing-field` (type), `unknown-type` | Give `type: data`, `control` or `dependency`. |
| `data-without-label` | Set `label` to the data (or the error) the relationship carries. |
| `exchange-incoherent` | An exchange's legs disagree: one input, same two nodes, what comes back runs callee → caller, results are `data` and error legs `error`, legs say which they are. |
| `unknown-leg`, `leg-without-exchange` | `leg` is `input`, `result`, `response` or `error`, and needs an `exchange`. |
| `single-leg-exchange` (info) | Fine for a call whose result does not matter. |
| `observed-without-site` | Cite where the data is handed over or taken in (`role: transfer`/`receive` with `focus`), or lower `certainty`. |
| `site-without-focus` | Add `focus` with the exact text of the statement. |
| `focus-missing` | The text is not in the ref's range (the message says if it is elsewhere in the file); re-read the code and the claim. |
| `focus-ambiguous` | The text occurs several times; lengthen it or narrow the ref. |
| `bad-focus`, `too-many-focus` | `focus` is text or a list of text; split long lists into several refs. |
| `verb-kind`, `old-schema-version` | An older map was read with conversions; see "Migrating from schema_version 2". |
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
| `coarse-mapping` (info) | Definitions covered only by a broad ref; fine for cohesive code, otherwise give them refs. |
| `coarse-new-members` | Definitions added since the reviewed revision are covered only by a broad ref: read and describe them. |
| `bad-owns`, `owns-missing-symbol`, `owns-excluded` | Malformed pattern, unknown symbol, or an excluded file in `owns`. |
| `ref-heuristic`, `ref-lines` (info) | Resolved approximately; check the range in the viewer, prefer a symbol. |
