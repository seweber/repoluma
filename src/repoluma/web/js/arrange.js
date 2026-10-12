// Connection-driven placement of the items inside one region.
//
// Pipeline (after HOLA, Kieffer et al. 2016, adapted to nested regions):
//   1. stress majorization gives neutral positions that reflect graph distance,
//      with optional anchors pulling items toward the side of external partners;
//   2. positions are snapped to the cells of a grid (optimal assignment);
//   3. local search swaps cells to shorten weighted route length and avoid bends;
//   4. empty rows/columns are removed and real cell sizes computed.
// Everything is deterministic: the same input gives the same arrangement.

// Tunable weights; the defaults were chosen with bench/layout_eval.py.
export const TUNING = {
  bend: 0.8, // cost of a two-bend route between non-neighbours, in cell units
  anchor: 0.6, // pull toward the side of outside partners
  slack: true, // allow an extra row in larger regions
  stress: true, // stress-majorization start (else author order)
  align: "top", // items sit at the top of their grid row ("center" to centre them)
  candidates: true, // try several arrangements per region and keep the best routed one
};

/** Arrangement variants tried per region (see layout.js: bestArrangement). */
export const CANDIDATES = [
  {},
  { bend: 0.3 },
  { bend: 2 },
  { stress: false },
  { stress: false, bend: 0.3 },
  { slack: false },
  { anchor: 0 },
  { anchor: 1.5 },
];

/**
 * @param {{w:number,h:number}[]} items sizes in region-local units
 * @param {Float64Array} W symmetric n*n weights (number of relationships between items)
 * @param {Array<Array<{x:number,y:number,w:number}>|null>} anchors per item: unit
 *   directions (x right, y down) toward external partners, with weights
 * @param {object} opts {gap, aspect, maxSize: {w,h} | undefined, init: Int32Array | undefined}
 */
export function arrange(items, W, anchors, opts) {
  const n = items.length;
  const gap = opts.gap;
  const T = opts.tuning ? { ...TUNING, ...opts.tuning } : TUNING;
  if (n === 0) return geometry(items, { cols: 0, rows: 0, cell: new Int32Array(0) }, gap);
  const adj = adjacency(W, n);
  let grid;
  if (opts.init && opts.init.cell.length === n) {
    grid = { cols: opts.init.cols, rows: opts.init.rows, cell: Int32Array.from(opts.init.cell) };
  } else {
    const [cols, rows] = gridShape(items, gap, opts.aspect || 1.5, T);
    let pos = null;
    if (n > 2 && T.stress) {
      // candidates share the same stress solution for the same grid shape
      const key = cols + "x" + rows;
      if (opts.cache && opts.cache.has(key)) pos = opts.cache.get(key);
      else {
        pos = stress(n, adj, anchors, cols, rows);
        if (opts.cache) opts.cache.set(key, pos);
      }
    }
    grid = { cols, rows, cell: pos ? snap(pos, cols, rows) : Int32Array.from({ length: n }, (_, i) => i) };
  }
  refine(items, grid, adj, anchors, gap, opts.maxSize, T);
  return geometry(items, compact(grid), gap);
}

function adjacency(W, n) {
  const adj = Array.from({ length: n }, () => []);
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const w = W[i * n + j];
      if (w > 0) {
        adj[i].push([j, w]);
        adj[j].push([i, w]);
      }
    }
  }
  return adj;
}

function gridShape(items, gap, aspect, T) {
  const n = items.length;
  const avgW = items.reduce((s, it) => s + it.w, 0) / n + gap;
  const avgH = items.reduce((s, it) => s + it.h, 0) / n + gap;
  let best = [n, 1], bestScore = Infinity;
  for (let cols = 1; cols <= n; cols++) {
    const rows = Math.ceil(n / cols);
    // leave a little slack in larger regions so the arrangement has room to move
    const slackRows = T.slack && n >= 6 && rows * cols - n < Math.ceil(n / 8) ? rows + 1 : rows;
    const score = Math.abs(Math.log((cols * avgW) / (slackRows * avgH) / aspect)) +
      0.02 * (slackRows * cols - n);
    if (score < bestScore) { bestScore = score; best = [cols, slackRows]; }
  }
  return best;
}

// ------------------------------------------------------------------- stress

