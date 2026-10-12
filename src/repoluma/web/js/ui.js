// DOM panels: inspector, outline, flows, filters, coverage. Everything is built
// with el()/textContent so map content can never inject markup.

import { DISPLAYABLE, EVIDENCE_LABEL, REL_TYPES, kindLabel, relText } from "./layout.js";
import { clear, copyText, el, plural } from "./util.js";

const EVIDENCE_HELP = {
  observed: "Directly supported by the referenced source.",
  inferred: "An interpretation drawn from source, tests or docs.",
  needs_review: "Plausible but not checked carefully.",
  unknown: "A known gap: not established.",
  planned: "Intended behavior that is not implemented.",
};
const REF_STATUS = {
  resolved: ["ok", "located by syntax tree"],
  heuristic: ["approx", "located by text pattern; verify"],
  lines: ["approx", "line range only; identity not verified"],
  file: ["ok", "whole file"],
  moved: ["warn", "symbol moved away from stated lines"],
  missing_file: ["bad", "file missing"],
  missing_symbol: ["bad", "symbol not found"],
  ambiguous: ["bad", "ambiguous symbol"],
  excluded: ["bad", "excluded from display"],
  out_of_range: ["bad", "lines beyond end of file"],
  binary: ["bad", "binary file"],
  too_large: ["bad", "file too large"],
  invalid_path: ["bad", "invalid path"],
  outside_root: ["bad", "outside repository"],
  not_a_file: ["bad", "not a file"],
};

function evidenceBadge(ev) {
  if (!ev) return el("span", { class: "pill muted", title: "No evidence status was stated",
    text: "evidence not stated" });
  return el("span", { class: `pill ev-${ev}`, title: EVIDENCE_HELP[ev] || "",
    text: EVIDENCE_LABEL[ev] || ev });
}

function refRow(app, ref, ownerId) {
  const res = app.refs[ref.key] || { status: "unknown", message: "not resolved" };
  const [cls, text] = REF_STATUS[res.status] || ["bad", res.status];
  const range = res.start ? ` L${res.start}–${res.end}` : "";
  const row = el("li", { class: "ref" },
    el("span", { class: `dot ${cls}`, "aria-hidden": "true" }),
    el("button", {
      class: "link mono", title: res.message,
      onclick: () => app.flyToRef(ownerId, ref.key),
      disabled: DISPLAYABLE.has(res.status) ? null : true,
    }, ref.key),
    el("span", { class: "muted small" }, ` ${text}${range}`),
    ref.role ? el("span", { class: "pill" }, ref.role) : null,
    res.changed_since_review ? el("span", { class: "pill warn",
      title: "File changed since scope.reviewed_revision; re-check the description" },
    "changed since review") : null,
    ref.note ? el("div", { class: "small muted" }, ref.note) : null);
  return row;
}

function section(title, ...children) {
  const body = children.flat().filter(Boolean);
  if (!body.length) return null;
  return el("section", { class: "insp-sec" }, el("h3", { text: title }), ...body);
}

function nodeLink(app, id, extra) {
  const n = app.nodeById.get(id);
  return el("button", { class: "link", title: id, onclick: () => app.select({ type: "node", id }, { fly: true }) },
    n ? n.label : id, extra ? el("span", { class: "muted small" }, " " + extra) : null);
}

export function renderInspector(app, root) {
  clear(root);
  const sel = app.selection;
  if (!sel) {
    root.append(renderOverviewCard(app));
    return;
  }
  if (sel.type === "node") root.append(renderNode(app, sel.id));
  else if (sel.type === "relationship") root.append(renderRelationship(app, sel.id));
  else if (sel.type === "bundle") root.append(renderBundle(app, sel.ids));
}

