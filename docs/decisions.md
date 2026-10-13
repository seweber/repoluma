# Design decisions

Short records: problem, alternatives, choice, what we observed, when to revisit.
"Observed" refers to the prototypes and measurements in this repository
(`bench/`, screenshots taken with headless Chromium during development).

## 1. Semantic zoom: fixed geometry, constant scale step, detail by projected size

**Problem.** One map must reveal more meaning as you zoom, stay coherent while panning,
and reach source code, without the layout jumping around.

**Alternatives.** (a) Expand-on-zoom layouts where parents grow to make room for
children. (b) Separate diagrams per level with drill-down. (c) Fixed nested geometry
where children are drawn smaller inside their parent, and detail appears as their
projected size grows.

**Choice.** (c). Each node's children are packed into its body at a constant scale
step `K = 0.4`; code panes sit inside their node at `0.55`. Geometry depends only on
the map, never on zoom, filters or selection. Rendering chooses per node by projected
size: an overview label (box ≥ 50 px), a full header once the title would be ≥ 7 px
(smaller sections are drawn as "greeked" bars that keep their shape), code panes as real
DOM text once code would be ≥ 4.5 px. Because `K` is global, every node at the same depth
has the same on-screen size at a given zoom: panning at close zoom shows comparable
detail everywhere without expanding anything.

**Observed.** `K = 0.3` (first prototype) made a parent's title enormous before its
children became readable, and reaching code required extreme zoom. `K = 0.4` with a
separate, larger pane scale gave three comfortable steps: overview → responsibilities
and contracts → code. Fly-to uses van Wijk & Nuij's zoom-out-travel-zoom-in path,
which keeps orientation on long jumps. Back history, breadcrumbs ("where am I"), the
minimap and search cover "a clear route back".

**Revisit if** users get lost in deep maps (consider an explicit focus+context lens), or
real maps have very uneven depth where a per-subtree step would read better.

## 2. Rendering: Canvas 2D for the map, DOM for code and panels

**Alternatives.** DOM/SVG for everything; Canvas 2D plus DOM detail; WebGL; an existing
graph library (e.g. a Cytoscape-style engine).

**Choice.** Canvas 2D draws boxes, text and edges each frame with viewport culling and
size-based level of detail; DOM is used only where it matters: code panes (selectable,
copyable text with line numbers), the inspector, outline (keyboard and screen-reader
path) and controls. No graph library: none we considered models nested semantic zoom
with in-map code panes without fighting its defaults, and the interaction code here is
small (camera ~150 lines, renderer ~600).

**Observed.** On a 10,110-node / 10,000-relationship synthetic map the 95th percentile of
per-frame render work is about 16 ms in headless Chromium with software rendering (see
`docs/performance.md`); the 2,000-node maps stay under 8 ms. The bottlenecks we hit were
not raw geometry but (1) pure-Python YAML parsing (fixed by using libyaml when present)
and (2) drawing thousands of summarized edges and badges at overview (fixed with an
edge budget, below). Nothing so far argues for WebGL.

**Revisit if** maps beyond ~10k nodes need fluid overview interaction, or text
rendering quality becomes an issue at intermediate zoom levels.

## 3. Layout and connections: connection-driven placement, simplest-shape orthogonal routing

**Problem.** The first version packed children in author order and drew relationships as
curves from centre to centre. Lines crossed boxes and text, related items could sit at
opposite corners, and all lines floated above the whole map.

**Alternatives considered.** Layered (Sugiyama) layout, e.g. via elkjs: good when there
is a dominant direction, but domain maps mostly connect capabilities in all directions,
and a layered layout imposes a reading order the content does not have. Force-directed
layout of nested groups: organic but unstable, and still needs a router. Libraries
(elkjs, libavoid via WASM, Graphviz, WebCola) do not support our constant-scale nesting
without workarounds. We wrote our own, following HOLA (Kieffer, Dwyer, Marriott,
Wybrow 2016): neutral stress-based placement, snapped to an orthogonal grid, then
orthogonal connector routing (Wybrow, Marriott, Stuckey 2009).

