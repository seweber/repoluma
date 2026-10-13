// Ownership on the client: which node owns each file (or line), what each node owns, and the
// short names the map shows for files. The server resolves the map's `owns` patterns;
// this module only indexes the result.

export const UNMAPPED_ID = "~unmapped"; // not a valid map id, so it cannot clash

export class FileIndex {
  constructor(ownership, model, refs = {}) {
    this.files = ownership?.files || {};
    this.refs = refs;
    this.rest = new Map(); // path -> [{area, segments}]: the rest-of-file panes of areas
    for (const rp of ownership?.rest || []) {
      if (!this.rest.has(rp.path)) this.rest.set(rp.path, []);
      this.rest.get(rp.path).push(rp);
    }
    this.unowned = ownership?.unowned || [];
    this.counts = ownership?.counts || null;
    this.byNode = new Map(); // node id -> {whole: [path], parts: [{path, symbol, start, end}]}
    this.cited = new Map(); // path -> [{node, ref}]
    const slot = (id) => {
      if (!this.byNode.has(id)) this.byNode.set(id, { whole: [], parts: [] });
      return this.byNode.get(id);
    };
    for (const [path, rec] of Object.entries(this.files)) {
      if (rec.owner) slot(rec.owner).whole.push(path);
      for (const p of rec.parts) slot(p.node).parts.push({ path, ...p });
    }
    if (this.unowned.length) slot(UNMAPPED_ID).whole.push(...this.unowned);
    for (const n of model.nodes) {
      for (const r of n.refs || []) {
        if (!this.cited.has(r.path)) this.cited.set(r.path, []);
        this.cited.get(r.path).push({ node: n.id, ref: r });
      }
    }
    this.short = shortNames(Object.keys(this.files));
  }

  /** Owner of a file, or of one line in it (the innermost part containing the line wins). */
  ownerAt(path, line) {
    const rec = this.files[path];
    if (!rec) return null;
    if (line != null) {
      let inner = null;
      for (const p of rec.parts) {
        if (p.start <= line && line <= p.end && (!inner || p.end - p.start <= inner.end - inner.start)) inner = p;
      }
      if (inner) return inner.node;
    }
    return rec.owner || (this.unowned.includes(path) ? UNMAPPED_ID : null);
  }

  /** Every node owning some of the file: the file's owner first, then the parts' owners. */
  ownersOf(path) {
    const rec = this.files[path];
    if (!rec) return [];
    const out = [];
    const owner = this.ownerAt(path);
    if (owner) out.push(owner);
    for (const p of rec.parts) if (!out.includes(p.node)) out.push(p.node);
    return out;
  }

  /**
   * Where a line is shown on the map: the pane of the node in its area that cites it, or
   * the area's own rest-of-file pane. Returns {node, ref} (ref is that pane's ref key).
   */
  shownAt(path, line) {
    const area = this.ownerAt(path, line);
    for (const rp of this.rest.get(path) || []) {
      if (rp.area !== area) continue;
      const seg = rp.segments.find((s) => s.start <= line && line <= s.end);
      if (seg?.kind === "shown") return { node: seg.node, ref: seg.ref };
      if (seg?.kind === "code") return { node: area, ref: `${path}#~rest` };
    }
    // no rest pane (everything is cited): the smallest citation containing the line
    let best = null;
    for (const c of this.cited.get(path) || []) {
      const res = this.refs[c.ref.key];
      if (!res?.start || line < res.start || line > res.end) continue;
      if (!best || res.end - res.start < best.size) best = { node: c.node, ref: c.ref.key, size: res.end - res.start };
    }
    return best ? { node: best.node, ref: best.ref } : (area ? { node: area, ref: null } : null);
  }

  ownedBy(id) {
    return this.byNode.get(id) || { whole: [], parts: [] };
  }

  /** The text items of a node's file line: short file names, grouped where many share a folder. */
  lineItems(id) {
    const own = this.ownedBy(id);
    const items = [];
    const byDir = new Map();
    for (const path of own.whole) {
      const dir = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
      if (!byDir.has(dir)) byDir.set(dir, []);
      byDir.get(dir).push(path);
    }
    for (const [dir, paths] of byDir) {
      if (paths.length >= 5 && dir) {
        const segs = dir.split("/").filter(Boolean);
        items.push(`${segs.slice(-2).join("/")}/ (${paths.length} files)`);
      } else {
        for (const p of paths.sort()) items.push(this.shortName(p));
      }
    }
    const partFiles = [...new Set(own.parts.map((p) => p.path))].filter((p) => !own.whole.includes(p));
    for (const p of partFiles) items.push(this.shortName(p) + " (part)");
    return items;
  }

  shortName(path) {
    return this.short.get(path) || path.split("/").pop();
  }

  /** All files the viewer knows about (owned, to be owned, or cited), sorted. */
  paths() {
    const all = new Set(Object.keys(this.files));
    for (const p of this.cited.keys()) all.add(p);
    return [...all].sort();
  }
}

/** Basename, or as many trailing folders as needed to tell files with the same name apart. */
export function shortNames(paths) {
  const out = new Map();
  const groups = new Map();
  for (const p of paths) {
    const base = p.split("/").pop();
    if (!groups.has(base)) groups.set(base, []);
    groups.get(base).push(p);
  }
  for (const [base, list] of groups) {
    if (list.length === 1) { out.set(list[0], base); continue; }
    for (const p of list) {
      const segs = p.split("/");
      let k = 2;
      const suffix = (q, n) => q.split("/").slice(-n).join("/");
      while (k < segs.length && list.some((q) => q !== p && suffix(q, k) === suffix(p, k))) k++;
      out.set(p, suffix(p, k));
    }
  }
  return out;
}

/** Split a path into its folder (with trailing slash) and file name. */
export function splitPath(path) {
  const i = path.lastIndexOf("/");
  return i < 0 ? ["", path] : [path.slice(0, i + 1), path.slice(i + 1)];
}