function renderOverviewCard(app) {
  const m = app.model;
  const s = m.scope || {};
  return el("div", { class: "insp" },
    el("div", { class: "insp-kind", text: "MAP" }),
    el("h2", { text: m.repository?.name || app.data.root_name }),
    m.repository?.description ? el("p", { text: m.repository.description }) : null,
    section("Scope", s.summary ? el("p", { text: s.summary }) : el("p", { class: "muted",
      text: "The map does not state its scope." })),
    section("Covered", s.covered?.length ? el("ul", {}, s.covered.map((c) => el("li", { text: c }))) : null),
    section("Not covered", s.not_covered?.length ? el("ul", {}, s.not_covered.map((c) => el("li", { text: c }))) : null),
    section("Start here",
      el("p", { class: "small", text: "Click a region to inspect it, scroll to zoom into it, or follow a flow from the Flows tab. Panning keeps the zoom level, so neighbouring regions show the same depth of detail." }),
      app.model.flows.length ? el("div", { class: "btn-row" }, app.model.flows.slice(0, 4).map((f) =>
        el("button", { class: "btn", title: "Show this flow's steps on the map", onclick: () => app.setFlow(f.id, 0) }, f.label))) : null));
}

function renderNode(app, id) {
  const n = app.nodeById.get(id);
  if (!n) return el("p", { class: "muted", text: `Node '${id}' no longer exists.` });
  const chain = app.layout.ancestors.get(id) || [id];
  const crumbs = el("nav", { class: "insp-crumbs", "aria-label": "Containment" },
    chain.slice(0, -1).map((a) => [nodeLink(app, a), el("span", { class: "muted", text: " › " })]));
  const out = app.outgoing.get(id) || [];
  const inc = app.incoming.get(id) || [];
  const flows = app.model.flows.filter((f) => f.steps.some((s) => s.node === id));
  const descendants = app.descendants(id);
  const innerRels = app.model.relationships.filter((r) => descendants.has(r.from) && descendants.has(r.to) && r.from !== id && r.to !== id);
  const crossing = app.model.relationships.filter((r) => (descendants.has(r.from) !== descendants.has(r.to)) && r.from !== id && r.to !== id);
  const contract = n.contract ? Object.entries({ inputs: "Inputs", outputs: "Outputs",
    guarantees: "Guarantees", errors: "Errors / failures", assumptions: "Assumptions" })
    .filter(([k]) => n.contract[k]?.length)
    .map(([k, name]) => el("div", { class: "contract" }, el("h4", { text: name }),
      el("ul", {}, n.contract[k].map((t) => el("li", { text: t }))))) : [];

  const impact = computeImpact(app, id);

  return el("div", { class: "insp" },
    crumbs,
    el("div", { class: "insp-kind" }, kindLabel(n.kind).toUpperCase(), " ", evidenceBadge(n.evidence),
      n.confidence ? el("span", { class: "pill", title: "Author's stated confidence; an annotation, not a probability" }, n.confidence + " confidence") : null),
    el("h2", { text: n.label }),
    el("div", { class: "insp-id" },
      el("code", { text: n.id }),
      el("button", { class: "btn small", title: "Copy a summary of this selection for a coding agent",
        onclick: async () => app.toast(await copyText(app.agentSummary(id)) ? "Copied selection for your agent" : "Copy failed") }, "Copy for agent"),
      el("button", { class: "btn small", title: "Copy a link that restores this view",
        onclick: async () => app.toast(await copyText(location.href) ? "Copied link to this view" : "Copy failed") }, "Copy link"),
      el("button", { class: "btn small", onclick: () => app.flyToNode(id) }, "Zoom to")),
    n.summary ? el("p", { class: "insp-summary", text: n.summary }) : el("p", { class: "muted", text: "No summary." }),
    n.details ? el("p", { text: n.details }) : null,
    section("Contract", contract),
    n.notes ? section("Notes", el("p", { text: n.notes })) : null,
    section("Evidence in source",
      (n.refs || []).length ? el("ul", { class: "refs" }, n.refs.map((r) => refRow(app, r, id))) :
        el("p", { class: "muted small", text: n.evidence === "planned" ? "Planned: no implementation expected yet." :
          n.children.length ? "No direct references; see contained items." :
            "No source reference: this claim is not linked to evidence." })),
    section("Relationships",
      relGroup(app, "Outgoing", out, "to"),
      relGroup(app, "Incoming", inc, "from"),
      crossing.length ? el("p", { class: "small muted" },
        `${plural(crossing.length, "relationship")} cross this region's boundary from inside: `,
        el("button", { class: "link", onclick: () => app.showCrossing(id) }, "list")) : null,
      innerRels.length ? el("p", { class: "small muted", text: `${plural(innerRels.length, "relationship")} between items inside.` }) : null,
      !out.length && !inc.length && !crossing.length ? el("p", { class: "muted small",
        text: "No modeled relationships. That does not prove there are none." }) : null),
    flows.length ? section("Part of flows", el("ul", {}, flows.map((f) => {
      const idx = f.steps.findIndex((s) => s.node === id);
      return el("li", {}, el("button", { class: "link", onclick: () => app.setFlow(f.id, idx) },
        `${f.label}`), el("span", { class: "muted small", text: ` step ${idx + 1}` }));
    }))) : null,
    section("Review candidates if this changes",
      impact.items.length ? el("ul", { class: "impact" }, impact.items.map((it) =>
        el("li", {}, nodeLink(app, it.id), el("div", { class: "small muted", text: it.path })))) :
        el("p", { class: "small muted", text: "No modeled dependents." }),
      el("p", { class: "small note", text: `Derived from ${plural(impact.considered, "modeled relationship")}: what depends on or starts this node or its contents, and where its data goes (up to 3 steps). A listed item may be unaffected; unmodeled dependents are possible.` +
        (app.model.scope?.not_covered?.length ? " The map's scope also excludes: " + app.model.scope.not_covered.join("; ") + "." : "") })),
    el("p", { class: "small muted", text: `Defined at ${app.data.map_path}:${n.line || "?"}` }));
}

