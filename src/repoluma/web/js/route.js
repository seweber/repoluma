// Orthogonal routing of connections between nested boxes.
//
// Principles (each applies everywhere; there are no special cases per situation):
//   * Obstacles: a line never passes through anything drawn (boxes, header text, code
//     panes) and keeps a fixed clearance from it. Region borders are not obstacles.
//   * Endpoints are box sides, not points: a line may touch a side anywhere within the
//     side's usable range.
//   * Shapes: within one region, a line takes the cheapest orthogonal shape that fits
//     through free space (straight, L or Z), scored by length, bends, crossings with lines
//     already placed and short jogs; a bend-penalised path through the grid channels is
//     the fallback when nothing simpler fits.
//   * Inside first: for a connection between items in different regions, the ways of
//     leaving every enclosing region are computed from the inside out (bottom-up), each
//     with its cost and either a free range along the border (when the inside path is
//     straight) or a fixed point. The region containing both ends then picks the cheapest
//     combination, so a border crossing is never fixed before the inside is known.
//   * Rip-up: lines are placed one after another; afterwards a line that crosses others is
//     placed again with all others known, and keeps the new way if it crosses fewer.
//   * Spacing: ports and border crossings are claimed as lines are placed, keeping lines
//     at least `sep` apart by construction; finally, parallel pieces sharing a free
//     corridor are ordered by where they turn off (which avoids crossings) and spaced.
// Every connection is its own line; only connections with identical endpoints share one.

const SIDES = ["right", "left", "bottom", "top"];
const OUT = { right: [1, 0], left: [-1, 0], bottom: [0, 1], top: [0, -1] };

// Routing compares coordinates for equality; rounding to a fixed precision keeps
// last-bit floating-point noise from changing decisions.
const QS = 1e4; // quantization steps per unit
export const quant = (v) => Math.round(v * QS) / QS;
const EPS = 1e-6;
const SATURATED = 400; // lines in one region beyond which crossings are no longer priced
const BUSY = 100; // lines in one region beyond which ways out are not split by the lines they cross

/** Distance kept from a box's corners when a line attaches to its side. */
export const sideInset = (len, gap) => Math.min(len * 0.2, gap * 0.3);

const horizontal = (O) => O.dir[0] !== 0; // a line leaving O starts horizontally
const alongOf = (O, p) => (horizontal(O) ? p[1] : p[0]);
const pointOn = (O, c) => (horizontal(O) ? [O.line, c] : [c, O.line]);

export class Level {
  /**
   * The routing space of one region (or of the top-level canvas), in its local units.
   * @param frame {w, h, ox, oy}: region size and grid origin
   * @param grid geometry from arrange.js (rows, columns, gap)
   * @param rects child item rects [{x, y, w, h, c, r, sides, id}]
   * @param blocked other things drawn here that lines must avoid (header text, panes)
   * @param world {x, y, scale}: where this region's origin is in world units
   * @param titleEnd where the region's title text ends along its top edge (crossings go beside it)
   */
  constructor(frame, grid, rects, blocked = [], world = { x: 0, y: 0, scale: 1 }, titleEnd = undefined) {
    this.frame = { ...frame, w: quant(frame.w), h: quant(frame.h) };
    this.grid = grid;
    this.gap = grid.gap;
    this.rects = rects.map((R) => ({ ...R, x: quant(R.x), y: quant(R.y), w: quant(R.w), h: quant(R.h) }));
    this.blocked = blocked.map((R) => ({ x: quant(R.x), y: quant(R.y), w: quant(R.w), h: quant(R.h) }));
    this.world = world;
    this.indexObstacles();
    this.clear = this.gap * 0.25; // distance kept from things a line does not connect to
    // the region's own top edge can be crossed beside its title
    this.top = titleEnd === undefined ? null : [titleEnd + this.clear, this.frame.w - this.clear];
    this.sep = this.gap * 0.2; // minimum distance between parallel lines
    this.bendCost = this.gap * 1.5;
    this.crossCost = this.bendCost * 2 + this.gap; // above the two bends that pass a line instead
    const { cols, rows, colX, colW, rowY, rowH } = grid;
    const f = this.frame;
    this.vx = [];
    for (let c = 0; c <= cols; c++) {
      this.vx.push(quant(f.ox + (c < cols ? colX[c] - this.gap / 2 : colX[cols - 1] + colW[cols - 1] + this.gap / 2)));
    }
    this.hy = [];
    for (let r = 0; r <= rows; r++) {
      this.hy.push(quant(f.oy + (r < rows ? rowY[r] - this.gap / 2 : rowY[rows - 1] + rowH[rows - 1] + this.gap / 2)));
    }
  }

  /** Bucket grid over everything drawn here: rects are 0..n-1, blocked n..; see obstaclesIn. */
  indexObstacles() {
    const all = [...this.rects, ...this.blocked];
    const area = all.reduce((a, R) => a + R.w * R.h, 0);
    this.cell = Math.max(this.gap, Math.sqrt(area / Math.max(1, all.length)) * 2);
    this.buckets = new Map();
    all.forEach((R, k) => {
      for (let i = Math.floor(R.x / this.cell); i <= Math.floor((R.x + R.w) / this.cell); i++) {
        for (let j = Math.floor(R.y / this.cell); j <= Math.floor((R.y + R.h) / this.cell); j++) {
          const key = i * 65536 + j;
          if (!this.buckets.has(key)) this.buckets.set(key, []);
          this.buckets.get(key).push(k);
        }
      }
    });
  }

  /** Indices (ascending) of obstacles whose rect may come within the box x0..x1, y0..y1. */
  obstaclesIn(x0, y0, x1, y1) {
    const found = new Set();
    for (let i = Math.floor(x0 / this.cell); i <= Math.floor(x1 / this.cell); i++) {
      for (let j = Math.floor(y0 / this.cell); j <= Math.floor(y1 / this.cell); j++) {
        for (const k of this.buckets.get(i * 65536 + j) || []) found.add(k);
      }
    }
    return [...found].sort((a, b) => a - b);
  }

  obstacle(k) {
    return k < this.rects.length ? this.rects[k] : this.blocked[k - this.rects.length];
  }

  // ------------------------------------------------------------- endpoints

  /** The ways of leaving child item i: one option per allowed side. */
  itemOptions(i) {
    const R = this.rects[i];
    const out = [];
    for (const side of SIDES) {
      const vertical = side === "left" || side === "right";
      const lo0 = vertical ? R.y : R.x, len = vertical ? R.h : R.w;
      const inset = sideInset(len, this.gap);
      let lo = lo0 + inset;
      // a region's title sits along its top edge: lines meet that edge beside the title
      if (side === "top" && R.titleEnd !== undefined) lo = Math.max(lo, R.titleEnd + this.clear);
      if (lo > lo0 + len - inset) continue;
      const line = side === "right" ? R.x + R.w : side === "left" ? R.x : side === "bottom" ? R.y + R.h : R.y;
      out.push({ side, line, lo, hi: lo0 + len - inset, dir: OUT[side], cost: 0,
        item: i, box: R.id, level: this, expand: () => [] });
    }
    return out;
  }

  /** A crossing on this region's own border; a line arriving there heads inward. */
  borderOption(side) {
    const f = this.frame;
    const vertical = side === "left" || side === "right";
    const m = this.clear;
    let lo = m, hi = (vertical ? f.h : f.w) - m;
    if (side === "top") {
      if (!this.top) return null;
      [lo, hi] = this.top;
    }
    if (hi < lo) return null;
    const line = side === "right" ? f.w : side === "left" ? 0 : side === "bottom" ? f.h : 0;
    const [dx, dy] = OUT[side];
    return { side, line, lo, hi, dir: [-dx, -dy], cost: 0, border: true, level: this, expand: () => [] };
  }

  // ---------------------------------------------------------------- shapes

