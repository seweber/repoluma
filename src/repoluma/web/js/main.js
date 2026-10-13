// repoluma viewer: application state, input handling, URL state, live refresh.

import { Camera } from "./camera.js";
import { TUNING } from "./arrange.js";
import { computeLayout, relText, worldRect } from "./layout.js";
import { PaneLayer, SourceCache } from "./panes.js";
import { Renderer, readTheme } from "./render.js";
import { FileIndex, UNMAPPED_ID } from "./files.js";
import { computeImpact, renderCoverage, renderFiles, renderFilters, renderFlowBar, renderFlows, renderInspector, renderMapLegend, renderOutline } from "./ui.js";
import { clamp, clear, copyText, debounce, el, plural } from "./util.js";

const UNMAPPED_PANES = 24; // code panes in the "not yet mapped" region at most

const TOKEN = document.querySelector('meta[name="repoluma-token"]').content;

async function api(path) {
  const res = await fetch(path, { headers: { "X-Repoluma-Token": TOKEN }, cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

class App {
  constructor() {
    this.$ = (sel) => document.querySelector(sel);
    this.stage = this.$("#stage");
    this.canvas = this.$("#map");
    this.camera = new Camera(() => this.requestRender());
    this.renderer = new Renderer(this.canvas, this);
    this.sources = new SourceCache(api, () => this.requestRender());
    this.panes = new PaneLayer(this.$("#panes"), this);
    this.selection = null;
    this.hover = null;
    this.flow = null;
    this.flowStep = 0;
    this.hiddenRelTypes = new Set();
    this.hiddenNodeKinds = new Set();
    this.hiddenTags = new Set();
    this.hidden = new Set();
    this.uncertainty = false;
    this.filterVersion = 0;
    this.history = [];
    this.digest = null;
    this.tokens = {};
    this.frame = null;
    this.layout = null;
    this.tab = "outline";
    this.measureCtx = document.createElement("canvas").getContext("2d");
    this.measureCache = new Map();
    this.perf = { frames: [] };
    this.bindEvents();
    this.resize();
  }

  // ------------------------------------------------------------- data
  async load(reason) {
    let data;
    try {
      data = await api("/api/bootstrap" + (reason === "force" ? "?force=1" : ""));
    } catch (err) {
      this.showFatal(`Cannot reach the repoluma server (${err.message}). Is \`repoluma serve\` still running?`);
      return;
    }
    const prevTokens = this.tokens;
    this.data = data;
    if (!data.model) {
      this.showOnboarding(data);
      const st = await api("/api/status").catch(() => null);
      if (st) { this.digest = st.digest; this.tokens = st.tokens; }
      return;
    }
    this.$("#onboarding").hidden = true;
    const anchor = this.captureAnchor();
    this.model = data.model;
    this.refs = data.refs;
    this.indexFiles(data.ownership);
    this.index();
    const t0 = performance.now();
    this.layout = computeLayout(this.model, this.refs, (font, text) => this.measure(font, text));
    this.perf.layoutMs = performance.now() - t0;
    this.renderer.edgeCache = null;
    this.recomputeHidden();
    const b = this.layout.bounds;
    renderMapLegend(this, this.$("#maplegend")); // before fitting: the fit keeps clear of it
    const fit = this.camera.fitState(b, 0.05, this.insets());
    this.camera.minZ = fit.z / 3;
    this.camera.maxZ = 4 / this.layout.minScale;
    if (this.selection && !this.recordExists(this.selection)) this.selection = null;
    if (this.flow) this.flow = this.flowById.get(this.flow.id) || null;
    if (this.flow) this.flowStep = clamp(this.flowStep, 0, this.flow.steps.length - 1);
    this.panes.reset();
    if (reason === "initial") {
      if (!this.restoreFromHash()) this.camera.set(fit, false);
    } else if (anchor) {
      this.restoreAnchor(anchor);
    }
    // invalidate source excerpts for files whose stat token changed
    const status = await api("/api/status").catch(() => null);
    if (status) {
      const changed = new Set();
      for (const [p, tok] of Object.entries(status.tokens)) if (prevTokens[p] && prevTokens[p] !== tok) changed.add(p);
      const listing = changed.delete("__files__");
      if (reason !== "initial") this.sources.invalidate(changed.size ? changed : null);
      this.tokens = status.tokens;
      this.digest = status.digest;
      if (reason === "poll") {
        const files = [...changed].filter((p) => p !== "__map__");
        this.toast(changed.has("__map__") ? "Map changed: reloaded" + (files.length ? ` (and ${plural(files.length, "source file")})` : "") :
          files.length ? `Source changed: ${files.slice(0, 3).join(", ")}${files.length > 3 ? "…" : ""}` :
            listing ? "Files added or removed: ownership updated" : "Reloaded");
      }
    }
    this.renderChrome();
    this.requestRender();
  }

  /** Recompute the layout (used by the layout evaluation harness). */
  relayout() {
    const t0 = performance.now();
    this.layout = computeLayout(this.model, this.refs, (font, text) => this.measure(font, text));
    this.perf.layoutMs = performance.now() - t0;
    this.panes.reset();
    this.requestRender();
    return this.perf.layoutMs;
  }

  /**
   * Index which node owns which file, add the "Not yet mapped" region for code files without
   * an owner, and give every node the short list of files its header shows.
   */
  indexFiles(ownership) {
    const m = this.model;
    this.files = new FileIndex(ownership, m, this.refs);
    const n = this.files.unowned.length;
    if (n) {
      m.nodes.push({ id: UNMAPPED_ID, label: "Not yet mapped", kind: "unmapped", parent: null, depth: 0,
        summary: `${plural(n, "code file")} ${n === 1 ? "has" : "have"} no owner in the map yet. Give each to ` +
          "the node it belongs to with 'owns', or list it under source.ownership.ignore.",
        refs: [], owns: [], children: [], tags: [], synthetic: true });
    }
    // files are named on the outermost boxes; inside, the panes' captions name them
    const byId = new Map(m.nodes.map((node) => [node.id, node]));
    for (const node of m.nodes) {
      node.fileItems = node.parent == null ? this.files.lineItems(node.id) : [];
      node.restPanes = [];
    }
    for (const rp of ownership?.rest || []) byId.get(rp.area)?.restPanes.push(rp);
    // "not yet mapped" is a to-do list: a few files' code, the rest listed in its inspector
    const todo = byId.get(UNMAPPED_ID);
    if (todo && todo.restPanes.length > UNMAPPED_PANES) {
      todo.summary += ` The code of the first ${UNMAPPED_PANES} is shown here; all are listed in the Files tab.`;
      todo.restPanes = todo.restPanes.slice(0, UNMAPPED_PANES);
    }
  }

  index() {
    const m = this.model;
    this.nodeById = new Map(m.nodes.map((n) => [n.id, n]));
    this.relById = new Map(m.relationships.map((r) => [r.id, r]));
    this.flowById = new Map(m.flows.map((f) => [f.id, f]));
    this.outgoing = new Map();
    this.incoming = new Map();
    for (const r of m.relationships) {
      if (!this.outgoing.has(r.from)) this.outgoing.set(r.from, []);
      if (!this.incoming.has(r.to)) this.incoming.set(r.to, []);
      this.outgoing.get(r.from).push(r);
      this.incoming.get(r.to).push(r);
    }
    this.descCache = new Map();
  }

  descendants(id) {
    if (this.descCache.has(id)) return this.descCache.get(id);
    const out = new Set([id]);
    const stack = [id];
    while (stack.length) {
      const n = this.nodeById.get(stack.pop());
      for (const c of n?.children || []) { out.add(c); stack.push(c); }
    }
    this.descCache.set(id, out);
    return out;
  }


  recordExists(sel) {
    if (sel.type === "bundle") return sel.ids.every((id) => this.relById.has(id));
    if (sel.type === "file") return !!this.files.files[sel.path] || this.files.cited.has(sel.path);
    return sel.type === "node" ? this.nodeById.has(sel.id) : this.relById.has(sel.id);
  }

  measure(font, text) {
    const key = font + "\u0000" + text;
    let w = this.measureCache.get(key);
    if (w === undefined) {
      if (this.measureCtx.font !== font) this.measureCtx.font = font;
      w = this.measureCtx.measureText(text).width;
      if (this.measureCache.size > 50000) this.measureCache.clear();
      this.measureCache.set(key, w);
    }
    return w;
  }

  // ---------------------------------------------------------- filters
  recomputeHidden() {
    this.hidden = new Set();
    for (const n of this.model.nodes) {
      if (this.hiddenNodeKinds.has(n.kind) || (n.tags || []).some((t) => this.hiddenTags.has(t))) {
        for (const d of this.descendants(n.id)) this.hidden.add(d);
      }
    }
    this.filterVersion++;
  }

  toggleSet(name, value, on) {
    if (on) this[name].add(value); else this[name].delete(value);
    if (name !== "hiddenRelTypes") this.recomputeHidden(); else this.filterVersion++;
    this.afterFilterChange();
  }

  setRelTypes(hidden) {
    this.hiddenRelTypes = new Set(hidden);
    this.filterVersion++;
    this.afterFilterChange();
    renderFilters(this, this.$("#tab-filters"));
  }

  setUncertainty(on) {
    this.uncertainty = on;
    this.afterFilterChange();
  }

  afterFilterChange() {
    renderMapLegend(this, this.$("#maplegend"));
    this.requestRender();
    this.updateHash();
    if (this.tab === "outline") renderOutline(this, this.$("#tab-outline"));
  }

  focusSet() {
    if (!this.flow) return null;
    // the flow's participants: the steps' nodes and both ends of the relationships the
    // flow is drawn on (a coordinator that is not a step itself still takes part)
    const ids = new Set();
    for (const s of this.flow.steps) {
      if (s.node) ids.add(s.node);
      const rel = s.link ? this.relById.get(s.link) : null;
      if (rel) ids.add(rel.from).add(rel.to);
    }
    const set = new Set();
    for (const id of ids) {
      for (const a of this.layout.ancestors.get(id) || []) set.add(a);
      for (const d of this.descendants(id)) set.add(d);
    }
    return set;
  }

  // ------------------------------------------------------- selection
  /** Fetch lines of a file the map owns or cites (used by the file view). */
  fetchSource(path, start, end) {
    return api(`/api/source?path=${encodeURIComponent(path)}&start=${start}&end=${end}`);
  }

  select(sel, opts = {}) {
    this.selection = sel;
    document.body.classList.toggle("file-open", sel?.type === "file");
    if (this.tab === "files") {
      for (const b of document.querySelectorAll(".ffile")) b.classList.toggle("selected", sel?.type === "file" && b.dataset.path === sel.path);
    }
    if (opts.fly && sel?.type === "node") this.flyToNode(sel.id);
    renderInspector(this, this.$("#inspector-body"));
    this.$("#inspector").classList.toggle("empty", !sel);
    if (this.tab === "outline") this.markOutlineSelection();
    this.requestRender();
    this.updateHash();
  }

  /** Show a file (and which pane shows each line) and move the map to its area. */
  selectFile(path, line = null, opts = { fly: true }) {
    if (!this.files.files[path] && !this.files.cited.has(path)) {
      this.toast(`${path} is not owned or cited by the map`);
      return;
    }
    this.select({ type: "file", path, line });
    const owner = this.files.ownerAt(path, line) || this.files.cited.get(path)?.[0]?.node;
    if (opts.fly && owner && this.layout.boxes.has(owner)) this.flyToNode(owner);
  }

  selectRecord(id) {
    if (this.nodeById.has(id)) this.select({ type: "node", id }, { fly: true });
    else if (this.relById.has(id)) this.select({ type: "relationship", id });
    else if (this.flowById.has(id)) this.setFlow(id, 0);
    else if (id && id.includes("/")) this.setFlow(id.split("/")[0], 0);
  }

  markOutlineSelection() {
    for (const b of document.querySelectorAll(".tree-item.selected")) b.classList.remove("selected");
    if (this.selection?.type === "node") {
      const b = document.querySelector(`.tree-item[data-id="${CSS.escape(this.selection.id)}"]`);
      if (b) { b.classList.add("selected"); b.scrollIntoView({ block: "nearest" }); }
    }
  }

  setFlow(id, step = 0) {
    this.flow = id ? this.flowById.get(id) : null;
    this.flowStep = step;
    if (this.flow) {
      this.switchTab("flows");
      const s = this.flow.steps[step];
      if (s?.node) {
        this.selection = { type: "node", id: s.node };
        renderInspector(this, this.$("#inspector-body"));
        this.flyToFlowStep(step);
      }
    }
    renderFlows(this, this.$("#tab-flows"));
    renderFlowBar(this, this.$("#flowbar"));
    renderMapLegend(this, this.$("#maplegend"));
    this.requestRender();
    this.updateHash();
  }

  flyToFlowStep(i) {
    // Frame the step together with the relationship it uses (both ends), or with the
    // previous step when it has none, so the move stays legible.
    const s = this.flow.steps[i];
    const rel = s.link ? this.relById.get(s.link) : null;
    const prev = s.after ? this.flow.steps.find((x) => x.id === s.after) : null;
    const ids = rel ? [s.node, rel.from, rel.to] : [s.node, prev?.node];
    const rects = ids.filter((id) => id && this.layout.boxes.has(id)).map((id) => worldRect(this.layout.boxes.get(id)));
    if (!rects.length) return;
    const x = Math.min(...rects.map((r) => r.x)), y = Math.min(...rects.map((r) => r.y));
    const r = { x, y, w: Math.max(...rects.map((q) => q.x + q.w)) - x, h: Math.max(...rects.map((q) => q.y + q.h)) - y };
    this.flyToRect(r, 0.25);
  }

  stepFlow(delta) {
    if (!this.flow) return;
    const i = clamp(this.flowStep + delta, 0, this.flow.steps.length - 1);
    this.setFlow(this.flow.id, i);
  }

  showCrossing(id) {
    const inside = this.descendants(id);
    const list = this.model.relationships.filter((r) => inside.has(r.from) !== inside.has(r.to));
    const body = this.$("#inspector-body");
    const label = (x) => this.nodeById.get(x)?.label || x;
    body.append(el("section", { class: "insp-sec" }, el("h3", { text: "Crossing relationships" }),
      el("ul", {}, list.map((r) => el("li", {},
        el("button", { class: "link", onclick: () => this.select({ type: "relationship", id: r.id }) },
          `${label(r.from)} —${relText(r)}→ ${label(r.to)}`))))));
  }

  // ------------------------------------------------------- navigation
  insets() {
    // keep fitted content clear of the floating controls, the legend, the status bar and
    // the flow's step bar
    const legend = this.$("#maplegend");
    return { left: 0, right: 48, top: legend?.offsetHeight ? legend.offsetHeight + 16 : 0, bottom: this.flow ? 80 : 24 };
  }

  pushHistory() {
    this.history.push(this.camera.state);
    if (this.history.length > 50) this.history.shift();
    this.$("#btn-back").disabled = false;
  }

  back() {
    const s = this.history.pop();
    if (s) this.camera.flyTo(s);
    this.$("#btn-back").disabled = !this.history.length;
  }

  flyToRect(rect, padding = 0.08) {
    this.pushHistory();
    this.camera.flyTo(this.camera.fitState(rect, padding, this.insets()));
  }

  flyToNode(id) {
    const box = this.layout.boxes.get(id);
    if (box) this.flyToRect(worldRect(box));
  }

  flyToRef(nodeId, refKey) {
    const pane = this.layout.panes.get(`${nodeId}::${refKey}`);
    if (pane) {
      this.flyToRect({ x: pane.x, y: pane.y, w: pane.w * pane.scale, h: pane.h * pane.scale }, 0.06);
    } else {
      this.toast("That reference cannot be displayed; see its status.");
    }
  }

  fitAll() {
    this.pushHistory();
    this.camera.flyTo(this.camera.fitState(this.layout.bounds, 0.05, this.insets()));
  }

  zoomBy(f) {
    this.camera.zoomAt(this.camera.vw / 2, this.camera.vh / 2, f);
  }

  captureAnchor() {
    if (!this.layout) return null;
    const id = this.selection?.type === "node" ? this.selection.id : this.centerNode();
    const box = id && this.layout.boxes.get(id);
    if (!box) return null;
    const [sx, sy] = this.camera.toScreen(box.x, box.y);
    return { id, sx, sy, ppw: this.camera.z * box.scale };
  }

  restoreAnchor(a) {
    const box = this.layout.boxes.get(a.id);
    if (!box) return;
    const z = a.ppw / box.scale;
    this.camera.set({ z, x: box.x - a.sx / z, y: box.y - a.sy / z }, false);
  }

  centerNode() {
    // deepest node containing the viewport center that is at least ~40% of the view wide
    if (!this.layout) return null;
    const [wx, wy] = this.camera.toWorld(this.camera.vw / 2, this.camera.vh / 2);
    let found = null;
    const visit = (box) => {
      const r = worldRect(box);
      if (wx < r.x || wy < r.y || wx > r.x + r.w || wy > r.y + r.h) return;
      if (r.w * this.camera.z < this.camera.vw * 0.35) return;
      found = box.id;
      for (const it of box.items) if (it.kind === "node") visit(it.box);
    };
    for (const id of this.layout.roots) visit(this.layout.boxes.get(id));
    return found;
  }

  // ---------------------------------------------------------- render
  requestRender() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      const t0 = performance.now();
      this.renderer.draw();
      this.panes.update(this.renderer.visiblePanes);
      this.drawMinimap();
      this.updateCrumbs();
      this.updateStatusBar();
      this.updateHash();
      const dt = performance.now() - t0;
      this.perf.frames.push(dt);
      if (this.perf.frames.length > 600) this.perf.frames.shift();
    });
  }

  renderChrome() {
    renderFlowBar(this, this.$("#flowbar"));
    renderMapLegend(this, this.$("#maplegend"));
    const d = this.data;
    const m = this.model;
    this.$("#repo-name").textContent = m.repository?.name || d.root_name;
    document.title = `${m.repository?.name || d.root_name} · repoluma`;
    const errors = d.diagnostics.filter((x) => x.severity === "error").length;
    const warns = d.diagnostics.filter((x) => x.severity === "warning").length;
    const health = this.$("#health");
    health.textContent = errors ? `⚠ ${plural(errors, "map error")}` : warns ? `${plural(warns, "warning")}` : "✓ map valid";
    health.className = "chip " + (errors ? "bad" : warns ? "warn" : "ok");
    const g = d.git || {};
    const git = this.$("#git");
    if (g.is_repo) {
      const head = g.head ? g.head.slice(0, 8) : "no commits yet";
      const dirty = g.dirty?.length ? ` · ${g.dirty.length} uncommitted` : " · clean";
      let rev = "";
      if (g.reviewed?.known) rev = ` · ${plural(g.reviewed.changed.length, "file")} changed since review`;
      git.textContent = `${g.head ? (g.branch || "detached") + "@" : ""}${head}${dirty}${rev}`;
      git.title = "Source is read from the working tree (including uncommitted changes).";
    } else {
      git.textContent = "working tree (not a git repository)";
    }
    renderInspector(this, this.$("#inspector-body"));
    this.$("#inspector").classList.toggle("empty", !this.selection);
    document.body.classList.toggle("file-open", this.selection?.type === "file");
    this.renderTab();
  }

  switchTab(tab) {
    this.tab = tab;
    for (const b of document.querySelectorAll("[role=tab]")) {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    }
    for (const p of document.querySelectorAll("[role=tabpanel]")) p.hidden = p.id !== "tab-" + tab;
    this.renderTab();
  }

  renderTab() {
    if (!this.model) return;
    const fn = { outline: renderOutline, files: renderFiles, flows: renderFlows, filters: renderFilters, coverage: renderCoverage }[this.tab];
    fn(this, this.$("#tab-" + this.tab));
  }

  drawMinimap() {
    const c = this.$("#minimap");
    if (!this.layout || c.hidden) return;
    const ctx = c.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = c.clientWidth, H = c.clientHeight;
    if (c.width !== W * dpr) { c.width = W * dpr; c.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const theme = this.renderer.theme;
    ctx.fillStyle = theme.bg;
    ctx.fillRect(0, 0, W, H);
    const b = this.layout.bounds;
    const s = Math.min((W - 8) / b.w, (H - 8) / b.h);
    const ox = (W - b.w * s) / 2, oy = (H - b.h * s) / 2;
    this.minimapTransform = { s, ox, oy };
    for (const id of this.layout.roots) {
      const box = this.layout.boxes.get(id);
      const r = worldRect(box);
      ctx.fillStyle = this.renderer.fillFor(box);
      ctx.strokeStyle = this.renderer.strokeFor(box);
      ctx.fillRect(ox + r.x * s, oy + r.y * s, r.w * s, r.h * s);
      ctx.strokeRect(ox + r.x * s, oy + r.y * s, r.w * s, r.h * s);
    }
    const v = this.camera.viewRect();
    ctx.strokeStyle = theme.sel;
    ctx.lineWidth = 1.5;
    const vx = ox + v.x * s, vy = oy + v.y * s, vw = Math.max(3, v.w * s), vh = Math.max(3, v.h * s);
    ctx.strokeRect(vx, vy, vw, vh);
    if (vw < 6) { // far zoomed in: mark position clearly
      ctx.beginPath();
      ctx.arc(vx + vw / 2, vy + vh / 2, 5, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  updateCrumbs() {
    const nav = this.$("#crumbs");
    const id = this.centerNode();
    const key = id + "|" + (this.selection?.id || "");
    if (this.lastCrumbs === key) return;
    this.lastCrumbs = key;
    clear(nav);
    nav.append(el("button", { class: "crumb", onclick: () => this.fitAll(), title: "Fit the whole map (0)" }, "Overview"));
    if (!id) return;
    for (const a of this.layout.ancestors.get(id)) {
      nav.append(el("span", { class: "sep", text: "›" }),
        el("button", { class: "crumb", onclick: () => this.flyToNode(a) }, this.nodeById.get(a).label));
    }
  }

  updateStatusBar() {
    const bar = this.$("#statusbar");
    const rd = this.renderer;
    if (!this.model || !rd.shownRels) return;
    const total = this.model.relationships.length;
    let hiddenKind = 0, hiddenNode = 0;
    for (const r of this.model.relationships) {
      if (this.hiddenRelTypes.has(r.type)) hiddenKind++;
      else if (this.hidden.has(r.from) || this.hidden.has(r.to)) hiddenNode++;
    }
    const shown = rd.shownRels.size;
    const notHere = total - hiddenKind - hiddenNode - shown;
    const parts = [`${shown} of ${plural(total, "relationship")} visible (${plural(rd.stats.edges, "line")})`];
    if (notHere > 0) parts.push(`${notHere} inside regions not expanded at this zoom or off-screen`);
    if (hiddenKind) parts.push(`${hiddenKind} hidden by relationship filter`);
    if (hiddenNode) parts.push(`${hiddenNode} touch hidden nodes`);
    if (this.hidden.size) parts.push(`${plural(this.hidden.size, "node")} hidden by filter`);
    const text = this.statusHint || parts.join(" · ");
    if (bar.textContent !== text) bar.textContent = text;
  }

  setStatusHint(text) {
    this.statusHint = text;
    this.updateStatusBar();
  }

  toast(text) {
    const t = this.$("#toast");
    t.textContent = text;
    t.hidden = false;
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  // --------------------------------------------------------- URL state
  updateHash = debounce(() => {
    if (!this.layout) return;
    const c = this.camera;
    const p = new URLSearchParams();
    p.set("c", [c.x, c.y, c.z].map((v) => +v.toPrecision(6)).join(","));
    const sel = this.selection;
    if (sel?.type === "file") p.set("file", sel.path + (sel.line ? ":" + sel.line : ""));
    else if (sel && sel.type !== "bundle") p.set("s", (sel.type === "relationship" ? "r:" : "") + sel.id);
    if (this.flow) p.set("f", `${this.flow.id},${this.flowStep}`);
    if (this.hiddenRelTypes.size) p.set("hr", [...this.hiddenRelTypes].join(","));
    if (this.hiddenNodeKinds.size) p.set("hk", [...this.hiddenNodeKinds].join(","));
    if (this.hiddenTags.size) p.set("ht", [...this.hiddenTags].join(","));
    if (this.uncertainty) p.set("u", "1");
    const hash = "#" + p.toString();
    if (location.hash !== hash) history.replaceState(null, "", hash);
  }, 250);

  restoreFromHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const split = (k) => (p.get(k) || "").split(",").filter(Boolean);
    this.hiddenRelTypes = new Set(split("hr"));
    this.hiddenNodeKinds = new Set(split("hk"));
    this.hiddenTags = new Set(split("ht"));
    this.uncertainty = p.get("u") === "1";
    this.recomputeHidden();
    const s = p.get("s");
    if (s) {
      const sel = s.startsWith("r:") ? { type: "relationship", id: s.slice(2) } : { type: "node", id: s };
      if (this.recordExists(sel)) this.selection = sel;
    }
    const file = p.get("file");
    if (file) {
      const m = /^(.*?)(?::(\d+))?$/.exec(file);
      const sel = { type: "file", path: m[1], line: m[2] ? +m[2] : null };
      if (this.recordExists(sel)) this.selection = sel;
    }
    const f = p.get("f");
    if (f) {
      const [fid, step] = f.split(",");
      if (this.flowById.has(fid)) { this.flow = this.flowById.get(fid); this.flowStep = +step || 0; this.tab = "flows"; }
    }
    const c = (p.get("c") || "").split(",").map(Number);
    if (c.length === 3 && c.every(Number.isFinite) && c[2] > 0) {
      this.camera.set({ x: c[0], y: c[1], z: c[2] }, false);
      return true;
    }
    if (this.selection?.type === "file") {
      // a link from an editor (#file=path:line) has no view yet: show the owner
      const s = this.selection;
      const owner = this.files.ownerAt(s.path, s.line) || this.files.cited.get(s.path)?.[0]?.node;
      const box = owner && this.layout.boxes.get(owner);
      if (box) {
        this.camera.set(this.camera.fitState(worldRect(box), 0.08, this.insets()), false);
        return true;
      }
    }
    return false;
  }

  // -------------------------------------------------------- onboarding
  showOnboarding(data) {
    const box = this.$("#onboarding");
    box.hidden = false;
    const body = this.$("#onboarding-body");
    clear(body);
    const diags = data.diagnostics || [];
    const missing = !data.map_exists;
    const cmd = "repoluma";
    const prompt = `Please build a repoluma map of this repository.
1. Run \`${cmd} guide\` and follow those instructions exactly.
2. Write the map to ${data.map_path}, describing what the software does before how files are organized.
3. Run \`${cmd} validate\` and fix every error; re-run until it passes.
4. Tell me what you covered, what you left out, and which claims need review.`;
    body.append(
      el("h2", { text: missing ? "No map yet" : "The map could not be read" }),
      missing ? el("p", {}, "repoluma shows a map that your coding agent writes to ", el("code", { text: data.map_path }), ". Nothing is generated automatically and no model provider is needed.") :
        el("ul", { class: "diags" }, diags.map((x) => el("li", { class: "diag error" }, el("strong", { text: x.message }), x.line ? ` (line ${x.line})` : "", x.hint ? el("div", { class: "small muted", text: "hint: " + x.hint }) : null))),
      el("ol", { class: "onboard-steps" },
        missing ? el("li", {}, "Optional: run ", el("code", { text: `${cmd} init` }), " to create a placeholder map.") : null,
        el("li", {}, "Give your coding agent this prompt:",
          el("pre", { class: "prompt", text: prompt }),
          el("button", { class: "btn", onclick: async () => this.toast(await copyText(prompt) ? "Prompt copied" : "Copy failed") }, "Copy prompt")),
        el("li", { text: "Keep this page open: it reloads automatically as soon as a valid map appears." })),
      el("p", { class: "small muted" }, "Want to see an example first? Run ", el("code", { text: `${cmd} example ./repoluma-example` }), " and then ", el("code", { text: `${cmd} serve --root ./repoluma-example` }), "."));
  }

  showFatal(text) {
    this.$("#onboarding").hidden = false;
    const body = this.$("#onboarding-body");
    clear(body);
    body.append(el("h2", { text: "Disconnected" }), el("p", { text }));
  }

  // ------------------------------------------------------------- input
  resize() {
    const r = this.stage.getBoundingClientRect();
    this.camera.resize(r.width, r.height);
    this.renderer.resize(r.width, r.height);
    this.requestRender();
  }

  bindEvents() {
    window.addEventListener("resize", () => this.resize());
    new ResizeObserver(() => this.resize()).observe(this.stage);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener?.("change", () => { this.renderer.theme = readTheme(); this.requestRender(); });

    // wheel: zoom around the pointer, except inside an active code pane
    this.stage.addEventListener("wheel", (ev) => {
      if (this.panes.isActiveBody(ev.target) && !ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault();
      const r = this.stage.getBoundingClientRect();
      const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? r.height : 1;
      if (ev.shiftKey && !ev.ctrlKey) {
        this.camera.panBy(-(ev.deltaY || ev.deltaX) * unit, 0);
        return;
      }
      const dy = clamp(ev.deltaY * unit, -300, 300);
      const factor = Math.exp(-dy * (ev.ctrlKey ? 0.01 : 0.0018));
      this.camera.zoomAt(ev.clientX - r.left, ev.clientY - r.top, factor);
      this.hideTip();
    }, { passive: false });

    const pointers = new Map();
    let drag = null;
    this.stage.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0 && ev.pointerType === "mouse") return;
      if (this.panes.isActiveBody(ev.target)) return; // text selection inside the active pane
      if (ev.target.closest && ev.target.closest("button, a, input")) return;
      pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      this.stage.setPointerCapture(ev.pointerId);
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        drag = { pinch: Math.hypot(a.x - b.x, a.y - b.y), moved: true };
        return;
      }
      drag = { x: ev.clientX, y: ev.clientY, sx: ev.clientX, sy: ev.clientY, moved: false, target: ev.target };
      this.camera.stop();
    });
    this.stage.addEventListener("pointermove", (ev) => {
      if (pointers.has(ev.pointerId)) pointers.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (drag && drag.pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const r = this.stage.getBoundingClientRect();
        this.camera.zoomAt((a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top, d / drag.pinch);
        drag.pinch = d;
        return;
      }
      if (drag && pointers.has(ev.pointerId)) {
        const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!drag.moved && Math.hypot(ev.clientX - drag.sx, ev.clientY - drag.sy) > 4) {
          drag.moved = true;
          this.stage.classList.add("panning");
        }
        if (drag.moved) {
          this.camera.panBy(dx, dy);
          drag.x = ev.clientX;
          drag.y = ev.clientY;
        }
        return;
      }
      this.onHover(ev);
    });
    const end = (ev) => {
      pointers.delete(ev.pointerId);
      this.stage.classList.remove("panning");
      if (!drag) return;
      const d = drag;
      if (pointers.size === 0) drag = null;
      if (!d.moved && ev.type === "pointerup") this.onClick(ev, d.target);
    };
    this.stage.addEventListener("pointerup", end);
    this.stage.addEventListener("pointercancel", end);
    this.stage.addEventListener("pointerleave", () => { this.hover = null; this.hideTip(); this.requestRender(); });
    this.stage.addEventListener("dblclick", (ev) => {
      if (this.panes.isActiveBody(ev.target)) return;
      const r = this.stage.getBoundingClientRect();
      const hit = this.renderer.hitTest(ev.clientX - r.left, ev.clientY - r.top);
      if (hit?.type === "node") this.flyToNode(hit.id);
      else if (hit?.type === "pane") {
        const p = this.layout.panes.get(hit.id);
        this.flyToRect({ x: p.x, y: p.y, w: p.w * p.scale, h: p.h * p.scale }, 0.06);
      }
    });

    // minimap navigation
    const mm = this.$("#minimap");
    const mmJump = (ev) => {
      const t = this.minimapTransform;
      if (!t) return;
      const r = mm.getBoundingClientRect();
      const wx = (ev.clientX - r.left - t.ox) / t.s, wy = (ev.clientY - r.top - t.oy) / t.s;
      const c = this.camera;
      c.set({ z: c.z, x: wx - c.vw / c.z / 2, y: wy - c.vh / c.z / 2 });
    };
    mm.addEventListener("pointerdown", (ev) => { this.pushHistory(); mm.setPointerCapture(ev.pointerId); mmJump(ev); mm.dataset.drag = "1"; });
    mm.addEventListener("pointermove", (ev) => { if (mm.dataset.drag) mmJump(ev); });
    mm.addEventListener("pointerup", () => { delete mm.dataset.drag; });

    // toolbar
    this.$("#btn-fit").addEventListener("click", () => this.fitAll());
    this.$("#btn-back").addEventListener("click", () => this.back());
    this.$("#btn-zoom-in").addEventListener("click", () => this.zoomBy(1.6));
    this.$("#btn-zoom-out").addEventListener("click", () => this.zoomBy(1 / 1.6));
    this.$("#btn-help").addEventListener("click", () => { this.$("#help").hidden = !this.$("#help").hidden; });
    this.$("#help-close").addEventListener("click", () => { this.$("#help").hidden = true; });
    this.$("#inspector-close").addEventListener("click", () => this.select(null));
    for (const b of document.querySelectorAll("[role=tab]")) {
      b.addEventListener("click", () => this.switchTab(b.dataset.tab));
      b.addEventListener("keydown", (ev) => {
        if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
        const tabs = [...document.querySelectorAll("[role=tab]")];
        const i = tabs.indexOf(b) + (ev.key === "ArrowDown" ? 1 : -1);
        const t = tabs[(i + tabs.length) % tabs.length];
        t.focus();
        this.switchTab(t.dataset.tab);
        ev.preventDefault();
      });
    }
    this.$("#health").addEventListener("click", () => this.switchTab("coverage"));

    this.bindSearch();
    window.addEventListener("keydown", (ev) => this.onKey(ev));
    window.addEventListener("hashchange", () => {
      // external navigation (pasted link): apply it
      if (this.layout && this.restoreFromHash()) { renderInspector(this, this.$("#inspector-body")); this.renderTab(); this.requestRender(); }
    });
  }

  onClick(ev, target) {
    const paneId = this.panes.paneAt(target);
    const r = this.stage.getBoundingClientRect();
    const route = paneId ? null : this.renderer.hitRoute(ev.clientX - r.left, ev.clientY - r.top);
    if (route) {
      this.panes.deactivate();
      if (route.members.length === 1) this.select({ type: "relationship", id: route.members[0].id });
      else this.select({ type: "bundle", ids: route.members.map((m) => m.id) });
      return;
    }
    const hit = paneId ? { type: "pane", id: paneId } : this.renderer.hitTest(ev.clientX - r.left, ev.clientY - r.top);
    if (!hit) {
      this.panes.deactivate();
      if (this.selection) this.select(null);
      return;
    }
    if (hit.type === "caption") {
      this.panes.deactivate();
      this.selectFile(hit.path, hit.line, { fly: false });
      return;
    }
    if (hit.type === "pane") {
      const pane = this.layout.panes.get(hit.id);
      if (this.selection?.id !== pane.nodeId) this.select({ type: "node", id: pane.nodeId });
      if (paneId) this.panes.activate(paneId);
      return;
    }
    this.panes.deactivate();
    this.select({ type: "node", id: hit.id });
  }

  onHover(ev) {
    const r = this.stage.getBoundingClientRect();
    const sx = ev.clientX - r.left, sy = ev.clientY - r.top;
    if (this.hoverPending) { this.hoverPending = { sx, sy, ev }; return; }
    this.hoverPending = { sx, sy, ev };
    requestAnimationFrame(() => {
      const { sx: x, sy: y, ev: e } = this.hoverPending;
      this.hoverPending = null;
      const hit = this.renderer.hitTest(x, y);
      const id = hit?.type === "node" ? hit.id : null;
      if (id !== this.hover) this.requestRender();
      this.hover = id;
      this.stage.style.cursor = hit ? "pointer" : "";
      this.showTip(hit, e);
    });
  }

  showTip(hit, ev) {
    const tip = this.$("#tip");
    if (!hit || hit.type !== "node") { this.hideTip(); return; }
    const box = this.layout.boxes.get(hit.id);
    const px = this.camera.z * box.scale;
    if (30 * px >= 7 && 17 * px >= 9) { this.hideTip(); return; } // already readable on the map
    const n = box.node;
    if (tip.dataset.id !== n.id) {
      clear(tip);
      tip.append(el("div", { class: "tip-kind", text: n.kind }), el("strong", { text: n.label }),
        n.summary ? el("div", { class: "small", text: n.summary }) : null);
      tip.dataset.id = n.id;
    }
    tip.hidden = false;
    const r = this.stage.getBoundingClientRect();
    tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 300) + "px";
    tip.style.top = (ev.clientY - r.top + 16) + "px";
  }

  hideTip() {
    const tip = this.$("#tip");
    tip.hidden = true;
    delete tip.dataset.id;
  }

  onKey(ev) {
    const tag = (ev.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || ev.target.isContentEditable;
    if (ev.key === "Escape") {
      if (typing) { ev.target.blur(); this.closeSearch(); return; }
      if (!this.$("#help").hidden) { this.$("#help").hidden = true; return; }
      if (this.panes.active) { this.panes.deactivate(); return; }
      if (this.selection) { this.select(null); return; }
      if (this.flow) { this.setFlow(null); return; }
      return;
    }
    if (typing || ev.metaKey || ev.ctrlKey) return;
    if (this.panes.active && ["ArrowUp", "ArrowDown", "PageUp", "PageDown", " "].includes(ev.key)) return;
    const pan = 120;
    const handled = {
      "/": () => this.$("#search").focus(),
      "?": () => { this.$("#help").hidden = !this.$("#help").hidden; },
      "ArrowLeft": () => (ev.altKey ? this.back() : this.camera.panBy(pan, 0)),
      "ArrowRight": () => this.camera.panBy(-pan, 0),
      "ArrowUp": () => this.camera.panBy(0, pan),
      "ArrowDown": () => this.camera.panBy(0, -pan),
      "+": () => this.zoomBy(1.4),
      "=": () => this.zoomBy(1.4),
      "-": () => this.zoomBy(1 / 1.4),
      "0": () => this.fitAll(),
      "Home": () => this.fitAll(),
      "f": () => this.selection?.type === "node" && this.flyToNode(this.selection.id),
      "Enter": () => this.selection?.type === "node" && this.flyToNode(this.selection.id),
      "Backspace": () => this.back(),
      "b": () => this.back(),
      "n": () => this.stepFlow(1),
      "p": () => this.stepFlow(-1),
      "u": () => { this.parentOfSelection(); },
      "c": async () => {
        if (this.selection?.type === "node") this.toast(await copyText(this.agentSummary(this.selection.id)) ? "Copied selection for your agent" : "Copy failed");
      },
    }[ev.key];
    if (handled) {
      if (ev.target.closest && ev.target.closest("button") && ev.key === "Enter") return;
      ev.preventDefault();
      handled();
    }
  }

  parentOfSelection() {
    if (this.selection?.type !== "node") return;
    const p = this.nodeById.get(this.selection.id)?.parent;
    if (p) this.select({ type: "node", id: p }, { fly: true });
    else this.fitAll();
  }

  // ------------------------------------------------------------ search
  bindSearch() {
    const input = this.$("#search");
    const list = this.$("#search-results");
    let results = [];
    let active = 0;
    const run = () => {
      const q = input.value.trim().toLowerCase();
      clear(list);
      if (!q || !this.model) { list.hidden = true; return; }
      results = this.search(q).slice(0, 14);
      active = 0;
      results.forEach((r, i) => list.append(el("li", { role: "option", id: "sr-" + i, "aria-selected": i === 0 ? "true" : "false",
        onmousedown: (e) => { e.preventDefault(); this.openResult(r); } },
      el("span", { class: "sr-kind", text: r.kind }), el("span", { text: r.label }), r.sub ? el("span", { class: "muted small", text: "  " + r.sub }) : null)));
      if (!results.length) list.append(el("li", { class: "muted", text: "No matches. Unmodeled parts of the repository are not searchable here." }));
      list.hidden = false;
    };
    input.addEventListener("input", run);
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        active = clamp(active + (ev.key === "ArrowDown" ? 1 : -1), 0, Math.max(0, results.length - 1));
        [...list.children].forEach((li, i) => li.setAttribute("aria-selected", i === active ? "true" : "false"));
      } else if (ev.key === "Enter" && results[active]) {
        this.openResult(results[active]);
      }
    });
    input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 150));
    input.addEventListener("focus", run);
  }

  closeSearch() {
    this.$("#search-results").hidden = true;
  }

  search(q) {
    const out = [];
    const score = (text, base) => {
      if (!text) return 0;
      const t = text.toLowerCase();
      if (t === q) return base + 3;
      if (t.startsWith(q)) return base + 2;
      if (t.includes(q)) return base + 1;
      return 0;
    };
    for (const n of this.model.nodes) {
      const s = Math.max(score(n.label, 30), score(n.id, 20), score(n.summary, 5),
        ...(n.refs || []).map((r) => score(r.key, 8)));
      if (s) out.push({ type: "node", id: n.id, label: n.label, kind: n.kind, sub: n.id, s });
    }
    for (const path of this.files.paths()) {
      const s = Math.max(score(path, 7), score(path.split("/").pop(), 12));
      if (!s) continue;
      const owner = this.files.ownerAt(path);
      out.push({ type: "file", id: path, label: path, kind: "file",
        sub: owner ? this.nodeById.get(owner)?.label : "no owner", s });
    }
    for (const f of this.model.flows) {
      const s = Math.max(score(f.label, 28), score(f.id, 18));
      if (s) out.push({ type: "flow", id: f.id, label: f.label, kind: "flow", s });
    }
    for (const r of this.model.relationships) {
      const s = Math.max(score(r.label, 10), score(r.id, 9));
      if (s) out.push({ type: "relationship", id: r.id, label: `${this.nodeById.get(r.from)?.label} → ${this.nodeById.get(r.to)?.label}`, kind: r.type, sub: relText(r), s });
    }
    return out.sort((a, b) => b.s - a.s);
  }

  openResult(r) {
    this.$("#search").blur();
    this.closeSearch();
    if (r.type === "node") this.select({ type: "node", id: r.id }, { fly: true });
    else if (r.type === "flow") this.setFlow(r.id, 0);
    else if (r.type === "file") this.selectFile(r.id);
    else this.select({ type: "relationship", id: r.id });
  }

  // ------------------------------------------------------- agent export
  agentSummary(id) {
    if (id === UNMAPPED_ID) {
      return [`repoluma: ${plural(this.files.unowned.length, "code file")} without an owner in ${this.data.map_path}:`,
        ...this.files.unowned.map((p) => `  - ${p}`),
        "Give each to the node it belongs to with 'owns' (see `repoluma guide`), or list it under source.ownership.ignore.",
        `View: ${location.href}`].join("\n");
    }
    const n = this.nodeById.get(id);
    const d = this.data;
    const chain = this.layout.ancestors.get(id).map((a) => `${this.nodeById.get(a).label} (\`${a}\`)`).join(" › ");
    const lines = [];
    lines.push(`repoluma selection: \`${n.id}\` — ${n.label} [${n.kind}${n.evidence ? ", " + n.evidence : ""}]`);
    const g = d.git || {};
    lines.push(`Map: ${d.map_path}:${n.line || "?"}` + (g.head ? ` @ ${g.head.slice(0, 12)}${g.dirty?.length ? " (uncommitted changes present)" : ""}` : ""));
    lines.push(`Location: ${chain}`);
    if (n.summary) lines.push(`Summary: ${n.summary}`);
    if (n.details) lines.push(`Details: ${n.details}`);
    if (n.contract) {
      for (const [k, v] of Object.entries(n.contract)) if (v.length) lines.push(`${k[0].toUpperCase() + k.slice(1)}: ${v.join("; ")}`);
    }
    const own = this.files.ownedBy(id);
    if (own.whole.length || own.parts.length) {
      lines.push("Owns (files that belong to this part of the map):");
      for (const p of own.whole) lines.push(`  - ${p}`);
      for (const p of own.parts) lines.push(`  - ${p.path}#${p.symbol} (lines ${p.start}-${p.end})`);
    }
    if ((n.refs || []).length) {
      lines.push("Source:");
      for (const r of n.refs) {
        const res = this.refs[r.key] || {};
        lines.push(`  - ${r.key}${res.start ? ` (lines ${res.start}-${res.end})` : ""} [${res.status}]${r.role ? " " + r.role : ""}`);
      }
    }
    const rel = (r, dir) => `  - ${r.type} ${dir === "out" ? "→" : "←"} ${dir === "out" ? r.to : r.from}: ${relText(r)}${r.type !== "data" && r.label ? ` (${r.label})` : ""}${r.evidence ? ` [${r.evidence}]` : ""}`;
    const out = this.outgoing.get(id) || [], inc = this.incoming.get(id) || [];
    if (out.length || inc.length) {
      lines.push("Relationships:");
      out.forEach((r) => lines.push(rel(r, "out")));
      inc.forEach((r) => lines.push(rel(r, "in")));
    }
    const impact = computeImpact(this, id);
    if (impact.items.length) lines.push(`Review candidates if this changes (modeled only): ${impact.items.map((i) => i.id).join(", ")}`);
    if (this.flow) lines.push(`Active flow: ${this.flow.id}, step ${this.flowStep + 1} (${this.flow.steps[this.flowStep]?.label || ""})`);
    lines.push(`View: ${location.href}`);
    return lines.join("\n");
  }

  // ------------------------------------------------------------ polling
  startPolling() {
    const tick = async () => {
      if (document.visibilityState === "visible") {
        try {
          const st = await api("/api/status");
          if (st.digest !== this.digest) {
            this.digest = st.digest;
            await this.load(this.data?.model ? "poll" : "initial");
          }
          this.$("#conn").hidden = true;
        } catch {
          this.$("#conn").hidden = false;
        }
      }
      setTimeout(tick, 1500);
    };
    setTimeout(tick, 1500);
  }
}

const app = new App();
window.repoluma = app; // handy for debugging and the benchmark harness
app.tuning = TUNING;
app.load("initial").then(() => {
  app.startPolling();
  if (app.flow) renderFlows(app, document.querySelector("#tab-flows"));
});