function relGroup(app, title, rels, endKey) {
  if (!rels.length) return null;
  const byType = new Map(REL_TYPES.map((t) => [t.type, []]));
  for (const r of rels) byType.get(r.type)?.push(r);
  return el("div", { class: "relgroup" }, el("h4", { text: `${title} (${rels.length})` }),
    [...byType.entries()].filter(([, list]) => list.length).map(([type, list]) => el("div", { class: "relkind" },
      el("span", { class: `cat rel-${type}`, text: type }),
      el("ul", {}, list.map((r) => el("li", {},
        nodeLink(app, r[endKey]),
        el("span", { class: "muted small", text: ` — ${relText(r)}${r.type !== "data" && r.label ? ": " + r.label : ""}` }),
        r.evidence && r.evidence !== "observed" ? el("span", { class: `pill ev-${r.evidence}`, text: EVIDENCE_LABEL[r.evidence] }) : null,
        el("button", { class: "link small", title: "Inspect relationship", onclick: () => app.select({ type: "relationship", id: r.id }) }, "details")))))));
}

function renderRelationship(app, id) {
  const r = app.relById.get(id);
  if (!r) return el("p", { class: "muted", text: `Relationship '${id}' no longer exists.` });
  return el("div", { class: "insp" },
    el("div", { class: "insp-kind" }, `${r.type.toUpperCase()} RELATIONSHIP `,
      r.kind ? el("span", { class: `cat rel-${r.type}`, text: r.kind.replace("_", " ") }) : null, " ", evidenceBadge(r.evidence)),
    el("h2", {}, nodeLink(app, r.from), el("span", { class: "muted", text: "  →  " }), nodeLink(app, r.to)),
    el("div", { class: "insp-id" }, el("code", { text: r.id })),
    r.label ? el("p", { class: "insp-summary", text: r.label }) : null,
    r.summary ? el("p", { text: r.summary }) : null,
    r.notes ? section("Notes", el("p", { text: r.notes })) : null,
    section("Evidence in source", (r.refs || []).length ? el("ul", { class: "refs" }, r.refs.map((x) => refRow(app, x, r.from))) :
      el("p", { class: "muted small", text: "No source reference for this relationship." })),
    el("p", { class: "small muted", text: `Defined at ${app.data.map_path}:${r.line || "?"}` }));
}

function renderBundle(app, ids) {
  const rels = ids.map((id) => app.relById.get(id)).filter(Boolean);
  const label = (x) => app.nodeById.get(x)?.label || x;
  return el("div", { class: "insp" },
    el("div", { class: "insp-kind", text: "CONNECTION BUNDLE" }),
    el("h2", { text: `${rels.length} relationships share this line` }),
    el("p", { class: "small muted", text: "Relationships between items inside the same two regions are routed together. Zoom in to see where each one goes, or pick one:" }),
    el("ul", {}, rels.map((r) => el("li", {},
      el("span", { class: `cat rel-${r.type}`, text: relText(r) }), " ",
      el("button", { class: "link", onclick: () => app.select({ type: "relationship", id: r.id }) },
        `${label(r.from)} → ${label(r.to)}`),
      r.label ? el("span", { class: "muted small", text: ` — ${r.label}` }) : null))));
}

