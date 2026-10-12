// Source fetching (bounded, cached, cancellable) and in-map DOM code panes.

import { PANE_HEAD, PANE_LINE } from "./layout.js";
import { languageOf, tokenize } from "./highlight.js";
import { clear, copyText, el } from "./util.js";

const MAX_DOM_PANES = 36;
const MAX_CONCURRENT = 4;
const NEW_PANES_PER_FRAME = 4; // spread DOM construction over frames while zooming
const LINE_CHUNK = 80; // lines rendered up front; the rest is appended while scrolling
const CACHE_LIMIT = 300;

export class SourceCache {
  constructor(api, onLoaded) {
    this.api = api;
    this.onLoaded = onLoaded;
    this.entries = new Map(); // key -> {state, data, used}
    this.queue = [];
    this.active = 0;
  }

  key(pane) {
    const extra = pane.extra || { before: 0, after: 0 };
    const start = Math.max(1, pane.start - extra.before);
    const end = pane.end + extra.after;
    return { key: `${pane.ref.path}|${start}|${end}`, start, end };
  }

  peek(pane) {
    const { key } = this.key(pane);
    const e = this.entries.get(key);
    if (!e) return undefined;
    e.used = performance.now();
    return e.state === "done" ? e.data : e.state === "error" ? { error: e.data } : null;
  }

  want(pane) {
    const { key, start, end } = this.key(pane);
    if (this.entries.has(key)) return;
    this.entries.set(key, { state: "queued", used: performance.now() });
    this.queue.push({ key, path: pane.ref.path, start, end });
    this.pump();
  }

  /** Drop queued fetches that are no longer visible (camera moved on). */
  retain(visibleKeys) {
    this.queue = this.queue.filter((q) => {
      if (visibleKeys.has(q.key)) return true;
      this.entries.delete(q.key);
      return false;
    });
  }

  pump() {
    while (this.active < MAX_CONCURRENT && this.queue.length) {
      const job = this.queue.shift();
      const entry = this.entries.get(job.key);
      if (!entry) continue;
      entry.state = "loading";
      this.active++;
      this.api(`/api/source?path=${encodeURIComponent(job.path)}&start=${job.start}&end=${job.end}`)
        .then((data) => {
          if (data.error) { entry.state = "error"; entry.data = data; }
          else { entry.state = "done"; entry.data = data; }
        })
        .catch((err) => { entry.state = "error"; entry.data = { message: String(err) }; })
        .finally(() => {
          this.active--;
          this.evict();
          this.onLoaded(job.path);
          this.pump();
        });
    }
  }

  evict() {
    if (this.entries.size <= CACHE_LIMIT) return;
    const sorted = [...this.entries.entries()].filter(([, e]) => e.state === "done" ||
      e.state === "error").sort((a, b) => a[1].used - b[1].used);
    for (const [k] of sorted.slice(0, this.entries.size - CACHE_LIMIT)) this.entries.delete(k);
  }

  invalidate(paths) {
    for (const k of [...this.entries.keys()]) {
      const path = k.split("|")[0];
      if (!paths || paths.has(path)) this.entries.delete(k);
    }
  }
}

export class PaneLayer {
  constructor(container, app) {
    this.container = container;
    this.app = app;
    this.els = new Map(); // pane id -> {root, body, rendered}
    this.active = null;
  }

  update(visiblePanes) {
    const want = visiblePanes.filter((v) => v.dom)
      .sort((a, b) => b.px - a.px).slice(0, MAX_DOM_PANES);
    const keep = new Set();
    let created = 0;
    for (const v of want) {
      let rec = this.els.get(v.pane.id);
      if (!rec) {
        if (created >= NEW_PANES_PER_FRAME) { this.app.requestRender(); continue; }
        created++;
      }
      keep.add(v.pane.id);
      if (!rec) {
        rec = this.create(v.pane);
        this.els.set(v.pane.id, rec);
        this.container.append(rec.root);
      }
      rec.root.style.transform = `translate(${v.sx}px, ${v.sy}px) scale(${v.px})`;
      const data = this.app.sources.peek(v.pane);
      if (data === undefined) this.app.sources.want(v.pane);
      const stamp = data ? (data.error ? "err" : data.token + data.start + ":" + data.end) : "none";
      if (rec.rendered !== stamp) this.fill(rec, v.pane, data);
    }
    for (const [id, rec] of this.els) {
      if (!keep.has(id)) {
        if (this.active === id) this.deactivate();
        rec.root.remove();
        this.els.delete(id);
      }
    }
    const keys = new Set(visiblePanes.map((v) => this.app.sources.key(v.pane).key));
    this.app.sources.retain(keys);
  }

