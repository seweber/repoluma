"""End-to-end journey in a real browser (skipped when Playwright/Chromium are unavailable).

uv run --with playwright pytest tests/test_e2e_browser.py
"""

import threading
import time
from itertools import pairwise
from pathlib import Path

import pytest

from repoluma.server import create_server
from repoluma.workspace import Workspace

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - browser binary or system libraries missing
            pytest.skip(f"chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, shop):
    httpd, url, _ = create_server(Workspace.open(shop))
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url)
    wait(pg, "!!(window.repoluma && window.repoluma.layout)")
    yield pg, shop
    assert not errors, errors
    pg.close()
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def selfmap(browser):
    """This repository's own map: larger than the shop, with lines across nested regions."""
    root = Path(__file__).resolve().parents[1]
    httpd, url, _ = create_server(Workspace.open(root))
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    pg.goto(url)
    wait(pg, "!!(window.repoluma && window.repoluma.layout)")
    yield pg
    pg.close()
    httpd.shutdown()
    httpd.server_close()


def wait(pg, expr, timeout=10):
    t0 = time.time()
    while not pg.evaluate(expr):
        if time.time() - t0 > timeout:
            raise AssertionError(f"timed out waiting for {expr}")
        time.sleep(0.05)


def cam(pg):
    return pg.evaluate("({...window.repoluma.camera.state})")


def stage_center(pg):
    box = pg.locator("#stage").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, box


def test_overview_to_contract_to_code_and_back(page):
    pg, _ = page
    start = cam(pg)
    # overview names the capabilities (outline mirrors the map for keyboard users)
    assert "Purchasing" in pg.inner_text("#tab-outline")

    # search for the contract and open it
    pg.keyboard.press("/")
    pg.keyboard.type("authorize")
    pg.keyboard.press("Enter")
    wait(pg, "window.repoluma.selection && window.repoluma.selection.id === 'authorize-payment'")
    insp = pg.inner_text("#inspector")
    assert "Never captures money" in insp and "PaymentDeclined" in insp
    assert "review candidates" in insp.lower() and "Place an order" in insp

    # zoom to the code: the pane shows working-tree source with line numbers
    pg.evaluate(
        "window.repoluma.flyToRef('authorize-payment', 'shop/payments.py#PaymentService.authorize')"
    )
    wait(pg, "!!document.querySelector('.pane .ln.in')")
    text = pg.inner_text("#panes")
    assert "def authorize(self, payment_token" in text
    assert "symbol located" in text

    # wheel over an inactive pane zooms the map; after activation it scrolls the code
    pane = pg.locator(".pane").first
    pb = pane.bounding_box()
    pg.mouse.move(pb["x"] + pb["width"] / 2, pb["y"] + pb["height"] / 2)
    z0 = cam(pg)["z"]
    pg.mouse.wheel(0, 100)
    time.sleep(0.2)
    assert cam(pg)["z"] < z0
    pg.mouse.wheel(0, -100)
    time.sleep(0.2)

    # back to the overview and the original camera (reversible navigation)
    pg.keyboard.press("Escape")
    pg.keyboard.press("0")
    time.sleep(1.4)
    end = cam(pg)
    assert abs(end["z"] - start["z"]) / start["z"] < 0.01


def test_zoom_is_anchored_and_reversible(page):
    pg, _ = page
    x, y, _ = stage_center(pg)
    pg.mouse.move(x + 100, y + 50)
    before = cam(pg)
    world_before = pg.evaluate(
        f"window.repoluma.camera.toWorld({x + 100} - document.querySelector('#stage').getBoundingClientRect().left, {y + 50} - document.querySelector('#stage').getBoundingClientRect().top)"
    )
    for _ in range(5):
        pg.mouse.wheel(0, -120)
        time.sleep(0.03)
    world_after = pg.evaluate(
        f"window.repoluma.camera.toWorld({x + 100} - document.querySelector('#stage').getBoundingClientRect().left, {y + 50} - document.querySelector('#stage').getBoundingClientRect().top)"
    )
    assert abs(world_before[0] - world_after[0]) < 1e-6 * max(1, abs(world_before[0])) + 1e-3
    for _ in range(5):
        pg.mouse.wheel(0, 120)
        time.sleep(0.03)
    after = cam(pg)
    assert abs(after["z"] - before["z"]) / before["z"] < 1e-6


