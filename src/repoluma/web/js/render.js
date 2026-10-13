// Canvas renderer: nested boxes with level of detail, orthogonal routes drawn inside
// their regions, flow overlays, and hit testing. Code panes that are large enough are rendered
// as DOM by panes.js; this file only draws their placeholders.

import {
  CAPTION_H, CARD_W, K, MONO, PANE_HEAD, PANE_K, PANE_LINE, SANS, STYLES, fontFor, headerRect, relText,
  restRows, worldRect,
} from "./layout.js";
import { UNMAPPED_ID, splitPath } from "./files.js";
import { LANE_SEP } from "./route.js";
import { clamp, rectsIntersect } from "./util.js";

export const TITLE_MIN_PX = 7; // below this a node shows an overview label instead
export const OVERVIEW_MIN_W = 50; // smallest box (px) that gets an overview label
export const EDGE_MIN_W = 120; // smallest box (px) that counts as visible for selection outlines
export const FLOW_MIN_W = 48; // flow step badges attach to boxes at least this wide
export const SEG_MIN_W = 48; // connections inside a region are drawn once its items are this wide
export const PANE_DOM_MIN_FONT = 4.5; // code font px at which panes become real DOM text

const KIND_GLYPH = {
  system: "◎", domain: "◆", capability: "◆", responsibility: "●", component: "▣",
  contract: "⇄", rule: "§", resource: "⛁", data: "▤", external: "⬚", actor: "☺",
  test: "✓", area: "▢",
};
const UNCERTAIN = new Set(["needs_review", "unknown", "planned"]);
// relationship types: data lines are the most prominent, dependencies background
const TYPE_ORDER = ["data", "error", "control", "dependency"];
const TYPE_STYLE = {
  data: { width: 1.25, alpha: 0.85 },
  error: { width: 1.15, alpha: 0.85, dash: [6, 4] },
  control: { width: 0.9, alpha: 0.7 },
  dependency: { width: 0.7, alpha: 0.55 },
};
const LANE_MAX = 3; // px between a pair's route and each lane, at most (closer when zoomed out)
const LANE_MAX_OUTSIDE = 6; // the same between the top-level regions, where lanes are wider
const LANE_GAP = 6; // px a lane keeps clear of a neighbouring line (less when zoomed out: sep)
const GAP_MAX_LINES = 400; // regions drawing more lines than this get no crossing gaps
const GAP_MAX_CROSSINGS = 3000; // at most this many gaps per region and frame
const DASH = { needs_review: [8, 5], unknown: [2, 4], planned: [12, 6] };

export function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  const v = (name) => cs.getPropertyValue(name).trim();
  return {
    dark: v("--is-dark") === "1",
    bg: v("--map-bg"), ink: v("--ink"), ink2: v("--ink-2"), ink3: v("--ink-3"),
    line: v("--line"), sel: v("--sel"), hover: v("--hover"), warn: v("--warn"),
    flow: v("--flow"), flowFail: v("--flow-fail"), codeBg: v("--code-bg"), ghost: v("--ghost"),
    rel: { data: v("--rel-data"), error: v("--rel-error"), control: v("--rel-control"),
      dependency: v("--rel-dependency") },
  };
}

// Domain hues for top-level nodes (fills are pale tints; hue identifies the region).
const HUES = [212, 152, 32, 272, 352, 190, 82, 300, 12, 232];

