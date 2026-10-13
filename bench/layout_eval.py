"""Measure layout quality for maps under different tuning settings.

    uv run python bench/layout_eval.py ROOT [ROOT ...]

For every region, routes are checked for: crossings between different lines (proper
orthogonal crossings), shared collinear stretches (overlaps), bends, length in channel
widths, and any line passing through an item box (should be zero). Also reports the
aspect ratio of the whole map and layout time.
"""

import json
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from repoluma.server import create_server
from repoluma.workspace import Workspace

METRICS = r"""
(settings) => {
  const app = window.repoluma;
  Object.assign(app.tuning, settings);
  const ms = app.relayout();
  const L = app.layout;
  const byC = new Map();
  for (const s of L.routes) {
    if (!s.members.some((m) => m.rel)) continue; // flow-only routes are shown one flow at a time
    if (!byC.has(s.container)) byC.set(s.container, []);
    byC.get(s.container).push(s);
  }
  let crossings = 0, routeCrossings = 0, stubX = 0, overlaps = 0, bends = 0, length = 0, through = 0, lines = 0, steps = 0;
  const eps = 1e-6;
  for (const [cid, segs] of byC) {
    const C = cid == null ? L.root : L.boxes.get(cid);
    const scale = cid == null ? 1 : C.scale;
    const gapW = C.gap * scale;
    const pieces = segs.map((s) => {
      const out = [];
      for (let i = 1; i < s.points.length; i++) out.push([s.points[i - 1], s.points[i]]);
      return out;
    });
    // item rectangles of this region in world units, shrunk slightly
    const kids = cid == null ? L.roots : C.node.children;
    const rects = kids.map((id) => { const b = L.boxes.get(id); const w = b.w * b.scale, h = b.h * b.scale;
      return [b.x + w * 0.02, b.y + h * 0.02, b.x + w * 0.98, b.y + h * 0.98]; });
    // a pair (an exchange's request and answer) is one route drawn as two lanes: it counts as
    // two lines, and each crossing of it as two crossings
    const lanes = segs.map((s) => (s.pair ? 2 : 1));
    pieces.forEach((ps, i) => {
      lines += lanes[i];
      bends += Math.max(0, ps.length - 1);
      // a short piece between two bends is a visible "step" (jog) in the line
      for (let k = 1; k < ps.length - 1; k++) {
        const [a, b] = ps[k];
        if (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) < 0.5 * gapW) steps++;
      }
      for (const [a, b] of ps) {
        length += (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1])) / gapW;
        for (const r of rects) {
          const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
          if (x1 > r[0] && x0 < r[2] && y1 > r[1] && y0 < r[3]) through++;
        }
      }
      for (let j = i + 1; j < pieces.length; j++) {
        // routes fanning out of one shared port legitimately share their first stretch
        const sharePort = (segs[i].srcItem === null && segs[j].srcItem === null) ||
          (segs[i].dstItem === null && segs[j].dstItem === null);
        for (const [a, b] of ps) for (const [c, d] of pieces[j]) {
          const h1 = Math.abs(a[1] - b[1]) < eps, h2 = Math.abs(c[1] - d[1]) < eps;
          if (h1 !== h2) {
            const [H0, H1, V0, V1] = h1 ? [a, b, c, d] : [c, d, a, b];
            const x = V0[0], y = H0[1];
            if (x > Math.min(H0[0], H1[0]) + eps && x < Math.max(H0[0], H1[0]) - eps &&
                y > Math.min(V0[1], V1[1]) + eps && y < Math.max(V0[1], V1[1]) - eps) {
              crossings += lanes[i] * lanes[j];
              routeCrossings++;
              // crossing on a connector piece that touches a box (first/last piece)
              const isStub = (P, piece) => piece === P[0] || piece === P[P.length - 1];
              if (isStub(ps, ps.find((q) => q[0] === a && q[1] === b)) || isStub(pieces[j], pieces[j].find((q) => q[0] === c && q[1] === d))) stubX += lanes[i] * lanes[j];
            }
          } else if (sharePort) {
            continue;
          } else if (h1 && Math.abs(a[1] - c[1]) < eps) {
            if (Math.min(Math.max(a[0], b[0]), Math.max(c[0], d[0])) - Math.max(Math.min(a[0], b[0]), Math.min(c[0], d[0])) > eps) overlaps++;
          } else if (!h1 && Math.abs(a[0] - c[0]) < eps) {
            if (Math.min(Math.max(a[1], b[1]), Math.max(c[1], d[1])) - Math.max(Math.min(a[1], b[1]), Math.min(c[1], d[1])) > eps) overlaps++;
          }
        }
      }
    });
  }
  const b = L.bounds;
  return { ms: Math.round(ms), lines, crossings, routeCrossings, stubX, overlaps, bends, steps, length: Math.round(length), through,
           aspect: +(b.w / b.h).toFixed(2) };
}
"""

VARIANTS = {
    "default": {},
    "single-candidate": {"candidates": False},
}


def main(roots):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for root in roots:
            httpd, url, _ = create_server(Workspace.open(root))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(url)
            t0 = time.time()
            while not page.evaluate("!!(window.repoluma && window.repoluma.layout)"):
                if time.time() - t0 > 60:
                    raise TimeoutError
                time.sleep(0.05)
            defaults = page.evaluate("({...window.repoluma.tuning})")
            print(f"== {Path(root).resolve().name}")
            for name, settings in VARIANTS.items():
                r = page.evaluate(METRICS, {**defaults, **settings})
                print(f"  {name:>15}: " + json.dumps(r))
            page.evaluate("(d) => Object.assign(window.repoluma.tuning, d)", defaults)
            page.close()
            httpd.shutdown()
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:])
