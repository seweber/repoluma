# Repoluma: code-grounded diagrams and inspectable data exchanges

Implementation handoff · 13 October 2026

## 1. Mandate and decision boundaries

Improve Repoluma so a reader can understand what a repository does, follow meaningful data exchanges between concrete operations, and inspect the source supporting each exchange without losing their place in the diagram. Rework the map format and the agent authoring guide/skill together with the viewer and validation needed to support them.

The owner is open to a large rewrite but wants improvements without losing existing useful behavior. A rewrite is a means, not a deliverable. Prefer retaining components that already meet the requirements. Do not interpret this brief as permission to merge, publish, or deploy automatically; follow the implementation session's authorization and repository instructions.

This brief was produced without changing project code. It combines explicit user requirements, findings from a source review, and proposed designs. These have different authority:

- **Required:** code-grounded inner boxes; accountable coverage; meaningful input/result pairs where appropriate; clickable data labels opening contextual source evidence in the right panel; explanatory outer/intermediate groups; concrete underlying connection endpoints; improved map authoring instructions; preservation of existing useful capabilities.
- **Recommended:** the conceptual model, candidate schema, reference-resolution approach, and implementation sequence below. Improve these if experiments expose a simpler or safer design that meets the requirements.
- **Investigate:** paired routing geometry, source selection anchors, coverage granularity, and acceptable performance/readability tradeoffs. Do not represent these as settled conclusions.

No design can guarantee that every user task becomes strictly better. Operationalize “strict improvements” as preserved baseline tasks and capabilities, demonstrated gains on target tasks, and no material regressions on the agreed baseline. Explaining a regression does not make it acceptable: fix it, choose another design, or present concrete alternatives for an owner decision before treating the change as complete. If a required goal conflicts with another, expose the tradeoff with evidence rather than silently dropping a goal or declaring success.

## 2. Current project baseline

Review baseline: `seweber/repoluma`, commit `b5da6014a103f94edbdcd158871f31e84a96905b`. Inspect the actual checkout and its instructions before implementation; it may have advanced. This review read source and tests but did not run the UI or tests.

Important existing behavior:

- A coding agent authors `.repoluma/map.yaml`; the application does not embed an LLM. The installed `repoluma guide` supplies the authoring instructions, and the guide can be exported as a skill.
- Schema version 2 already favors data relationships labeled with the data. The guide recommends separate input and result arrows. Control and dependency relationships remain available.
- Arbitrary parent nesting, semantic zoom, in-map source panes, file ownership, file-to-map navigation, flow steps/failures, and evidence uncertainty already exist.
- Relationships accept source references. The relationship inspector lists them; selecting a reference navigates to source. This is not yet the requested focused provenance panel.
- Source references resolve Python symbols through the AST and other supported definitions through disclosed text heuristics. `symbol` plus `lines` uses lines as a location/disambiguation hint, not as an independent highlight inside a function.
- Coverage checks require relevant non-glue code to appear in references within its owning area. Whole-class and whole-file references count. This checks displayed coverage, not whether every behavior has been meaningfully explained.
- The relationship validator checks that endpoints exist but does not distinguish explanatory containers from concrete endpoints.
- The renderer already handles nested routes, zoom-dependent overview representation, relationship selection, and bundles. Label placement and line hit testing exist, but explicit accessible label targets and focused evidence need work.

Concrete examples motivating the change:

1. In the self-map, “Check map structure” cites both `validate_document` and all of `model.py`. A broad citation can cover code beyond the named responsibility.
2. The shop map already has “payment token and total” and “authorization” arrows, but those relationship records have no focused source references.
3. In `OrderService.place_order`, the input edge could show the `payment_token` parameter, `total = self.price(lines)`, and `self.payments.authorize(payment_token, total)`. The returned authorization can be inspected separately while remaining part of the same exchange.

Preserve the current strengths: purpose-oriented explanations, source honesty, useful rule/contract descriptions, failure flows, code navigation, fixed geometry during zoom, readable overview, and local operation without executing the inspected repository.

## 3. Product behavior and invariants

### 3.1 Concrete inner entities and explanatory groups

Separate the meaning of an explanatory group from the meaning of a concrete endpoint. Separate top-level YAML collections are recommended; a well-validated discriminated record type is also acceptable.

