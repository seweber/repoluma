// Deterministic nested layout with a constant scale step per depth.
//
// Every node has a local coordinate system. Its children are arranged in a grid in its
// body at scale K relative to the node (connection-driven, see arrange.js), its source
// panes below them, and connections are routed through the grid's channels (route.js).
// Because K is the same
// everywhere, all nodes at the same depth have the same projected text size for a
// given camera zoom: panning at a fixed zoom reveals comparable detail everywhere.
// Geometry does not depend on zoom, filters, or selection, so the map never
// rearranges itself while you navigate.

import { CANDIDATES, TUNING, arrange } from "./arrange.js";
import { Level, quant, routeNets } from "./route.js";

export const K = 0.4; // child scale relative to parent
export const PANE_K = 0.55; // code pane scale relative to its node: code is one short zoom away
export const CARD_W = 640; // minimum node width (local units)
export const PAD = 24;
export const GAP = 16; // gap between packed items (parent-local units)
export const BODY_GAP = 18;
export const PANE_W = 820;
export const PANE_HEAD = 34;
export const PANE_LINE = 19;
export const PANE_MAX_VISIBLE = 30;
export const PANE_CONTEXT = 3;
export const PANE_MAX_LINES = 600;
const TEXT_MAX_W = 760;

export const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, "DejaVu Sans", sans-serif';
export const MONO = 'ui-monospace, "SFMono-Regular", Menlo, Consolas, "DejaVu Sans Mono", monospace';

export const STYLES = {
  kind: { size: 13, lh: 20, weight: 600 },
  title: { size: 30, lh: 36, weight: 700 },
  summary: { size: 17, lh: 24, weight: 400 },
  body: { size: 15, lh: 21, weight: 400 },
  label: { size: 12, lh: 22, weight: 700 },
  item: { size: 14, lh: 20, weight: 400 },
  note: { size: 14, lh: 20, weight: 400, italic: true },
  warn: { size: 13, lh: 19, weight: 600 },
};

export const DISPLAYABLE = new Set(["resolved", "heuristic", "lines", "moved", "file"]);

export function fontFor(style, px = 1, family = SANS) {
  return `${style.italic ? "italic " : ""}${style.weight} ${style.size * px}px ${family}`;
}

const KIND_LABEL = {
  system: "System", domain: "Domain", capability: "Capability", responsibility: "Responsibility",
  component: "Component", contract: "Contract", rule: "Rule", resource: "Resource", data: "Data",
  external: "External system", actor: "Actor", test: "Tests", area: "Area",
};
export const EVIDENCE_LABEL = {
  observed: "observed in source", inferred: "inferred", needs_review: "needs review",
  unknown: "unknown", planned: "planned, not implemented",
};
export function kindLabel(kind) {
  return KIND_LABEL[kind] || kind;
}

/** Relationship types and what an arrow from `from` to `to` means for each (legend, filters). */
export const REL_TYPES = [
  { type: "data", label: "data", arrow: "source → destination, labelled with the data" },
  { type: "control", label: "control", arrow: "who starts it → what is started" },
  { type: "dependency", label: "dependency", arrow: "dependent → what it relies on" },
];

/** Short text for a relationship: the data it carries, otherwise its kind or type. */
export function relText(r) {
  if (r.type === "data" && r.label) return r.label;
  return (r.kind || r.type).replace("_", " ");
}

export function wrapText(measure, text, style, maxW) {
  const font = fontFor(style);
  const out = [];
  for (const para of String(text).split(/\n/)) {
    const words = para.split(/\s+/).filter(Boolean);
    let line = "";
    for (const w of words) {
      const cand = line ? line + " " + w : w;
      if (measure(font, cand) <= maxW || !line) {
        if (!line && measure(font, w) > maxW) {
          // hard-break very long tokens (paths, identifiers)
          let chunk = "";
          for (const ch of w) {
            if (measure(font, chunk + ch) > maxW && chunk) { out.push(chunk); chunk = ""; }
            chunk += ch;
          }
          line = chunk;
        } else line = cand;
      } else {
        out.push(line);
        line = w;
      }
    }
    if (line) out.push(line);
  }
  return out.length ? out : [""];
}

