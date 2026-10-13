"""Measure viewer performance on maps (stress shapes or any repository).

    uv run python bench/run.py ROOT [ROOT ...] [--json OUT]

Needs a Chromium for Playwright (`uv run playwright install chromium`).
For each repository it records: analysis (validation + reference resolution) time,
page-ready time, layout time, per-frame render work during a scripted zoom/pan
sequence, frame intervals during animated fly-to, and source-pane open latency.
"""

import json
import platform
import sys
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from repoluma.server import create_server
from repoluma.workspace import Workspace

PAGE_SCRIPT = r"""
async () => {
  const app = window.repoluma;
  const cam = app.camera;
  const q = (xs, p) => { const s = [...xs].sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
  const summary = (xs) => ({ n: xs.length, p50: q(xs, 0.5), p95: q(xs, 0.95), max: Math.max(...xs) });
  const frame = () => new Promise((r) => requestAnimationFrame(r));
  const work = [];
  const drawn = [];
  const timed = () => {
    const t0 = performance.now();
    app.renderer.draw();
    app.panes.update(app.renderer.visiblePanes);
    work.push(performance.now() - t0);
    drawn.push(app.renderer.stats.nodes + app.renderer.stats.edges + app.renderer.stats.panes);
  };
  // 1) zoom from overview into 6 different places and back, panning at depth
  cam.set(cam.fitState(app.layout.bounds, 0.05, app.insets()));
  const targets = [...app.layout.panes.values()];
  for (let k = 0; k < 6; k++) {
    const p = targets[Math.floor((k + 0.5) * targets.length / 6)];
    const [sx, sy] = cam.toScreen(p.x, p.y);
    for (let i = 0; i < 40; i++) { cam.zoomAt(sx, sy, 1.18); timed(); await frame(); }
    for (let i = 0; i < 30; i++) { cam.panBy(-25, -8); timed(); await frame(); }
    for (let i = 0; i < 40; i++) { cam.zoomAt(cam.vw / 2, cam.vh / 2, 1 / 1.18); timed(); await frame(); }
  }
  // 2) wall-clock frame intervals during animated fly-tos (includes paint/composite)
  const intervals = [];
  for (let k = 0; k < 4; k++) {
    const p = targets[Math.floor((k + 0.3) * targets.length / 4)];
    const done = new Promise((r) => cam.flyTo(cam.fitState({ x: p.x, y: p.y, w: p.w * p.scale, h: p.h * p.scale }, 0.1), r));
    let last = performance.now();
    let running = true;
    done.then(() => { running = false; });
    while (running) { await frame(); const now = performance.now(); intervals.push(now - last); last = now; }
    // 3) source pane open latency at this destination
  }
  const latencies = [];
  for (let k = 0; k < 5; k++) {
    const p = targets[Math.floor((k + 0.7) * targets.length / 5)];
    app.sources.invalidate(null);
    cam.set(cam.fitState({ x: p.x, y: p.y, w: p.w * p.scale, h: p.h * p.scale }, 0.1));
    const t0 = performance.now();
    while (performance.now() - t0 < 5000) {
      await frame();
      const el = document.querySelector(`.pane[data-pane="${CSS.escape(p.id)}"] .ln`);
      if (el) { latencies.push(performance.now() - t0); break; }
    }
  }
  // 4) inspector with the cited code of a data line (the line with the most cited sites)
  const evidence = [];
  const rels = [...app.model.relationships].filter((r) => (r.refs || []).some((x) => (x.anchors || []).length))
    .sort((a, b) => b.refs.length - a.refs.length).slice(0, 5);
  for (const r of rels) {
    app.select(null);
    await frame();
    app.sources.invalidate(null);
    const t0 = performance.now();
    app.select({ type: "relationship", id: r.id });
    while (performance.now() - t0 < 5000) {
      await frame();
      if (document.querySelector(".ev-card .ev-fc")) { evidence.push(performance.now() - t0); break; }
    }
  }
  return { renderWork: summary(work), primitives: summary(drawn), frameInterval: summary(intervals),
           evidenceOpen: evidence.length ? summary(evidence) : null,
           paneOpen: summary(latencies), layoutMs: app.perf.layoutMs,
           nodes: app.model.nodes.length, relationships: app.model.relationships.length,
           panes: app.layout.panes.size };
}
"""


def measure(root: Path, browser) -> dict:
    ws = Workspace.open(root)
    t0 = time.perf_counter()
    analysis = ws.analyze()
    analyze_s = time.perf_counter() - t0
    httpd, url, _ = create_server(ws)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        t0 = time.perf_counter()
        page.goto(url)
        while not page.evaluate("!!(window.repoluma && window.repoluma.layout)"):
            if time.perf_counter() - t0 > 120:
                raise TimeoutError("viewer did not become ready")
            time.sleep(0.02)
        ready_s = time.perf_counter() - t0
        result = page.evaluate(PAGE_SCRIPT)
        heap = page.evaluate("performance.memory ? performance.memory.usedJSHeapSize : null")
        page.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    result.update(
        {
            "repo": root.resolve().name,
            "analyzeSeconds": round(analyze_s, 3),
            "pageReadySeconds": round(ready_s, 3),
            "refs": len(analysis["refs"]),
            "jsHeapMB": round(heap / 1e6, 1) if heap else None,
        }
    )
    return result


def main(argv):
    out = None
    if "--json" in argv:
        i = argv.index("--json")
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2 :]
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        env = {
            "browser": f"chromium {browser.version} (headless)",
            "python": platform.python_version(),
            "machine": platform.machine(),
            "system": platform.platform(),
        }
        for root in argv:
            r = measure(Path(root), browser)
            results.append(r)
            fmt = lambda s: f"p50 {s['p50']:.1f} / p95 {s['p95']:.1f} / max {s['max']:.1f} ms"
            print(
                f"{r['repo']:>10}: {r['nodes']} nodes, {r['relationships']} rels, {r['panes']} panes | "
                f"analyze {r['analyzeSeconds']}s, ready {r['pageReadySeconds']}s, layout {r['layoutMs']:.0f}ms\n"
                f"{'':>12}render work {fmt(r['renderWork'])} (primitives p95 {r['primitives']['p95']})\n"
                f"{'':>12}frame interval {fmt(r['frameInterval'])}\n"
                f"{'':>12}pane open {fmt(r['paneOpen'])}; heap {r['jsHeapMB']} MB",
                flush=True,
            )
        browser.close()
    if out:
        Path(out).write_text(json.dumps({"environment": env, "results": results}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