  /**
   * Candidate polylines from option P to option Q. Each comes with the coordinates used on
   * P and Q; a straight line between two ranges keeps its free range so that callers can
   * still choose where exactly it runs.
   */
  shapes(P, Q) {
    const out = [];
    const hP = horizontal(P), hQ = horizontal(Q);
    const clamp = (O, v) => Math.min(O.hi, Math.max(O.lo, v));
    if (hP === hQ && P.dir[0] + Q.dir[0] === 0 && P.dir[1] + Q.dir[1] === 0) {
      // facing each other along one axis
      if ((Q.line - P.line) * (P.dir[0] + P.dir[1]) <= EPS) return out;
      for (const [lo, hi] of this.freeSpans(P, Q)) {
        const c = (lo + hi) / 2;
        out.push({ bends: 0, pts: [pointOn(P, c), pointOn(Q, c)], span: [lo, hi] });
      }
      const a = clamp(P, (Q.lo + Q.hi) / 2), b = clamp(Q, a);
      const a0 = clamp(P, Q.lo > P.hi ? Q.lo : Q.hi), b0 = clamp(Q, a0);
      const zs = (mids) => {
        const zout = [];
        for (const [pa, qb] of [[a0, b0], [a, b]]) {
          if (Math.abs(pa - qb) < EPS) continue; // that would be a straight line
          for (const mid of mids) {
            zout.push({ bends: 2, pts: hP ? [[P.line, pa], [mid, pa], [mid, qb], [Q.line, qb]]
              : [[pa, P.line], [pa, mid], [qb, mid], [qb, Q.line]] });
          }
        }
        return zout;
      };
      out.push(...zs([0.5, 0.35, 0.65, 0.2, 0.8].map((t) => P.line + (Q.line - P.line) * t)));
      // more Zs, passing beside lines the best path so far crosses (see connect)
      out.push({ bends: 2, more: (lines) => zs(this.besidePlaced(P, Q, lines)) });
    } else if (hP !== hQ) {
      // one leaves horizontally, the other vertically: an L with its corner in front of both
      const H = hP ? P : Q, V = hP ? Q : P;
      for (const cy of uniq([clamp(H, V.line), clamp(H, (H.lo + H.hi) / 2)])) {
        for (const cx of uniq([clamp(V, H.line), clamp(V, (V.lo + V.hi) / 2)])) {
          if ((cx - H.line) * H.dir[0] < this.clear * 0.5 || (cy - V.line) * V.dir[1] < this.clear * 0.5) continue;
          const pts = [[H.line, cy], [cx, cy], [cx, V.line]];
          if (!hP) pts.reverse();
          out.push({ bends: 1, pts });
        }
      }
    }
    return out;
  }

  /**
   * Where the middle piece of a Z between facing options P and Q can run right beside a
   * parallel piece of one of `lines` (sep away, on either side), so that it passes that
   * line instead of crossing it; the nearest few to the middle.
   */
  besidePlaced(P, Q, lines) {
    const h = horizontal(P), k = h ? 0 : 1; // the middle piece runs across axis k
    const a = Math.min(P.line, Q.line) + this.clear, b = Math.max(P.line, Q.line) - this.clear;
    if (b < a) return [];
    const lo = Math.min(P.lo, Q.lo), hi = Math.max(P.hi, Q.hi);
    const out = [];
    for (const { points: p } of lines) {
      for (let i = 1; i < p.length; i++) {
        if (Math.abs(p[i][k] - p[i - 1][k]) > EPS) continue; // not parallel to the middle piece
        const c0 = Math.min(p[i][1 - k], p[i - 1][1 - k]), c1 = Math.max(p[i][1 - k], p[i - 1][1 - k]);
        if (c1 <= lo || c0 >= hi) continue;
        for (const m of [p[i][k] - this.sep, p[i][k] + this.sep]) if (m >= a && m <= b) out.push(m);
      }
    }
    const centre = (P.line + Q.line) / 2;
    return uniq(out).sort((x, y) => Math.abs(x - centre) - Math.abs(y - centre)).slice(0, 6);
  }

  /** Sub-ranges of the overlap of P and Q where a straight line between them is free. */
  freeSpans(P, Q) {
    let spans = [[Math.max(P.lo, Q.lo), Math.min(P.hi, Q.hi)]];
    if (spans[0][1] < spans[0][0]) return [];
    const h = horizontal(P);
    const a = Math.min(P.line, Q.line), b = Math.max(P.line, Q.line);
    const cut = (R, pad) => {
      const r0 = h ? R.x : R.y, r1 = h ? R.x + R.w : R.y + R.h; // extent along the line
      if (r1 <= a + EPS || r0 >= b - EPS) return;
      const s0 = (h ? R.y : R.x) - pad, s1 = (h ? R.y + R.h : R.x + R.w) + pad; // across it
      spans = spans.flatMap(([lo, hi]) => {
        if (s1 <= lo || s0 >= hi) return [[lo, hi]];
        return [[lo, Math.min(hi, s0)], [Math.max(lo, s1), hi]].filter(([x, y]) => y - x > -EPS);
      });
    };
    const [lo, hi] = spans[0], m = this.clear;
    const ks = h ? this.obstaclesIn(a, lo - m, b, hi + m) : this.obstaclesIn(lo - m, a, hi + m, b);
    const n = this.rects.length;
    for (const k of ks) if (k !== P.item && k !== Q.item) cut(this.obstacle(k), k < n ? m : m * 0.5);
    return spans;
  }

  /**
   * Ranges of positions on `border` that option io can reach with a straight line or an L
   * through free space. Each: {bends, lo, hi, cost, io, at(c) -> io coordinate, pts(c)}.
   */
  exitRanges(io, border, ctx) {
    const out = [];
    const s = this.world.scale;
    const hI = horizontal(io), hB = horizontal(border);
    if (hI === hB) {
      if (io.dir[0] + border.dir[0] !== 0 || io.dir[1] + border.dir[1] !== 0) return out;
      // straight: anywhere both ranges overlap with nothing in between
      for (const [lo, hi] of this.freeSpans(io, border)) {
        out.push({ bends: 0, lo, hi, io, cost: io.cost + s * Math.abs(border.line - io.line),
          at: (c) => c, pts: (c) => [pointOn(io, c), pointOn(border, c)] });
      }
      return out;
    }
    // an L: leave io at `a`, turn at the crossing coordinate c, run to the border.
    // Work in a frame where io leaves vertically (swap axes if it leaves horizontally).
    const sw = hI ? ([x, y]) => [y, x] : (p) => p;
    const rect = (R) => (hI ? { x: R.y, y: R.x, w: R.h, h: R.w } : R);
    const dy = hI ? io.dir[0] : io.dir[1];
    const bl = border.line;
    const a0 = Math.min(io.hi, Math.max(io.lo, bl)); // as close to the border as io allows
    const pad = this.clear;
    const f = this.frame;
    // where a0 is claimed, leaving on the one or the other side of that line are different
    // topologies: one L for each
    for (const a of ctx.spots([io], [io.lo, io.hi], a0)) {
      let near = io.line + dy * pad; // the turn must lie beyond io's side
      let far = dy > 0 ? Infinity : -Infinity; // ... and before anything in the way of the first piece
      const blocks = [];
      const x0 = Math.min(a, bl), x1 = Math.max(a, bl);
      // everything that matters lies in the strip both pieces can occupy (swapped frame)
      const u0 = Math.min(a, x0) - pad, u1 = Math.max(a, x1) + pad;
      const strip = hI ? [-Infinity, u0, Infinity, u1] : [u0, -Infinity, u1, Infinity];
      const ks = this.obstaclesIn(Math.max(strip[0], 0), Math.max(strip[1], 0), Math.min(strip[2], f.w), Math.min(strip[3], f.h));
      for (const R of ks.filter((k) => k !== io.item).map((k) => rect(this.obstacle(k)))) {
        if (R.x - pad < a && a < R.x + R.w + pad) {
          // the first piece runs along x = a: stop before R if R lies ahead
          if (dy < 0 && R.y + R.h <= io.line + EPS) far = Math.max(far, R.y + R.h + pad);
          if (dy > 0 && R.y >= io.line - EPS) far = Math.min(far, R.y - pad);
        }
        if (R.x - pad < x1 && x0 < R.x + R.w + pad) blocks.push([R.y - pad, R.y + R.h + pad]);
      }
      let lo = Math.max(border.lo, Math.min(near, far)), hi = Math.min(border.hi, Math.max(near, far));
      if (hi < lo) continue;
      // remove positions where the second piece would hit something; keep the part nearest io
      let spans = [[lo, hi]];
      for (const [b0, b1] of blocks) {
        spans = spans.flatMap(([u, v]) => (b1 <= u || b0 >= v ? [[u, v]] : [[u, Math.min(v, b0)], [Math.max(u, b1), v]]))
          .filter(([u, v]) => v - u > -EPS);
      }
      if (!spans.length) continue;
      const span = dy < 0 ? spans[spans.length - 1] : spans[0];
      const ref = dy < 0 ? span[1] : span[0];
      out.push({ bends: 1, lo: span[0], hi: span[1], io,
        cost: io.cost + s * (this.bendCost + Math.abs(ref - io.line) + Math.abs(bl - a)),
        at: () => a, pts: (c) => [sw([a, io.line]), sw([a, c]), sw([bl, c])] });
    }
    return out;
  }