export class Renderer {
  constructor(canvas, app) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.app = app;
    this.theme = readTheme();
    this.dpr = 1;
    this.stats = {};
    this.visiblePanes = [];
    this.edgeCache = null;
  }

  resize(w, h) {
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
  }

  // ------------------------------------------------------------------ helpers
  hueOf(id) {
    const L = this.app.layout;
    const root = L.ancestors.get(id)?.[0] ?? id;
    const idx = L.roots.indexOf(root);
    return HUES[(idx < 0 ? 0 : idx) % HUES.length];
  }

  fillFor(box) {
    const h = this.hueOf(box.id);
    const t = this.theme;
    const kind = box.node.kind;
    if (kind === "external" || kind === "actor" || box.id === UNMAPPED_ID) {
      return t.dark ? "hsl(220 8% 20%)" : "hsl(220 10% 95%)";
    }
    const l = t.dark ? (box.depth % 2 ? 17 : 21) : (box.depth % 2 ? 99 : 95.5);
    const s = t.dark ? 22 : 60;
    return `hsl(${h} ${s}% ${l}%)`;
  }

  strokeFor(box) {
    if (box.id === UNMAPPED_ID) return this.theme.ink3;
    const h = this.hueOf(box.id);
    return this.theme.dark ? `hsl(${h} 30% 45%)` : `hsl(${h} 30% 62%)`;
  }

  /** Deepest depth whose nodes are large enough to act as relationship endpoints. */
  visibleDepth(z, minW = EDGE_MIN_W) {
    const d = Math.floor(Math.log(minW / (z * CARD_W)) / Math.log(K));
    return Math.max(0, d);
  }

  rep(id, dmax) {
    const chain = this.app.layout.ancestors.get(id);
    if (!chain) return null;
    return chain[Math.min(chain.length - 1, dmax)];
  }

  // -------------------------------------------------------------------- frame
  draw() {
    const t0 = performance.now();
    const { ctx } = this;
    const app = this.app;
    const cam = app.camera;
    this.theme = this.theme || readTheme();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = this.theme.bg;
    ctx.fillRect(0, 0, cam.vw, cam.vh);
    this.stats = { nodes: 0, panes: 0, edges: 0, text: 0 };
    this.hitSegs = [];
    this.labelHits = []; // placed annotations: click targets for their line
    this.noteRects = []; // annotations placed this frame (they must not overlap)
    this.notes = []; // annotations wanted this frame, placed by drawNotes
    this.noted = []; // the lines annotated this frame (their relationship ids)
    this.shownRels = new Set();
    this.drawnContainers = new Set();
    this.visiblePanes = [];
    if (!app.layout) return;
    const view = cam.viewRect(60);
    this.dmax = this.visibleDepth(cam.z);
    this.flowDmax = this.visibleDepth(cam.z, FLOW_MIN_W);
    this.focusSet = app.focusSet(); // null or Set of ids to keep bright
    const sel = app.selection;
    this.focusDesc = sel?.type === "node" ? app.descendants(sel.id) : null;
    this.relFocus = sel?.type === "relationship" ? sel.id : null;
    // the other legs of the selected relationship's exchange are highlighted, more quietly
    this.pairFocus = this.relFocus ? new Set(app.exchangeLegs(this.relFocus).filter((id) => id !== this.relFocus)) : null;
    this.hoverRel = app.hoverRel || null;
    this.busy = app.layout.routes.length > 120 && cam.z * CARD_W < 400;
    this.drawSegments(null, view);
    for (const id of app.layout.roots) this.drawNode(app.layout.boxes.get(id), view, false);
    this.drawNotes();
    if (app.flow) this.drawFlow(view);
    this.drawSelection();
    this.stats.ms = performance.now() - t0;
  }

  drawNode(box, view, ghostParent) {
    const cam = this.app.camera;
    const wr = worldRect(box);
    if (!rectsIntersect(wr, view)) return;
    const px = cam.z * box.scale; // screen px per local unit
    const sw = box.w * px;
    if (sw < 2) return;
    const { ctx } = this;
    const [sx, sy] = cam.toScreen(box.x, box.y);
    const sh = box.h * px;
    const ghost = ghostParent || this.app.hidden.has(box.id);
    const dim = this.focusSet && !this.focusSet.has(box.id);
    this.stats.nodes++;
    if (sw < 10 && !ghost) {
      // too small for any detail: a plain block keeps the shape of the region cheaply
      ctx.globalAlpha = dim ? 0.32 : 1;
      ctx.fillStyle = this.strokeFor(box);
      ctx.fillRect(sx, sy, sw, sh);
      ctx.globalAlpha = 1;
      return;
    }
    ctx.save();
    if (dim) ctx.globalAlpha = 0.32;
    const r = clamp(10 * px, 1, 14);
    roundRect(ctx, sx, sy, sw, sh, r);
    if (ghost) {
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = this.theme.ghost;
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      if (sw > 90 && box.node.label) {
        ctx.fillStyle = this.theme.ghost;
        ctx.font = `600 11px ${SANS}`;
        ctx.textBaseline = "top";
        ctx.fillText(fitText(ctx, `hidden: ${box.node.label}`, sw - 12), sx + 6, sy + 5);
      }
      ctx.restore();
      return; // a hidden node hides its contents too; its place stays reserved
    }
    ctx.fillStyle = this.fillFor(box);
    ctx.fill();
    const ev = box.node.certainty;
    ctx.lineWidth = clamp(1.6 * px, 0.6, 2);
    ctx.strokeStyle = this.strokeFor(box);
    const dash = box.id === UNMAPPED_ID ? [6, 4] : DASH[ev];
    if (dash) ctx.setLineDash(dash);
    ctx.stroke();
    ctx.setLineDash([]);
    if ((box.node.kind === "external" || box.node.kind === "actor") && sw > 20 && sh > 20) {
      roundRect(ctx, sx + 4, sy + 4, sw - 8, sh - 8, Math.max(1, r - 3));
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    if (this.app.uncertainty && (UNCERTAIN.has(ev) || (!ev && !(box.node.refs || []).length))) {
      hatch(ctx, sx, sy, sw, sh, this.theme.warn);
    }

    const titlePx = STYLES.title.size * px;
    if (titlePx >= TITLE_MIN_PX) this.drawHeader(box, sx, sy, px);
    const overview = titlePx < TITLE_MIN_PX && sw >= OVERVIEW_MIN_W;
    ctx.restore();

    // connections inside this region (beneath its children), then children and panes
    if (box.items.length && sw * K >= 5) {
      if (box.grid) {
        this.drawnContainers.add(box.id);
        this.drawSegments(box.id, view);
      }
      for (const it of box.items) {
        if (it.kind === "node") this.drawNode(it.box, view, false);
        else this.drawPane(it.box, view, dim);
      }
    } else if (box.items.length && sw > 24) {
      // too small to draw children: show that there is more inside
      ctx.fillStyle = this.theme.ink3;
      ctx.font = `10px ${SANS}`;
      ctx.textBaseline = "bottom";
      ctx.fillText("+" + box.items.length, sx + 4, sy + sh - 3);
    }
    if (overview) {
      // drawn last so the region's name stays on top of its (unreadable) contents
      ctx.save();
      if (dim) ctx.globalAlpha = 0.32;
      this.drawOverviewLabel(box, sx, sy, sw, sh);
      ctx.restore();
    }
  }

  drawHeader(box, sx, sy, px) {
    const { ctx } = this;
    const t = this.theme;
    ctx.textBaseline = "alphabetic";
    const x0 = sx + 24 * px;
    const y0 = sy + 24 * px;
    for (const b of box.header.blocks) {
      const fpx = b.style.size * px;
      const by = y0 + b.y * px;
      if (fpx < 2) continue;
      const color = b.role === "title" ? t.ink : b.role === "kind" || b.role === "tags" ||
        b.role === "label" || b.role === "files" ? t.ink2 : b.role === "warn" ? t.warn : t.ink;
      if (fpx < 5) {
        // greeked text keeps the shape of what is there without unreadable glyphs
        ctx.fillStyle = color;
        ctx.globalAlpha *= 0.22;
        b.lines.forEach((ln, i) => {
          const w = Math.min(ln.length * b.style.size * 0.52, 760) * px;
          ctx.fillRect(x0 + (b.indent || 0) * px, by + (i * b.style.lh + b.style.lh * 0.3) * px,
            w, Math.max(0.6, b.style.size * 0.55 * px));
        });
        ctx.globalAlpha /= 0.22;
        continue;
      }
      ctx.fillStyle = color;
      let text = null;
      if (b.role === "kind") {
        text = (KIND_GLYPH[box.node.kind] || "▢") + "  " + b.lines[0];
      }
      ctx.font = fontFor(b.style, px);
      b.lines.forEach((ln, i) => {
        ctx.fillText(i === 0 && text ? text : ln, x0 + (b.indent || 0) * px,
          by + (i * b.style.lh + b.style.size) * px);
      });
      this.stats.text += b.lines.length;
    }
  }

  drawOverviewLabel(box, sx, sy, sw, sh) {
    const { ctx } = this;
    const text = (KIND_GLYPH[box.node.kind] || "") + " " + box.node.label;
    const maxW = sw - 10;
    // room for the label: the header band when there is content below, else the whole box
    const room = (box.items.length ? box.headerH * (sw / box.w) : sh) - 4;
    // shrink to fit one line, else wrap at a small size, else ellipsis
    let size = clamp(sw / 10, 9, 14);
    ctx.font = `600 ${size}px ${SANS}`;
    let lines = [text];
    if (ctx.measureText(text).width > maxW) {
      size = clamp(size * maxW / ctx.measureText(text).width, 9, 14);
      ctx.font = `600 ${size}px ${SANS}`;
      // a label may cover the (unreadable) top of the children: names matter more here
      const maxLines = clamp(Math.floor(room / (size * 1.2)), 2, 3);
      lines = wrapFit(ctx, text, maxW, maxLines);
    }
    let lh = size * 1.2;
    if (lines.length * lh + 6 > sh) {
      // a short box: one shortened line at the smallest size rather than no name at all
      size = 9;
      lh = size * 1.2;
      ctx.font = `600 ${size}px ${SANS}`;
      lines = [fitText(ctx, text, maxW)];
    }
    const blockH = lines.length * lh;
    const ty = sy + Math.min(6, sh * 0.1);
    if (blockH + 4 > sh || !lines[0]) return;
    ctx.textBaseline = "top";
    const tw = Math.max(...lines.map((l) => ctx.measureText(l).width));
    ctx.fillStyle = this.fillFor(box);
    ctx.fillRect(sx + 3, ty - 2, tw + 6, blockH + 3);
    ctx.fillStyle = this.theme.ink;
    lines.forEach((ln, i) => ctx.fillText(ln, sx + 6, ty + i * lh));
    this.stats.text += lines.length;
  }

  drawPane(pane, view, dim) {
    const cam = this.app.camera;
    const wr = { x: pane.x, y: pane.y, w: pane.w * pane.scale, h: pane.h * pane.scale };
    if (!rectsIntersect(wr, view)) return;
    const px = cam.z * pane.scale;
    const sw = pane.w * px;
    if (sw < 8) return;
    const { ctx } = this;
    const [sx, sy] = cam.toScreen(pane.x, pane.y);
    const sh = pane.h * px;
    this.stats.panes++;
    this.drawCaption(pane, dim);
    const fontPx = 13 * px;
    this.visiblePanes.push({ pane, px, sx, sy, dom: fontPx >= PANE_DOM_MIN_FONT });
    ctx.save();
    if (dim) ctx.globalAlpha = 0.32;
    roundRect(ctx, sx, sy, sw, sh, clamp(6 * px, 1, 8));
    ctx.fillStyle = this.theme.codeBg;
    ctx.fill();
    // code no node explains is a gap in the map: drawn like one
    ctx.strokeStyle = pane.rest ? this.theme.warn : this.theme.line;
    ctx.lineWidth = pane.rest ? 1.5 : 1;
    if (pane.rest) ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
    if (fontPx >= PANE_DOM_MIN_FONT) { ctx.restore(); return; } // DOM pane draws over it
    // header strip (the file itself is named by the caption above the pane)
    ctx.fillStyle = this.theme.line;
    ctx.fillRect(sx, sy + PANE_HEAD * px - 1, sw, 1);
    // greek the actual code shape when it is loaded
    const src = this.app.sources.peek(pane);
    const lh = PANE_LINE * px;
    if (lh >= 1.5) {
      ctx.fillStyle = this.theme.ink3;
      ctx.globalAlpha *= 0.5;
      const maxLines = Math.floor((pane.h - PANE_HEAD - 10) / PANE_LINE);
      const charW = 7.8 * px;
      const rows = pane.rest && src && src.lines ? restRows(pane.segments, src.start, src.end) : null;
      for (let i = 0; i < maxLines; i++) {
        let indent = 0, len = 24 + ((i * 37) % 30);
        if (rows) {
          const row = rows[i];
          if (!row) break;
          if (row.fold) { // a fold: a short faint bar where the line numbers are
            ctx.fillRect(sx + 20 * px, sy + PANE_HEAD * px + 5 * px + i * lh, 24 * px, Math.max(0.5, lh * 0.3));
            continue;
          }
          const ln = src.lines[row.n - src.start];
          if (ln === undefined) break;
          indent = ln.length - ln.trimStart().length;
          len = ln.trim().length;
        } else if (src && src.lines) {
          const ln = src.lines[i];
          if (ln === undefined) break;
          indent = ln.length - ln.trimStart().length;
          len = ln.trim().length;
        } else if (!src) {
          continue;
        }
        if (!len) continue;
        ctx.fillRect(sx + (56 * px) + indent * charW, sy + PANE_HEAD * px + 5 * px + i * lh,
          Math.min(len * charW, sw - 64 * px), Math.max(0.5, lh * 0.5));
      }
    }
    ctx.restore();
    if (!src) this.app.sources.want(pane);
  }

  /**
   * A pane's caption: the file (folder dimmed, name strong) and symbol it shows, readable at
   * the zoom where the node's own text is, and the file's owner when that is another node.
   */
  drawCaption(pane, dim) {
    const cam = this.app.camera;
    const ns = pane.scale / PANE_K; // the node's scale
    const npx = cam.z * ns;
    const st = STYLES.caption;
    const fpx = st.size * npx;
    if (fpx < 1.5) return;
    const { ctx } = this;
    const [sx, sy0] = cam.toScreen(pane.x, pane.y - CAPTION_H * ns);
    const w = pane.w * pane.scale * cam.z;
    const sy = sy0 + (CAPTION_H - 8) * npx; // baseline, just above the pane
    ctx.save();
    if (dim) ctx.globalAlpha *= 0.32;
    const [dir, name] = splitPath(pane.ref.path);
    const sym = pane.rest ? (pane.nodeId === UNMAPPED_ID ? "  · no owner yet" : "  · not yet explained") :
      pane.ref.symbol ? "  › " + pane.ref.symbol : "";
    const files = this.app.files;
    const owner = files?.ownerAt(pane.ref.path, pane.res.start);
    const home = !pane.rest && owner && owner !== pane.nodeId && !this.app.descendants(pane.nodeId).has(owner) ?
      this.app.nodeById.get(owner) : null;
    const t = this.theme;
    if (fpx < 4) {
      // greeked: the caption's shape, so the pane still reads as "a file"
      ctx.fillStyle = t.ink2;
      ctx.globalAlpha *= 0.3;
      const len = Math.min((dir.length + name.length + sym.length) * st.size * 0.6 * npx, w);
      ctx.fillRect(sx, sy - fpx * 0.7, len, Math.max(0.6, fpx * 0.6));
      ctx.restore();
      return;
    }
    const strong = `600 ${fpx}px ${MONO}`, weak = `${st.weight} ${fpx}px ${MONO}`;
    let homeText = home ? `↗ ${home.label}` : "";
    ctx.font = `${fpx * 0.86}px ${SANS}`;
    let homeW = homeText ? ctx.measureText(homeText).width + 12 * npx : 0;
    if (homeW > w * 0.4) { homeText = fitText(ctx, homeText, w * 0.4 - 12 * npx); homeW = w * 0.4; }
    let room = w - homeW;
    ctx.textBaseline = "alphabetic";
    // name first (it identifies the file), then as much of the folder as fits before it
    ctx.font = strong;
    const nameW = ctx.measureText(name).width;
    ctx.font = weak;
    const symText = fitText(ctx, sym, Math.max(0, room - nameW));
    const symW = symText ? ctx.measureText(symText).width : 0;
    const dirText = fitTextLeft(ctx, dir, Math.max(0, room - nameW - symW));
    const dirW = dirText ? ctx.measureText(dirText).width : 0;
    let x = sx;
    ctx.fillStyle = t.ink3;
    if (dirText) ctx.fillText(dirText, x, sy);
    x += dirW;
    ctx.font = strong;
    ctx.fillStyle = t.ink;
    ctx.fillText(fitText(ctx, name, room), x, sy);
    x += nameW;
    ctx.font = weak;
    ctx.fillStyle = pane.rest ? t.warn : t.ink2;
    if (symText) ctx.fillText(symText, x, sy);
    if (homeText) {
      ctx.font = `${fpx * 0.86}px ${SANS}`;
      ctx.fillStyle = t.ink3;
      ctx.textAlign = "right";
      ctx.fillText(homeText, sx + w, sy);
      ctx.textAlign = "left";
    }
    this.stats.text++;
    ctx.restore();
  }

  // -------------------------------------------------------------------- edges
  // --------------------------------------------------------------- routes
  /** Index route segments by the region they are drawn in (once per layout). */
  routeIndex() {
    const L = this.app.layout;
    if (this.routeLayout === L) return this.segsByContainer;
    this.routeLayout = L;
    this.segsByContainer = new Map();
    for (const seg of L.routes) {
      const key = seg.container ?? "";
      if (!this.segsByContainer.has(key)) this.segsByContainer.set(key, []);
      this.segsByContainer.get(key).push(seg);
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (const [x, y] of seg.points) {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
      }
      seg.bbox = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    }
    for (const list of this.segsByContainer.values()) {
      for (const seg of list) if (seg.pair) [seg.near, seg.clear] = nearestAlongside(seg, list);
    }
    return this.segsByContainer;
  }

  /** Items at this depth are large enough on screen for connections to attach to them. */
  levelVisible(depth) {
    return depth <= 0 || this.app.camera.z * Math.pow(K, depth) * CARD_W >= SEG_MIN_W;
  }

  visibleMembers(seg) {
    const app = this.app;
    const out = [];
    for (const net of seg.members) {
      if (!net.rel) continue;
      if (app.hiddenRelTypes.has(net.rel.type)) continue;
      if (app.hidden.has(net.from) || app.hidden.has(net.to)) continue;
      out.push(net);
    }
    return out;
  }

  drawSegments(containerId, view) {
    const segs = this.routeIndex().get(containerId ?? "");
    if (!segs) return;
    const box = containerId == null ? null : this.app.layout.boxes.get(containerId);
    const depth = box ? box.depth + 1 : 0;
    if (!this.levelVisible(depth)) return;
    const app = this.app;
    const { ctx } = this;
    const cam = app.camera;
    const nextVisible = this.levelVisible(depth + 1);
    const drawn = [];
    for (const seg of segs) {
      if (!rectsIntersect(seg.bbox, view)) continue;
      const visible = this.visibleMembers(seg);
      if (!visible.length) continue;
      const screen = seg.points.map(([x, y]) => cam.toScreen(x, y));
      for (const lane of this.lanes(seg, visible, screen, nextVisible)) {
        const { members, pts, arrow } = lane;
        const fd = this.focusDesc;
        const incident = fd && members.some((m) => fd.has(m.from) || fd.has(m.to));
        const isRel = this.relFocus && members.some((m) => m.id === this.relFocus);
        const isPair = !isRel && this.pairFocus && members.some((m) => this.pairFocus.has(m.id));
        const isHover = this.hoverRel && members.some((m) => m.id === this.hoverRel);
        // a line shared by several types is drawn as the most important one: data first
        const type = TYPE_ORDER.find((t) => members.some((m) => m.rel.type === t)) || "control";
        const style = TYPE_STYLE[type];
        let alpha = style.alpha;
        if (fd || this.relFocus) alpha = incident || isRel ? 1 : isPair ? 0.7 : 0.13;
        if (app.flow) alpha = Math.min(alpha, 0.15);
        if (isHover) alpha = Math.max(alpha, 0.95);
        const color = this.theme.rel[type];
        const width = (isRel ? 1.8 : incident || isHover ? 1.4 : isPair ? 1.2 : 1) * style.width * (1.3 + Math.min(3, Math.log2(members.length)));
        // error lines are dashed; unconfirmed lines are dotted only while uncertainty is highlighted
        const dash = style.dash || (app.uncertainty && members.every((m) => UNCERTAIN.has(m.rel.certainty)) ? [3, 3] : null);
        const d = { seg, members, line: lane.line, pts, color, width, alpha, dash, incident, isRel, isPair, isHover };
        drawn.push(d);
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = width;
        if (dash) ctx.setLineDash(dash);
        polyline(ctx, pts, 5);
        ctx.stroke();
        ctx.setLineDash([]);
        if (arrow) arrowTip(ctx, pts, 6 + width);
        ctx.restore();
        this.stats.edges++;
        this.hitSegs.push({ pts, members, route: seg.members, piece: seg, center: screen, sep: seg.sep * cam.z });
        for (const m of members) this.shownRels.add(m.id);
      }
    }
    // crossings: a short gap in the horizontal line makes a crossing read as a crossing,
    // not as a junction where lines meet
    // (only where lines can still be told apart: in a thicket of lines the gaps are noise,
    // and their number grows with the square of the lines)
    const bg = box ? this.fillFor(box) : this.theme.bg;
    const crossings = drawn.length <= GAP_MAX_LINES ? screenCrossings(drawn) : [];
    for (const c of crossings) {
      const { h, v, x, y } = c;
      const g = Math.max(3, v.width / 2 + 2.5);
      ctx.save();
      ctx.fillStyle = bg;
      ctx.fillRect(x - g, y - h.width / 2 - 1, 2 * g, h.width + 2);
      ctx.globalAlpha = v.alpha;
      ctx.strokeStyle = v.color;
      ctx.lineWidth = v.width;
      if (v.dash) ctx.setLineDash(v.dash);
      ctx.beginPath();
      ctx.moveTo(x, y - g - 1);
      ctx.lineTo(x, y + g + 1);
      ctx.stroke();
      ctx.restore();
    }
    // annotations are placed once all lines are drawn (drawNotes): a line crossing region
    // borders is drawn in pieces, one per region, but is annotated once
    const notes = [];
    for (const d of drawn) {
      const { members, incident, isRel, isPair, isHover } = d;
      const focused = incident || isRel || isPair || isHover;
      const one = members.length === 1 ? members[0].rel : null;
      const named = members.length <= 3 && members.every((m) => (m.rel.type === "data" || m.rel.type === "error") && m.rel.label);
      if (members.length > 1 && named && (focused || (!this.busy && !app.flow))) {
        // a few data lines sharing a route (a result and an error, say) name each payload
        notes.push({ d, rank: isRel ? -1 : focused ? 0 : 2, text: members.map((m) => noteText(m.rel)).join("  /  ") });
      } else if (members.length > 1 && (!this.busy || focused)) {
        notes.push({ d, rank: focused ? 0 : 1, badge: "×" + members.length });
      } else if (one && (focused || ((one.type === "data" || one.type === "error") && one.label && !this.busy && !app.flow))) {
        // data lines name what they carry; other lines say what they are when focused
        notes.push({ d, rank: isRel ? -1 : focused ? 0 : 2, text: noteText(one) });
      }
    }
    if (!notes.length) return;
    // annotations keep off the boxes of the piece's region and its title
    const blocked = [];
    const items = box ? box.node.children : app.layout.roots;
    const rects = items.filter((id) => app.layout.boxes.has(id)).map((id) => worldRect(app.layout.boxes.get(id)));
    for (const r of [...rects, ...(box ? [headerRect(box)] : [])]) {
      const [x0, y0] = cam.toScreen(r.x, r.y), [x1, y1] = cam.toScreen(r.x + r.w, r.y + r.h);
      blocked.push({ x0, y0, x1, y1 });
    }
    for (const n of notes) this.notes.push({ ...n, crossings, blocked });
  }

  /**
   * What a routed piece is drawn as: one line, or for an exchange's input and answer two
   * lanes of the same route, each drawn in its own direction (its points end where its arrow
   * is) and offset to its left, so the two never cross each other. `line` identifies the
   * lane across the pieces of its route (for one annotation per lane).
   */
  lanes(seg, visible, pts, nextVisible) {
    const boxes = this.app.layout.boxes;
    const atDst = seg.dstItem && (seg.terminal || !nextVisible || !boxes.get(seg.dstItem)?.grid);
    if (!seg.pair) return [{ members: visible, pts, arrow: atDst, line: seg.members }];
    if (!this.laneKeys) this.laneKeys = new WeakMap();
    let keys = this.laneKeys.get(seg.members);
    if (!keys) {
      keys = [{}, {}];
      this.laneKeys.set(seg.members, keys);
    }
    const atSrc = seg.srcItem && (seg.terminalSrc || !nextVisible || !boxes.get(seg.srcItem)?.grid);
    // lanes lie LANE_SEP·sep from the route (the room the router keeps), at most LANE_MAX
    // px; where another line runs closer, only as far as leaves a clear gap of
    // min(sep, LANE_GAP) px to it. Each piece uses what it has, so a pair can be two lanes in
    // one region and one line in a crowded one (zoomed out).
    // Between the top-level regions every pair has the same, wider lanes at a given zoom:
    // the router keeps their room there. Only a pair it could not give that room (a border
    // too crowded) narrows like the pairs inside regions do.
    const z = this.app.camera.z, sepPx = seg.sep * z, laneSep = seg.laneSep ?? LANE_SEP;
    const room = (seg.near * z - Math.min(sepPx, LANE_GAP)) / 2;
    const roomy = seg.clear - laneSep * seg.sep >= seg.sep - 1e-6; // the router kept its room
    const lane = seg.outside && roomy ? Math.min(LANE_MAX_OUTSIDE, laneSep * sepPx) :
      Math.max(0, Math.min(seg.outside ? LANE_MAX_OUTSIDE : LANE_MAX, laneSep * sepPx, room));
    const ask = visible.filter((m) => m.rel.leg === "input");
    const back = visible.filter((m) => m.rel.leg !== "input");
    const out = [];
    if (ask.length) out.push({ members: ask, pts: offsetPolyline(pts, lane), arrow: atDst, line: keys[0] });
    if (back.length) {
      out.push({ members: back, pts: offsetPolyline([...pts].reverse(), lane), arrow: atSrc, line: keys[1] });
    }
    return out;
  }

  /**
   * One annotation per line (its pieces share their members): focused lines first, then
   * bundle counts, then data labels; each on the piece with the longest straight run where
   * it fits, away from crossings, boxes and annotations already placed. A line with no free
   * spot is left unannotated (hover shows it).
   */
  drawNotes() {
    const { ctx } = this;
    const lines = new Map();
    for (const n of this.notes) {
      const key = n.d.line;
      if (!lines.has(key)) lines.set(key, { rank: n.rank, pieces: [] });
      const line = lines.get(key);
      line.rank = Math.min(line.rank, n.rank);
      line.pieces.push(n);
    }
    const order = [...lines.values()].sort((a, b) => a.rank - b.rank);
    for (const { pieces } of order) {
      pieces.sort((a, b) => longestRun(b.d.pts) - longestRun(a.d.pts));
      for (const n of pieces) {
        const { d } = n;
        ctx.font = n.badge ? `700 11px ${SANS}` : `11px ${SANS}`;
        const text = n.text && fitText(ctx, n.text, 260);
        const size = n.badge ? [ctx.measureText(n.badge).width + 10, 18] : [ctx.measureText(text).width + 8, 16];
        const spot = annotationSpot(d.pts, n.crossings, size, this.noteRects, n.blocked);
        if (!spot) continue;
        ctx.save();
        ctx.globalAlpha = d.alpha;
        const emphasis = d.isRel ? this.theme.sel : d.isHover || d.isPair ? d.color : null;
        if (n.badge) badge(ctx, spot[0], spot[1], n.badge, d.color, this.theme.bg);
        else label(ctx, spot[0], spot[1], text, this.theme, emphasis);
        ctx.restore();
        this.noted.push(d.members.map((m) => m.id).join(","));
        // the annotation is a click target for its line (slightly larger than drawn)
        this.labelHits.push({ x0: spot[0] - size[0] / 2 - 3, y0: spot[1] - size[1] / 2 - 3,
          x1: spot[0] + size[0] / 2 + 3, y1: spot[1] + size[1] / 2 + 3, members: d.members, pts: d.pts });
        break;
      }
    }
  }

  inside(ancestorId, id) {
    const chain = this.app.layout.ancestors.get(id);
    return chain ? chain.includes(ancestorId) : false;
  }

  // --------------------------------------------------------------------- flows
  drawFlow(view) {
    const app = this.app;
    const flow = app.flow;
    const { ctx } = this;
    const cam = app.camera;
    const steps = flow.steps;
    const t = this.theme;
    // a flow is drawn on the relationships its steps use (step.link), never on lines of
    // its own; a relationship used by several steps is highlighted once
    const byRel = new Map();
    steps.forEach((s, i) => {
      if (!s.link) return;
      if (!byRel.has(s.link)) byRel.set(s.link, []);
      byRel.get(s.link).push(i);
    });
    for (const seg of app.layout.routes) {
      const nets = seg.members.filter((m) => byRel.has(m.id));
      if (!nets.length) continue;
      const depth = seg.container == null ? 0 : app.layout.boxes.get(seg.container).depth + 1;
      if (!this.levelVisible(depth)) continue;
      if (seg.container != null && !this.drawnContainers.has(seg.container)) continue;
      if (!rectsIntersect(seg.bbox, view)) continue;
      const screen = seg.points.map(([x, y]) => cam.toScreen(x, y));
      const nextVisible = this.levelVisible(depth + 1);
      // on a pair, only the lane of the leg the step uses
      for (const lane of this.lanes(seg, nets, screen, nextVisible)) {
        const used = lane.members.flatMap((m) => byRel.get(m.id));
        const failure = used.every((i) => steps[i].kind === "failure" || steps[i].kind === "compensation");
        const current = used.includes(app.flowStep);
        ctx.save();
        ctx.strokeStyle = failure ? t.flowFail : t.flow;
        ctx.fillStyle = ctx.strokeStyle;
        ctx.lineWidth = current ? 4 : 2.5;
        ctx.globalAlpha = current ? 1 : 0.85;
        if (failure) ctx.setLineDash([7, 5]);
        polyline(ctx, lane.pts, 5);
        ctx.stroke();
        ctx.setLineDash([]);
        // the relationship's own direction, as for any relationship line
        if (lane.arrow) arrowTip(ctx, lane.pts, current ? 11 : 9);
        ctx.restore();
      }
    }
    // numbered badges, stacked per representative box
    const perRep = new Map();
    steps.forEach((s, i) => {
      if (!s.node) return;
      const r = this.rep(s.node, this.flowDmax);
      if (!perRep.has(r)) perRep.set(r, []);
      perRep.get(r).push({ s, i });
    });
    for (const [r, list] of perRep) {
      const box = app.layout.boxes.get(r);
      const wr = worldRect(box);
      if (!rectsIntersect(wr, view)) continue;
      const [sx, sy] = cam.toScreen(wr.x, wr.y);
      let x = sx - 8;
      for (const { s, i } of list) {
        const mark = s.kind === "failure" ? " ✕" : s.kind === "compensation" ? " ↺" :
          s.kind === "alternative" ? " ⑂" : "";
        const text = String(i + 1) + mark;
        const fail = s.kind === "failure" || s.kind === "compensation";
        const w = flowBadge(ctx, x, sy - 8, text, fail ? t.flowFail : t.flow, i === app.flowStep);
        x += w + 4;
      }
    }
  }

  /**
   * Relationship(s) under the pointer, from the last frame: an annotation (label or bundle
   * badge) first, since it names one line exactly, then the nearest line within 6 px.
   */
  hitRoute(sx, sy) {
    for (let i = (this.labelHits || []).length - 1; i >= 0; i--) {
      const h = this.labelHits[i];
      if (sx >= h.x0 && sx <= h.x1 && sy >= h.y0 && sy <= h.y1) return { ...h, label: true };
    }
    let best = null, bestD = 6;
    for (const h of this.hitSegs || []) {
      for (let i = 1; i < h.pts.length; i++) {
        const d = segDist(sx, sy, h.pts[i - 1], h.pts[i]);
        if (d < bestD) { bestD = d; best = h; }
      }
    }
    return best;
  }

  drawSelection() {
    const app = this.app;
    const { ctx } = this;
    const cam = app.camera;
    const outline = (id, color, width) => {
      const box = app.layout.boxes.get(id);
      if (!box) return;
      const wr = worldRect(box);
      const [sx, sy] = cam.toScreen(wr.x, wr.y);
      const sw = wr.w * cam.z, sh = wr.h * cam.z;
      if (sw < 1) return;
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      roundRect(ctx, sx - width / 2, sy - width / 2, sw + width, sh + width,
        clamp(10 * box.scale * cam.z, 2, 14));
      ctx.stroke();
      ctx.restore();
    };
    if (app.hover && app.hover !== app.selection?.id) outline(app.hover, this.theme.hover, 2);
    const sel = app.selection;
    if (sel?.type === "node") outline(sel.id, this.theme.sel, 3);
    if (sel?.type === "file" && app.files) {
      // the file's owners, and (fainter) the nodes that cite it
      const owners = app.files.ownersOf(sel.path);
      for (const c of app.files.cited.get(sel.path) || []) if (!owners.includes(c.node)) outline(c.node, this.theme.hover, 2);
      for (const id of owners) outline(id, this.theme.sel, 3);
    }
    if (sel?.type === "relationship") {
      const rel = app.relById.get(sel.id);
      if (rel) {
        outline(this.rep(rel.from, this.dmax), this.theme.sel, 2);
        outline(this.rep(rel.to, this.dmax), this.theme.sel, 2);
      }
    }
  }

  // --------------------------------------------------------------- hit testing
  hitTest(sx, sy) {
    const app = this.app;
    if (!app.layout) return null;
    const cam = app.camera;
    const [wx, wy] = cam.toWorld(sx, sy);
    let found = null;
    const visit = (box) => {
      const wr = worldRect(box);
      if (wx < wr.x || wy < wr.y || wx > wr.x + wr.w || wy > wr.y + wr.h) return false;
      if (wr.w * cam.z < 4) return false;
      found = { type: "node", id: box.id };
      if (app.hidden.has(box.id)) return true;
      if (wr.w * cam.z * K < 2) return true;
      for (const it of box.items) {
        if (it.kind === "node") {
          if (visit(it.box)) return true;
        } else {
          const p = it.box;
          const pw = p.w * p.scale, ph = p.h * p.scale;
          if (wx >= p.x && wy >= p.y && wx <= p.x + pw && wy <= p.y + ph && pw * cam.z >= 4) {
            found = { type: "pane", id: p.id, nodeId: box.id };
            return true;
          }
          const ch = CAPTION_H * p.scale / PANE_K;
          if (wx >= p.x && wy >= p.y - ch && wx <= p.x + pw && wy < p.y && ch * cam.z >= 6) {
            found = { type: "caption", id: p.id, nodeId: box.id, path: p.ref.path, line: p.res.start };
            return true;
          }
        }
      }
      return true;
    };
    for (const id of app.layout.roots) if (visit(app.layout.boxes.get(id))) break;
    return found;
  }
}