function stress(n, adj, anchors, cols, rows) {
  // graph distances (unweighted BFS); disconnected pairs get a long ideal distance
  const D = new Float64Array(n * n).fill(Infinity);
  let dmax = 1;
  for (let s = 0; s < n; s++) {
    D[s * n + s] = 0;
    const queue = [s];
    for (let q = 0; q < queue.length; q++) {
      const u = queue[q];
      for (const [v] of adj[u]) {
        if (D[s * n + v] === Infinity) {
          D[s * n + v] = D[s * n + u] + 1;
          dmax = Math.max(dmax, D[s * n + v]);
          queue.push(v);
        }
      }
    }
  }
  const far = Math.max(dmax + 1, Math.sqrt(n));
  for (let i = 0; i < n * n; i++) if (D[i] === Infinity) D[i] = far;
  // initial positions: author order on the grid, deterministic
  const X = new Float64Array(n), Y = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    X[i] = (i % cols) + 0.01 * Math.sin(i * 12.9898);
    Y[i] = Math.floor(i / cols) + 0.01 * Math.cos(i * 78.233);
  }
  // anchor points sit outside the grid in the partner's direction
  const R = Math.max(cols, rows) / 2 + 1;
  const cx = (cols - 1) / 2, cy = (rows - 1) / 2;
  const anc = anchors.map((list) => (list || []).map((a) => ({
    x: cx + a.x * R, y: cy + a.y * R, w: a.w,
  })));
  for (let iter = 0; iter < 200; iter++) {
    let moved = 0;
    for (let i = 0; i < n; i++) {
      let sx = 0, sy = 0, sw = 0;
      for (let j = 0; j < n; j++) {
        if (j === i) continue;
        const d = D[i * n + j];
        const w = 1 / (d * d);
        const dx = X[i] - X[j], dy = Y[i] - Y[j];
        const dist = Math.hypot(dx, dy) || 1e-6;
        sx += w * (X[j] + (d * dx) / dist);
        sy += w * (Y[j] + (d * dy) / dist);
        sw += w;
      }
      for (const a of anc[i]) {
        const w = 0.5 * a.w;
        const dx = X[i] - a.x, dy = Y[i] - a.y;
        const dist = Math.hypot(dx, dy) || 1e-6;
        sx += w * (a.x + (1 * dx) / dist);
        sy += w * (a.y + (1 * dy) / dist);
        sw += w;
      }
      const nx = sx / sw, ny = sy / sw;
      moved = Math.max(moved, Math.abs(nx - X[i]) + Math.abs(ny - Y[i]));
      X[i] = nx;
      Y[i] = ny;
    }
    if (moved < 1e-4) break;
  }
  return { X, Y };
}

function snap(pos, cols, rows) {
  const n = pos.X.length;
  const norm = (A, k) => {
    let lo = Infinity, hi = -Infinity;
    for (const v of A) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const span = hi - lo || 1;
    return Array.from(A, (v) => ((v - lo) / span) * (k - 1));
  };
  const xs = norm(pos.X, cols), ys = norm(pos.Y, rows);
  const m = cols * rows;
  const cost = (i, c) => {
    const dx = xs[i] - (c % cols), dy = ys[i] - Math.floor(c / cols);
    return dx * dx + dy * dy;
  };
  return hungarian(n, m, cost);
}

/** Minimum-cost assignment of n rows to m >= n columns (Kuhn–Munkres, O(n^2 m)). */
export function hungarian(n, m, cost) {
  const INF = 1e18;
  const u = new Float64Array(n + 1), v = new Float64Array(m + 1);
  const p = new Int32Array(m + 1), way = new Int32Array(m + 1);
  for (let i = 1; i <= n; i++) {
    p[0] = i;
    let j0 = 0;
    const minv = new Float64Array(m + 1).fill(INF);
    const used = new Uint8Array(m + 1);
    do {
      used[j0] = 1;
      const i0 = p[j0];
      let delta = INF, j1 = 0;
      for (let j = 1; j <= m; j++) {
        if (used[j]) continue;
        const cur = cost(i0 - 1, j - 1) - u[i0] - v[j];
        if (cur < minv[j]) { minv[j] = cur; way[j] = j0; }
        if (minv[j] < delta) { delta = minv[j]; j1 = j; }
      }
      for (let j = 0; j <= m; j++) {
        if (used[j]) { u[p[j]] += delta; v[j] -= delta; } else minv[j] -= delta;
      }
      j0 = j1;
    } while (p[j0] !== 0);
    do {
      const j1 = way[j0];
      p[j0] = p[j1];
      j0 = j1;
    } while (j0);
  }
  const cell = new Int32Array(n);
  for (let j = 1; j <= m; j++) if (p[j]) cell[p[j] - 1] = j - 1;
  return cell;
}

// ------------------------------------------------------------- local search