export function computeImpact(app, id) {
  const start = app.descendants(id);
  const seen = new Map(); // id -> path text
  let frontier = [...start];
  let considered = 0;
  const label = (x) => app.nodeById.get(x)?.label || x;
  for (let depth = 0; depth < 3 && frontier.length; depth++) {
    const next = [];
    for (const target of frontier) {
      // who is affected when target changes: whoever depends on it or starts it (the
      // `from` of control and dependency lines), and whoever receives its data
      const edges = [
        ...(app.incoming.get(target) || []).filter((r) => r.type !== "data").map((r) => [r, r.from]),
        ...(app.outgoing.get(target) || []).filter((r) => r.type === "data").map((r) => [r, r.to]),
      ];
      for (const [r, other] of edges) {
        considered++;
        if (start.has(other) || seen.has(other)) continue;
        const via = seen.get(target);
        const step = `${label(r.from)} —${relText(r)}→ ${label(r.to)}`;
        seen.set(other, via ? `${step}; ${via}` : step);
        next.push(other);
      }
    }
    frontier = next;
  }
  return { items: [...seen.entries()].map(([nid, path]) => ({ id: nid, path })), considered };
}

// ------------------------------------------------------------------ sidebar

export function renderOutline(app, root) {
  clear(root);
  const filter = el("input", { type: "search", class: "outline-filter", placeholder: "Filter outline…", "aria-label": "Filter outline" });
  const tree = el("ul", { class: "tree", role: "tree" });
  const build = (ids, parentUl, q) => {
    for (const id of ids) {
      const n = app.nodeById.get(id);
      const match = !q || n.label.toLowerCase().includes(q) || n.id.includes(q);
      const sub = el("ul", { role: "group" });
      if (n.children.length) build(n.children, sub, match ? "" : q);
      if (!match && !sub.childNodes.length) continue;
      const li = el("li", { role: "treeitem" },
        el("button", {
          class: "tree-item" + (app.selection?.id === id ? " selected" : "") + (app.hidden.has(id) ? " hidden-node" : ""),
          dataset: { id },
          title: n.summary || n.label,
          onclick: () => app.select({ type: "node", id }, { fly: true }),
        }, el("span", { class: "tree-kind", text: kindLabel(n.kind) }), n.label,
        n.evidence && n.evidence !== "observed" && n.evidence !== "inferred" ? el("span", { class: `pill ev-${n.evidence}`, text: EVIDENCE_LABEL[n.evidence] }) : null),
        sub.childNodes.length ? sub : null);
      parentUl.append(li);
    }
  };
  build(app.layout.roots, tree, "");
  filter.addEventListener("input", () => {
    clear(tree);
    build(app.layout.roots, tree, filter.value.trim().toLowerCase());
  });
  root.append(filter, tree);
}