function headerBlocks(node, measure, width, refInfo) {
  const blocks = [];
  let y = 0;
  const push = (role, style, lines, extra = {}) => {
    blocks.push({ role, style, lines, y, ...extra });
    y += lines.length * style.lh;
  };
  const meta = [kindLabel(node.kind).toUpperCase()];
  if (node.evidence) meta.push(EVIDENCE_LABEL[node.evidence] || node.evidence);
  if (node.confidence) meta.push(`${node.confidence} confidence`);
  push("kind", STYLES.kind, [meta.join("  ·  ")]);
  y += 2;
  push("title", STYLES.title, wrapText(measure, node.label, STYLES.title, width));
  y += 6;
  if (node.summary) {
    push("summary", STYLES.summary, wrapText(measure, node.summary, STYLES.summary, width));
    y += 6;
  }
  if (node.details) {
    push("details", STYLES.body, wrapText(measure, node.details, STYLES.body, width));
    y += 6;
  }
  if (node.contract) {
    const names = { inputs: "Inputs", outputs: "Outputs", guarantees: "Guarantees",
      errors: "Errors / failures", assumptions: "Assumptions" };
    for (const [key, name] of Object.entries(names)) {
      const items = node.contract[key] || [];
      if (!items.length) continue;
      push("label", STYLES.label, [name.toUpperCase()], { field: key });
      for (const it of items) {
        push("item", STYLES.item, wrapText(measure, "• " + it, STYLES.item, width - 8),
          { indent: 8, field: key });
      }
    }
    y += 6;
  }
  if (node.notes) {
    push("note", STYLES.note, wrapText(measure, "Note: " + node.notes, STYLES.note, width));
    y += 4;
  }
  const broken = (node.refs || []).filter((r) => !DISPLAYABLE.has(refInfo[r.key]?.status));
  for (const r of broken) {
    const st = refInfo[r.key]?.status || "unresolved";
    push("warn", STYLES.warn, wrapText(measure, `⚠ ${st.replace("_", " ")}: ${r.key}`,
      STYLES.warn, width));
  }
  if (node.tags && node.tags.length) {
    push("tags", STYLES.kind, [node.tags.map((t) => "#" + t).join("  ")]);
  }
  // how far the header text reaches (the kind line is drawn with a glyph in front)
  let textW = 0;
  for (const b of blocks) {
    for (const ln of b.lines) textW = Math.max(textW, (b.indent || 0) + measure(fontFor(b.style), ln) +
      (b.role === "kind" ? STYLES.kind.size * 2 : 0));
  }
  return { blocks, height: y, width: textW };
}

export function paneGeometry(ref, res) {
  // Which lines a pane shows and how tall it is (local units).
  const total = res.total_lines || 0;
  const start = Math.max(1, (res.start || 1) - PANE_CONTEXT);
  let end = Math.min(Math.max(total, 1), (res.end || 1) + PANE_CONTEXT);
  let truncated = false;
  if (end - start + 1 > PANE_MAX_LINES) {
    end = start + PANE_MAX_LINES - 1;
    truncated = true;
  }
  const count = end - start + 1;
  const visible = Math.min(count, PANE_MAX_VISIBLE);
  return { start, end, count, truncated, h: PANE_HEAD + visible * PANE_LINE + 10, w: PANE_W };
}

function pack(items, minRowW) {
  // Shelf packing in author order, used for code panes. items: {w, h}.
  if (!items.length) return { w: 0, h: 0 };
  const area = items.reduce((s, it) => s + (it.w + GAP) * (it.h + GAP), 0);
  const widest = Math.max(...items.map((it) => it.w));
  const target = Math.max(minRowW, widest, Math.sqrt(area) * 1.5);
  let x = 0, y = 0, rowH = 0, maxW = 0;
  for (const it of items) {
    if (x > 0 && x + it.w > target) {
      y += rowH + GAP;
      x = 0;
      rowH = 0;
    }
    it.px = x;
    it.py = y;
    x += it.w + GAP;
    rowH = Math.max(rowH, it.h);
    maxW = Math.max(maxW, x - GAP);
  }
  return { w: maxW, h: y + rowH };
}

const ROOT_GAP = 120;
const SIDE_PAD = 10;

function regionGap(weight, root) {
  // wider channels where more connections have to pass
  return root ? clampN(110 + 10 * Math.sqrt(weight), 120, 320) : clampN(30 + 5 * Math.sqrt(weight), 34, 110);
}
const clampN = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * Route a candidate arrangement on its own and score it (lower is better): crossings
 * weigh most, then bends, then length in channel widths. External partners are
 * approximated by ports on the side of the region that faces them.
 */