- Groups explain purpose. They may contain groups and concrete entities, but do not execute work, produce data, or consume it.
- Concrete operations represent functions, methods, meaningful implementation regions, or cohesive sets of such units. Their display labels explain behavior in familiar language; source identifiers remain available in the inspector.
- Concrete state/resource entities represent meaningful storage, queues, event buses, or mutable state. They cite declarations and/or repository integration points. Do not pretend every resource is a function.
- External entities and actors are explicitly outside local implementation coverage. Cite local integration points when available; never invent external implementation evidence.
- Rules and contracts remain expressible. Prefer attaching them to operations/exchanges; preserve separately inspectable rule entities where they help explain dependencies or existing flows. Such a rule is a claim or constraint, not a data producer.
- All authored runtime connections target concrete entities, never explanatory groups. Dependency links may also target explicitly modeled constraints/contracts. No connection targets a group.
- Each entity has one canonical placement in the containment tree. Multiple evidence citations do not create duplicate entities. Do not require every entity to have the same nesting depth.

“Inner” means a concrete endpoint in this model, not “a particular geometric depth.” Do not make external actors or a standalone resource acquire artificial children just to satisfy a leaf rule. If a concrete operation is decomposed, use an explanatory group plus explicit concrete suboperations, or keep a coarse operation with internal details. Avoid simultaneously representing the same execution twice without explaining the relationship.

At overview zoom, a route can appear to enter a group because its endpoint is inside it. This is a projection of concrete connections, not a new semantic group-to-group edge. Selecting it must identify the original endpoints. Group boundaries can carry transit markers; they should not falsely imply that the group itself receives a call.

### 3.2 Data exchanges and pairs

Prefer paired arrows for meaningful request/result exchanges:

- One direction names what is supplied.
- The reverse direction names what is returned.
- Both belong to one explicit exchange and can be inspected together.

Do not impose a pair quota. Events, pipelines, writes, and commands without meaningful results may be one-way. Do not manufacture return values or acknowledgments. Distinguish synchronous return, asynchronous response, failure, and state mutation. A future/promise return and its eventual resolved value are not automatically the same transfer.

Two opposite arrows are paired only when the author explicitly associates them with the same exchange. Same endpoints, similar labels, or adjacent source lines are insufficient evidence. Two invocations of the same method may require distinct exchange identities when context or meaning differs.

Keep control edges for triggers not adequately expressed by data movement and dependency edges for constraints and reliance. Do not add a call edge redundantly to every data exchange. An important control-only call and its meaningful result can still belong to one exchange.

### 3.3 Clickable data labels

Clicking a data label or its line selects that specific directional transfer and opens the right inspector. Keep the camera position stable. Highlight its exact endpoints and, more subtly, its paired transfer when one exists.

The inspector provides:

1. A concise data name, description, direction, and exchange context.
2. The transfer site: the call argument, return, publication, read, or write establishing this movement.
3. Relevant construction/origin snippets: parameters, assignments, setters, transformations, or upstream results.
4. Highlighted relevant statements or expressions within readable surrounding code.
5. Expandable upstream evidence and explicit navigation to the existing in-map pane or full file.
6. Claim certainty, reference-resolution status, and freshness as separate signals.

A minimal useful panel can show one grounded transfer site. Richer construction evidence can be added incrementally. Missing evidence must be explicit rather than replaced with an arbitrary whole-function excerpt presented as proof.

Labels must have reliable pointer targets, hover/focus feedback, and an accessible equivalent in the DOM inspector/outline. Preserve keyboard access, selection URLs, copy-for-agent summaries, and backward navigation. Offscreen or deliberately hidden labels must remain discoverable through the inspector or an edge list. Do not make every overview label permanently visible if that destroys readability.

## 4. Proposed conceptual model and YAML direction

Use a new schema version, expected to be version 3 if the current checkout still writes version 2. Preserve repository/scope metadata, exclusions, reviewed revision, stable identifiers, and extension conventions where practical.

Recommended concepts:

| Concept | Authored responsibility | Derived behavior |
| --- | --- | --- |
| Groups | Purpose, summary, containment | Overview geometry and projected connections |
| Entities | Concrete meaning, implementation mapping, contract/claims | Boxes, code panes, implementation coverage |
| Exchanges | Related directional transfers and their evidence | Paired lines, exchange inspection |
| Links | Control/dependency relations not otherwise represented | Corresponding edges |
| Flows | Ordered scenario steps, explicit edge references, branches | Step overlays on existing edges |