  /**
   * The border positions a path's last piece can move to (see slideTo) while it and the
   * piece before it stay free: the stretch of that piece's line free of obstacles, cut to
   * where a straight run from it to the border is free. [c, c] when it cannot move.
   * Clearances are those of `free`; `skip` is the item the path starts at.
   */
  slideRange(pts, border, c, skip) {
    const n = pts.length;
    if (n < 3) return [c, c];
    const h = horizontal(border); // the last piece runs across the border's axis
    const k = h ? 1 : 0; // coordinate along the border
    const before = pts[n - 3], prev = pts[n - 2];
    const m = h ? prev[0] : prev[1]; // where the piece before runs, parallel to the border
    const f = this.frame;
    let lo = border.lo, hi = border.hi;
    const from = before[k], nr = this.rects.length;
    // the piece before extends along its line until something lies across it
    const ks = h ? this.obstaclesIn(m - this.clear, 0, m + this.clear, f.h) : this.obstaclesIn(0, m - this.clear, f.w, m + this.clear);
    for (const q of ks) {
      const R = this.obstacle(q);
      // the path's own box only bounds the piece that leaves it
      const pad = q >= nr ? this.clear * 0.5 : q === skip ? (n === 3 ? 0 : this.clear) : this.clear;
      const a0 = h ? R.x : R.y, a1 = h ? R.x + R.w : R.y + R.h; // across the piece
      if (a1 + pad <= m || a0 - pad >= m) continue;
      const b0 = (h ? R.y : R.x) - pad, b1 = (h ? R.y + R.h : R.x + R.w) + pad; // along it
      if (b1 <= from) lo = Math.max(lo, b1);
      else if (b0 >= from) hi = Math.min(hi, b0);
      else return [c, c];
    }
    if (hi < lo) return [c, c];
    const proxy = { line: m, lo, hi, dir: [-border.dir[0], -border.dir[1]] };
    const span = this.freeSpans(proxy, border).find(([a, b]) => a <= c + EPS && c <= b + EPS);
    return span || [c, c];
  }