def test_panning_at_close_zoom_reveals_the_same_depth_elsewhere(page):
    pg, _ = page
    # zoom close to one responsibility, then pan to another region at the same scale
    pg.evaluate("window.repoluma.flyToNode('reserve-stock')")
    time.sleep(1.4)
    z = cam(pg)["z"]
    pg.evaluate("""(() => { const a = window.repoluma; const b = a.layout.boxes.get('ship-order');
        const c = a.camera; c.set({ z: c.z, x: b.x + b.w * b.scale / 2 - c.vw / c.z / 2,
                                    y: b.y + b.h * b.scale / 2 - c.vh / c.z / 2 }); })()""")
    time.sleep(0.3)
    assert cam(pg)["z"] == z
    # the other region's node is drawn with its full header (same depth, same size)
    sizes = pg.evaluate("""(() => { const a = window.repoluma;
        return ['reserve-stock', 'ship-order'].map((id) => a.layout.boxes.get(id).scale * a.camera.z); })()""")
    assert sizes[0] == sizes[1]
    assert pg.evaluate("window.repoluma.renderer.stats.text") > 5


def test_hit_testing_and_selection_at_viewport_edges(page):
    pg, _ = page
    pg.evaluate("window.repoluma.flyToNode('purchasing')")
    time.sleep(1.4)
    # a node that straddles the viewport edge is still hit-testable where visible
    hit = pg.evaluate("""(() => { const a = window.repoluma; const b = a.layout.boxes.get('purchasing');
        const [sx, sy] = a.camera.toScreen(b.x, b.y); return a.renderer.hitTest(Math.max(2, sx + 3), Math.max(2, sy + 3)); })()""")
    assert hit and hit["id"] == "purchasing"
    assert pg.evaluate("window.repoluma.renderer.hitTest(-50, -50)") is None


def test_filters_disclose_what_they_hide(page):
    pg, _ = page
    pg.click("[role=tab][data-tab=filters]")
    before = pg.evaluate("window.repoluma.renderer.shownRels.size")
    assert "data" in pg.inner_text("#maplegend")
    pg.locator("label.check", has_text="data").locator("input").uncheck()
    time.sleep(0.3)
    status = pg.inner_text("#statusbar")
    assert "hidden by relationship filter" in status
    assert pg.evaluate("window.repoluma.renderer.shownRels.size") < before
    assert "hr=data" in pg.url  # reproducible view link
    assert "where the data comes from" not in pg.inner_text("#maplegend")  # legend follows filters
    pg.locator("label.check", has_text="Rule").locator("input").uncheck()
    time.sleep(0.3)
    assert "hidden by filter" in pg.inner_text("#statusbar")


def test_flow_steps_and_agent_summary(page):
    pg, _ = page
    assert pg.is_hidden("#flowbar")
    pg.click("#sidebar [role=tab] >> text=Flows")
    pg.locator(".flow-card", has_text="Place an order").locator("button", has_text="Show").click()
    time.sleep(1.2)
    assert pg.is_visible("#flowbar")
    assert "1 / 9" in pg.inner_text("#flowbar")
    pg.click("#flowbar button[aria-label^='Next']")
    pg.keyboard.press("n")
    time.sleep(1.2)
    assert pg.evaluate("window.repoluma.flowStep") == 2
    assert "3 / 9" in pg.inner_text("#flowbar")
    assert "f=flow-place-order%2C2" in pg.url or "f=flow-place-order,2" in pg.url
    summary = pg.evaluate("window.repoluma.agentSummary('place-order')")
    assert "`place-order`" in summary and "shop/orders.py#OrderService.place_order" in summary
    assert "Active flow: flow-place-order" in summary
    # a flow draws no lines of its own: every step with a line uses a relationship
    assert pg.evaluate(
        """(() => { const a = window.repoluma;
          const routed = a.layout.routes.every((s) => s.members.every((m) => m.rel));
          const linked = a.flow.steps.filter((s) => s.link).every((s) => a.relById.has(s.link));
          return routed && linked && a.flow.steps.slice(1).every((s) => s.link); })()"""
    )
    # both ends of every relationship the flow is drawn on take part (not dimmed)
    assert pg.evaluate(
        """(() => { const a = window.repoluma, f = a.focusSet();
          return a.flow.steps.filter((s) => s.link).map((s) => a.relById.get(s.link))
            .every((r) => f.has(r.from) && f.has(r.to)); })()"""
    )
    pg.click("#flowbar button[aria-label^='Close']")
    assert pg.is_hidden("#flowbar") and pg.evaluate("window.repoluma.flow") is None
    pg.evaluate("window.repoluma.setFlow('flow-place-order', 0)")
    pg.keyboard.press("Escape")  # first clears the step's selection, then closes the flow
    pg.keyboard.press("Escape")
    assert pg.is_hidden("#flowbar")