An exchange owns its directional legs. Normalize those legs into the existing/internal relationship representation if that minimizes disruption. Do not author the same edge both under `exchanges` and again under `relationships`; that creates two sources of truth. Each leg has a stable explicit ID. An exchange itself is not an extra visible edge.

Allow an exchange to contain a single leg. Normal pairs have two. A failure leg is optional, not universally required; the flow and contract can explain a failure without always drawing another line. Each leg specifies concrete `from` and `to` endpoints. Validate coherence, but do not require all kinds of exchanges to be synchronous A/B calls. More elaborate messaging correlation should remain out of the initial scope unless needed by a real fixture.

Use inline data descriptions initially. A separate global data-object registry is not required: repeated labels such as “request” do not establish shared identity. Add reusable definitions only if actual maps demonstrate enough duplication to justify the extra indirection. Keep identity of a transfer distinct from identity of a runtime object or its successive states.

### 4.1 Illustrative YAML slice — not the final schema

The following is a design sketch of part of the bundled shop, not a complete map and not valid input to today's validator. It intentionally leaves constructor/state mapping and other operations to the complete shop migration. Field names may change after the design spike; retain the semantics.

```yaml
schema_version: 3

groups:
  - id: ordering
    label: Ordering
    summary: Turn a cart into a confirmed order.
  - id: payments
    label: Payments
    summary: Hold, collect, and return customer funds.

entities:
  - id: place-order
    kind: operation
    parent: ordering
    label: Place order
    implementation:
      - path: shop/orders.py
        symbol: OrderService.place_order
    # Supporting tests/docs are separate from implementation ownership.
    evidence:
      - path: tests/test_orders.py
        symbol: test_declined_payment_releases_reservation
        role: test

  - id: authorize-payment
    kind: operation
    parent: payments
    label: Authorize payment
    implementation:
      - path: shop/payments.py
        symbol: PaymentService.authorize

exchanges:
  - id: order-payment-authorization
    kind: call
    label: Authorize the order payment
    legs:
      - id: order-authorizes
        type: data
        from: place-order
        to: authorize-payment
        role: input
        label: Payment token and total
        certainty: observed
        provenance:
          - role: transfer
            path: shop/orders.py
            symbol: OrderService.place_order
            focus:
              text: self.payments.authorize(payment_token, total)
          - role: construction
            path: shop/orders.py
            symbol: OrderService.place_order
            focus:
              text: total = self.price(lines)
          # The full map should also explain payment_token's parameter origin.

      - id: authorization-returned
        type: data
        from: authorize-payment
        to: place-order
        role: result
        label: Authorization
        certainty: observed
        provenance:
          - role: receive
            path: shop/orders.py
            symbol: OrderService.place_order
            focus:
              text: authorization = self.payments.authorize(payment_token, total)
          # Cite the callee return separately when tracing result construction.
```

The text focus above is a possible minimal anchor, not an instruction to use unrestricted substring matching as semantic proof. The input/result share a call site deliberately; evidence can legitimately overlap while implementation ownership remains unique. The final schema must distinguish authored claim certainty from lists of supporting references more clearly than today's overloaded use of “evidence.”

Preserve existing flow references to leg IDs where possible. An optional exchange reference can be useful for “inspect the whole exchange,” but a flow step that highlights one direction must identify that direction unambiguously.

## 5. Code mapping and coverage

Separate three things:

1. **Placement/ownership:** where a file or implementation unit has its canonical home.
2. **Implementation mapping:** what code a concrete entity represents.
3. **Evidence:** citations supporting claims, including code represented by other entities.

Evidence is many-to-many. Canonical implementation mapping should account for each in-scope unit once, unless an explicitly modeled decomposition explains the overlap. A group, a test citation, or a provenance excerpt cannot satisfy implementation coverage by itself.

Recommended practical approach:

- Reuse the existing file inventory, ownership rules, and source resolver where they remain appropriate.
- For Python, enumerate named definitions and relevant module/class-level executable or declarative code with AST support. Include constructors, constants, data records, and initialization behavior; do not only count public methods.
- A cohesive entity may represent several explicit units. A deliberately coarse entity may represent a cohesive module/class, but record this as coarse coverage and expose its member inventory. A whole-file supporting reference must not silently count as detailed coverage.
- Support meaningful regions inside a function when needed, but do not require agents to split every branch or statement into its own entity. Resolve region ownership and overlap deliberately.
- For other languages, retain disclosed heuristic mapping and line-range fallbacks initially. Do not claim exact semantic coverage where the parser cannot establish it.
- Show unmapped units and unresolved mappings as actionable diagnostics with a path to their place on the map.
- Distinguish “unmapped,” “mapped coarsely,” “mapped to an operation,” and “excluded by policy.” Do not equate any of them with proven semantic explanation.