  /** Does a polyline keep clear of everything except the boxes it connects? */
  free(pts, skipA, skipB) {
    const m = this.clear;
    const f = this.frame;
    for (let i = 1; i < pts.length; i++) {
      const [a, b] = [pts[i - 1], pts[i]];
      const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]);
      const y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
      if (x0 < -EPS || y0 < -EPS || x1 > f.w + EPS || y1 > f.h + EPS) return false;
      const first = i === 1, last = i === pts.length - 1;
      const hit = (R, pad) => x1 > R.x - pad + EPS && x0 < R.x + R.w + pad - EPS &&
        y1 > R.y - pad + EPS && y0 < R.y + R.h + pad - EPS;
      const n = this.rects.length;
      for (const k of this.obstaclesIn(x0 - m, y0 - m, x1 + m, y1 + m)) {
        // a line touches its own boxes with its first and last piece; nothing else may
        if ((k === skipA && first) || (k === skipB && last)) continue;
        const pad = k >= n ? m * 0.5 : k === skipA || k === skipB ? 0 : m;
        if (hit(this.obstacle(k), pad)) return false;
      }
    }
    return true;
  }

  /**
   * The cheapest connection from any option in Ps to any option in Qs (costs in world
   * units, including the options' own costs). Coordinates on claimed sides keep `sep`
   * from lines already there. Returns {pts, P, Q, cP, cQ, cost} or null.
   */
  connect(Ps, Qs, ctx) {
    const s = this.world.scale;
    let best = null;
    const consider = (pts, P, Q, bends) => {
      pts = simplify(pts.map(([x, y]) => [quant(x), quant(y)]));
      if (!orthogonal(pts)) return;
      // cheapest checks first: a candidate that cannot beat the best is not examined further
      const base = P.cost + Q.cost + s * (length(pts) + (bends + jogs(pts, this.gap * 0.5)) * this.bendCost);
      if (best && base >= best.cost - EPS) return;
      if (!this.free(pts, P.item, Q.item)) return;
      // running alongside a placed line (closer than sep) costs like a detour of that length;
      // in a saturated region (hundreds of lines) these costs are skipped for speed
      let cost = base, placed = [];
      if (ctx.count(this) <= SATURATED) {
        placed = ctx.near(this, pts, this.sep);
        cost += s * (crossings(pts, placed) * this.crossCost + alongside(pts, placed, this.sep) * 3);
      }
      if (!best || cost < best.cost - EPS) best = { pts, P, Q, cP: alongOf(P, pts[0]), cQ: alongOf(Q, pts[pts.length - 1]), cost, placed };
    };
    // the placed lines the best path so far crosses (computed once per best)
    const crossedByBest = () => {
      if (!best) return [];
      best.crossed ??= best.placed.filter((r) => crossings(best.pts, [r]) > 0);
      return best.crossed;
    };
    // claimed spots are respected by every candidate; only if no path can respect them
    // is a line allowed to share a spot (last resort, so that it is still drawn)
    // the strict pass cannot succeed when every option at one end is fully claimed
    const room = (O) => ctx.freeOn([O], O.lo, O.hi).free.length > 0;
    const passes = Ps.some(room) && Qs.some(room) ? [true, false] : [false];
    for (const strict of passes) {
      const pick = (opts, range, want) => ctx.pick(opts, range, want, strict);
      const spots = (opts, range, want) => ctx.spots(opts, range, want, strict);
      // option pairs in order of a lower bound on their cost (branch and bound)
      const pairs = [];
      for (const P of Ps) for (const Q of Qs) pairs.push([P, Q, this.lowerBound(P, Q)]);
      pairs.sort((x, y) => x[2] - y[2]);
      const tryShape = (sh, P, Q) => {
        if (sh.span) {
          // a straight line may run anywhere in its free span: take a free spot near its middle
          for (const c of spots([P, Q], sh.span, (sh.span[0] + sh.span[1]) / 2)) consider([pointOn(P, c), pointOn(Q, c)], P, Q, 0);
          return;
        }
        for (const a of spots([P], [P.lo, P.hi], alongOf(P, sh.pts[0]))) {
          for (const b of spots([Q], [Q.lo, Q.hi], alongOf(Q, sh.pts[sh.pts.length - 1]))) {
            consider(adjust(sh.pts, P, a, Q, b), P, Q, sh.bends);
          }
        }
      };
      for (const [P, Q, lb] of pairs) {
        if (best && lb >= best.cost - EPS) break;
        for (const sh of this.shapes(P, Q)) {
          // the shape's bends are known before its ends are placed: skip it if it cannot win
          if (sh.more || (best && this.lowerBound(P, Q, sh.span ? 0 : sh.bends) >= best.cost - EPS)) continue;
          tryShape(sh, P, Q);
        }
      }
      // if the best path crosses lines, Zs that pass beside them instead (only now are
      // those lines known)
      const lines = crossedByBest();
      if (lines.length) {
        for (const [P, Q, lb] of pairs) {
          if (lb >= best.cost - EPS) break;
          if (this.lowerBound(P, Q, 2) >= best.cost - EPS) continue;
          for (const sh of this.shapes(P, Q)) if (sh.more) for (const z of sh.more(lines)) tryShape(z, P, Q);
        }
      }
      if (!best) {
        const fb = this.channelRoute(Ps, Qs);
        if (fb) {
          const a = pick([fb.P], [fb.P.lo, fb.P.hi], alongOf(fb.P, fb.pts[0]));
          const b = pick([fb.Q], [fb.Q.lo, fb.Q.hi], alongOf(fb.Q, fb.pts[fb.pts.length - 1]));
          if (a !== null && b !== null) consider(adjust(fb.pts, fb.P, a, fb.Q, b), fb.P, fb.Q, fb.pts.length - 2);
        }
      }
      if (best) break;
    }
    return best;
  }

  /** A lower bound on the cost of any connection between options P and Q (with `bends` bends). */
  lowerBound(P, Q, bends = undefined) {
    const seg = (O) => (horizontal(O) ? [[O.line, O.line], [O.lo, O.hi]] : [[O.lo, O.hi], [O.line, O.line]]);
    const [px, py] = seg(P), [qx, qy] = seg(Q);
    const gap = (a, b) => Math.max(0, a[0] - b[1], b[0] - a[1]);
    const facing = horizontal(P) === horizontal(Q) && P.dir[0] + Q.dir[0] === 0 && P.dir[1] + Q.dir[1] === 0;
    const least = horizontal(P) !== horizontal(Q) ? 1 : facing ? 0 : 2;
    return P.cost + Q.cost + this.world.scale * (gap(px, qx) + gap(py, qy) + Math.max(least, bends ?? 0) * this.bendCost);
  }

  /** Fallback: bend-penalised shortest path along the channel centre lines. */
  channelRoute(S, T) {
    const rows = this.hy.length, cols = this.vx.length;
    const node = (c, r) => c * rows + r;
    const xy = (id) => [this.vx[Math.floor(id / rows)], this.hy[id % rows]];
    // where an option meets the channel network: straight out from the middle of its range
    const join = (O) => {
      const along = quant((O.lo + O.hi) / 2);
      if (horizontal(O)) {
        const xs = this.vx.map((x, c) => [x, c]).filter(([x]) => (x - O.line) * O.dir[0] > EPS);
        if (!xs.length) return null;
        const [x, c] = O.dir[0] > 0 ? xs[0] : xs[xs.length - 1];
        const r = Math.max(0, Math.min(rows - 2, this.hy.findIndex((y) => y > along) - 1));
        return { O, along, entry: [x, along], a: node(c, r), b: node(c, r + 1), orient: 1 };
      }
      const ys = this.hy.map((y, r) => [y, r]).filter(([y]) => (y - O.line) * O.dir[1] > EPS);
      if (!ys.length) return null;
      const [y, r] = O.dir[1] > 0 ? ys[0] : ys[ys.length - 1];
      const c = Math.max(0, Math.min(cols - 2, this.vx.findIndex((x) => x > along) - 1));
      return { O, along, entry: [along, y], a: node(c, r), b: node(c + 1, r), orient: 0 };
    };
    const J = S.map(join).filter(Boolean), K = T.map(join).filter(Boolean);
    if (!J.length || !K.length) return null;
    const bend = this.bendCost;
    const dist = new Map(), prev = new Map(), from = new Map();
    const heap = new Heap();
    const d0 = (A, id) => Math.abs(A.entry[0] - xy(id)[0]) + Math.abs(A.entry[1] - xy(id)[1]);
    J.forEach((A, i) => {
      for (const id of [A.a, A.b]) {
        const st = id * 2 + A.orient, c = d0(A, id) + bend;
        if (!(dist.get(st) <= c)) { dist.set(st, c); from.set(st, i); heap.push(c, st); }
      }
    });
    const targets = new Map();
    K.forEach((B, i) => { for (const id of [B.a, B.b]) targets.set(id, [...(targets.get(id) || []), i]); });
    let best = null;
    while (heap.size) {
      const [d, st] = heap.pop();
      if (d > dist.get(st) || (best && d >= best.cost)) continue;
      const id = st >> 1, o = st & 1;
      for (const ti of targets.get(id) || []) {
        const B = K[ti];
        const c = d + d0(B, id) + (o !== B.orient ? bend : 0) + bend;
        if (!best || c < best.cost) best = { cost: c, st, ti };
      }
      const c0 = Math.floor(id / rows), r0 = id % rows;
      for (const [nc, nr, no] of [[c0 - 1, r0, 0], [c0 + 1, r0, 0], [c0, r0 - 1, 1], [c0, r0 + 1, 1]]) {
        if (nc < 0 || nr < 0 || nc >= cols || nr >= rows) continue;
        const nb = node(nc, nr);
        const [x0, y0] = xy(id), [x1, y1] = xy(nb);
        const c = d + Math.abs(x1 - x0) + Math.abs(y1 - y0) + (no !== o ? bend : 0);
        const ns = nb * 2 + no;
        if (!(dist.get(ns) <= c)) { dist.set(ns, c); prev.set(ns, st); from.set(ns, from.get(st)); heap.push(c, ns); }
      }
    }
    if (!best) return null;
    const chain = [];
    for (let st = best.st; st !== undefined; st = prev.get(st)) chain.push(st >> 1);
    chain.reverse();
    const A = J[from.get(best.st)], B = K[best.ti];
    return { pts: [pointOn(A.O, A.along), A.entry, ...chain.map(xy), B.entry, pointOn(B.O, B.along)], P: A.O, Q: B.O };
  }

  // ------------------------------------------------------------ separation

  /**
   * Order and space parallel pieces. Pieces of different lines that run alongside each
   * other in one free corridor are ordered by where their lines turn off at both ends,
   * so they do not cross there, then spread at least `sep` apart. A piece moves only
   * within its allowed range: along its box side for a piece touching a box; not at all
   * where it crosses a region border; within its corridor otherwise.
   */
  separate(pieces) {
    const sep = this.sep;
    for (const horiz of [true, false]) {
      const list = [];
      pieces.forEach((pc, pi) => {
        const p = pc.pts;
        for (let i = 0; i + 1 < p.length; i++) {
          if ((Math.abs(p[i][1] - p[i + 1][1]) < EPS) !== horiz) continue;
          const k = horiz ? 0 : 1, coord = horiz ? p[i][1] : p[i][0];
          const q = horiz ? 1 : 0; // the axis the piece moves along
          const lo = Math.min(p[i][k], p[i + 1][k]), hi = Math.max(p[i][k], p[i + 1][k]);
          let min = 0, max = horiz ? this.frame.h : this.frame.w, pinned = false;
          for (const [end, O] of [[i === 0, pc.P], [i + 2 === p.length, pc.Q]]) {
            if (!end || horizontal(O) !== horiz) continue; // this piece leaves O's side
            if (O.border || O.crossing || O.fixed) { pinned = true; min = max = coord; } else { min = Math.max(min, O.lo); max = Math.min(max, O.hi); }
          }
          // never past the far end of a neighbouring piece (the line would double back) ...
          for (const nb of [p[i - 1], p[i + 2]]) {
            if (!nb) continue;
            if (nb[q] < coord - EPS) min = Math.max(min, nb[q] + sep / 2);
            if (nb[q] > coord + EPS) max = Math.min(max, nb[q] - sep / 2);
          }
          // ... and never into anything drawn alongside it
          const skip = new Set([i === 0 ? pc.P.item : undefined, i + 2 === p.length ? pc.Q.item : undefined]);
          // only what lies alongside matters: the strip over the piece's extent, up to its bounds
          const c0 = Math.max(min, 0) - this.clear, c1 = Math.min(max, horiz ? this.frame.h : this.frame.w) + this.clear;
          const near = horiz ? this.obstaclesIn(lo - this.clear, c0, hi + this.clear, c1)
            : this.obstaclesIn(c0, lo - this.clear, c1, hi + this.clear);
          for (const R of near.filter((n) => !skip.has(n)).map((n) => this.obstacle(n))) {
            const e0 = horiz ? R.x : R.y, e1 = horiz ? R.x + R.w : R.y + R.h; // along the piece
            if (e1 <= lo - this.clear || e0 >= hi + this.clear) continue;
            const r0 = horiz ? R.y : R.x, r1 = horiz ? R.y + R.h : R.x + R.w; // across it
            if (r1 <= coord + EPS) min = Math.max(min, r1 + this.clear);
            else if (r0 >= coord - EPS) max = Math.min(max, r0 - this.clear);
          }
          // which way the line goes on at each end: -1 toward smaller, +1 larger coordinates
          const turnAt = (q) => (q ? Math.sign((horiz ? q[1] : q[0]) - coord) : 0);
          const forward = p[i + 1][k] >= p[i][k];
          const tStart = turnAt(p[i - 1]), tEnd = turnAt(p[i + 2]);
          list.push({ pi, i, coord, lo, hi, min: Math.min(min, coord), max: Math.max(max, coord), pinned,
            tLo: forward ? tStart : tEnd, tHi: forward ? tEnd : tStart });
        }
      });
      // 1. the order across: by coordinate, except that pieces close together (within
      //    3·sep) and alongside each other are ordered by where their lines turn off, so
      //    they do not cross there (insertion, so these local swaps compose)
      list.sort((p, q) => p.coord - q.coord || p.pi - q.pi || p.i - q.i);
      const along = (s, t) => Math.min(s.hi, t.hi) - Math.max(s.lo, t.lo) > EPS;
      for (let n = 1; n < list.length; n++) {
        const x = list[n];
        let k = n;
        while (k > 0 && x.coord - list[k - 1].coord <= 3 * sep) {
          const y = list[k - 1];
          if (along(y, x) && y.pi !== x.pi && !(laneOrder(y, x) > 0)) break;
          k--;
        }
        // passing only pieces x does not run alongside changes nothing: stay put then
        if (k < n && list.slice(k, n).some((y) => along(y, x) && y.pi !== x.pi)) list.splice(n, 1), list.splice(k, 0, x);
      }
      list.forEach((x, n) => { x.rank = n; });
      // 2. constraints between pieces that run alongside each other in one free corridor:
      //    the earlier one in that order must end up at least sep before the other. Only
      //    pieces that are neighbours somewhere along their extent are paired (sweep line,
      //    as in VPSC); the order of the others follows transitively.
      const cons = [];
      const pair = (s, t) => {
        if (!s || !t || s.pi === t.pi || (s.pinned && t.pinned)) return;
        if (Math.abs(t.coord - s.coord) >= this.gap * 1.5) return;
        const lo = Math.max(s.lo, t.lo), hi = Math.min(s.hi, t.hi);
        if (hi - lo < EPS) return;
        const c0 = Math.min(s.coord, t.coord), c1 = Math.max(s.coord, t.coord);
        const between = horiz ? { x: lo, y: c0, w: hi - lo, h: c1 - c0 } : { x: c0, y: lo, w: c1 - c0, h: hi - lo };
        if (this.obstaclesIn(between.x, between.y, between.x + between.w, between.y + between.h)
          .some((n) => overlaps(this.obstacle(n), between))) return;
        cons.push([s, t]);
      };
      // pieces open at lo and close at hi; at equal positions closing comes first, since
      // pieces that only touch end to end do not run alongside each other
      const events = [];
      for (const x of list) if (x.hi - x.lo > EPS) events.push([x.lo, 1, x], [x.hi, 0, x]);
      events.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2].rank - b[2].rank);
      const active = []; // by rank
      for (const [, open, x] of events) {
        if (open) {
          const k = rankIndex(active, x.rank);
          active.splice(k, 0, x);
          pair(active[k - 1], x);
          pair(x, active[k + 1]);
        } else {
          const k = rankIndex(active, x.rank);
          active.splice(k, 1);
          pair(active[k - 1], active[k]);
        }
      }
      // least movement satisfying the constraints within each piece's bounds (projection)
      for (const x of list) x.want = x.coord;
      for (let it = 0; it < 60; it++) {
        let worst = 0;
        for (const [s, t] of cons) {
          const deficit = s.want + sep - t.want;
          if (deficit <= EPS) continue;
          worst = Math.max(worst, deficit);
          const ws = s.pinned ? 0 : 1, wt = t.pinned ? 0 : 1;
          if (!ws && !wt) continue;
          s.want = Math.max(s.min, s.want - (deficit * ws) / (ws + wt));
          t.want = Math.min(t.max, t.want + (deficit * wt) / (ws + wt));
        }
        if (worst < EPS) break;
      }
      for (const s of list) {
        const v = quant(s.want);
        if (Math.abs(v - s.coord) < EPS) continue;
        const pc = pieces[s.pi];
        const old = pc.pts.map((q) => q.slice());
        const idx = horiz ? 1 : 0;
        pc.pts[s.i][idx] = v;
        pc.pts[s.i + 1][idx] = v;
        if (!orthogonal(pc.pts) || !this.free(pc.pts, pc.P.item, pc.Q.item)) pc.pts = old;
      }
    }
    for (const pc of pieces) pc.pts = simplify(pc.pts);
  }

  // ------------------------------------------------------- single region

  /**
   * Route connections between items (or border points) of this region only; used to
   * score candidate arrangements. requests: [{src: {item} | {port: {side, pos}}, dst}].
   */
  routeAll(requests) {
    const ctx = new Claims();
    const opts = (e) => {
      if (e.item !== undefined) return this.itemOptions(e.item);
      const { side, pos } = e.port;
      const [dx, dy] = OUT[side];
      const line = side === "right" ? this.frame.w : side === "bottom" ? this.frame.h : 0;
      return [{ side, line, lo: pos, hi: pos, dir: [-dx, -dy], cost: 0, fixed: true, level: this, expand: () => [] }];
    };
    const pieces = [];
    for (const rq of requests) {
      const r = this.connect(opts(rq.src), opts(rq.dst), ctx);
      pieces.push(r ? { pts: r.pts, P: r.P, Q: r.Q } : null);
      if (r) ctx.commit(this, r);
    }
    this.separate(pieces.filter(Boolean));
    return pieces.map((pc) => (pc ? { points: pc.pts } : null));
  }
}