  create(pane) {
    const status = pane.res.status;
    const statusText = {
      resolved: "✓ symbol located (syntax tree)",
      heuristic: "≈ located by text pattern",
      lines: "≈ line range only",
      moved: "⚠ moved: stated lines outdated",
      file: "whole file",
    }[status] || status;
    const range = pane.res.start ? `L${pane.res.start}–${pane.res.end}` : "";
    const changed = pane.res.changed_since_review ? el("span", { class: "pill warn",
      title: "This file changed since the map was last reviewed; the description may be stale",
      text: "changed since review" }) : null;
    const copyBtn = el("button", { class: "pane-btn", title: "Copy the referenced lines",
      text: "copy" });
    const root = el("div", { class: "pane", dataset: { pane: pane.id } },
      el("div", { class: "pane-head" },
        el("span", { class: "pane-path", title: pane.ref.key }, pane.ref.path,
          pane.ref.symbol ? el("span", { class: "pane-sym", text: " # " + pane.ref.symbol }) : null),
        el("span", { class: "pane-meta" },
          pane.ref.role ? el("span", { class: "pill", text: pane.ref.role }) : null,
          changed,
          el("span", { class: `pill st-${status}`, title: pane.res.message, text: statusText }),
          el("span", { class: "pane-range", text: range }),
          copyBtn)),
      el("div", { class: "pane-body", tabindex: "-1" }));
    root.style.width = pane.w + "px";
    root.style.height = pane.h + "px";
    const body = root.querySelector(".pane-body");
    body.style.height = (pane.h - PANE_HEAD) + "px";
    copyBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      const data = this.app.sources.peek(pane);
      if (!data || data.error) return;
      const lines = data.lines.slice(pane.res.start - data.start, pane.res.end - data.start + 1);
      const ok = await copyText(lines.join("\n"));
      this.app.toast(ok ? `Copied ${pane.ref.key} (L${pane.res.start}–${pane.res.end})` :
        "Copy failed");
    });
    return { root, body, rendered: null };
  }

  fill(rec, pane, data) {
    const body = rec.body;
    clear(body);
    if (rec.onScroll) body.removeEventListener("scroll", rec.onScroll);
    rec.onScroll = null;
    if (data === null || data === undefined) {
      body.append(el("div", { class: "pane-msg", text: "loading source…" }));
      rec.rendered = "loading";
      return;
    }
    if (data.error) {
      body.append(el("div", { class: "pane-msg warn",
        text: `Cannot show source: ${data.error.message || data.error.error || "error"}` }));
      rec.rendered = "err";
      return;
    }
    const lang = languageOf(pane.ref.path);
    const frag = document.createDocumentFragment();
    const extra = pane.extra || { before: 0, after: 0 };
    if (data.start > 1) {
      frag.append(el("button", { class: "pane-more", text: `▲ show ${Math.min(20, data.start - 1)} more lines above`,
        onclick: (ev) => { ev.stopPropagation(); pane.extra = { ...extra, before: extra.before + 20 }; this.app.requestRender(); } }));
    }
    const lineEl = (i) => {
      const n = data.start + i;
      const inRange = n >= pane.res.start && n <= pane.res.end;
      const code = el("span", { class: "tx" });
      for (const [cls, t] of tokenize(data.lines[i], lang)) {
        if (cls) code.append(el("span", { class: "t" + cls, text: t }));
        else code.append(document.createTextNode(t));
      }
      return el("div", { class: inRange ? "ln in" : "ln" }, el("span", { class: "no", text: String(n) }), code);
    };
    const lines = el("div", { class: "lines" });
    let done = Math.min(LINE_CHUNK, data.lines.length);
    for (let i = 0; i < done; i++) lines.append(lineEl(i));
    frag.append(lines);
    const more = data.end < data.total_lines ? el("button", { class: "pane-more",
      text: `▼ show ${Math.min(20, data.total_lines - data.end)} more lines below (file has ${data.total_lines})`,
      onclick: (ev) => { ev.stopPropagation(); pane.extra = { ...extra, after: extra.after + 20 }; this.app.requestRender(); } }) : null;
    if (more) more.hidden = done < data.lines.length;
    if (more) frag.append(more);
    if (done < data.lines.length) {
      // long excerpts: append further lines only when the reader scrolls toward them
      const onScroll = () => {
        if (body.scrollTop + body.clientHeight < body.scrollHeight - 300) return;
        const stop = Math.min(done + LINE_CHUNK, data.lines.length);
        const chunk = document.createDocumentFragment();
        for (let i = done; i < stop; i++) chunk.append(lineEl(i));
        lines.append(chunk);
        done = stop;
        if (done >= data.lines.length) {
          body.removeEventListener("scroll", onScroll);
          if (more) more.hidden = false;
        }
      };
      body.addEventListener("scroll", onScroll, { passive: true });
      rec.onScroll = onScroll;
    }
    body.append(frag);
    body.style.setProperty("--line-h", PANE_LINE + "px");
    if (rec.rendered === null || rec.rendered === "loading") {
      body.scrollTop = 0;
    }
    rec.rendered = data.token + data.start + ":" + data.end;
  }

  paneAt(target) {
    const root = target.closest && target.closest(".pane");
    return root ? root.dataset.pane : null;
  }

  activate(id) {
    if (this.active === id) return;
    this.deactivate();
    const rec = this.els.get(id);
    if (!rec) return;
    this.active = id;
    rec.root.classList.add("active");
    rec.body.focus({ preventScroll: true });
    this.app.setStatusHint("Code pane active: wheel scrolls code, text is selectable. Esc or click the map to release.");
  }

  deactivate() {
    if (!this.active) return;
    const rec = this.els.get(this.active);
    if (rec) rec.root.classList.remove("active");
    this.active = null;
    this.app.setStatusHint(null);
  }

  isActiveBody(target) {
    if (!this.active) return false;
    const rec = this.els.get(this.active);
    return rec && rec.root.contains(target);
  }

  reset() {
    for (const rec of this.els.values()) rec.root.remove();
    this.els.clear();
    this.active = null;
  }
}