Complete code accountability is the target, not every detail visible at once. Disclose excluded tests, generated code, dependencies, docs, and tooling through the inventory already available, with counts where known and explicit exclusion rules otherwise. Do not crawl excluded dependency trees merely to count them. Reuse existing defaults initially where sensible, but disclose the resulting scope; “all code” must always mean all code in the declared scope. Adding a file/function must reveal a gap unless covered by a deliberately coarse mapping whose expanded membership is visibly marked changed. Do not let a broad wildcard make new behavior silently look reviewed.

Detecting changed coarse membership needs a real baseline: use a reviewed revision or an explicitly authored inventory/checkpoint. Without one, report freshness as unknown rather than promising detection that cannot be implemented. The viewer must not write review checkpoints to the inspected repository automatically. Do not require cached full source or a duplicate symbol database inside YAML merely to support this feature.

The guide should challenge incoherent coarse boxes, but a validator cannot determine whether a description is conceptually sufficient. Avoid declaring “100% behavior explained” from range coverage. Useful reported measures include accounted units, unexplained units, coarse mappings, and evidence completeness for displayed data transfers.

Investigate whether the current top-level `owns` rules can remain the file navigation index while entity mappings determine finer placement. If a file spans multiple areas, preserve existing symbol-level splits and ensure `where path:line` still resolves meaningfully. Avoid maintaining two contradictory authoritative ownership maps.

## 6. Provenance semantics, uncertainty, and freshness

### 6.1 What provenance means

Provenance is an inspectable explanation supported by source locations, not a claim of complete static analysis. Keep three independent facts:

- **Claim certainty:** the agent observed this relationship, inferred it, or knows it needs review.
- **Location resolution:** the cited symbol/focus resolved exactly, heuristically, ambiguously, or not at all.
- **Freshness:** the relevant source changed since the claim was reviewed.

Exact location resolution does not prove that the label or causal explanation is correct. “Observed” means the author inspected the code; it does not mean the application executed or formally verified the transfer.

Recommended evidence roles are origin, construction/transformation, transfer, receive, and consumption. Keep the taxonomy small and document the difference. Consumption evidence is optional when the transfer site already explains the connection. A result received at a call site does not by itself explain how the callee constructed it; label that scope honestly.

### 6.2 Setter and mutable-state cases

Summarize several incidental setters as one configured object on the edge. Include the relevant setter/assignment sites in the panel and enough setter implementation evidence to support a claim that it merely changes configuration when that matters.

Do not hide meaningful validation, I/O, event emission, or other side effects inside a “configuration” summary. If a setter is itself an important operation, model it accordingly.

The evidence must refer to the object state relevant to the transfer. Later mutation, aliasing, conditional assignments, escaping references, callbacks, and deferred consumption can make the exact state uncertain. In particular, passing an object reference is not proof of a snapshot at call time. Do not combine mutually exclusive branches into a value claimed to exist on every execution. Mark uncertainty and describe conditions where analysis cannot establish the state; do not silently manufacture field-level provenance.

### 6.3 Focus anchors

Separate containing context from focused highlights. Support multiple focused spans and multiple files. Context can expand without changing what is asserted as relevant.

Investigate a small, robust initial selector design:

- Resolve the enclosing path and qualified symbol using existing machinery.
- Within it, resolve a precise focus using an exact unique text anchor and/or validated range. A language-aware expression selector may improve this later.
- Treat no match or multiple matches as unresolved/ambiguous; never silently choose the first or a fuzzy replacement.
- Preserve context even when a focus breaks, clearly indicating that the precise evidence is unresolved.
- Track changes to the enclosing relevant source and review revision. Content hashes can help detect stale anchors, but a stable excerpt does not prove that its surrounding semantics stayed unchanged.

Ranges alone may still point to different code after edits. Label them as location hints unless an anchor or comparison verifies identity. Start with statement/line highlighting if that is robust; expression-level character spans are useful but must not block the initial workflow or falsely imply greater semantic precision.