def test_live_refresh_shows_changed_and_broken_references(page):
    pg, shop = page
    pg.evaluate("window.repoluma.flyToRef('ship-order', 'shop/fulfillment.py#Fulfillment.ship')")
    wait(pg, "!!document.querySelector('.pane .ln.in')")
    p = shop / "shop" / "fulfillment.py"
    p.write_text(p.read_text().replace('"""Commit stock', '"""LIVE EDIT. Commit stock'))
    wait(pg, "document.querySelector('#panes').innerText.includes('LIVE EDIT')", timeout=8)
    p.write_text(p.read_text().replace("def ship(", "def dispatch("))
    wait(pg, "document.querySelector('#health').innerText.includes('error')", timeout=8)
    assert (
        pg.evaluate("window.repoluma.refs['shop/fulfillment.py#Fulfillment.ship'].status")
        == "missing_symbol"
    )


def test_untrusted_text_is_not_interpreted_as_html(browser, shop):
    m = shop / ".repoluma" / "map.yaml"
    m.write_text(
        m.read_text().replace(
            "label: Purchasing", "label: '<img src=x onerror=window.pwned=1>Purchasing'"
        )
    )
    httpd, url, _ = create_server(Workspace.open(shop))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    pg = browser.new_page()
    pg.goto(url)
    wait(pg, "!!(window.repoluma && window.repoluma.layout)")
    pg.evaluate("window.repoluma.select({type: 'node', id: 'purchasing'})")
    time.sleep(0.3)
    assert pg.evaluate("window.pwned === undefined")
    assert pg.locator("#inspector img").count() == 0
    assert "<img src=x" in pg.inner_text("#inspector")
    pg.close()
    httpd.shutdown()


def test_routes_are_orthogonal_and_avoid_boxes(page):
    pg, _ = page
    r = pg.evaluate("""(() => {
      const L = window.repoluma.layout; let diagonal = 0, through = 0;
      for (const s of L.routes) {
        const kids = s.container == null ? L.roots : L.boxes.get(s.container).node.children;
        const rects = kids.map((id) => { const b = L.boxes.get(id); const w = b.w * b.scale, h = b.h * b.scale;
          return [b.x + w * 0.02, b.y + h * 0.02, b.x + w * 0.98, b.y + h * 0.98]; });
        for (let i = 1; i < s.points.length; i++) {
          const [a, b] = [s.points[i - 1], s.points[i]];
          if (Math.abs(a[0] - b[0]) > 1e-6 && Math.abs(a[1] - b[1]) > 1e-6) diagonal++;
          for (const q of rects) {
            const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
            if (x1 > q[0] && x0 < q[2] && y1 > q[1] && y0 < q[3]) through++;
          }
        }
      }
      return { n: L.routes.length, diagonal, through };
    })()""")
    assert r["n"] > 20 and r["diagonal"] == 0 and r["through"] == 0