export function renderFlows(app, root) {
  clear(root);
  if (!app.model.flows.length) {
    root.append(el("p", { class: "muted", text: "This map defines no flows yet. A flow traces an important use case step by step, including failure paths." }));
    return;
  }
  for (const f of app.model.flows) {
    const active = app.flow?.id === f.id;
    const card = el("div", { class: "flow-card" + (active ? " active" : "") },
      el("div", { class: "flow-head" },
        el("button", { class: "btn" + (active ? " primary" : ""), "aria-expanded": active ? "true" : "false",
          title: active ? "Close this flow (Esc)" : "Show this flow's steps on the map",
          onclick: () => (active ? app.setFlow(null) : app.setFlow(f.id, 0)) }, active ? "✕ Close" : "Show"),
        el("strong", { text: f.label }), " ", f.evidence ? evidenceBadge(f.evidence) : null),
      f.summary ? el("p", { class: "small", text: f.summary }) : null);
    if (active) {
      const ol = el("ol", { class: "steps" });
      f.steps.forEach((s, i) => {
        const node = app.nodeById.get(s.node);
        const mark = { failure: "✕ failure", compensation: "↺ compensation", alternative: "⑂ alternative" }[s.kind];
        ol.append(el("li", { class: `step k-${s.kind}` + (i === app.flowStep ? " current" : "") },
          el("button", { class: "step-btn", onclick: () => app.setFlow(f.id, i) },
            el("span", { class: "step-no", text: String(i + 1) }),
            el("span", {}, s.label || "(no label)",
              el("span", { class: "small muted", text: ` — ${node ? node.label : "missing node"}` }),
              mark ? el("span", { class: "pill fail", text: mark }) : null,
              s.after && f.steps[i - 1]?.id !== s.after ? el("span", { class: "small muted", text: ` (after step ${f.steps.findIndex((x) => x.id === s.after) + 1})` }) : null,
              unlinked(f, s) ? el("span", { class: "pill warn", title: "No relationship of the map connects this step; see the validation warnings", text: "no line" }) : null))));
      });
      card.append(ol, el("p", { class: "small muted", text: "Step through with the bar on the map, or n / p. Steps are drawn on the relationships they use." }));
    }
    root.append(card);
  }
}

/** A step that moves to another node but has no relationship to be drawn on. */
function unlinked(f, s) {
  const prev = s.after ? f.steps.find((x) => x.id === s.after) : null;
  return !s.link && (s.via || (prev && prev.node && s.node && prev.node !== s.node));
}

/** The step bar on the map while a flow is shown: previous, where we are, next, close. */
export function renderFlowBar(app, root) {
  clear(root);
  const f = app.flow;
  root.hidden = !f;
  if (!f) return;
  const i = app.flowStep, s = f.steps[i], n = f.steps.length;
  const node = app.nodeById.get(s.node);
  const mark = { failure: " ✕", compensation: " ↺", alternative: " ⑂" }[s.kind] || "";
  const btn = (text, label, onclick, disabled) =>
    el("button", { class: "icon-btn", title: label, "aria-label": label, disabled: disabled ? true : null, onclick }, text);
  root.append(
    el("span", { class: "fb-title", title: f.label, text: f.label }),
    btn("‹", "Previous step (p)", () => app.stepFlow(-1), i === 0),
    el("span", { class: `fb-step k-${s.kind}` },
      el("span", { class: "step-no", text: String(i + 1) + mark }),
      el("span", { class: "fb-label", title: s.label, text: s.label || (node ? node.label : "") }),
      el("span", { class: "fb-count", text: `${i + 1} / ${n}` })),
    btn("›", "Next step (n)", () => app.stepFlow(1), i === n - 1),
    btn("✕", "Close flow (Esc)", () => app.setFlow(null)));
}

/**
 * The legend on the map: what each line type means, in the from → to reading every
 * arrow follows. Lists only the types this map uses and the filters show.
 */
export function renderMapLegend(app, root) {
  clear(root);
  if (!app.model) { root.hidden = true; return; }
  root.hidden = false;
  let open = true;
  try { open = localStorage.getItem("repoluma.legend") !== "closed"; } catch { /* storage unavailable */ }
  const toggle = el("button", { class: "lg-toggle", "aria-expanded": open ? "true" : "false",
    title: open ? "Hide the legend" : "Show the legend",
    onclick: () => {
      try { localStorage.setItem("repoluma.legend", open ? "closed" : "open"); } catch { /* ignore */ }
      renderMapLegend(app, root);
    } }, open ? "Legend ▾" : "Legend ▸");
  root.append(toggle);
  if (!open) return;
  const used = new Set(app.model.relationships.map((r) => r.type));
  const row = (cls, name, text) => el("li", {}, el("span", { class: `lg-line ${cls}` }),
    el("span", { class: "lg-name", text: name }), el("span", { class: "lg-text", text }));
  const rows = REL_TYPES.filter((t) => used.has(t.type) && !app.hiddenRelTypes.has(t.type))
    .map((t) => row(`lg-${t.type}`, t.label, t.arrow));
  rows.push(row("lg-uncertain", "dotted", "not confirmed in source"));
  if (app.flow) {
    rows.push(row("lg-flow", "flow", "steps on the relationships they use"));
    rows.push(row("lg-fail", "failure", "failure or compensation step"));
  }
  root.append(el("ul", { class: "lg-rows" }, rows));
}

