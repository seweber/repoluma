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
  that routes best.
- *Routing* (`route.js`) follows a few rules that apply everywhere, with no special
  cases per situation:
  1. **Obstacles.** A line never passes through anything drawn — boxes, the region's
     title text (measured, not the whole header band), code panes — and keeps a fixed
     clearance from it. Region borders are not obstacles.
  2. **Sides, not points.** A line may touch a box anywhere within a side's usable range.
  3. **Simplest shape.** Within a region a line takes the cheapest straight, L or Z
     shape through free space, priced by length, bends, short jogs, crossings and running
     alongside other lines; a bend-penalised path through the grid channels is the
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
  5. **Claims.** Spots on box sides and region borders are claimed as lines are placed
     (sorted per side), so lines keep `sep` apart by construction; only when no path can
     respect the claims does a line take the spot farthest from the others.
  6. **Rip-up.** Lines are placed one after another, so an early line can take the way a
     later one needs and force a crossing on it. Afterwards each line that crosses others
     is placed again with all others known and keeps the new way only if it crosses fewer
     lines; then the same for the lines crossing one that moved. It costs about one more
     routing pass, so maps with more than 1,500 nodes skip it (as they skip arrangement
     candidates).
  7. **Separation.** Parallel pieces that run alongside each other are first put in
     order: by position, except that pieces close together (within three line spacings)
     are ordered by where their lines turn off, so they do not cross there. A sweep line
     (as in VPSC, Dwyer, Marriott, Stuckey 2005) then pairs only pieces that are
     neighbours somewhere in a free corridor, and they move as little as needed to keep
     that order `sep` apart (projection), within bounds taken from the geometry — never
     into a box, never past a neighbouring corner.
  Every relationship is its own line; only identical endpoints share one. In a region
  with more than 400 lines, crossings and running alongside are no longer priced (speed).
- *Flows* add no lines and do not influence placement. Each step is drawn on the
  relationship it uses (`via`, else one between the previous step's node and this one),
  resolved once by the validator, which warns when a step has none. Earlier versions drew
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

Rip-up and the crossing price removed crossings a single pass could not avoid (two lines
between the same regions taking each other's side); where ends cannot line up, a short
step is now preferred over a crossing.

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

## Assumptions (not stated by the brief)

- Desktop browsers first; pinch-zoom works but touch was not tuned.
- Maps are authored by one agent/person at a time; no merge tooling beyond readable YAML.
- Repositories up to a few thousand files for the coverage summary (the listing is
  capped at 50,000 files).