def test_layout_is_deterministic(page):
    pg, _ = page
    sig = """(() => { const L = window.repoluma.layout;
      return JSON.stringify([...L.boxes.values()].map((b) => [b.id, +b.x.toFixed(3), +b.y.toFixed(3)])
        .concat(L.routes.map((s) => s.points.flat().map((v) => +v.toFixed(3))))); })()"""
    first = pg.evaluate(sig)
    pg.evaluate("window.repoluma.relayout()")
    assert pg.evaluate(sig) == first


def test_clicking_a_line_selects_its_relationships(page):
    pg, _ = page
    pg.evaluate("window.repoluma.flyToNode('purchasing')")
    time.sleep(1.4)
    pt = pg.evaluate("""(() => { const a = window.repoluma; let best = null;
      for (const h of a.renderer.hitSegs) for (let i = 1; i < h.pts.length; i++) {
        const [p, q] = [h.pts[i - 1], h.pts[i]]; const len = Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]);
        const m = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2];
        if ((!best || len > best[2]) && m[0] > 20 && m[1] > 20 && m[0] < a.camera.vw - 220 && m[1] < a.camera.vh - 160) best = [m[0], m[1], len];
      }
      return best; })()""")
    box = pg.locator("#stage").bounding_box()
    pg.mouse.click(box["x"] + pt[0], box["y"] + pt[1])
    time.sleep(0.3)
    sel = pg.evaluate("window.repoluma.selection")
    assert sel["type"] in ("relationship", "bundle")


def test_no_small_steps_in_lines(page):
    """Lines do not jog: no short piece between two bends (shared ports, straight neighbours).

    Inside a region the router decides alone, so there are none. Between top-level regions
    a short jog remains where the free corridors on the two sides do not line up (which
    is decided by placement); those must stay rare.
    """
    pg, _ = page
    inside, between, lines = pg.evaluate("""(() => {
      const L = window.repoluma.layout; let inside = 0, between = 0;
      for (const s of L.routes) {
        const C = s.container == null ? L.root : L.boxes.get(s.container);
        const gapW = C.gap * (s.container == null ? 1 : C.scale);
        for (let i = 2; i < s.points.length - 1; i++) {
          const [a, b] = [s.points[i - 1], s.points[i]];
          if (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) < 0.5 * gapW) {
            if (s.container == null) between++; else inside++;
          }
        }
      }
      return [inside, between, L.routes.length];
    })()""")
    assert inside == 0
    assert between <= lines / 20


def test_a_line_placed_early_does_not_force_a_crossing(selfmap):
    """Diagnostics (analysis -> diagnostics) is placed before the normalized model
    (check integrity -> analysis); both run between the same regions. Placed greedily, the
    first took the side the second needed and forced a crossing; rip-up re-places it."""
    pts = selfmap.evaluate("""(() => {
      const one = (id) => window.repoluma.layout.routes.filter((s) => s.members.some((m) => m.id === id))
        .map((s) => s.points);
      return [one("validate-command-analyzes"), one("analysis-validates")]; })()""")
    a, b = ([seg for pieces in line for seg in pairwise(pieces)] for line in pts)
    assert a and b
    assert not any(crosses(s, t) for s in a for t in b)


def test_a_line_is_annotated_once(selfmap):
    """A line crossing region borders is drawn in pieces, one per region; it carries one label."""
    for node in ("serve", "navigate", "validate", "discuss"):
        selfmap.evaluate(f"window.repoluma.flyToNode('{node}')")
        time.sleep(1.4)
        noted = selfmap.evaluate("window.repoluma.renderer.noted")
        assert noted, node
        assert len(noted) == len(set(noted)), (node, noted)


def crosses(s, t):
    """Proper crossing of a horizontal and a vertical segment."""
    (a, b), (c, d) = s, t
    if (abs(a[1] - b[1]) < 1e-6) == (abs(c[1] - d[1]) < 1e-6):
        return False
    (h0, h1), (v0, v1) = (s, t) if abs(a[1] - b[1]) < 1e-6 else (t, s)
    x, y = v0[0], h0[1]
    return (
        min(h0[0], h1[0]) + 1e-6 < x < max(h0[0], h1[0]) - 1e-6
        and min(v0[1], v1[1]) + 1e-6 < y < max(v0[1], v1[1]) - 1e-6
    )