function refine(items, grid, adj, anchors, gap, maxSize, T) {
  const n = items.length;
  const { cols, rows } = grid;
  const m = cols * rows;
  const occ = new Int32Array(m).fill(-1);
  for (let i = 0; i < n; i++) occ[grid.cell[i]] = i;
  const cw = items.reduce((s, it) => s + it.w, 0) / n + gap;
  const ch = items.reduce((s, it) => s + it.h, 0) / n + gap;
  const cx0 = (cols - 1) / 2, cy0 = (rows - 1) / 2;
  const pairCost = (ci, cj) => {
    const dc = Math.abs((ci % cols) - (cj % cols));
    const dr = Math.abs(Math.floor(ci / cols) - Math.floor(cj / cols));
    // neighbours connect straight through the channel between them; anything else bends twice
    const bends = dc + dr === 1 ? 0 : T.bend * (cw + ch);
    return dc * cw + dr * ch + bends;
  };
  const anchorCost = (i, c) => {
    const list = anchors[i];
    if (!list) return 0;
    const col = c % cols, row = Math.floor(c / cols);
    let s = 0;
    for (const a of list) {
      // distance to the grid edge in the partner's direction
      const tx = cx0 + a.x * (cols / 2 + 0.5), ty = cy0 + a.y * (rows / 2 + 0.5);
      s += a.w * T.anchor * (Math.abs(col - tx) * cw + Math.abs(row - ty) * ch);
    }
    return s;
  };
  const itemCost = (i, c, skip) => {
    let s = anchorCost(i, c);
    for (const [j, w] of adj[i]) if (j !== skip) s += w * pairCost(c, grid.cell[j]);
    return s;
  };
  const fits = maxSize ? () => {
    const g = geometry(items, compact({ cols, rows, cell: grid.cell }), gap);
    return g.width <= maxSize.w + 1e-6 && g.height <= maxSize.h + 1e-6;
  } : () => true;
  for (let pass = 0; pass < 40; pass++) {
    let improved = false;
    for (let a = 0; a < m; a++) {
      for (let b = a + 1; b < m; b++) {
        const i = occ[a], j = occ[b];
        if (i < 0 && j < 0) continue;
        let before = 0, after = 0;
        if (i >= 0) { before += itemCost(i, a, j); after += itemCost(i, b, j); }
        if (j >= 0) { before += itemCost(j, b, i); after += itemCost(j, a, i); }
        // the i–j pair keeps its distance under a swap, so it cancels out
        if (after < before - 1e-9) {
          if (i >= 0) grid.cell[i] = b;
          if (j >= 0) grid.cell[j] = a;
          if (!fits()) {
            if (i >= 0) grid.cell[i] = a;
            if (j >= 0) grid.cell[j] = b;
            continue;
          }
          occ[a] = j;
          occ[b] = i;
          improved = true;
        }
      }
    }
    if (!improved) break;
  }
}

// -------------------------------------------------------------- geometry

function compact(grid) {
  const { cols, rows, cell } = grid;
  const usedC = new Uint8Array(cols), usedR = new Uint8Array(rows);
  for (const c of cell) { usedC[c % cols] = 1; usedR[Math.floor(c / cols)] = 1; }
  const mapC = [], mapR = [];
  let nc = 0, nr = 0;
  for (let c = 0; c < cols; c++) mapC[c] = usedC[c] ? nc++ : -1;
  for (let r = 0; r < rows; r++) mapR[r] = usedR[r] ? nr++ : -1;
  const out = new Int32Array(cell.length);
  cell.forEach((c, i) => { out[i] = mapR[Math.floor(c / cols)] * nc + mapC[c % cols]; });
  return { cols: nc, rows: nr, cell: out };
}

/** Real geometry: column widths/row heights from contents, items centred in cells. */
export function geometry(items, grid, gap) {
  const { cols, rows, cell } = grid;
  const colW = new Array(cols).fill(0), rowH = new Array(rows).fill(0);
  items.forEach((it, i) => {
    const c = cell[i] % cols, r = Math.floor(cell[i] / cols);
    colW[c] = Math.max(colW[c], it.w);
    rowH[r] = Math.max(rowH[r], it.h);
  });
  const colX = [], rowY = [];
  let x = gap;
  for (let c = 0; c < cols; c++) { colX.push(x); x += colW[c] + gap; }
  let y = gap;
  for (let r = 0; r < rows; r++) { rowY.push(y); y += rowH[r] + gap; }
  const pos = items.map((it, i) => {
    const c = cell[i] % cols, r = Math.floor(cell[i] / cols);
    const top = TUNING.align === "top";
    return { x: colX[c] + (colW[c] - it.w) / 2, y: rowY[r] + (top ? 0 : (rowH[r] - it.h) / 2), c, r };
  });
  return { cols, rows, cell, colX, colW, rowY, rowH, gap, pos,
    width: cols ? x : 0, height: rows ? y : 0 };
}