Avoid an elaborate language-independent AST query language in the first version. Do not overload today's `lines` hint with new semantics in old schema versions. Retain existing path confinement, excluded-file handling, output escaping, file-size limits, and read-only source access. The viewer must not execute or import the repository to resolve evidence.

## 7. Viewer and layout requirements

### 7.1 Pair presentation

Try drawing paired transfers as two nearby directional lanes with distinct arrowheads and labels, routed as a related exchange. Select one leg precisely; highlight its counterpart secondarily. Failure paths should remain distinguishable and should not force every pair into a three-line bundle.

This visual treatment is provisional. Keeping lanes adjacent through nested regions can create extra crossings or obscure direction. Compare against existing independently routed arrows on the shop, self-map, and dense fixtures. It is acceptable to retain independent geometry with explicit pair highlighting if adjacency makes results worse.

Preserve directional information in overview bundling. Do not replace a pair with a generic bidirectional arrow that hides different payloads. At low zoom, a compact exchange indicator or bundle count may be necessary; inspection must reveal each leg and true endpoint. Labels should not become permanently unclickable because of collision suppression.

### 7.2 Inspector and navigation

Reuse source retrieval and syntax highlighting. Load excerpts lazily; cancel or ignore stale responses when the user selects another edge. Bound initial rendering and offer expansion for long or numerous excerpts.

Show why each snippet is relevant. Do not dump every upstream function into the panel. Default to the transfer plus the most relevant construction evidence, with progressive expansion. Preserve in-map code panes alongside the right-panel experience.

Keep stable IDs through selection, URL state, refresh, flow highlighting, and copy-for-agent output. If a selected leg disappears after refresh, explain that and keep navigation usable. Ensure label hit targets do not accidentally select a neighboring parallel leg. Provide an accessible list-based route to all visible exchanges.

### 7.3 Preserve useful performance behavior

Avoid rewriting the router, camera, and renderer solely because the schema changes. Introduce a normalized internal model that reuses existing relationship/route machinery where possible. Preserve viewport culling, lazy code panes, hidden-count disclosure, and deterministic geometry during zoom/filtering/selection.

Measure analysis time, layout time, first useful render, inspector opening, and steady-state interaction separately. Do not infer acceptable readability from frame rate or acceptable performance from synthetic node counts alone.

## 8. Authoring guide and exported skill

Rework the canonical `src/repoluma/guide/AUTHORING.md`, `repoluma guide`, schema documentation, init prompt, and exported skill together. The installed package remains the source of version-matched instructions. Do not create a second independently maintained skill with divergent rules or silently install anything in an agent's global configuration.

The authoring process should be code-grounded and iterative:

1. Read project purpose and inventory in-scope implementation units and external boundaries.
2. Identify representative entry points and follow meaningful data exchanges and failures.
3. Choose concrete operation/resource entities and their implementation mappings.
4. Organize them into explanatory groups. Revise groups as understanding improves; do not mirror directories mechanically.
5. Author transfers with labels and provenance together. Pair only actual related directions.
6. Preserve/describe contracts, rules, assumptions, and failure/compensation flows.
7. Run validation; repair errors and disclose unresolved evidence and coarse coverage.
8. Review diagrams for reader tasks and inspect representative labels, rather than stopping when YAML passes validation.

The guide must explicitly teach:

- Implementation ownership differs from evidence citations and from file placement.
- Complete accountability does not require a box per statement or a line per call.
- Paired exchanges are preferred when meaningful, not enforced by a numerical target.
- A setter/building step can be summarized only when its meaningful effects are preserved.
- A text match proves location, not dataflow; no invented runtime certainty.
- Useful labels name the data as understood by a reader, while evidence identifies actual variables/expressions.
- Unimplemented external bridges remain gaps. For example, the shop's missing HTTP handler must not become a proven frontend-to-backend transfer during migration.
- New source can make an old map incomplete even if every old reference still resolves.

Include worked before/after examples for a simple input/result call; a configured object built by setters; a setter with significant side effects; shared mutable state; one-way events; exception/compensation handling; and ambiguous/moved evidence. Examples must parse against the implemented schema, except explicitly labeled pseudocode. Use a small synthetic fixture where the existing shop lacks a behavior; do not alter shop business behavior to manufacture it.

Migration instructions must preserve useful labels, summaries, contracts, rules, identities, failure paths, uncertainty, and useful evidence. Do not tell an agent simply to regenerate a map from scratch. Include a completion checklist and a concise repair loop keyed to actual validator diagnostics.