// ----------------------------------------------------------------- geometry
function roundRect(ctx, x, y, w, h, r) {
  w = Math.max(0, w);
  h = Math.max(0, h);
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function hatch(ctx, x, y, w, h, color) {
  ctx.save();
  ctx.clip();
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.18;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let d = -h; d < w; d += 12) {
    ctx.moveTo(x + d, y + h);
    ctx.lineTo(x + d + h, y);
  }
  ctx.stroke();
  ctx.restore();
}

function polyline(ctx, pts, radius) {
  // orthogonal polyline with slightly rounded corners
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length - 1; i++) {
    const [ax, ay] = pts[i - 1], [bx, by] = pts[i], [cx, cy] = pts[i + 1];
    const r = Math.min(radius, Math.hypot(bx - ax, by - ay) / 2, Math.hypot(cx - bx, cy - by) / 2);
    if (r > 0.5) ctx.arcTo(bx, by, cx, cy, r);
    else ctx.lineTo(bx, by);
  }
  const last = pts[pts.length - 1];
  ctx.lineTo(last[0], last[1]);
}

function arrowTip(ctx, pts, size) {
  const tip = pts[pts.length - 1];
  let k = pts.length - 2;
  while (k > 0 && Math.hypot(pts[k][0] - tip[0], pts[k][1] - tip[1]) < 0.5) k--;
  const from = pts[k];
  const ang = Math.atan2(tip[1] - from[1], tip[0] - from[0]);
  ctx.beginPath();
  ctx.moveTo(tip[0], tip[1]);
  ctx.lineTo(tip[0] - size * Math.cos(ang - 0.42), tip[1] - size * Math.sin(ang - 0.42));
  ctx.lineTo(tip[0] - size * Math.cos(ang + 0.42), tip[1] - size * Math.sin(ang + 0.42));
  ctx.closePath();
  ctx.fill();
}