export function renderFilters(app, root) {
  clear(root);
  const relCounts = new Map();
  for (const r of app.model.relationships) relCounts.set(r.type, (relCounts.get(r.type) || 0) + 1);
  const kindCounts = new Map();
  for (const n of app.model.nodes) kindCounts.set(n.kind, (kindCounts.get(n.kind) || 0) + 1);
  const tagCounts = new Map();
  for (const n of app.model.nodes) for (const t of n.tags || []) tagCounts.set(t, (tagCounts.get(t) || 0) + 1);

  const check = (label, checked, onchange, extra) => el("label", { class: "check" },
    el("input", { type: "checkbox", checked: checked ? true : null, onchange: (e) => onchange(e.target.checked) }),
    label, extra);

  const rels = REL_TYPES.filter((t) => relCounts.has(t.type));
  const relList = el("div", { class: "checks" }, rels.map((t) =>
    check(el("span", { class: `cat rel-${t.type}`, title: t.arrow, text: t.label }), !app.hiddenRelTypes.has(t.type),
      (on) => app.toggleSet("hiddenRelTypes", t.type, !on), el("span", { class: "muted small", text: ` ${relCounts.get(t.type)}` }))));
  const kinds = [...kindCounts.entries()].sort();
  const kindList = el("div", { class: "checks" }, kinds.map(([kind, c]) =>
    check(kindLabel(kind), !app.hiddenNodeKinds.has(kind), (on) => app.toggleSet("hiddenNodeKinds", kind, !on),
      el("span", { class: "muted small", text: ` ${c}` }))));
  const tags = [...tagCounts.entries()].sort();
  const tagList = tags.length ? el("div", { class: "checks" }, tags.map(([tag, c]) =>
    check("#" + tag, !app.hiddenTags.has(tag), (on) => app.toggleSet("hiddenTags", tag, !on),
      el("span", { class: "muted small", text: ` ${c}` })))) : el("p", { class: "muted small", text: "No tags in this map." });

  root.append(
    el("h3", { text: "Relationships" }),
    el("div", { class: "btn-row" },
      el("button", { class: "btn small", onclick: () => app.setRelTypes([]) }, "all"),
      el("button", { class: "btn small", onclick: () => app.setRelTypes(rels.map((t) => t.type)) }, "none")),
    relList,
    el("h3", { text: "Node kinds" }), kindList,
    el("h3", { text: "Tags" }), tagList,
    el("h3", { text: "Uncertainty" }),
    check("Highlight uncertain and unsupported claims", app.uncertainty, (on) => app.setUncertainty(on)),
    el("p", { class: "small muted", text: "Hidden nodes keep their place as dashed outlines so the map does not rearrange. Hidden counts appear at the bottom of the map." }),
    renderLegend(app));
}

function renderLegend(app) {
  return el("div", { class: "legend" },
    el("h3", { text: "Legend" }),
    el("p", { class: "small", text: "Fill hue = top-level region. Lines: arrows always point from → to; the legend on the map says what that means per type." }),
    el("ul", { class: "small legend-list" },
      el("li", {}, el("span", { class: "sw sw-solid" }), "solid border: observed or inferred"),
      el("li", {}, el("span", { class: "sw sw-dash" }), "dashed border: needs review"),
      el("li", {}, el("span", { class: "sw sw-dot" }), "dotted border: unknown"),
      el("li", {}, el("span", { class: "sw sw-long" }), "long dashes: planned (not implemented)"),
      el("li", {}, el("span", { class: "sw sw-double" }), "double border: outside the repository"),
      el("li", {}, el("span", { class: "sw sw-band" }), "thicker line with ×n: n relationships share this route; zoom in to see where each goes"),
      el("li", {}, el("span", { class: "sw sw-dotline" }), "dotted line: relationship not confirmed in source"),
      el("li", {}, el("span", { class: "sw sw-flow" }), "numbered steps, drawn on the relationships they use: open flow; dashed red with ✕/↺: failure or compensation")));
}