function scoreArrangement(g, W, anchors, titles) {
  const n = g.pos.length;
  const frame = { w: g.width, h: g.height, ox: 0, oy: 0 };
  const rects = g.pos.map((p, i) => ({ x: p.x, y: p.y, w: g.items[i].w, h: g.items[i].h, c: p.c, r: p.r,
    titleEnd: titles[i] === undefined ? undefined : p.x + titles[i] }));
  const lvl = new Level(frame, g, rects);
  const requests = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) if (W[i * n + j] > 0) requests.push({ src: { item: i }, dst: { item: j } });
    const bySide = new Map();
    for (const a of anchors[i] || []) {
      const horizontal = Math.abs(a.x) >= Math.abs(a.y);
      const side = horizontal ? (a.x > 0 ? "right" : "left") : (a.y > 0 ? "bottom" : "top");
      if (bySide.has(side)) continue;
      const pos = quant(horizontal ? g.height * (0.5 + a.y / 2) : g.width * (0.5 + a.x / 2));
      bySide.set(side, pos);
      requests.push({ src: { item: i }, dst: { port: { side, pos } } });
    }
  }
  if (!requests.length) return 0;
  const routes = lvl.routeAll(requests, requests.length <= 60 ? 1 : 0);
  let bends = 0, length = 0, crossings = 0;
  const segs = [];
  routes.forEach((rt, ri) => {
    if (!rt) { bends += 20; return; }
    for (let i = 1; i < rt.points.length; i++) {
      const [a, b] = [rt.points[i - 1], rt.points[i]];
      length += Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
      segs.push([a, b, ri]);
    }
    bends += rt.points.length - 2;
  });
  return countCrossings(segs) * 4 + bends + length / g.gap * 0.25;
}

/** Proper crossings between horizontal and vertical pieces of different routes. */
export function countCrossings(segs) {
  const eps = 1e-6;
  const H = [], V = [];
  for (const [a, b, r] of segs) {
    if (Math.abs(a[1] - b[1]) < eps) H.push([a[1], Math.min(a[0], b[0]), Math.max(a[0], b[0]), r]);
    else if (Math.abs(a[0] - b[0]) < eps) V.push([a[0], Math.min(a[1], b[1]), Math.max(a[1], b[1]), r]);
  }
  H.sort((p, q) => p[0] - q[0]);
  const ys = H.map((h) => h[0]);
  let crossings = 0;
  for (const [x, y0, y1, r] of V) {
    // horizontals strictly between y0 and y1
    let lo = 0, hi = ys.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (ys[mid] <= y0 + eps) lo = mid + 1; else hi = mid; }
    for (let k = lo; k < H.length && H[k][0] < y1 - eps; k++) {
      const h = H[k];
      if (h[3] !== r && x > h[1] + eps && x < h[2] - eps) crossings++;
    }
  }
  return crossings;
}


/** Try the candidate arrangements and keep the one that routes best. */
function bestArrangement(sizes, W, anchors, titles, opts) {
  const n = sizes.length;
  const pairs = W.reduce((c, w) => c + (w > 0), 0);
  let cands = [{}];
  if (TUNING.candidates && !opts.large && n >= 3 && n <= 120 && pairs <= 600) {
    cands = n <= 30 ? CANDIDATES : CANDIDATES.slice(0, 3); // fewer tries in large regions
  }
  const cache = new Map();
  let best = null;
  for (const tuning of cands) {
    const g = arrange(sizes, W, anchors, { ...opts, tuning, cache });
    if (opts.maxSize && (g.width > opts.maxSize.w + 1e-6 || g.height > opts.maxSize.h + 1e-6)) continue;
    g.items = sizes;
    // regions that keep the screen's proportions are easier to navigate
    // (tolerate ±25%, then scale the score up so the penalty matters for busy regions too)
    const score = cands.length > 1 ? scoreWithAspect(g) : 0;
    if (!best || score < best.score - 1e-9) best = { g, score };
  }
  return best.g;

  function scoreWithAspect(g) {
    // regions that keep the screen's proportions are easier to navigate
    // (tolerate ±25%, then scale the score up so the penalty matters for busy regions too)
    const excess = opts.aspect ? Math.max(0, Math.abs(Math.log(g.width / g.height / opts.aspect)) - Math.log(1.25)) : 0;
    return scoreArrangement(g, W, anchors, titles) * (1 + 2 * excess) + 40 * excess;
  }
}