/**
 * Bookkeeping shared by all regions while lines are placed: the pieces already drawn in
 * each region (for crossing costs) and the coordinates claimed on each box side (in
 * world units, so a border crossing claims the same spot seen from either side).
 */
class Claims {
  constructor() {
    this.index = new Map(); // Level -> {cell, buckets: Map("i,j" -> [piece])}
    this.sides = new Map(); // "box|side" -> [world coordinate along the side]
    this.optKeys = new WeakMap(); // option -> its part of a cache key
    this.cache = new Map(); // sides and range -> free intervals
    this.cachedBySide = new Map(); // side key -> [cache keys that read it]; dropped when it is claimed
  }

  count(level) {
    const ix = this.index.get(level);
    return ix ? ix.count : 0;
  }

  /** Placed pieces of a level whose boxes come within `pad` of a polyline (grid buckets). */
  near(level, pts, pad) {
    const ix = this.index.get(level);
    if (!ix) return [];
    const b = bbox(pts);
    const found = new Set();
    for (let i = Math.floor((b.x0 - pad) / ix.cell); i <= Math.floor((b.x1 + pad) / ix.cell); i++) {
      for (let j = Math.floor((b.y0 - pad) / ix.cell); j <= Math.floor((b.y1 + pad) / ix.cell); j++) {
        for (const pc of ix.buckets.get(i + "," + j) || []) found.add(pc);
      }
    }
    return [...found];
  }

  keys(O) {
    if (O.keys) return O.keys;
    if (O.border) return [`${O.level.id}|${O.side}`];
    return O.box !== undefined ? [`${O.box}|${O.side}`] : [];
  }

  toWorld(O, c) {
    const w = O.level.world;
    return horizontal(O) ? w.y + c * w.scale : w.x + c * w.scale;
  }

  fromWorld(O, v) {
    const w = O.level.world;
    return ((horizontal(O) ? v - w.y : v - w.x)) / w.scale;
  }

  /**
   * A coordinate within [lo, hi] near `want` that keeps sep from lines on the given sides;
   * null if there is none. Not strict: the coordinate as far from those lines as possible.
   */
  pick(opts, range, want, strict = true) {
    const c = this.spots(opts, range, want, strict);
    return c.length ? c[0] : null;
  }

  /**
   * Like pick, but with the free spot on each side of `want` (nearest first): where want
   * is claimed, passing the line there on its one or its other side are different
   * topologies, and only the whole path can tell which one avoids crossing it.
   */
  spots(opts, [lo, hi], want, strict = true) {
    const { taken, free } = this.freeOn(opts, lo, hi);
    want = Math.min(hi, Math.max(lo, want));
    if (!taken.length) return [quant(want)];
    if (free.length) {
      // the free interval at or after want, and the one before
      let i = 0, j = free.length;
      while (i < j) {
        const m = (i + j) >> 1;
        if (free[m][1] < want) i = m + 1;
        else j = m;
      }
      const out = [free[i - 1], free[i]].filter(Boolean).map(([a, b]) => quant(Math.min(b, Math.max(a, want))));
      out.sort((x, y) => Math.abs(x - want) - Math.abs(y - want));
      return uniq(out);
    }
    if (strict) return [];
    // no free spot: the one farthest from its neighbours (range ends or midway between claims)
    const marks = [lo, ...taken.filter((t) => t > lo && t < hi), hi];
    let bestC = want, bestD = -1;
    for (let i = 0; i + 1 < marks.length; i++) {
      const c = (marks[i] + marks[i + 1]) / 2, d = (marks[i + 1] - marks[i]) / 2;
      if (d > bestD + EPS) { bestD = d; bestC = c; }
    }
    return [quant(bestC)];
  }

  /** Identifies the claimed sides an option reads and the coordinates it reads them in. */
  optKey(O) {
    let k = this.optKeys.get(O);
    if (k === undefined) {
      k = `${O.level.id}:${O.level.depth}:${this.keys(O).join("+")}`;
      this.optKeys.set(O, k);
    }
    return k;
  }

  /**
   * Claimed spots near [lo, hi] on the sides of opts (sorted, in the level's coordinates)
   * and the free intervals between them, each claim blocking sep on both sides. Claims only
   * change on commit, so this is computed once per side and range until then.
   */
  freeOn(opts, lo, hi) {
    const key = (opts.length === 1 ? this.optKey(opts[0]) : opts.map((O) => this.optKey(O)).join("/")) + `@${lo},${hi}`;
    const hit = this.cache.get(key);
    if (hit) return hit;
    const sep = opts[0].level.sep; // all options of one connection belong to one level
    const taken = [];
    for (const O of opts) {
      // claims are kept sorted in world units: look only at the window around the range
      const w0 = this.toWorld(O, lo - sep), w1 = this.toWorld(O, hi + sep);
      for (const k of this.keys(O)) {
        const list = this.sides.get(k);
        if (!list) continue;
        for (let i = lowerBound(list, Math.min(w0, w1)); i < list.length && list[i] <= Math.max(w0, w1); i++) {
          const t = this.fromWorld(O, list[i]);
          if (t > lo - sep && t < hi + sep) taken.push(t);
        }
      }
    }
    taken.sort((a, b) => a - b);
    const free = [];
    let start = lo;
    for (const t of taken) {
      if (t - sep >= start - EPS) free.push([start, Math.min(hi, t - sep)]);
      start = Math.max(start, t + sep);
    }
    if (start <= hi + EPS) free.push([start, hi]);
    const out = { taken, free: free.filter(([a, b]) => b >= a - EPS) };
    this.cache.set(key, out);
    for (const O of opts) {
      for (const k of this.keys(O)) {
        if (!this.cachedBySide.has(k)) this.cachedBySide.set(k, []);
        this.cachedBySide.get(k).push(key);
      }
    }
    return out;
  }