## 9. Compatibility and migration

Follow the repository's schema compatibility policy, checking the current version first. Keep reading existing supported maps for the documented period. Existing valid maps should remain useful without automatic source edits.

Distinguish two activities:

- **Compatibility loading:** preserve old meaning and render old records, including old group endpoints, through a disclosed legacy adapter. New rules must not reinterpret old group endpoints as invented concrete operations.
- **Authoring migration:** rewrite a map into the new model with source-aware decisions, preserving IDs where possible and flagging unresolved decisions for review.

Do not silently expand a legacy group-to-group relationship into every possible pair of leaves. That invents edges. Do not infer request/result pairing merely from opposite directions. Do not upgrade existing broad citations to detailed implementation mappings without inspecting the code.

A mechanical converter can preserve fields, propose groups, and produce migration diagnostics. It cannot fully establish semantic provenance or choose operation boundaries. If a migration command is introduced, make it explicit, write to a separate output by default, and never overwrite a user's authored map without the command's documented opt-in behavior.

Preserve IDs for leg relationships referenced by flows and links wherever possible. Keep an explicit old-to-new mapping when an ID must change; preserve selection URLs or provide a clear redirect/obsolete-reference notice. Schema generation and parsing must agree. Unknown future schema versions must remain clearly refused rather than misread.

Migrate the complete shop map and self-map as part of implementation. Verify preservation of existing useful behavior and explanations, not just successful conversion.

## 10. Validation policy

Use errors for structural contradictions, clear diagnostics for missing required evidence, and honest status reporting for semantic uncertainty. Keep partial/broken maps inspectable where the current application already supports this.

Expected structural errors in the new schema include nonexistent/group endpoints, containment cycles, duplicate IDs, invalid paths/ranges, invalid flow references, and conflicting canonical implementation ownership. Rules/constraints should not be accepted as runtime data endpoints unless explicitly modeled as actual runtime values.

In newly authored maps, an `observed` data transfer should have at least one resolvable relevant transfer/receive site. If this is unavailable, the author should downgrade the claim to an appropriate uncertain status and explain the limitation; a note must not bypass the evidence requirement while leaving the claim apparently verified. Decide and document whether missing evidence is a normal validation error or a strict-mode error; do not silently weaken legacy compatibility. Inferred external relationships can remain useful, but must retain conspicuous uncertainty and local evidence when available. The resolver can establish that the site exists, not automatically that it is semantically relevant; that final judgment belongs to authoring and review.

Diagnostics should identify the record, source/map location, what failed, and a concrete repair action. Coverage reporting must distinguish an incomplete map from an unsupported parser. Preserve current resolution and freshness distinctions rather than flattening them into one confidence score.

## 11. Acceptance scenarios and tests

Establish fresh baselines in the implementation environment. Existing tests and repository benchmark results are inputs, not proof that the new implementation passes. Preserve relevant current tests or replace them with tests of equivalent behavior when the model changes legitimately.

| Scenario | Required observable result |
| --- | --- |
| Shop payment authorization | Distinct clickable input/result labels, explicitly paired; correct call-site evidence; result construction accessible when mapped |
| Configured object | One useful summarized data label; panel shows relevant setters/assignments and transfer, without pretending later mutation was already present |
| Setter with side effects | Significant validation/I/O/event behavior remains visible or explicitly explained |
| One-way publication | No fabricated return arrow; producer, event route, and consumer evidence remain distinguishable |
| Payment decline | Failure and stock-release compensation remain understandable; no false successful result on that branch |
| Missing HTTP bridge | Existing uncertainty in the shop remains visible after migration |
| Deep grouping | Underlying endpoints remain concrete; overview inspection reveals them; no invented group semantics |
| New function/file | Coverage gap or changed coarse membership is visible; yesterday's coverage cannot silently imply review of new behavior |
| Repeated/moved/deleted focus | Unique moves resolve honestly; ambiguity/deletion is disclosed; no silent retargeting |
| Shared implementation | One canonical location, multiple supporting citations, no duplicated execution claims |
| Legacy map | Existing meaning, flow references, and useful navigation are preserved; unresolved migration choices are reported |
| Dense paired connections | Leg direction and selection remain usable; pair routing meets the agreed layout/performance regression budget |