/**
 * Connections used for placement and routing: the relationships. Flows add none; they
 * are drawn along the relationships their steps use.
 */
export function layoutNets(model) {
  const nets = [];
  const nodes = new Set(model.nodes.map((n) => n.id));
  for (const r of model.relationships) {
    if (nodes.has(r.from) && nodes.has(r.to)) nets.push({ id: r.id, from: r.from, to: r.to, rel: r, w: 1 });
  }
  return nets;
}

/**
 * Compute the layout.
 * @param model normalized map model from the server
 * @param refInfo ref key -> resolution
 * @param measure (font, text) => width in px at that font
 */
export function computeLayout(model, refInfo, measure, opts = {}) {
  const byId = new Map(model.nodes.map((n) => [n.id, n]));
  // candidate search, rip-up and crossing-priced ways out cost time in proportion to the
  // map; only small maps get them
  const large = model.nodes.length > 1500;
  const boxes = new Map();
  const panes = new Map();
  let maxDepth = 0;
  const ancestors = new Map();
  for (const n of model.nodes) {
    const chain = [];
    let cur = n;
    while (cur) { chain.unshift(cur.id); cur = cur.parent != null ? byId.get(cur.parent) : null; }
    ancestors.set(n.id, chain);
  }

  // --- connection weights between siblings, and anchors toward outside partners
  const nets = layoutNets(model);
  const pairW = new Map(); // container id ("" = root) -> Map("a\0b" -> weight)
  const anchorRecs = new Map(); // container id -> [{item, partner (chain or null = header), w}]
  const regionWeight = new Map();
  const addW = (cid, a, b, w) => {
    if (!pairW.has(cid)) pairW.set(cid, new Map());
    const key = a < b ? a + "\u0000" + b : b + "\u0000" + a;
    const m = pairW.get(cid);
    m.set(key, (m.get(key) || 0) + w);
    regionWeight.set(cid, (regionWeight.get(cid) || 0) + w);
  };
  for (const net of nets) {
    const ca = ancestors.get(net.from), cb = ancestors.get(net.to);
    let k = 0;
    while (k < ca.length && k < cb.length && ca[k] === cb[k]) k++;
    const lca = k === 0 ? "" : ca[k - 1];
    if (k < ca.length && k < cb.length) addW(lca, ca[k], cb[k], net.w);
    for (const [chain, other] of [[ca, cb], [cb, ca]]) {
      for (let i = k; i < chain.length - 1; i++) {
        const P = chain[i];
        if (!anchorRecs.has(P)) anchorRecs.set(P, []);
        anchorRecs.get(P).push({ item: chain[i + 1], partner: k < other.length ? other : null, w: net.w });
        regionWeight.set(P, (regionWeight.get(P) || 0) + net.w);
      }
    }
  }
  const weightMatrix = (cid, ids) => {
    const n = ids.length;
    const W = new Float64Array(n * n);
    const m = pairW.get(cid);
    if (!m) return W;
    const idx = new Map(ids.map((id, i) => [id, i]));
    for (const [key, w] of m) {
      const [a, b] = key.split("\u0000");
      const i = idx.get(a), j = idx.get(b);
      if (i === undefined || j === undefined) continue;
      W[i * n + j] += w;
      W[j * n + i] += w;
    }
    return W;
  };

  // --- phase 1: sizes bottom-up, arrangement from internal connections only
  function measureNode(id) {
    const n = byId.get(id);
    const kids = n.children.map((cid) => measureNode(cid));
    const paneItems = [];
    const paneIds = [];
    const seen = new Set();
    for (const r of n.refs || []) {
      const res = refInfo[r.key];
      if (!res || !DISPLAYABLE.has(res.status) || seen.has(r.key)) continue;
      seen.add(r.key);
      const g = paneGeometry(r, res);
      const pid = `${id}::${r.key}`;
      const pane = { id: pid, nodeId: id, ref: r, res, ...g };
      panes.set(pid, pane);
      paneIds.push(pid);
      paneItems.push({ kind: "pane", id: pid, box: pane, w: pane.w * PANE_K, h: pane.h * PANE_K });
    }
    const box = { id, node: n, paneIds, depth: n.depth, x: 0, y: 0, scale: 1, items: [] };
    let gridW = 0, gridH = 0;
    if (kids.length) {
      const sizes = kids.map((b) => ({ w: b.w * K, h: b.h * K }));
      const gap = regionGap(regionWeight.get(id) || 0, false);
      const g = bestArrangement(sizes, weightMatrix(id, n.children), sizes.map(() => null),
        kids.map((b) => (b.grid ? b.titleW * K : undefined)), { gap, aspect: 1.5, large });
      box.grid = g;
      box.gap = gap;
      gridW = g.width;
      gridH = g.height;
    }
    const panesBody = pack(paneItems, CARD_W - 2 * PAD);
    // keep the outermost channel well inside the border so its lines do not hug it
    const sidePad = kids.length ? Math.max(SIDE_PAD, box.gap * 0.35) : SIDE_PAD;
    const w = Math.max(CARD_W, gridW + 2 * sidePad, panesBody.w + 2 * PAD);
    const textW = Math.min(w, TEXT_MAX_W) - 2 * PAD;
    const header = headerBlocks(n, measure, textW, refInfo);
    const headerBottom = PAD + header.height;
    let y = headerBottom + (kids.length ? 4 : paneItems.length ? BODY_GAP : 0);
    if (kids.length) {
      box.frame = { w, h: 0, ox: (w - gridW) / 2, oy: y, headerBottom };
      box.gridSize = { w: gridW, h: gridH };
      y += gridH;
      box.items = kids.map((kb, i) => ({ kind: "node", id: kb.id, box: kb, w: kb.w * K, h: kb.h * K,
        lx: box.frame.ox + box.grid.pos[i].x, ly: y - gridH + box.grid.pos[i].y }));
      if (paneItems.length) y += BODY_GAP / 2;
    }
    for (const it of paneItems) {
      it.lx = PAD + it.px;
      it.ly = y + it.py;
      box.items.push(it);
    }
    if (paneItems.length) y += panesBody.h;
    const h = y + (kids.length && !paneItems.length ? sidePad : PAD);
    // text drawn at small sizes runs up to ~15% wider than measured (glyph hinting)
    Object.assign(box, { w, h, header, headerH: headerBottom, bodyY: headerBottom,
      titleW: Math.min(w, PAD + header.width * 1.15) });
    if (box.frame) box.frame.h = h;
    boxes.set(id, box);
    maxDepth = Math.max(maxDepth, n.depth);
    return box;
  }

  const roots = model.nodes.filter((n) => n.parent == null).map((n) => n.id);
  const rootBoxes = roots.map((id) => measureNode(id));
  const rootGap = regionGap(regionWeight.get("") || 0, true);
  const rootSizes = rootBoxes.map((b) => ({ w: b.w, h: b.h }));
  const rootGrid = bestArrangement(rootSizes, weightMatrix("", roots), rootSizes.map(() => null),
    rootBoxes.map((b) => (b.grid ? b.titleW : undefined)), { gap: rootGap, aspect: 1.6, large });
  rootBoxes.forEach((b, i) => { b.x = rootGrid.pos[i].x; b.y = rootGrid.pos[i].y; b.scale = 1; });
  const root = {
    id: null, depth: -1, w: rootGrid.width, h: rootGrid.height, grid: rootGrid, gap: rootGap,
    frame: { w: rootGrid.width, h: rootGrid.height, ox: 0, oy: 0, headerBottom: 0 },
    childIds: roots, toWorld: (x, y) => [x, y],
  };

  // --- phase 2: top-down, pull items toward the side of their outside partners
  const center = (b) => [b.x + (b.w * b.scale) / 2, b.y + (b.h * b.scale) / 2];
  const containerOrder = [null];
  const queue = [...rootBoxes];
  for (let q = 0; q < queue.length; q++) {
    const box = queue[q];
    if (!box.grid) {
      placeItems(box);
      continue;
    }
    containerOrder.push(box.id);
    const ids = box.node.children;
    const anchors = ids.map(() => null);
    const recs = anchorRecs.get(box.id) || [];
    if (recs.length && opts.anchors !== false) {
      const idx = new Map(ids.map((id, i) => [id, i]));
      const merged = new Map(); // same item and partner region: one anchor with summed weight
      for (const rec of recs) {
        const pid = rec.partner ? rec.partner[Math.min(box.depth, rec.partner.length - 1)] : "^";
        const key = rec.item + "\u0000" + pid;
        if (merged.has(key)) merged.get(key).w += rec.w;
        else merged.set(key, { ...rec });
      }
      const [cx, cy] = center(box);
      for (const rec of merged.values()) {
        let dx = 0, dy = -1; // partner is an ancestor: toward the header
        if (rec.partner) {
          const pid = rec.partner[Math.min(box.depth, rec.partner.length - 1)];
          const [px, py] = center(boxes.get(pid));
          const len = Math.hypot(px - cx, py - cy) || 1;
          // rounded so that last-bit differences cannot flip decisions (e.g. side at 45°)
          dx = Math.round(((px - cx) / len) * 1e6) / 1e6;
          dy = Math.round(((py - cy) / len) * 1e6) / 1e6;
        }
        const i = idx.get(rec.item);
        if (i === undefined) continue;
        if (!anchors[i]) anchors[i] = [];
        anchors[i].push({ x: dx, y: dy, w: rec.w });
      }
      const sizes = ids.map((id) => ({ w: boxes.get(id).w * K, h: boxes.get(id).h * K }));
      const g = bestArrangement(sizes, weightMatrix(box.id, ids), anchors,
        ids.map((id) => (boxes.get(id).grid ? boxes.get(id).titleW * K : undefined)),
        { gap: box.gap, init: box.grid, maxSize: box.gridSize, large });
      // keep the region's size: centre the (possibly smaller) grid in the reserved area
      const ox = box.frame.ox + (box.gridSize.w - g.width) / 2;
      const oy0 = box.frame.oy;
      box.grid = g;
      box.frame.ox = ox;
      box.items.forEach((it, i) => {
        if (it.kind !== "node") return;
        it.lx = ox + g.pos[i].x;
        it.ly = oy0 + g.pos[i].y;
      });
    }
    placeItems(box);
    for (const it of box.items) if (it.kind === "node") queue.push(it.box);
  }

  function placeItems(box) {
    const s = box.scale;
    for (const it of box.items) {
      const x = box.x + it.lx * s, y = box.y + it.ly * s;
      if (it.kind === "node") Object.assign(it.box, { x, y, scale: s * K });
      else Object.assign(it.box, { x, y, scale: s * PANE_K });
    }
  }

  // --- phase 3: an item smaller than its grid cell moves within the cell to where its
  // lines can run straight: level with the partners beside it (and in line with those
  // above or below). Region by region from the top, once everything has a position.
  const rectOf = (id) => { const b = boxes.get(id); return [b.x, b.y, b.x + b.w * b.scale, b.y + b.h * b.scale]; };
  // per item, the lines of its subtree to nodes outside it (not to its own ancestors):
  // [end inside, end outside, weight]
  const leaving = new Map();
  for (const net of nets) {
    const ca = ancestors.get(net.from), cb = ancestors.get(net.to);
    let k = 0;
    while (k < ca.length && k < cb.length && ca[k] === cb[k]) k++;
    for (const [chain, other, a, o] of [[ca, cb, net.from, net.to], [cb, ca, net.to, net.from]]) {
      if (k === other.length) continue; // the other end contains this one
      for (let i = k; i < chain.length; i++) {
        if (!leaving.has(chain[i])) leaving.set(chain[i], []);
        leaving.get(chain[i]).push([a, o, net.w]);
      }
    }
  }
  const shift = (b, dx, dy) => {
    b.x += dx;
    b.y += dy;
    for (const it of b.items || []) if (it.kind === "node") shift(it.box, dx, dy); else Object.assign(it.box, { x: it.box.x + dx, y: it.box.y + dy });
  };
  for (const cid of containerOrder) {
    const C = cid === null ? null : boxes.get(cid);
    const g = C ? C.grid : rootGrid, s = C ? C.scale : 1;
    const [ox, oy] = C ? [C.frame.ox, C.frame.oy] : [0, 0];
    const ids = C ? C.node.children : roots;
    ids.forEach((id, i) => {
      const b = boxes.get(id), { c, r } = g.pos[i];
      const it = C ? C.items[i] : { lx: b.x, ly: b.y };
      const w = C ? it.w : b.w, h = C ? it.h : b.h;
      if (!leaving.has(id)) return;
      const outside = leaving.get(id).map(([a, o, wt]) => [rectOf(a), rectOf(o), wt]);
      // along axis k: the offset within the cell where the most lines can run straight
      const best = (k, lo, hi) => {
        const q = 1 - k; // partners apart along q can be met straight along k
        const lines = outside.filter(([a, o]) => a[q + 2] <= o[q] || o[q + 2] <= a[q]);
        if (!lines.length || hi - lo < 1e-6) return 0;
        // how far a line's ends face each other, up to half the shorter side
        const facing = (d) => lines.reduce((sum, [a, o, wt]) => sum + wt * clampN(Math.min(a[k + 2] + d, o[k + 2]) -
          Math.max(a[k] + d, o[k]), 0, Math.min(a[k + 2] - a[k], o[k + 2] - o[k]) / 2), 0);
        const centred = lines.map(([a, o]) => clampN((o[k] + o[k + 2] - a[k] - a[k + 2]) / 2, lo, hi));
        let d0 = 0, f0 = facing(0);
        for (const d of [lo, hi, ...centred]) {
          const f = facing(d);
          if (f > f0 + 1e-6 || (Math.abs(f - f0) <= 1e-6 && Math.abs(d) < Math.abs(d0))) { d0 = d; f0 = f; }
        }
        return d0;
      };
      // free room in the cell, in world units relative to where the item is now
      const dx = best(0, (ox + g.colX[c] - it.lx) * s, (ox + g.colX[c] + g.colW[c] - w - it.lx) * s);
      const dy = best(1, (oy + g.rowY[r] - it.ly) * s, (oy + g.rowY[r] + g.rowH[r] - h - it.ly) * s);
      if (!dx && !dy) return;
      if (C) { it.lx += dx / s; it.ly += dy / s; }
      shift(b, dx, dy);
    });
  }

  // --- routing levels
  const mkLevel = (C, childIds, childBoxes, scaleK) => {
    C.itemIndex = new Map(childIds.map((id, i) => [id, i]));
    const rects = childBoxes.map((b, i) => {
      const it = C === root ? { lx: b.x, ly: b.y } : C.items[i];
      return { id: b.id, x: it.lx, y: it.ly, w: b.w * scaleK, h: b.h * scaleK, c: C.grid.pos[i].c, r: C.grid.pos[i].r,
        titleEnd: b.grid ? it.lx + b.titleW * scaleK : undefined };
    });
    // things drawn in a region that lines must avoid: its header text and its code panes
    const blocked = C === root ? [] : [{ x: 0, y: 0, w: C.titleW, h: C.frame.headerBottom },
      ...C.items.filter((it) => it.kind === "pane").map((it) => ({ x: it.lx, y: it.ly, w: it.w, h: it.h }))];
    const world = C === root ? { x: 0, y: 0, scale: 1 } : { x: C.x, y: C.y, scale: C.scale };
    C.level = new Level(C.frame, C.grid, rects, blocked, world, C === root ? undefined : C.titleW);
    C.level.id = C === root ? null : C.id;
    C.level.depth = C.depth;
  };
  mkLevel(root, roots, rootBoxes, 1);
  for (const b of boxes.values()) {
    if (!b.grid) continue;
    b.toWorld = (x, y) => [b.x + x * b.scale, b.y + y * b.scale];
    mkLevel(b, b.node.children, b.node.children.map((id) => boxes.get(id)), K);
  }
  const L = { boxes, root, ancestors, containerOrder };
  const routes = routeNets(L, nets, { thorough: !large });

  let minScale = 1;
  for (const b of boxes.values()) minScale = Math.min(minScale, b.scale);
  for (const p of panes.values()) minScale = Math.min(minScale, p.scale);
  return {
    boxes, panes, roots, ancestors, maxDepth, minScale, root, routes, nets,
    bounds: { x: 0, y: 0, w: Math.max(root.w, 1), h: Math.max(root.h, 1) },
  };
}

export function worldRect(box) {
  return { x: box.x, y: box.y, w: box.w * box.scale, h: box.h * box.scale };
}

export function headerRect(box) {
  return { x: box.x, y: box.y, w: box.w * box.scale, h: (box.headerH + 6) * box.scale };
}