/**
 * The closest distance (world units) at which another line's piece runs alongside this
 * pair's piece, and the same less that line's own lane width if it is a pair (Infinity if
 * none): how much room its lanes have.
 */
function nearestAlongside(seg, others) {
  let near = Infinity, clear = Infinity;
  const runs = (s) => {
    const out = [];
    for (let i = 1; i < s.points.length; i++) out.push([s.points[i - 1], s.points[i]]);
    return out;
  };
  const mine = runs(seg);
  const pad = seg.sep * 2;
  const zone = { x: seg.bbox.x - pad, y: seg.bbox.y - pad, w: seg.bbox.w + 2 * pad, h: seg.bbox.h + 2 * pad };
  for (const o of others) {
    if (o === seg || o.members === seg.members || !rectsIntersect(o.bbox, zone)) continue;
    for (const [p, q] of mine) {
      for (const [r, t] of runs(o)) {
        for (const ax of [0, 1]) {
          const c = 1 - ax; // runs parallel to axis ax, at constant coordinate c
          if (Math.abs(p[c] - q[c]) > 1e-6 || Math.abs(r[c] - t[c]) > 1e-6) continue;
          const lo = Math.max(Math.min(p[ax], q[ax]), Math.min(r[ax], t[ax]));
          const hi = Math.min(Math.max(p[ax], q[ax]), Math.max(r[ax], t[ax]));
          if (hi - lo <= 1e-6) continue;
          const d = Math.abs(p[c] - r[c]);
          near = Math.min(near, d);
          clear = Math.min(clear, d - (o.pair ? (o.laneSep ?? 0) * o.sep : 0));
        }
      }
    }
  }
  return [near, clear];
}