  /** Record a placed piece: its segments for crossing costs and its ends as claims. */
  commit(level, r, owner = null) {
    if (!this.index.has(level)) this.index.set(level, { cell: level.gap * 2, buckets: new Map(), count: 0 });
    const ix = this.index.get(level);
    ix.count++;
    const pc = { points: r.pts, bbox: bbox(r.pts), owner };
    // each segment is entered into the buckets it passes through
    const buckets = [];
    for (let k = 1; k < r.pts.length; k++) {
      const sb = bbox([r.pts[k - 1], r.pts[k]]);
      for (let i = Math.floor(sb.x0 / ix.cell); i <= Math.floor(sb.x1 / ix.cell); i++) {
        for (let j = Math.floor(sb.y0 / ix.cell); j <= Math.floor(sb.y1 / ix.cell); j++) {
          const key = i + "," + j;
          if (!ix.buckets.has(key)) ix.buckets.set(key, []);
          const list = ix.buckets.get(key);
          if (list[list.length - 1] !== pc) list.push(pc), buckets.push(key);
        }
      }
    }
    const claims = [];
    for (const [O, c] of [[r.P, r.cP], [r.Q, r.cQ]]) {
      for (const k of this.keys(O)) {
        if (!this.sides.has(k)) this.sides.set(k, []);
        const list = this.sides.get(k), v = this.toWorld(O, c);
        list.splice(lowerBound(list, v), 0, v);
        claims.push([k, v]);
        this.invalidate(k);
      }
    }
    return { level, pc, buckets, claims };
  }

  /** Take back a commit (its return value), as if the piece had never been placed. */
  uncommit({ level, pc, buckets, claims }) {
    const ix = this.index.get(level);
    ix.count--;
    for (const key of buckets) {
      const list = ix.buckets.get(key);
      list.splice(list.indexOf(pc), 1);
    }
    for (const [k, v] of claims) {
      const list = this.sides.get(k);
      list.splice(lowerBound(list, v), 1);
      this.invalidate(k);
    }
  }

  invalidate(side) {
    for (const key of this.cachedBySide.get(side) || []) this.cache.delete(key);
    this.cachedBySide.delete(side);
  }
}

/** A path with its last piece (and the end of the piece before) moved to position c along option O. */
function slideTo(pts, O, c) {
  const out = pts.map((p) => p.slice());
  const k = horizontal(O) ? 1 : 0, n = out.length;
  if (n >= 3) out[n - 2][k] = c;
  out[n - 1][k] = c;
  return simplify(out);
}

/** Move the end pieces of a polyline to coordinates a (on P) and b (on Q), keeping it orthogonal. */
function adjust(pts, P, a, Q, b) {
  const out = pts.map((p) => p.slice());
  const n = out.length;
  const k = horizontal(P) ? 1 : 0;
  out[0][k] = a;
  out[1][k] = a;
  const kq = horizontal(Q) ? 1 : 0;
  out[n - 1][kq] = b;
  out[n - 2][kq] = b;
  return out;
}

// ------------------------------------------------------------- hierarchy

/**
 * Route connections ("nets") through the nested layout.
 * @param L layout: boxes (with .level, .itemIndex for regions), root, ancestors
 * @param nets [{id, from, to, ...}]
 * @param opts {thorough}: false (large maps) skips placing crossing lines again and
 *   offers each region's ways out unpriced by the lines they cross, one per side and shape
 * @returns segments [{container, depth, points (world), members, srcItem, dstItem, terminal}]
 */