export function renderCoverage(app, root) {
  clear(root);
  const d = app.data;
  const diags = d.diagnostics || [];
  const errors = diags.filter((x) => x.severity === "error");
  const warnings = diags.filter((x) => x.severity === "warning");
  const refs = Object.entries(d.refs || {});
  const byStatus = new Map();
  for (const [, r] of refs) byStatus.set(r.status, (byStatus.get(r.status) || 0) + 1);
  const unsupported = app.model.nodes.filter((n) => !(n.refs || []).length && !n.children.length &&
    !["planned", "unknown"].includes(n.evidence) && !["external", "actor"].includes(n.kind));
  const uncertain = app.model.nodes.filter((n) => ["needs_review", "unknown", "planned"].includes(n.evidence));
  const changed = d.git?.reviewed?.known ? app.model.nodes.filter((n) => (n.refs || []).some((r) => d.refs[r.key]?.changed_since_review)) : [];

  const diagList = (list) => el("ul", { class: "diags" }, list.slice(0, 200).map((x) => el("li", { class: `diag ${x.severity}` },
    el("span", { class: "pill " + (x.severity === "error" ? "bad" : "warn"), text: x.severity }),
    x.record ? el("button", { class: "link", onclick: () => app.selectRecord(x.record) }, x.record) : null,
    el("span", { text: " " + x.message }),
    x.hint ? el("div", { class: "small muted", text: "hint: " + x.hint }) : null,
    el("div", { class: "small muted", text: `${d.map_path}${x.line ? ":" + x.line : ""} ${x.location || ""}` }))));

  const nodeList = (list) => el("ul", {}, list.slice(0, 100).map((n) => el("li", {}, nodeLink(app, n.id))));
  const cov = d.coverage;
  root.append(el("div", {},
    el("h3", { text: "Map health" }),
    el("p", {}, el("strong", { text: plural(errors.length, "error") }), ", ", plural(warnings.length, "warning"),
      `; ${refs.length} distinct source references.`),
    errors.length ? diagList(errors) : null,
    warnings.length ? el("details", {}, el("summary", { text: `Warnings (${warnings.length})` }), diagList(warnings)) : null,
    el("h3", { text: "Source references" }),
    el("ul", { class: "small" }, [...byStatus.entries()].sort().map(([s, c]) => {
      const [cls, text] = REF_STATUS[s] || ["bad", s];
      return el("li", {}, el("span", { class: `dot ${cls}` }), ` ${c} × ${text}`);
    })),
    d.git?.reviewed ? el("div", {},
      el("h3", { text: "Changed since review" }),
      d.git.reviewed.known ? el("p", { class: "small", text: `Map reviewed at ${d.git.reviewed.revision.slice(0, 12)}; ${plural(d.git.reviewed.changed.length, "file")} changed since. Nodes whose evidence changed:` }) :
        el("p", { class: "small warn", text: `Reviewed revision ${d.git.reviewed.revision} is unknown to git.` }),
      changed.length ? nodeList(changed) : el("p", { class: "small muted", text: "None." })) :
      el("p", { class: "small muted", text: "The map does not record scope.reviewed_revision, so staleness cannot be tracked." }),
    el("h3", { text: "Claims without evidence" }),
    unsupported.length ? nodeList(unsupported) : el("p", { class: "small muted", text: "Every leaf claim has a source reference or an explicit status." }),
    el("h3", { text: "Marked uncertain" }),
    uncertain.length ? nodeList(uncertain) : el("p", { class: "small muted", text: "None." }),
    cov ? el("div", {},
      el("h3", { text: "Repository coverage" }),
      el("p", { class: "small", text: `${cov.referenced} of ${cov.files} files are referenced by the map${cov.truncated ? " (file list truncated)" : ""}. Unreferenced files are not necessarily unmodeled, but nothing links them to the map.` }),
      el("table", { class: "cov" }, el("tbody", {}, cov.by_dir.map((r) => el("tr", {},
        el("td", { class: "mono small", text: r.dir }),
        el("td", { class: "small num", text: `${r.referenced}/${r.files}` }),
        el("td", {}, el("div", { class: "bar" }, el("div", { class: "fill", dataset: { pct: Math.round(100 * r.referenced / Math.max(1, r.files)) } })))))))) : null));
  for (const fill of root.querySelectorAll(".bar .fill")) fill.style.width = fill.dataset.pct + "%";
}