/**
 * An orthogonal polyline moved `d` px to the left of its direction (screen coordinates,
 * y down): ends move along their piece's normal, corners by both normals (a mitre).
 */
function offsetPolyline(pts, d) {
  if (pts.length < 2) return pts;
  const normals = [];
  for (let i = 1; i < pts.length; i++) {
    const dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1];
    const len = Math.hypot(dx, dy) || 1;
    normals.push([dy / len, -dx / len]);
  }
  return pts.map((p, i) => {
    const a = normals[Math.max(0, i - 1)], b = normals[Math.min(normals.length - 1, i)];
    const same = Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
    const [nx, ny] = same ? a : [a[0] + b[0], a[1] + b[1]];
    return [p[0] + nx * d, p[1] + ny * d];
  });
}

/** A line's annotation: the data it carries (an error leg marked ✕), else what it is. */
function noteText(r) {
  if (r.type === "data" || r.type === "error") return relText(r);
  return r.label ? `${relText(r)}: ${r.label}` : relText(r);
}

/** Proper crossings between horizontal and vertical pieces of different drawn lines. */
function screenCrossings(drawn, limit = GAP_MAX_CROSSINGS) {
  const H = [], V = [];
  drawn.forEach((d) => {
    for (let i = 1; i < d.pts.length; i++) {
      const [p, q] = [d.pts[i - 1], d.pts[i]];
      if (Math.abs(p[1] - q[1]) < 0.01) H.push([p[1], Math.min(p[0], q[0]), Math.max(p[0], q[0]), d]);
      else if (Math.abs(p[0] - q[0]) < 0.01) V.push([p[0], Math.min(p[1], q[1]), Math.max(p[1], q[1]), d]);
    }
  });
  H.sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const [x, y0, y1, v] of V) {
    let lo = 0, hi = H.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (H[mid][0] <= y0 + 1) lo = mid + 1; else hi = mid; }
    for (let k = lo; k < H.length && H[k][0] < y1 - 1; k++) {
      const [y, x0, x1, h] = H[k];
      if (h !== v && x > x0 + 1 && x < x1 - 1) out.push({ x, y, h, v });
      if (out.length >= limit) return out;
    }
  }
  return out;
}