**Choice.**
- *Placement* (`arrange.js`), per region: stress majorization over graph distances
  (neutral, no preferred direction), optimal assignment (Hungarian) of positions to grid
  cells, then local swaps minimizing weighted route length plus a penalty for routes that
  need bends. Sizes are computed bottom-up; then, top-down, each region is re-arranged
  within its fixed size with *anchors* that pull items toward the side where their outside
  partners are. Maps up to 1,500 nodes also try a few variants per region and keep the one
  that routes best. Last, an item smaller than its grid cell moves within the cell to
  where its lines can run straight: level with partners beside it, in line with those
  above or below (otherwise it stays at the top of its row). A short box next to a tall
  one in the same row would otherwise force a bend on every line between them.
- *Routing* (`route.js`) follows a few rules that apply everywhere, with no special
  cases per situation:
  1. **Obstacles.** A line never passes through anything drawn — boxes, the region's
     title text (measured, not the whole header band), code panes — and keeps a fixed
     clearance from it. Region borders are not obstacles.
  2. **Sides, not points.** A line may touch a box anywhere within a side's usable range.
  3. **Simplest shape.** Within a region a line takes the cheapest straight, L or Z
     shape through free space, priced by length, bends, short jogs, crossings and running
     alongside other lines (two lines sharing a stretch count as crossing when they leave
     it on opposite sides at its two ends: spread apart, they must cross); a
     bend-penalised path through the grid channels is the
     fallback. Option pairs are tried in order of a lower bound (branch and bound). A
     crossing costs more than the two bends that pass a line instead. Where a claimed spot
     is wanted, the free spot on either side is tried (passing a line on its one or other
     side); if the best path still crosses lines, Zs whose middle piece runs right beside
     those lines are tried as well.
  4. **Inside first.** For a line between items in different regions, the ways out of
     every enclosing region are computed bottom-up. Straight and L-shaped ways out keep the
     *range* of border positions they can use; the region containing both ends then picks
     the cheapest combination. A way out of any other shape still keeps the range its last
     piece can slide along the border. A border crossing is therefore never fixed before
     the inside is known (the cause of the earlier steps right after entering a region).
     Where on the border a line crosses decides which placed lines its inside part
     crosses, so a range is split where that changes and each stretch is priced with its
     crossings; positions where the line would share a lane with placed lines (one lane
     per line, as separation will spread them) are not offered. Per side, the cheapest
     stretch and the cheapest one apart from it are offered, and an L way out tries both
     free spots beside a claimed one: passing a line on its one or other side is a
     different topology, which only the whole line can judge.
  5. **Claims.** Spots on box sides and region borders are claimed as lines are placed
     (sorted per side), so lines keep `sep` apart by construction; only when no path can
     respect the claims does a line take the spot farthest from the others.
  6. **Rip-up.** Lines are placed one after another, so an early line can take the way a
     later one needs and force a crossing on it. Afterwards each line that crosses others
     is placed again with all others known and keeps the new way only if it crosses fewer
     lines; then the same for the lines crossing one that moved. It costs about one more
     routing pass, so maps with more than 1,500 nodes skip it (as they skip arrangement
     candidates and the crossing-priced ways out of rule 4).
  7. **Separation.** Parallel pieces that run alongside each other are first put in
     order: by position, except that pieces close together (within three line spacings)
     are ordered by where their lines turn off, so they do not cross there. A sweep line
     (as in VPSC, Dwyer, Marriott, Stuckey 2005) then pairs only pieces that are
     neighbours somewhere in a free corridor, and they move as little as needed to keep
     that order `sep` apart (projection), within bounds taken from the geometry — never
     into a box, never past a neighbouring corner.
  Every relationship is its own line; only identical endpoints share one. In a region
  with more than 400 lines, crossings and running alongside are no longer priced, and
  beyond 100 lines ways out are not split by crossings (speed: single lines can no longer
  be followed there anyway).