Tests should cover the schema/parser, normalized model, reference resolution, ownership/coverage, migration, server evidence payloads, browser selection/labels, and key navigation. Include fixtures for aliasing/conditional construction with explicitly limited claims; do not build a general static analyzer just to satisfy those tests.

Retain regression coverage for reversible pointer-anchored zoom, equal-depth panning, deterministic layout, obstacle avoidance, live refresh, file navigation, copy-for-agent, filter disclosures, failure flows, source confinement, and escaping untrusted text. Test specific failure modes rather than mirroring implementation details.

Compare reader tasks on old and new diagrams: find the origin of an input, identify what a call returns, locate a relevant business rule, explain a failure path, and find newly unmapped code. Record answer correctness and interaction steps; time-to-answer is useful but sensitive to familiarity. Prefer an unfamiliar human reader when available. If only agent-based review is possible, disclose that limitation and avoid claiming proven human usability.

Measure existing shop/self-map plus broad, deep, and dense benchmarks with the same environment and settings. Record repeated timings and visible layout examples. Establish a material-regression budget before selecting the implementation, using measured variability; a percentage chosen after seeing the results is not a fair gate. Do not claim “strict improvement” merely because total nodes/edges or test counts increased.

## 12. Implementation sequence and decision gates

### Phase A — Inspect and baseline

Read repository instructions, compare the current checkout to the reviewed baseline, run the relevant existing checks, and document baseline tasks/screens and performance. Identify what can be reused. Preserve a reference rendering/model of old maps for comparison.

### Phase B — Small design spike

Express order placement, payment authorization, payment decline, and event publication in the candidate model. Add a separate minimal setter fixture. Prototype one clickable data label with a contextual provenance panel and one explicit input/result pair. Compare against current behavior before finalizing field names or replacing major subsystems.

Resolve the highest-risk questions: what counts as an implementation unit, how focus anchors fail honestly, how grouped legacy endpoints migrate, and whether nearby pair lanes improve readability. Record alternatives and evidence in the project's decision documentation. Routine reversible choices should not require repeated owner approval; material requirement conflicts should be surfaced.

### Phase C — Model, resolver, and compatibility

Implement the new schema and normalized representation, separate implementation/evidence, focus resolution, diagnostics, and compatibility loading. Add stable leg IDs and flow references. Avoid requiring complete interprocedural analysis.

### Phase D — Viewer integration

Implement label hit targets/accessibility, the provenance inspector, pair selection, and only the routing changes supported by experiments. Retain existing navigation and in-map source. Measure responsiveness with actual evidence payloads.

### Phase E — Authoring workflow and complete maps

Update guide, exported skill, prompts, docs, and schema examples together. Migrate the full shop and self-map. Use the public authoring workflow to validate them; do not rely on undocumented private format shortcuts.

### Phase F — Review and delivery

Run the meaningful regression and acceptance checks, compare the agreed baseline tasks, and resolve or disclose remaining gaps. Remove temporary experiments that are not part of the chosen solution. Provide a reviewable implementation with migration notes, measured results, and known limitations. Do not stop at a design spike unless a material unresolved conflict makes proceeding unsafe or misleading.

## 13. Open questions: investigate, do not assume

| Question | Starting recommendation | Evidence that should change the choice |
| --- | --- | --- |
| One exchange record or flat edges with pair IDs? | Author exchange-owned legs; normalize to flat edges internally | Migration complexity or real asynchronous examples make flat edges materially simpler without duplicated facts |
| Separate group/entity collections? | Yes, to make endpoint rules explicit | A discriminated union preserves clarity with substantially less duplication |
| Adjacent paired lanes? | Prototype; use explicit pairing regardless of geometry | More crossings, unreadable directions, or slower routing favor independent lanes plus paired highlighting |
| Symbol, region, or module as mapping unit? | Symbols by default, meaningful regions when needed, disclosed coarse modules | Real maps show excessive fragmentation or impractical authoring effort |
| Text anchors versus AST selectors? | Unique scoped text/range focus with honest failures first | Repeated failures on real code justify language-aware selectors or an optional parser dependency |
| How much provenance is mandatory? | A useful transfer/receive site for observed legs; richer construction progressively | Panel tasks remain unanswerable without additional required roles, or authoring cost becomes excessive |
| Rules as entities or annotations? | Attach simple rules; retain independent entities where existing flows/dependencies benefit | Reader tasks show either duplication/noise or loss of discoverability |
| Whole-repository automation? | Deterministic inventory plus agent-authored meaning | Demonstrated benefits justify optional language-specific analysis; never label heuristics as proof |
| Strict coverage severity for non-Python? | Preserve usefulness and disclose heuristic limits | Better parsing support permits stronger checks without false errors |