/** Length of the longest straight run of a polyline. */
function longestRun(pts) {
  let m = 0;
  for (let i = 1; i < pts.length; i++) m = Math.max(m, Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return m;
}

/**
 * Where an annotation of `size` [w, h] (screen px) can sit on a polyline: on the longest
 * straight run that holds it, away from crossings, from `blocked` rects (boxes) and from
 * rects already `taken` (the chosen rect is added). null if there is no such spot.
 */
function annotationSpot(pts, crossings, [w, h], taken, blocked = []) {
  const runs = [];
  for (let i = 1; i < pts.length; i++) {
    const [a, b] = [pts[i - 1], pts[i]];
    runs.push({ a, b, len: Math.hypot(b[0] - a[0], b[1] - a[1]), horiz: Math.abs(b[1] - a[1]) < 0.5 });
  }
  runs.sort((p, q) => q.len - p.len);
  const pad = 4;
  for (const { a, b, len, horiz } of runs) {
    const need = (horiz ? w : h) + 24; // the annotation plus room for the line on both sides
    if (len < need) continue;
    for (const t of [0.5, 0.35, 0.65, 0.2, 0.8]) {
      if (len * Math.min(t, 1 - t) < need / 2) continue;
      const x = a[0] + (b[0] - a[0]) * t, y = a[1] + (b[1] - a[1]) * t;
      const r = { x0: x - w / 2 - pad, y0: y - h / 2 - pad, x1: x + w / 2 + pad, y1: y + h / 2 + pad };
      if (crossings.some((c) => c.x > r.x0 - 6 && c.x < r.x1 + 6 && c.y > r.y0 - 6 && c.y < r.y1 + 6)) continue;
      const hits = (q) => q.x0 < r.x1 && r.x0 < q.x1 && q.y0 < r.y1 && r.y0 < q.y1;
      if (taken.some(hits) || blocked.some(hits)) continue;
      taken.push(r);
      return [x, y];
    }
  }
  return null;
}

function segDist(px, py, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 ? clamp(((px - a[0]) * dx + (py - a[1]) * dy) / l2, 0, 1) : 0;
  return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
}

function badge(ctx, x, y, text, color, bg) {
  ctx.font = `700 11px ${SANS}`;
  const w = ctx.measureText(text).width + 10;
  roundRect(ctx, x - w / 2, y - 9, w, 18, 9);
  ctx.fillStyle = bg;
  ctx.fill();
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = color;
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText(text, x, y + 0.5);
  ctx.textAlign = "left";
}

function label(ctx, x, y, text, theme, emphasis) {
  ctx.font = `11px ${SANS}`;
  const w = ctx.measureText(text).width + 8;
  ctx.globalAlpha = Math.min(1, ctx.globalAlpha + 0.2);
  ctx.fillStyle = theme.bg;
  ctx.fillRect(x - w / 2, y - 8, w, 16);
  if (emphasis) {
    // a selected or hovered line's label is framed: it is what a click picks
    ctx.strokeStyle = emphasis;
    ctx.lineWidth = 1.2;
    ctx.strokeRect(x - w / 2 + 0.5, y - 7.5, w - 1, 15);
  }
  ctx.fillStyle = emphasis ? theme.ink : theme.ink2;
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText(text, x, y);
  ctx.textAlign = "left";
}

function flowBadge(ctx, x, y, text, color, current) {
  ctx.font = `700 ${current ? 14 : 12}px ${SANS}`;
  const h = current ? 24 : 20;
  const w = Math.max(h, ctx.measureText(text).width + 12);
  roundRect(ctx, x, y, w, h, h / 2);
  ctx.fillStyle = color;
  ctx.fill();
  if (current) {
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
  }
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillText(text, x + w / 2, y + h / 2 + 0.5);
  ctx.textAlign = "left";
  return w;
}

/** Like fitText, but keeps the end of the text (the part of a folder nearest the file). */
function fitTextLeft(ctx, text, maxW) {
  if (!text || ctx.measureText(text).width <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ctx.measureText("…" + text.slice(mid)).width <= maxW) hi = mid; else lo = mid + 1;
  }
  return lo >= text.length ? "" : "…" + text.slice(lo);
}

export function fitText(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ctx.measureText(text.slice(0, mid) + "…").width <= maxW) lo = mid;
    else hi = mid - 1;
  }
  return lo > 0 ? text.slice(0, lo) + "…" : "";
}

function wrapFit(ctx, text, maxW, maxLines) {
  if (maxLines <= 1) return [fitText(ctx, text, maxW)];
  const words = text.split(/\s+/);
  const lines = [];
  let line = "";
  for (let i = 0; i < words.length; i++) {
    const cand = line ? line + " " + words[i] : words[i];
    if (ctx.measureText(cand).width <= maxW || !line) line = cand;
    else {
      lines.push(line);
      line = words[i];
      if (lines.length === maxLines - 1) {
        line = words.slice(i).join(" ");
        break;
      }
    }
  }
  if (line) lines.push(line);
  return lines.slice(0, maxLines).map((l) => fitText(ctx, l, maxW));
}