- *Flows* add no lines and do not influence placement. Each step is drawn on the
  relationship it uses (`via`, else one from where the flow is to this step's node),
  resolved once by the validator, which warns when a step has none. A step continues from
  where the flow is — the previous step's node, or the other end of the relationship that
  step was drawn on (a coordinator calling one service after another) — unless an actor
  starts something anew; otherwise the validator warns (`step-via-gap`). The viewer keeps
  only a flow's steps and the ends of their relationships bright, so a step that jumped
  left the nodes in between dimmed although the flow passes through them. Earlier versions drew
  a line from each step to the next; for flows run by a coordinator that showed
  connections the map does not have (checkout form → pricing, where the order service
  calls pricing).

**How we got here.** The first orthogonal version routed in grid channels to the centres
of box sides and repaired the results with several passes (straightening, port snapping,
pinning, lane swapping). A second version routed region by region from the top down,
bundled connections into trunks, and blocked the whole header band; that fixed a border
crossing before the inside was routed (steps right after entering a region), forced
detours where a region could have been left upwards beside its title, and its spacing
pass never reordered parallel lines (unnecessary double crossings). The current router
removes those causes instead of patching their effects. Two defects found on the way:
duplicate/collinear points let the spacing pass create diagonal segments, and the fallback
path ignored claims; both were fixed at their source. A routed swap search during
placement was removed: with this router, trying a few arrangements per region and keeping
the best-routed one is as good or better, and 5× cheaper. Separation first constrained
every pair of nearby pieces and decided swaps pair by pair; on the 10,000-relationship
stress map that meant 7.7 million constraints in one region, and the pairwise swaps
could contradict each other. Deciding the order first and pairing only neighbours fixed
both. Obstacle lookups use a spatial index per region.

Later, two lines between the same regions still crossed with some fonts (the ways out of
"Validate a map" and "Serve the viewer" on the self-map). Ways out were priced without the
lines already placed inside the region, a sliding way out ignored them entirely (and kept
a larger clearance from title text than paths need, so it often could not slide at all),
lines sharing a lane before separation counted as not crossing, and only the cheapest way
out per side reached the parent, so the crossing-free alternative never did. Rip-up could
not repair what was never offered. Each of these was fixed where it arose (see rule 4).

**Observed** (`bench/layout_eval.py`, relationship lines; crossings / bends / small steps;
zero overlaps and zero lines through boxes on all three). Since flows stopped pulling on
placement, requests went from 48 to 28 crossings and repoluma from 15 to 11. After the
data-first rewrite (§3a) the maps have different and more lines (repoluma 45 relationships,
shop 26); the numbers below are for those:

| map | crossings | bends | small steps |
| --- | --- | --- | --- |
| shop | 10 | 33 | 2 |
| repoluma | 5 | 33 | 1 |
| requests | 29 | 62 | 2 |

Text metrics change the layout, so these depend on the browser's fonts. With Noto Sans
as the UI font, crossing-aware ways out and placement within cells (2026-10-12) took
shop from 10 / 34 / 2 to 10 / 30 / 0 and repoluma from 7 / 39 / 2 to 5 / 33 / 0, still
with no overlaps and no lines through boxes.

Rip-up and the crossing price removed crossings a single pass could not avoid (two lines
between the same regions taking each other's side); where ends cannot line up, a short
step is now preferred over a crossing.

Spacing (2026-10-13). Lines read as cramped: parallel lines and arrowheads on one box side
were about 5 px apart at the zoom where items are readable, lines ran close to region
borders and started next to box corners. Raising the separation or clearance relative to
the channel width made lines cross more (repoluma 10 → 14 crossings each): the channels
became too narrow for the same lines. What helped was wider channels with the same ratios
(region gap `44 + 6√w`, at least 50, instead of `30 + 5√w`, at least 34), line ends kept
half a gap from box corners (was 0.3), region contents kept half a gap from the border,
and line ends on a box side spaced 1.6 × the line separation so arrowheads stay apart.
Measured on the maps as they now are (both larger than above, with ownership):
shop 8 / 26 / 0 → 8 / 28 / 0, repoluma 10 / 45 / 0 → 11 / 42 / 0, with about 45% more
space between lines. Results swing with the arrangement search (other gap formulas gave
7 to 21 crossings on repoluma), so changes here should be measured on several maps.

The remaining crossings and turn-backs come from where boxes sit (an item between a line's
end and the region border, a region title spanning most of the top edge); improving them
is a placement question.

**Revisit if** dense regions read poorly (consider edge bundling inside channels or
crossing-aware swap costs), users want manual pinning of positions, or startup on maps
with thousands of relationships matters (layout takes 2–7 s there; move it into a Web
Worker and cache it per map version).

## 3a. Relationships: data first, three types, arrows always from → to

**Problem.** Version 1 described relationships with 22 verbs in six colour categories,
read as "from *verb* to". The arrow of `A reads B` pointed against the data, `calls`
was the largest group in every map (about a third of all relationships), and the data
itself was mostly hidden inside calls: the three maps we had held 2–4 data nodes each.
Following the data, the easiest way to understand most systems, was not possible.

**Choice.** Three types — `data`, `control`, `dependency` — with one reading rule: the
arrow always points from `from` to `to`, and the legend on the map says what that means
per type. Data relationships go from where the data comes from to where it goes and are
labelled with the data itself, not a verb. Authors are told to prefer data: a call with
a result becomes two data lines; control is only for what no data movement expresses;
both are allowed where needed. Data lines are drawn most prominently, control thinner,
dependencies faint; dashes keep meaning "not confirmed in source". The impact list
follows data forward (whoever receives a node's data may be affected) and dependency
and control lines backward.

**Alternatives.** Keeping verbs and deriving the data direction per verb in the viewer
(the reader would have to know, per verb, which way an arrow points); dashes for
control (dashes already mean uncertain evidence and failure paths in flows).

**Compatibility.** `schema_version: 2`. Version 1 maps are converted on load
(`reads`, `subscribes`, `handles` swap their ends; ids are derived before the swap) with
a warning that explains the rewrite.

**Observed.** Rewriting the shop example data-first needed no control line at all; the
self-map kept two (change detection triggers re-analysis; the developer starts "Copy for
agent"). Labels on data lines answer most "what goes where" questions at a glance.
A line crossing region borders is drawn in one piece per region; it is labelled once,
on the piece with the longest free straight run (labelling each piece repeated the data).
Two short jogs between top-level regions appeared in the shop because the free corridors
on both sides do not line up; that is a placement question.

## 4. Code panes: bounded, in the map, click to activate

A pane shows the referenced range plus three context lines; at most 30 lines are visible
and the rest scroll inside the pane, so long functions cannot blow up the layout
(capped at 600 lines per pane; "show more" extends context on demand). The wheel zooms
the map over an inactive pane; clicking activates it (wheel scrolls code, text is
selectable), `Esc` or clicking the map releases it, and `Ctrl`+wheel always zooms. We
rejected "wheel scrolls code whenever over a pane" (zooming out becomes a trap) and
"modifier key to scroll code" (undiscoverable). The inspector lists the same references
with their status and jumps to the pane; it supplements, never replaces, the in-map code.

## 5. Map format: YAML, lenient validator, generated JSON Schema

YAML because agents write it reliably, it allows comments, and it diffs line by line.
The validator is hand-written so it can give record-level diagnostics with line numbers,
"did you mean" hints and repair advice, check referential integrity, and still return a
best-effort model for the viewer (a broken parent becomes top-level instead of failing
the whole map). The JSON Schema is generated from the validator's constants and tested
against both bundled maps. Ids share one namespace across record types so a single id
in a URL or prompt is unambiguous.

## 6. Source references: exact where possible, disclosed elsewhere, never retargeted

Python symbols resolve exactly through the syntax tree. Other languages use definition
patterns plus brace/indent matching and are labeled "located by text pattern". When a
symbol disappears we report `missing_symbol` (with a suggestion in the message only);
when a line hint no longer overlaps the symbol we display the symbol and flag `moved`.
Line-only references are shown but labeled as unverified. Adding tree-sitter would
improve other languages; we deferred it to keep the package small and pure-Python
until real maps show the heuristics failing.

## 7. Source mode: the working tree, with git context

The viewer always shows the working tree (what the developer and agent are editing),
and says so: the top bar shows HEAD and the count of uncommitted files. Staleness of
*descriptions* is tracked separately with `scope.reviewed_revision`: files changed since
that commit mark their nodes "changed since review". A pinned-revision mode was not
needed for the first release.

## 8. Agent workflow: the installed package is the source of the instructions

`repoluma init` creates a placeholder and prints a short prompt that tells the agent to
run `repoluma guide` (always matching the installed schema version) and `validate`. We
ship one provider-neutral Markdown guide; `repoluma guide --format skill --write DIR`
exports it with skill front matter for agents that support skills. Nothing is written
into global or agent configuration automatically. `serve` before a map exists shows
the same instructions and loads the map when it appears, so the first run never looks
like a failure.

## 9. Server and refresh: standard library, polling, no writes

A `ThreadingHTTPServer` on loopback with Host, token and Origin checks; the only
runtime dependency is PyYAML. The page polls a cheap status endpoint every 1.5 s that
digests `stat` results of the map and referenced files; any change triggers re-analysis,
relayout anchored on the current selection, and invalidation of changed excerpts. No
file-watcher dependency, and since nothing is written to the repository there is no
regeneration loop. Python code changes to repoluma itself still need a server restart.

## 10. Layout stability

Placement is deterministic: the same map always gives the same picture (a browser test
checks this). It is connection-driven rather than author-ordered, so adding a
relationship can move items within its region; regions keep their place unless their
size changes. If this proves disorienting after edits, the next step is to remember the
previous positions per id and add them as anchors.

## 11. Files on the map: one area per file, every line of code shown by a node

**Problem.** A reader who comes across a file (in an editor, a diff, a stack trace) should
find its role on the map, and later, changes to the source should show up on the map.
Both need every file to appear somewhere, and visibly. `refs` could not do this: they are
curated evidence, many-to-many (the self-map cited `sources.py` from 7 nodes), and they
covered 41% of the self-map's non-test source lines. Boxes did not say which files they
were built from.

**Alternatives.** (a) Require `refs` to cover everything: maps fill up with whole-file
panes and evidence loses its focus. (b) Let a file appear under every node that uses it:
"where is this file?" has several answers and a changed line lights up many boxes.
(c) A separate mapping from files to nodes that is a partition, as in architecture
conformance checking (reflexion models: Murphy, Notkin and Sullivan 1995; Sonargraph,
Structure101, Lattix, import-linter, Nx module boundaries, Bazel/Pants target ownership)
and `CODEOWNERS`. (d) Fine-grained concern mapping (symbol sets per concept): known to be
costly to keep current.

**Choice.** (c), with file-level ownership as the rule and symbol-level splits as the
exception, and **owners are areas** (top-level nodes). `owns` takes paths, folders and
globs; the most specific pattern wins (exact beats glob, then more fixed segments), the
CODEOWNERS idiom without its order dependence. `path#Symbol` gives part of a file to
another area: `cli.py` belongs to "Map authoring", while `cmd_validate` belongs to
"Map validation". No two areas own the same lines; a file used everywhere (`utils.py`,
`money.py`) gets an area of its own and is cited by its users. Tests, docs, tooling and
generated files need no owner by default.

A first version let any node own files. In use, the file line on inner boxes repeated what
their code panes' captions already said, the overview (where only areas are readable)
showed no files, and owning a file did not make its code appear: `index.html` was owned
but shown nowhere. So ownership moved to areas, and **every line of an owned file is shown
inside its area**: in the pane of the node that cites it, and otherwise in the area's own
"rest of the file" pane, where lines shown in other panes are folded into links to them
(`⋯ lines 12–14 shown in Cart limits`). Refs therefore decide where inside an area code
appears; the remainder never duplicates a pane of the same area. Lines cited only from
other areas stay in the owner's pane, so an area's code is complete on its own. Code
without an owner is shown in a "Not yet mapped" region (dashed, only while not empty, at
most 24 panes: it is a to-do list).

A second step made the remainder a fault. With "rest of the file" panes as a normal
place for code, much of the code sat in areas without a box, a summary or a line, so the
map said nothing about it (on the self-map about 4,000 lines, 90% real code). Now every
line of code must be shown by a node of its area; otherwise `validate` fails with
`unexplained-code`. It is a validation error rather than a format rule because coverage
depends on the code, not the map: adding a function makes yesterday's map incomplete, so
the viewer keeps showing such code (in a dashed "not yet explained" pane) while the error
tells the agent to fix the map. Glue (imports, docstrings, comments, closing brackets, a
class header whose members are shown) is exempt, and whole-class or whole-file refs count,
so a helper module can be one node instead of one per function. Relationships stay
optional: a widely used helper does not need a line from each user. Fixing the self-map
took three new nodes (Viewer app shell, Page and styles, Shared helpers) and whole-file
refs on the leaves that implement each file; the shop needed eight refs (exception and
record classes, constructors). Code panes now hold up to 2,000 lines (the server's limit
per request) so that whole-file refs show whole files; they render lazily and keep their
height.

**Visuals.** Each area names its files in one muted monospace line under its summary
(folder groups such as `web/js/ (9 files)`), readable at overview. Inner boxes show no file
line: their panes' captions (folder dimmed, file name strong, symbol, `↗ owner` when the
cited lines belong to another area) name the files at the node's text size. Rest-of-file
panes are as large as the code panes of the area's items, so all code reads at one zoom.
The file view in the inspector colours each line by the pane that shows it, labels where
that changes, marks cited lines, and outlines the owners on the map. The Files tab lists
every file with its area; `#file=path:line` links and `repoluma where` lead from a file or
line to its area and pane.

**Observed.** The shop moved its `owns` to seven areas plus "Shared building blocks"
(`money.py`, imported by four modules); the self-map to seven areas, with `cli.py` split by
command and `model.py#json_schema` and `#_link_steps` given to the areas they serve.
Layout metrics moved within the usual swing (repoluma 11 → 13 crossings) because areas now
hold rest panes. With every line shown exactly once per area, attributing a diff to panes
is a lookup; the change overlay is the next step.

**Revisit if** maps of large repositories need ownership by language-aware rules (e.g.
packages), or authors split files often enough that an explicit "shared" marker reads
better than a node of its own.

## 12. Box names: by kind, specific, readable alone, tested with readers

**Problem.** The self-map's labels were hard to interpret: verb and noun phrases mixed
among siblings, abstract names ("Navigate meaning", "Describe a repository"), labels that
only make sense under their parent ("Other languages"), button texts ("Copy for agent")
and lone generic nouns ("Analysis", "Source cache"). The guide said only "short display
name in the system's own language".

**Sources.** Capability-modelling guidance disagrees on nouns versus verbs (noun phrases
such as "Customer Management" in most guides; verb–noun with strong verbs in EDGY), but
agrees on consistency within a level, short names at the top, the domain's own words and
avoiding empty words like "manage". Identifier studies favour full words over
abbreviations (Lawrie, Morrell, Feild, Binkley 2007), and Feitelson et al. (2021, 2024)
found that choosing the concepts first, then the words, then the form yields better names
than asking for "longer, more descriptive" ones. DDD names modules after domain concepts
and treats Manager/Helper/Utils as smells.

**Experiment.** Writer agents labelled both bundled maps from the nodes' descriptions
alone (no existing labels) under four rule sets: the old guide (A), noun phrases everywhere
(B), nouns for areas and things with verb + object for responsibilities (C), and C refined
(D: rules stated as constraints, data named by what it holds, no lone generic nouns, a
final list check). Readers matching labels to a shuffled list of descriptions scored
nearly 100% for every set (elimination makes that test too easy). Readers instead wrote,
for each label alone, what they thought the box does; a blind judge scored the guesses
against the real descriptions (0 wrong, 1 partly, 2 right), mixed across sets:

| labels | repoluma (49) | shop (29) | wrong guesses |
| --- | --- | --- | --- |
| current | 1.80–1.82 | 1.90–1.93 | 2 |
| A old guide | 1.76 | 1.90 | 2 |
| B nouns | 1.80 | 1.90 | 2 |
| C by kind | 1.86–1.90 | 1.93–2.00 | 0–1 |
| D by kind, refined | 1.94 | 1.97 | 0 |

Replicates of C differ by up to 0.07, so C and D are close; both remove the misreadings
the others produce. The failures explain the rules: lone nouns ("Analysis", "Layout",
"Constant scale", "File lookup"), nominalisations read the other way ("Text pattern
matching"), rules named as topics ("Partial returns": feature or ban?) and abstract areas
("Navigate meaning"); verb + specific object ("Trace change impact", "Reanalyze on file
changes") and rules stated as constraints ("Whole-order returns only") were read right.
D's one weakness, rule labels growing into sentences, became the "short name of the
constraint" rule.

**Choice.** The guide's "Naming boxes" section: form by kind (areas and things noun
phrases, responsibilities verb + object, rules the rule itself), specific words, readable
alone, short, and a final check of the flat list. Both bundled maps were relabelled with it.

**Revisit if** human readers disagree with the agent readers (the test used one model
family as writer, reader and judge), or maps in other domains show labels drifting long.

## 13. Lines end on items, exchanges pair them, and every data line points at its code

**Problem.** A reader could see that "payment token and total" goes from Place order to
Payment authorization, but not where in the code that happens, what comes back, or whether
the line was more than an impression: neither bundled map cited a single site for any of
its 75 relationships. "Check map structure" cited all of `model.py`, so coverage was
complete while saying nothing about most of it. Nothing stopped a line from ending on a
container, and opposite lines were "pairs" only by appearance.

**Choice** (schema version 3), keeping the format as close to version 2 as the meaning
allows:

- *Containers and items are derived, not declared.* A node with children explains; a node
  without acts. Lines may not end on containers (an error in version 3, a note in version
  2). We considered separate `groups` and `entities` collections (the implementation brief
  recommended them) and an explicit `role` field; both duplicate what the tree already says
  and can contradict it, and both maps already followed the rule without exception.
- *Exchanges are flat legs with a shared id.* `exchange: payment-authorization` and
  `leg: input | result | response | error` on ordinary relationships. The alternative,
  an `exchanges` collection owning nested legs, was the brief's default; on the shop it
  turned two lines of YAML per call into about 35, changed where flows and links find
  relationship ids, and needed a second normalization path. Flat legs keep every id, every
  flow `via` and the viewer's line model unchanged. Pairing is never inferred.
- *Cited sites are refs with a role and focus text.* Roles `transfer`, `receive`, `origin`
  (three, not the five the brief suggested: "construction" and "origin" were the same
  thing in every example, and "consumption" was always the receive site). `focus` is exact
  text, unique within the ref's symbol or range, whitespace-normalized. We did not use line
  ranges (they drift silently) or an expression selector language (not needed by any
  example; the text anchors failed honestly in every edit we tried). An `observed` data
  line without a site is a warning, not an error, so existing maps stay usable while they
  are being grounded; `--strict` (and the self-map test) treat it as a failure.
- *`evidence` became `certainty`* in version 3, because "evidence" was also what refs are.
- *Broad coverage is disclosed, not forbidden.* Whole-file and whole-class refs still
  account for code; each one lists the definitions it covers without describing them, and
  definitions added inside one since `scope.reviewed_revision` are a warning. We did not
  add an authored inventory or checkpoint file: the reviewed revision plus `git show` is a
  real baseline, and without one the viewer says that freshness is unknown.
- *Migration is two activities.* Version 2 maps load unchanged. `repoluma migrate` does
  only the mechanical part (in a new file by default) and lists the decisions that need the
  code; the bundled maps were migrated by reading the code.

**Viewer.** Labels and bundle badges are click targets for exactly their line (they were
drawn but not hit-testable); clicking keeps the camera, frames the label, and highlights
the exchange's other legs more quietly. The inspector shows the leg, its exchange, and each
cited site with the focused text highlighted inside a few lines of context, loaded on
demand, stale answers dropped; "show all" expands the context without changing what is
highlighted. A line without a cited site says so instead of showing a function. Hovering a
line names its real ends (at overview it ends on regions). A Lines tab lists every
relationship, exchanges first, for keyboard and screen-reader access and for lines whose
labels did not fit. A selection that disappears after a refresh (or an old link) says that
the id is gone. "Copy for agent" covers lines, with sites and their status. Information about
something going wrong (raised exceptions and error values, and reported problems such as
validation diagnostics) is a relationship type of its own, `error`, drawn red and dashed
on a route of its own:
first drawn as data, a result and its error leg shared one line marked `×2`, which hid
both payloads and made the failure look like a normal result. A few data lines sharing a
route are labelled with each payload instead of `×n`. Labels lie along their line, never
across it: on a horizontal stretch if one is long enough, else turned to read up a vertical
one, else shortened with an ellipsis (at least six characters) on the longest stretch; a line
with no room for that is left unlabelled (hover and the Lines tab name it). Compared with
labels allowed across vertical lines, the self-map shows fewer labels at overview zoom
(20 instead of 38 of its lines at the fitted view, 43 instead of 59 at 2×; about the same
when zoomed in); the shop shows as many or more. The dotted style for unconfirmed
lines is drawn (and listed in the legend) only while uncertainty is highlighted, so dashes
on the map mean failure.

**Pair lanes.** At first not built: the router tended to put opposite legs side by side,
and pairs were rare. Completing the self-map's exchanges (most of its one-way lines were in
fact the answers of calls whose request was never drawn) changed that. Now the input and
the result (or response) of one exchange are routed once, from caller to callee, and the
renderer draws that route as two lanes offset to either side, each with its own arrow,
label, click target and flow highlight; error legs keep their own red dashed route. In
placement a pair pulls like one line (each leg weighs 1/n), otherwise the extra legs
rearranged the overview; for the width of the channels between boxes it counts as 1.3
lines, since its lanes need more room than one line (counting it as one line left
regions too tight, and pairs were squeezed into one line where they left a region). A pair is wider than a line: between the top-level regions its lanes lie 0.6·sep either
side of its route (at most 6 px), the same for every pair at a given zoom; inside regions
0.3·sep (at most 3 px). The router keeps that much more room around it (in the spots it claims on box sides and region borders, and in
the spacing pass), and the lanes lie that far from the route, so they shrink with the zoom
instead of a fixed 3 px (which overlapped neighbouring lines when zoomed out). Where a box
side is too crowded to keep the room, the router first falls back to the room of a single
line (rather than straight to sharing a spot), and the renderer narrows that piece's lanes
(inside regions only: between the top-level regions the router always finds the room, see
below) so that a gap of min(sep, 6 px) stays to the neighbouring line, down to one line with both
arrows when zoomed out (each piece on its own: a pair may run as two
lanes in one region and as one line where the next is crowded), so a lane never comes closer to another line
than the two routes are (a browser test checks this at four zoom levels). Two router changes make the room
outside always available: a top-level region's border is crossed by the same line at the
top level too, so spots on it keep the larger of the two levels' widths (the inside level
used to offer crossings that had no room for the wider outside lanes); and where every
straight or L-shaped way out of a region lands on a taken spot, a way out with more bends to
a free spot is offered as well (it used to be offered only when no straight or L way out
existed at all, so a crowded border made a pair share a spot). Cost on the self-map: route
crossings 24 → 30; shop 9 → 10. Measured on the self-map with its 18 new pairs: route crossings
25 before and 25 after (`bench/layout_eval.py`, `routeCrossings`); counting each lane as a
drawn line, as `crossings` now does, 57, since every crossing of a pair shows twice. Shop:
9 route crossings, 11 lane-counted, bends 27 → 20.

**Observed.** Shop: 33 relationships (26 before), 7 exchanges, 28 of 29 data lines cite a
site (the exception is the customer's return, which has no caller in the repository and
says so), 53 of 53 focus texts found; layout 9 crossings / 27 bends (8 / 17 before). Two
claims were corrected while grounding them: the confirmation e-mail only reaches an
in-memory outbox (now `inferred`), and the checkout result (`order id`, 402 on decline)
depends on an HTTP handler the repository does not contain (`needs_review`). Seven lines
were added because the code has them and the target reader tasks (what does a call return,
what happens on failure, where does an input come from) need them: the cart lines sent to
pricing, the reservation and the stock shortage coming back from Reserve stock, the decline
reason, the provider's response, the available quantities read from Stock levels, and the
order id the form expects back.

Self-map: 61 relationships (49 before), 43 of 48 data lines grounded, all 78 focus texts
found; 148 Python definitions are covered only by broad refs, which is now visible. Its
layout went from 8 to 24 crossings. On the old map, one added data line already moves the
count from 8 to 14 and one added leaf node to 10, so the count follows map size more than
any one change; but the first version of the new self-map (19 new lines) reached 40 and
rearranged the overview into a wide row with long detours. We removed six low-value lines
(four for the one-off migration command, two that only repeat that results end up in the
analysis payload) and dropped an intermediate grouping that made placement worse; the
overview arrangement now matches the old one. Remaining crossings are inside the server
and evidence regions. **Open for the owner:** keep the new self-map lines (24 crossings),
or document the new behavior with nodes only (closer to 8, but a reader cannot follow the
new data paths there).

**Revisit if** maps from other agents use legs in ways the four kinds do not cover
(correlation of asynchronous messages), text anchors fail often on real edits, or readers
miss lines that the self-map now leaves out.

## Assumptions (not stated by the brief)

- Desktop browsers first; pinch-zoom works but touch was not tuned.
- Maps are authored by one agent/person at a time; no merge tooling beyond readable YAML.
- Repositories up to a few thousand files for the coverage summary (the listing is
  capped at 50,000 files).