Multi-view maps, a global data registry, runtime tracing, a universal AST query language, full alias analysis, visual map editing, and a new rendering engine are not initial requirements. Do not expand into them merely because the redesign makes them imaginable.

## 14. Critical review incorporated into this brief

The proposal was checked against the existing project and revised to address these pitfalls:

- **Reinventing existing features:** paired data guidance, nesting, evidence references, and coverage already exist. The target is stronger semantics and interaction, not duplicate infrastructure.
- **Turning “most pairs” into a quota:** rejected; only actual exchanges receive paired directions.
- **Making all inner boxes methods:** rejected; meaningful operations, state, external boundaries, and inspectable constraints must remain representable.
- **Confusing exact references with verified dataflow:** rejected; claim certainty, resolution, and freshness are separate.
- **Claiming setter summaries are always harmless:** rejected; side effects and mutable-reference timing need explicit treatment.
- **Using full-file citations as semantic completeness:** rejected; broad mapping is disclosed as coarse, with inventory accountability.
- **Promising change detection without a baseline:** rejected; reviewed revisions or explicit checkpoints support detection, otherwise freshness is unknown.
- **Forcing exact granularity everywhere:** rejected; complete accountability must coexist with a readable overview and practical authoring effort.
- **Making pairs duplicate edge records:** rejected; one authored representation with stable directional IDs and a derived internal graph.
- **Breaking existing maps through stricter rules:** rejected; compatibility loading and source-aware migration are distinct.
- **Inventing detailed edges during migration:** rejected; group endpoints and reverse-edge pairs require real semantic decisions.
- **Assuming grouped endpoints must be visible at overview:** rejected; overview projection retains concrete underlying identity.
- **Treating a rewrite as inherently better:** rejected; reuse the renderer and routing where measurements support it.
- **Promising strict improvement without evaluation:** rejected; preserve baseline tasks, measure target gains, and disclose uncertainty.

Uncertainty remains chiefly in the best schema ergonomics, focus-anchor resilience, coverage granularity, and paired routing. Those are explicit implementation experiments, not reasons to relax the product requirements silently.

## 15. Required implementation handoff back to the owner

Deliver the implementation with:

- A concise explanation of the chosen schema and what changed from this proposal.
- Updated canonical guide/exported skill, docs, and validated examples.
- Migrated shop/self-map plus legacy-loading and migration behavior.
- Demonstration of clickable input/result labels and setter-construction evidence.
- Before/after evidence for the agreed reader tasks and performance/layout checks.
- Test results, known limitations, unresolved questions, and any material regression.
- Clear instructions for running, authoring, validating, and migrating maps.

Completion means the full workflow works: an agent can author a code-grounded map using the shipped instructions; validation finds real gaps without claiming semantic proof; a reader can follow an exchange and inspect its relevant code; and existing useful navigation and explanations remain available.

## 16. Source map for the implementing agent

All links below are pinned to the reviewed baseline; use corresponding current files when implementing.

- [README and product behavior](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/README.md)
- [Schema and compatibility policy](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/docs/schema.md)
- [Design decisions and earlier tradeoffs](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/docs/decisions.md)
- [Canonical authoring guide](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/guide/AUTHORING.md)
- [Model, validation, schema generation, flow linking](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/model.py)
- [Source resolution and excerpts](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/sources.py)
- [Ownership and code coverage](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/ownership.py)
- [Inspector and relationship details](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/web/js/ui.js)
- [Rendering, annotations, and hit testing](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/web/js/render.js)
- [Nested layout](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/web/js/layout.js)
- [Self-map](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/.repoluma/map.yaml)
- [Shop map](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/examples/shop/.repoluma/map.yaml)
- [Shop order implementation](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/src/repoluma/examples/shop/shop/orders.py)
- [Browser regression tests](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/tests/test_e2e_browser.py)
- [Ownership tests](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/tests/test_ownership.py)
- [Performance methods and recorded limits](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/docs/performance.md)
- [Evaluation and remaining reader-study gap](https://github.com/seweber/repoluma/blob/b5da6014a103f94edbdcd158871f31e84a96905b/docs/evaluation.md)