export function routeNets(L, nets, opts = {}) {
  const box = (id) => (id === null ? L.root : L.boxes.get(id));
  // connections with identical endpoints share a line
  const groups = new Map();
  for (const net of nets) {
    if (net.from === net.to || !L.ancestors.get(net.from) || !L.ancestors.get(net.to)) continue;
    const key = net.from + "\u0000" + net.to;
    if (!groups.has(key)) groups.set(key, { from: net.from, to: net.to, members: [] });
    groups.get(key).members.push(net);
  }
  const ordered = [...groups.values()].sort((a, b) => b.members.length - a.members.length ||
    a.from.localeCompare(b.from) || a.to.localeCompare(b.to));
  const ctx = new Claims();
  const thorough = opts.thorough !== false;

  /**
   * Options for leaving towards `chain[j..]` as seen in region C (C = chain[j - 1], or the
   * root): the item's own sides when it is a direct child, otherwise the best ways out of
   * the enclosing child region, computed from the inside out.
   */
  const endOptions = (C, chain, j) => {
    const lvl = C.level;
    if (j === chain.length) {
      // the line attaches to region C itself (a connection to its own descendant): its header
      const x = quant(C.w / 2);
      return [{ side: "top", line: C.frame.headerBottom, lo: x, hi: x, dir: [0, 1], cost: 0, fixed: true,
        level: lvl, expand: () => [] }];
    }
    const i = C.itemIndex.get(chain[j]);
    if (j === chain.length - 1) return lvl.itemOptions(i);
    return exits(box(chain[j]), chain, j + 1).map((O) => toParent(O, lvl, i));
  };

  /**
   * The ways out of region B through each of its sides, from chain[j..] inside. A way
   * out whose inside part is straight or an L keeps the range of border positions it
   * can use, so the outside can choose where to cross; other ways out are fixed points.
   */
  const exits = (B, chain, j) => {
    const lvl = B.level;
    const inner = endOptions(B, chain, j);
    const out = [];
    for (const side of SIDES) {
      const border = lvl.borderOption(side);
      if (!border) continue;
      const ranges = [];
      for (const io of inner) for (const r of lvl.exitRanges(io, border, ctx)) ranges.push(r);
      // per side: straight and L ways out
      for (const bends of [0, 1]) {
        for (const r of offered(lvl, border, ranges.filter((x) => x.bends === bends))) {
          out.push({ side, line: border.line, lo: r.lo, hi: r.hi, dir: OUT[side], cost: r.cost, border: true,
            level: lvl, keys: bends === 0 ? [...ctx.keys(r.io), ...ctx.keys(border)] : ctx.keys(border),
            expand: (c) => [...r.io.expand(r.at(c)), { level: lvl, pts: r.pts(c), P: r.io, Q: border }] });
        }
      }
      if (ranges.length) continue;
      // otherwise the cheapest path of any shape; its last piece can still slide along the
      // border as far as the free space allows, so the outside chooses where it crosses
      const r = lvl.connect(inner, [border], ctx);
      if (!r) continue;
      const [lo, hi] = lvl.slideRange(r.pts, border, r.cQ, r.P.item);
      const known = crossings(r.pts, r.placed) * lvl.world.scale * lvl.crossCost; // priced at r.cQ
      const slide = { lo, hi, cost: r.cost - known, known, pts: (c) => slideTo(r.pts, border, c) };
      for (const x of offered(lvl, border, [slide])) {
        out.push({ side, line: border.line, lo: x.lo, hi: x.hi, dir: OUT[side], cost: x.cost,
          border: true, level: lvl, keys: ctx.keys(border),
          expand: (c) => [...r.P.expand(r.cP), { level: lvl, pts: slideTo(r.pts, border, c), P: r.P, Q: border }] });
      }
    }
    return out;
  };

  /**
   * The ways out to offer among `ranges` ({lo, hi, cost, pts(c), known?}, cost not
   * counting crossings, `known` the price of those already found where they cannot be
   * split): where on the border a line crosses decides which placed lines its inside
   * part crosses, so each range is split into stretches priced with their crossings
   * (byCrossings). Offered are the cheapest stretch and the cheapest one apart from it
   * (passing placed lines on their other side, say: a different topology that only the
   * whole line can judge). Crossings only add cost, so ranges are split cheapest first and
   * only while they can still be offered.
   */
  const offered = (lvl, border, ranges) => {
    const crossCost = lvl.world.scale * lvl.crossCost;
    const pool = [];
    let kept = [];
    for (const r of ranges.sort((x, y) => x.cost - y.cost)) {
      if (kept.length === (thorough ? 2 : 1) && r.cost >= kept[kept.length - 1].cost - EPS) break;
      for (const part of byCrossings(lvl, border, r.lo, r.hi, r.pts)) {
        const priced = part.n === null ? r.known ?? 0 : part.n * crossCost;
        pool.push({ ...r, lo: part.lo, hi: part.hi, cost: r.cost + priced });
      }
      kept = [];
      for (const x of pool.sort((p, q) => p.cost - q.cost)) {
        if (kept.length < (thorough ? 2 : 1) && !kept.some((k) => x.lo <= k.hi + EPS && k.lo <= x.hi + EPS)) kept.push(x);
      }
    }
    return kept;
  };

  /**
   * Split the border positions lo..hi of a way out into stretches over which its inside
   * path pts(c) crosses the same number of placed lines: [{lo, hi, n}]. That number can
   * only change where c passes a corner or end of a placed piece, so the range is cut
   * there (keeping sep from the cut). Positions where the last piece would run within sep
   * of a placed piece parallel to it are left out: no room to spread them apart there.
   * Each split scans the lines around the way out, so in a busy region (where single
   * lines can no longer be followed anyway) and on large maps there is one stretch, not
   * priced (n null).
   */
  const byCrossings = (lvl, border, lo, hi, pts) => {
    if (ctx.count(lvl) === 0) return [{ lo, hi, n: 0 }];
    if (!thorough || ctx.count(lvl) > BUSY) return [{ lo, hi, n: null }];
    // the path stays within the box swept between its two extremes: only placed pieces
    // passing through it (or within sep of it) matter
    const sep = lvl.sep, swept = [...pts(lo), ...pts(hi)], sw = bbox(swept), segs = [];
    for (const pc of ctx.near(lvl, swept, sep)) {
      const o = pc.bbox;
      if (o.x1 < sw.x0 - sep || o.x0 > sw.x1 + sep || o.y1 < sw.y0 - sep || o.y0 > sw.y1 + sep) continue;
      for (let i = 1; i < pc.points.length; i++) {
        const [p, q] = [pc.points[i - 1], pc.points[i]];
        if (Math.max(p[0], q[0]) < sw.x0 - sep || Math.min(p[0], q[0]) > sw.x1 + sep ||
          Math.max(p[1], q[1]) < sw.y0 - sep || Math.min(p[1], q[1]) > sw.y1 + sep) continue;
        segs.push([pc.points, i - 1]);
      }
    }
    if (!segs.length) return [{ lo, hi, n: 0 }];
    const count = (c) => {
      const P = pts(c);
      let n = 0;
      for (let i = 0; i + 1 < P.length; i++) for (const [Q, j] of segs) n += meet(P, i, Q, j);
      return n;
    };
    const across = (p) => (horizontal(border) ? p[0] : p[1]);
    const P0 = pts(lo), e0 = across(P0[P0.length - 2]), e1 = across(P0[P0.length - 1]); // the last piece's extent
    const lanes = [], corners = [];
    for (const [Q, j] of segs) {
      const u = alongOf(border, Q[j]), v = alongOf(border, Q[j + 1]);
      if (Math.abs(u - v) >= EPS) corners.push(u, v);
      else if (Math.min(Math.max(across(Q[j]), across(Q[j + 1])), Math.max(e0, e1)) -
        Math.max(Math.min(across(Q[j]), across(Q[j + 1])), Math.min(e0, e1)) > EPS) lanes.push(u);
    }
    // the range without the lanes of placed parallel pieces (pieces on one line take a
    // lane each once spread apart), cut at corners
    const parts = [];
    let start = lo;
    lanes.sort((x, y) => x - y);
    for (let i = 0; i < lanes.length;) {
      const z = lanes[i];
      let m = 0;
      while (i < lanes.length && lanes[i] - z < EPS) i++, m++;
      if (z - m * sep >= start - EPS && start <= hi + EPS) parts.push([start, Math.min(hi, z - m * sep)]);
      start = Math.max(start, z + m * sep);
    }
    if (start <= hi + EPS) parts.push([start, hi]);
    corners.sort((x, y) => x - y);
    const out = [];
    for (const [plo, phi] of parts) {
      if (phi - plo < EPS) { out.push({ lo: plo, hi: phi, n: count(plo) }); continue; }
      const marks = [plo];
      for (const v of corners) if (v > plo + EPS && v < phi - EPS && v > marks[marks.length - 1] + EPS) marks.push(v);
      marks.push(phi);
      const first = out.length;
      for (let i = 0; i + 1 < marks.length; i++) {
        const a = i === 0 ? plo : marks[i] + sep, b = i + 2 === marks.length ? phi : marks[i + 1] - sep;
        if (b < a - EPS) continue;
        const n = count((a + b) / 2);
        const last = out.length > first ? out[out.length - 1] : null;
        if (last && last.n === n) last.hi = b;
        else out.push({ lo: a, hi: b, n });
      }
    }
    return out.length ? out : [{ lo, hi, n: count((lo + hi) / 2) }];
  };


  /** An option on child region i's border, seen from the parent level (as a side of item i). */
  const toParent = (O, parent, i) => {
    const R = parent.rects[i];
    const f = R.w / O.level.frame.w;
    const off = horizontal(O) ? R.y : R.x;
    const line = horizontal(O) ? R.x + O.line * f : R.y + O.line * f;
    return { ...O, line, lo: off + O.lo * f, hi: off + O.hi * f, item: i, box: O.level.id, level: parent,
      border: false, crossing: true, expand: (c) => O.expand((c - off) / f) };
  };

  /** The pieces of a group's line, from the source outwards, the shared piece, then into the target. */
  const route = (g) => {
    const ca = L.ancestors.get(g.from), cb = L.ancestors.get(g.to);
    let k = 0;
    while (k < ca.length && k < cb.length && ca[k] === cb[k]) k++;
    const C = box(k === 0 ? null : ca[k - 1]);
    const r = C.level.connect(endOptions(C, ca, k), endOptions(C, cb, k), ctx);
    if (!r) return null;
    const src = r.P.expand(r.cP);
    const dst = r.Q.expand(r.cQ).reverse().map((pc) => ({ level: pc.level, pts: pc.pts.slice().reverse(), P: pc.Q, Q: pc.P }));
    return [...src, { level: C.level, pts: r.pts, P: r.P, Q: r.Q }, ...dst];
  };
  const place = (g, pieces) => {
    g.pieces = pieces;
    g.commits = pieces.map((pc) => ctx.commit(pc.level, { pts: pc.pts, P: pc.P, Q: pc.Q,
      cP: alongOf(pc.P, pc.pts[0]), cQ: alongOf(pc.Q, pc.pts[pc.pts.length - 1]) }, g));
  };
  const unplace = (g) => g.commits.forEach((c) => ctx.uncommit(c));
  /** Crossings of a group's line with the others, where crossings are priced. */
  const crossed = (g) => g.pieces.reduce((n, pc) => n + (ctx.count(pc.level) > SATURATED ? 0
    : crossings(pc.pts, ctx.near(pc.level, pc.pts, 0).filter((o) => o.owner !== g))), 0);

  for (const g of ordered) {
    const pieces = route(g);
    if (pieces) place(g, pieces);
  }
  // Lines are placed one after another, so an early line cannot know where later ones
  // must go and may block them (a crossing the later line cannot avoid). Rip up: each
  // line that crosses others is placed again with all others known, and keeps the new
  // way only if it crosses fewer lines. Then the same for the lines crossing one that
  // moved, while any moves. Costs about one more routing pass, so large maps skip it.
  let todo = thorough ? ordered.filter((g) => g.pieces) : [];
  for (let pass = 0; pass < 4 && todo.length; pass++) {
    const moved = [];
    const candidates = todo.map((g) => [g, crossed(g)]).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
    for (const [g] of candidates) {
      const before = crossed(g);
      if (!before) continue;
      const old = g.pieces;
      unplace(g);
      const pieces = route(g);
      if (pieces) {
        place(g, pieces);
        if (crossed(g) < before) { moved.push(g); continue; }
        unplace(g);
      }
      place(g, old);
    }
    const next = new Set();
    for (const g of moved) {
      next.add(g);
      for (const pc of g.pieces) for (const o of ctx.near(pc.level, pc.pts, 0)) if (o.owner) next.add(o.owner);
    }
    todo = [...next];
  }
  const segments = [];
  for (const g of ordered) for (const pc of g.pieces || []) segments.push({ ...pc, members: g.members });

  // order and space parallel pieces, region by region
  const byLevel = new Map();
  for (const s of segments) {
    if (!byLevel.has(s.level)) byLevel.set(s.level, []);
    byLevel.get(s.level).push(s);
  }
  for (const [lvl, list] of byLevel) lvl.separate(list);

  return segments.map((s) => {
    const lvl = s.level;
    const w = lvl.world;
    const item = (O) => (O.border || O.item === undefined ? null : lvl.rects[O.item].id);
    const dstItem = item(s.Q);
    return {
      container: lvl.id,
      depth: lvl.depth + 1, // depth of the items this piece connects
      points: s.pts.map(([x, y]) => [w.x + x * w.scale, w.y + y * w.scale]),
      members: s.members,
      srcItem: item(s.P),
      dstItem,
      terminal: dstItem !== null && s.members.some((m) => m.to === dstItem),
    };
  });
}

