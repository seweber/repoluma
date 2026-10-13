# Performance

## Method

`bench/gen_stress.py` generates synthetic repositories with maps of different shapes.
`bench/run.py` serves each repository, opens it in headless Chromium, and measures:

- **analyze**: server-side validation and resolution of every reference (Python);
- **ready**: from navigation until the viewer has loaded and laid out the map;
- **layout**: the layout computation alone;
- **render work**: CPU time for one frame's drawing (canvas plus code-pane DOM updates)
  over 660 frames of scripted navigation: zooming from the overview into six places,
  panning at depth, and zooming back out;
- **frame interval**: wall-clock time between animation frames during four animated
  fly-tos, including paint and compositing (16.7 ms = 60 Hz);
- **pane open**: from arriving at a code pane with an empty cache until its source lines
  are in the DOM (fetch, highlighting, DOM construction).

Synthetic maps test engineering limits, not semantic usefulness. Reproduce with
`just bench`, or see `docs/development.md` for the commands.

## Environment of the published numbers

Dev container on an AMD Ryzen 7 5700G (16 threads), Linux 6.8, Python 3.12, headless
Chromium 153 with **software rendering** (no GPU in the container), 1440×900 viewport.
A desktop browser with GPU compositing should do no worse. Raw data:
`bench/results/2026-10-12-container.json`.

## Results (2026-10-12, with orthogonal routing)

"Ready" and "layout" were re-measured after the routing rework (inside-first router,
see `docs/decisions.md` §3); the other columns are from the full run before it, and the
router does not affect drawing cost.

| dataset | nodes | relationships | code panes | analyze | ready | layout | render work p50 / p95 / max | frame interval p50 / p95 | pane open p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| example shop | 27 | 24 | 23 | 0.02 s | 0.10 s | 35 ms | 0.6 / 3.5 / 11 ms | 16.7 / 18.0 ms | 50 ms |
| repoluma self-map | 43 | 43 | 59 | 0.14 s | 0.21 s | 42 ms | 1.5 / 3.2 / 16 ms | 16.7 / 17.9 ms | 52 ms |
| broad (40 × 50) | 2,040 | 2,000 | 2,000 | 0.36 s | 2.1 s | 1.3 s | 1.5 / 7.8 / 19 ms | 16.7 / 20.8 ms | 50 ms |
| deep (3^7) | 3,279 | 3,000 | 2,187 | 0.50 s | 3.9 s | 2.7 s | 1.3 / 4.3 / 12 ms | 16.6 / 18.9 ms | 50 ms |
| dense | 300 | 6,000 | 285 | 0.27 s | 3.2 s | 2.4 s | 1.6 / 5.2 / 15 ms | 16.7 / 18.7 ms | 50 ms |
| long source (400-line functions) | 200 | 200 | 190 | 0.34 s | 0.8 s | 0.2 s | 1.9 / 3.8 / 19 ms | 16.7 / 18.9 ms | 50 ms |
| huge | 10,110 | 10,000 | 9,000 | 2.3 s | 10.1 s | 7.3 s | 2.0 / 7.6 / 23 ms | 16.9 / 21.8 ms | 50 ms |

The inside-first router decides, for every line, how it leaves each enclosing region
before it fixes where it crosses a border, and keeps every relationship as its own line.
That gives cleaner lines and costs more on the stress maps than the earlier top-down
router (deep 0.6 s → 2.7 s, dense 0.7 s → 2.4 s, huge 2.2 s → 7.3 s); real maps still lay
out in about 40 ms. In regions with more than 400 lines, crossings and running alongside
are no longer priced. Rip-up (placing crossing lines again) and the Zs that pass beside a
crossed line added deep 2.8 s → 3.8 s, dense 2.6 s → 2.9 s, huge 7.5 s → 8.4 s (layout,
`bench/layout_eval.py`); maps above 1,500 nodes skip the rip-up, which alone had made the
huge map take 77 s. Ways out of a region priced by the lines they cross, a second way out
per side, and placing items within their grid cells (`docs/decisions.md` §3) added, in
the same container: dense 2.8 s → 3.6 s, broad 1.3 s → 1.4 s, deep 4.2 s → 4.7 s, huge
8.9 s → 9.7 s; shop and self-map unchanged at about 40 ms. Maps above 1,500 nodes keep
the unpriced ways out, and regions with more than 100 lines are not split by crossings.

The ~50 ms pane-open floor is three animation frames (draw, request, fill), not
server time. "Layout" now includes connection-driven placement and orthogonal routing
of every relationship and flow step; it runs once per map version.

Compared with the earlier curved-edge version: drawing got cheaper (routes are drawn
per region, only for regions whose items are visible), layout got more expensive
(10k nodes: 0.9 s → 7.3 s; 6,000 relationships: 28 ms → 2.4 s).

## What we changed because of measurements