// ------------------------------------------------------------- helpers

/** First index in a sorted array whose value is >= v. */
function lowerBound(arr, v) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function orthogonal(pts) {
  for (let i = 1; i < pts.length; i++) {
    if (Math.abs(pts[i][0] - pts[i - 1][0]) > EPS && Math.abs(pts[i][1] - pts[i - 1][1]) > EPS) return false;
  }
  return true;
}

/** Index of the first entry of a rank-sorted list with rank >= r. */
function rankIndex(list, r) {
  let lo = 0, hi = list.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (list[m].rank < r) lo = m + 1;
    else hi = m;
  }
  return lo;
}

function overlaps(R, S) {
  return R.x < S.x + S.w - EPS && R.x + R.w > S.x + EPS && R.y < S.y + S.h - EPS && R.y + R.h > S.y + EPS;
}

/**
 * Order of two parallel pieces across their corridor: where one piece's line turns off
 * while the other continues, the one turning off goes to the side it turns to.
 */
function laneOrder(a, b) {
  let vote = 0;
  if (Math.abs(a.lo - b.lo) > EPS) vote += a.lo > b.lo ? a.tLo : -b.tLo;
  else vote += a.tLo - b.tLo;
  if (Math.abs(a.hi - b.hi) > EPS) vote += Math.sign(a.hi < b.hi ? a.tHi : -b.tHi);
  else vote += Math.sign(a.tHi - b.tHi);
  return vote !== 0 ? Math.sign(vote) : a.coord - b.coord || a.pi - b.pi; // undecided: keep the order
}

/** Pieces between two bends that are shorter than `min` (they read as steps). */
function jogs(pts, min) {
  let n = 0;
  for (let i = 2; i < pts.length - 1; i++) {
    if (Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]) < min) n++;
  }
  return n;
}

function uniq(vals) {
  const out = [];
  for (const v of vals) if (!out.some((w) => Math.abs(w - v) < EPS)) out.push(v);
  return out;
}

function length(pts) {
  let s = 0;
  for (let i = 1; i < pts.length; i++) s += Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]);
  return s;
}

/** Length over which a polyline runs parallel to placed ones at less than `sep` distance. */
function alongside(pts, placed, sep) {
  let total = 0;
  const b = bbox(pts);
  for (const r of placed) {
    const o = r.bbox;
    if (o.x1 < b.x0 - sep || o.x0 > b.x1 + sep || o.y1 < b.y0 - sep || o.y0 > b.y1 + sep) continue;
    for (let i = 1; i < pts.length; i++) {
      const [a, c] = [pts[i - 1], pts[i]];
      const h = Math.abs(a[1] - c[1]) < EPS;
      for (let j = 1; j < r.points.length; j++) {
        const [d, e] = [r.points[j - 1], r.points[j]];
        if ((Math.abs(d[1] - e[1]) < EPS) !== h) continue;
        const k = h ? 0 : 1, q = h ? 1 : 0;
        if (Math.abs(a[q] - d[q]) >= sep - EPS) continue;
        const ov = Math.min(Math.max(a[k], c[k]), Math.max(d[k], e[k])) - Math.max(Math.min(a[k], c[k]), Math.min(d[k], e[k]));
        if (ov > 0) total += ov;
      }
    }
  }
  return total;
}

/**
 * Crossings between a polyline and placed ones ({points, bbox}): proper crossings, and
 * stretches a line shares with another (collinear until `separate` spreads them) that
 * cross once spread apart.
 */
export function crossings(pts, placed) {
  let n = 0;
  const b = bbox(pts);
  for (const r of placed) {
    const o = r.bbox;
    if (o.x1 < b.x0 || o.x0 > b.x1 || o.y1 < b.y0 || o.y0 > b.y1) continue;
    for (let i = 0; i + 1 < pts.length; i++) {
      for (let j = 0; j + 1 < r.points.length; j++) n += meet(pts, i, r.points, j);
    }
  }
  return n;
}

/**
 * Do the pieces P[i]-P[i+1] and Q[j]-Q[j+1] of two lines cross: properly, or by sharing
 * a collinear stretch that the lines leave on opposite sides at its two ends (spread
 * apart, they then cross once, whichever goes first)? Where a side is undecided (both
 * turn the same way, or end there), they do not.
 */
function meet(P, i, Q, j) {
  const [a, b, c, d] = [P[i], P[i + 1], Q[j], Q[j + 1]];
  const h = Math.abs(a[1] - b[1]) < EPS;
  if (h !== (Math.abs(c[1] - d[1]) < EPS)) {
    const [H0, H1, V0, V1] = h ? [a, b, c, d] : [c, d, a, b];
    const x = V0[0], y = H0[1];
    return x > Math.min(H0[0], H1[0]) + EPS && x < Math.max(H0[0], H1[0]) - EPS &&
      y > Math.min(V0[1], V1[1]) + EPS && y < Math.max(V0[1], V1[1]) - EPS ? 1 : 0;
  }
  const k = h ? 0 : 1, q = 1 - k; // along, across
  if (Math.abs(a[q] - c[q]) >= EPS) return 0;
  const lo = Math.max(Math.min(a[k], b[k]), Math.min(c[k], d[k])), hi = Math.min(Math.max(a[k], b[k]), Math.max(c[k], d[k]));
  if (hi - lo < EPS) return 0;
  // where a piece ends at e, the side its line turns to there; null where it runs on past e
  const turn = (L, s, e) => {
    const [u, v] = [L[s], L[s + 1]];
    if (Math.abs(u[k] - e) >= EPS && Math.abs(v[k] - e) >= EPS) return null;
    const nb = Math.abs(u[k] - e) < EPS ? L[s - 1] : L[s + 2];
    return nb ? Math.sign(nb[q] - u[q]) : 0;
  };
  // the side Q's line is on relative to P's line at e (0: undecided)
  const side = (e) => {
    const tp = turn(P, i, e), tq = turn(Q, j, e);
    if (tp === null) return tq ?? 0;
    if (tq === null) return -tp;
    return Math.sign(tq - tp);
  };
  const s0 = side(lo), s1 = side(hi);
  return s0 !== 0 && s1 !== 0 && s0 !== s1 ? 1 : 0;
}

function bbox(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  return { x0, y0, x1, y1 };
}

function simplify(pts) {
  const out = [];
  for (const p of pts) {
    const q = out[out.length - 1];
    if (q && Math.abs(q[0] - p[0]) < EPS && Math.abs(q[1] - p[1]) < EPS) continue;
    out.push(p);
    while (out.length >= 3) {
      const [a, b, c] = out.slice(-3);
      const col = (Math.abs(a[0] - b[0]) < EPS && Math.abs(b[0] - c[0]) < EPS) ||
        (Math.abs(a[1] - b[1]) < EPS && Math.abs(b[1] - c[1]) < EPS);
      if (!col) break;
      out.splice(out.length - 2, 1);
    }
  }
  return out;
}

class Heap {
  constructor() { this.k = []; this.v = []; }
  get size() { return this.k.length; }
  push(key, val) {
    const k = this.k, v = this.v;
    let i = k.length;
    k.push(key); v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (k[p] <= key) break;
      k[i] = k[p]; v[i] = v[p]; i = p;
    }
    k[i] = key; v[i] = val;
  }
  pop() {
    const k = this.k, v = this.v;
    const top = [k[0], v[0]];
    const lk = k.pop(), lv = v.pop();
    if (k.length) {
      let i = 0;
      const n = k.length;
      for (;;) {
        let m = i;
        const l = 2 * i + 1, r = l + 1;
        if (l < n && k[l] < (m === i ? lk : k[m])) m = l;
        if (r < n && k[r] < (m === i ? lk : k[m])) m = r;
        if (m === i) break;
        k[i] = k[m]; v[i] = v[m]; i = m;
      }
      k[i] = lk; v[i] = lv;
    }
    return top;
  }
}