| bottleneck observed | fix | effect |
| --- | --- | --- |
| Pure-Python YAML parsing (15 s of a 17 s profile on the 10k map) | use libyaml's C loader when available, keeping line numbers | analyze 6.9 s → 2.4 s |
| Overview drew every summarized edge plus a measured badge for each | budget of 300 edges per frame, weakest dropped first and **counted in the status bar**; badges only when ≤ 60 edges | broad p95 render 54 → 7 ms; huge 45 → 14 ms |
| Thousands of sub-10 px boxes with rounded corners and greeked text | plain blocks below 10 px; no children below 5 px | fewer primitives at overview |
| Long excerpts built hundreds of DOM lines per pane at once | first 80 lines up front, the rest while scrolling; at most 4 new panes per frame | long-source max frame work 96 → 18 ms |
| Arrangement candidates scored with an all-pairs crossing count and one routing request per external relationship | sort-based crossing count; one request per item side; shared stress solution; reroute only small regions; candidates only for maps ≤ 1,500 nodes | dense layout 12.2 s → 0.7 s; huge 6.3 s → 2.2 s |
| Inside-first router: spacing constraints between every pair of nearby parallel pieces (7.7 million in one region of the huge map); obstacle checks scanning every box; free spots on a box side recomputed for each candidate shape | constraints only between neighbours (sweep line, after the order is decided); a spatial index of obstacles per region; free intervals per side cached until the side is claimed; shapes skipped by a lower bound before spots are picked; no strict search on fully claimed sides | deep 6.1 s → 2.7 s; dense 6.7 s → 2.4 s; huge 7.3 s (separation alone had taken 9.5 s) |
| Rip-up placed every crossing line again in up to four full passes (40,000 reroutes on the huge map) | later passes only revisit lines crossing a line that moved; skipped above 1,500 nodes; Zs beside crossed lines only for the lines the best path crosses, after the ordinary shapes | huge 77 s → 8.4 s; deep 6.1 s → 3.8 s |

| Gaps marking line crossings were computed and drawn for every crossing of every drawn line (millions on the dense map at overview) | gaps only in regions drawing ≤ 400 lines, at most 3,000 per region and frame | dense overview frame 6.3 s → 80 ms |

**Open (2026-10-13).** The 300-edge overview budget above did not survive the routing
rewrite: overview frames draw every visible route again. Render p95 on the stress maps is
now deep 28 ms, dense 72 ms, huge 76 ms (bundled maps 4–5 ms); see
`bench/results/2026-10-13-container.json`. A per-frame budget for routes, weakest first and
counted in the status bar, is the next step.

## Supported limits (declared)

- **Fluid (60 Hz, p95 frame interval ≤ ~22 ms) and opening within ~4 s:** maps up to
  about **3,000 nodes and 6,000 relationships**, including deep hierarchies and long
  source files.
- **Fluid while navigating, slow to open:** about **10,000 nodes / 10,000
  relationships**; startup takes about 10 s (parsing, layout and routing). Moving the
  layout into a Web Worker and caching it per map version would hide most of this.
- Beyond that it is untested. The next costs would be layout (single-threaded, about
  O(n)) and loading the whole model at once. Chunked loading or a worker would be the
  next steps, only if real maps get that large.
- Coverage scanning lists at most 50,000 repository files.

Real semantic maps are much smaller than these limits: the maps an agent produces for
a medium-sized repository have tens to a few hundred nodes.

## Schema 3 (2026-10-13)

Same container, Chromium 153 headless, 1440×900; three runs each of the previous commit
(b5da601, "before") and this one ("after"), medians, range in brackets. The stress maps
are schema_version 1 and identical in both; the shop and self-map are the migrated maps
(more relationships after: shop 26 → 33, self-map 49 → 62).

| dataset | analyze before | analyze after | layout before | layout after | render p95 before / after | pane open p95 before / after | evidence open p95 |
| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |
| shop | 0.045 s | 0.047 s | 91 ms (86–98) | 93 ms (76–113) | 6.3 / 6.6 ms | 50 / 50 ms | 20 ms |
| self-map | 0.32 s | 0.53 s¹ | 208 ms (158–209) | 206 ms (109–209) | 6.4 / 6.5 ms | 59 / 58 ms | 19 ms |
| broad | 0.48 s | 0.47 s | 1.73 s (1.64–1.73) | 1.69 s (1.67–1.72) | 10.1 / 10.1 ms | 50 / 50 ms | – |
| deep | 0.66 s (0.62–0.69) | 0.73 s² (0.68–0.77) | 5.0 s (4.8–5.2) | 5.4 s (4.8–5.6) | 26.5 / 27.1 ms | 50 / 50 ms | – |
| dense | 0.41 s | 0.43 s | 4.8 s (4.1–5.1) | 4.6 s (4.5–4.9) | 70.3 / 73.0 ms | 51 / 57 ms | – |

¹ On the *old* self-map the new code takes 0.34 s (+6%, the definition inventory); the
rest is the new map's 150 references (95 before), mostly JavaScript symbols resolved by
text pattern. A first version of the focus resolver normalized the whole file for every
reference and took 0.67 s; it now normalizes each file once.
² The definition inventory parses every owned Python file (about 2,000 generated files in
this map): about +10% analysis time on the largest maps.

Raw data: `bench/results/2026-10-13-schema3-before.json` and `…-after.json` (one run of
each; the table gives medians of three).

"Evidence open" is new: from selecting a line with the most cited sites until its first
excerpt is in the DOM, with an empty excerpt cache. Apart from deep-map analysis, the
differences in the stress rows are within the run-to-run range. Layout geometry of unchanged maps is identical before and
after (`bench/layout_eval.py`: old self-map 8 crossings, shop 8, with either version); the
changes in the bundled maps' layouts come from their content (see `docs/decisions.md` §13).
