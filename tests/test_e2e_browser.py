"""End-to-end journey in a real browser (skipped when Playwright/Chromium are unavailable).

uv run pytest tests/test_e2e_browser.py
"""

import re
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


def open_map(browser, root):
    """A page on the map in root (the server stops with the test process)."""
    httpd, url, _ = create_server(Workspace.open(root))
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    pg = browser.new_page(viewport={"width": 1400, "height": 900})
    pg.goto(url)
    wait(pg, "!!(window.repoluma && window.repoluma.layout)")
    return pg


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
    assert "Ordering" in pg.inner_text("#tab-outline")

    # search for the contract and open it
    pg.keyboard.press("/")
    pg.keyboard.type("authorize")
    pg.keyboard.press("Enter")
    wait(pg, "window.repoluma.selection && window.repoluma.selection.id === 'authorize-payment'")
    insp = pg.inner_text("#inspector")
    assert "Never captures money" in insp and "PaymentDeclined" in insp
    assert "review candidates" in insp.lower() and "Place order" in insp

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
    # outside the map: nothing to hit
    assert pg.evaluate("""(() => { const a = window.repoluma; const b = a.layout.bounds;
        const [sx, sy] = a.camera.toScreen(b.x - 50 / a.camera.z, b.y - 50 / a.camera.z);
        return a.renderer.hitTest(sx, sy); })()""") is None


def test_filters_disclose_what_they_hide(page):
    pg, _ = page
    pg.click("[role=tab][data-tab=filters]")
    before = pg.evaluate("window.repoluma.renderer.shownRels.size")
    assert "data" in pg.inner_text("#maplegend")
    pg.get_by_role("checkbox", name=re.compile(r"^data \d")).uncheck()
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
    assert "1 / 13" in pg.inner_text("#flowbar")
    pg.click("#flowbar button[aria-label^='Next']")
    pg.keyboard.press("n")
    time.sleep(1.2)
    assert pg.evaluate("window.repoluma.flowStep") == 2
    assert "3 / 13" in pg.inner_text("#flowbar")
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
            "label: Ordering", "label: '<img src=x onerror=window.pwned=1>Ordering'"
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
    first took the side the second needed and forced a crossing. Rip-up places it again,
    and the ways out of a region are priced with the placed lines their inside part
    crosses, so the crossing-free way out is offered at all."""
    pts = selfmap.evaluate("""(() => {
      const one = (id) => window.repoluma.layout.routes.filter((s) => s.members.some((m) => m.id === id))
        .map((s) => s.points);
      return [one("validate-command-analyzes"), one("analysis-validates")]; })()""")
    a, b = ([seg for pieces in line for seg in pairwise(pieces)] for line in pts)
    assert a and b
    assert not any(crosses(s, t) for s in a for t in b)


def test_a_short_item_sits_level_with_its_partner(browser, tmp_path):
    """An actor much shorter than the region beside it, linked to an item low in that
    region: at the top of its cell its line needs two bends; within its cell it moves
    level with its partner, so the line can run straight."""
    items = "".join(f"\n  - {{id: i{k}, parent: region, kind: responsibility, label: Item {k}}}"
                    for k in range(9))
    chain = "".join(f"\n  - {{id: r{k}, from: i{k}, to: i{k + 1}, type: data, label: x}}"
                    for k in range(8))
    (tmp_path / ".repoluma").mkdir()
    (tmp_path / ".repoluma/map.yaml").write_text(
        "schema_version: 2\nscope: {summary: test}\nnodes:"
        "\n  - {id: region, kind: capability, label: A wide region}" + items
        + "\n  - {id: actor, kind: actor, label: Someone}\nrelationships:" + chain
        + "\n  - {id: link, from: actor, to: i8, type: data, label: request}\n")
    pg = open_map(browser, tmp_path)
    actor, item = pg.evaluate("""['actor', 'i8'].map((id) => { const b = window.repoluma.layout.boxes.get(id);
      return [b.y, b.y + b.h * b.scale]; })""")
    assert actor[0] < item[1] and item[0] < actor[1]  # level with each other
    pg.close()

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


# ------------------------------------------------------------------ files on the map


def test_a_pane_caption_opens_the_file_with_its_owners(page):
    pg, _ = page
    pg.evaluate("window.repoluma.flyToNode('pricing')")
    time.sleep(1.2)
    # click the caption above the pane that cites Catalog.get
    x, y = pg.evaluate("""(() => { const a = window.repoluma;
      const p = a.layout.panes.get('pricing::shop/catalog.py#Catalog.get');
      const [sx, sy] = a.camera.toScreen(p.x + 20 / a.camera.z, p.y - 6 / a.camera.z); return [sx, sy]; })()""")
    box = pg.locator("#stage").bounding_box()
    pg.mouse.click(box["x"] + x, box["y"] + y)
    wait(pg, "window.repoluma.selection?.type === 'file'")
    assert pg.evaluate("window.repoluma.selection.path") == "shop/catalog.py"
    wait(pg, "!!document.querySelector('.fc-ln')")
    text = pg.inner_text("#inspector")
    assert "belongs to" in text.lower() and "Price the cart" in text
    wait(pg, "location.hash.includes('file=shop%2Fcatalog.py')")  # a link to this file


def test_files_tab_and_links_lead_from_a_file_to_its_place(page):
    pg, _ = page
    pg.click("[role=tab][data-tab=files]")
    assert "All 11 code files have an owner" in pg.inner_text("#tab-files")
    pg.locator(".ffile", has_text="money.py").click()
    wait(pg, "window.repoluma.selection?.path === 'shop/money.py'")
    time.sleep(1.2)
    assert pg.evaluate("window.repoluma.centerNode()") in ("money", "shared")
    # a link from an editor: #file=path:line opens the owner of that line
    pg.goto(pg.url.split("#")[0] + "#file=shop/orders.py:43")
    pg.reload()
    wait(pg, "window.repoluma.selection?.type === 'file'")
    assert pg.evaluate("window.repoluma.selection.line") == 43
    wait(pg, "!!document.querySelector('.fc-ln.target')")
    # search finds files too
    pg.fill("#search", "inventory.py")
    assert "Stock" in pg.inner_text("#search-results")


def test_new_code_files_appear_as_not_yet_mapped(page, monkeypatch):
    import repoluma.server

    monkeypatch.setattr(repoluma.server, "LISTING_INTERVAL", 0)
    pg, shop = page
    assert not pg.evaluate("window.repoluma.layout.boxes.has('~unmapped')")
    (shop / "shop" / "coupons.py").write_text("RATE = 0\n")
    wait(pg, "window.repoluma.layout.boxes.has('~unmapped')", timeout=8)
    assert pg.evaluate("window.repoluma.nodeById.get('~unmapped').fileItems") == ["coupons.py"]
    pg.evaluate("window.repoluma.select({type: 'node', id: '~unmapped'})")
    text = pg.inner_text("#inspector")
    assert "coupons.py" in text and "how to map them" in text.lower()


def test_outer_boxes_name_their_files_and_show_all_their_code(page):
    pg, _ = page
    items = pg.evaluate("""Object.fromEntries(['purchasing', 'shared', 'place-order', 'pricing']
      .map((id) => [id, window.repoluma.nodeById.get(id).fileItems]))""")
    assert items["purchasing"] == ["catalog.py", "orders.py", "checkout.ts"]
    assert items["shared"] == ["__init__.py", "money.py"]
    assert items["place-order"] == [] and items["pricing"] == []  # captions name them
    roles = pg.evaluate("window.repoluma.layout.boxes.get('purchasing').header.blocks.map((b) => b.role)")
    assert roles.index("files") == roles.index("summary") + 1
    # every line of every owned file appears in some pane of its area
    covered = pg.evaluate("""(() => { const a = window.repoluma; const out = {};
      for (const [path, rec] of Object.entries(a.files.files)) {
        if (!rec.lines) continue;
        let missing = 0;
        for (let n = 1; n <= rec.lines; n++) if (!a.files.shownAt(path, n)) missing++;
        out[path] = missing;
      } return out; })()""")
    assert covered and not any(covered.values()), covered


def test_code_no_node_shows_is_flagged_where_it_lives(page):
    pg, shop = page
    assert not pg.evaluate("window.repoluma.layout.panes.has('purchasing::shop/orders.py#~rest')")
    p = shop / "shop" / "orders.py"
    p.write_text(p.read_text() + "\n\ndef apply_coupon(total, code):\n    return total\n")
    wait(pg, "window.repoluma.layout.panes.has('purchasing::shop/orders.py#~rest')", timeout=8)
    assert "error" in pg.inner_text("#health")
    pg.evaluate("window.repoluma.flyToRef('purchasing', 'shop/orders.py#~rest')")
    sel = '.pane[data-pane="purchasing::shop/orders.py#~rest"]'
    wait(pg, f"!!document.querySelector('{sel} .ln.fold')")
    pane = pg.locator(sel)
    assert "unexplained" in pane.get_attribute("class")
    assert "not yet explained" in pane.locator(".pane-head").inner_text()
    assert "def apply_coupon" in pane.inner_text()
    fold = pane.locator(".ln.fold").first.inner_text()
    assert fold.count("Place order") == 1 and "Max 50 lines per cart" in fold and "imports and comments" in fold
    pane.locator(".fold-link", has_text="Max 50 lines per cart").click()
    time.sleep(1.2)
    assert pg.evaluate("window.repoluma.centerNode()") in ("cart-limits", "purchasing")


def test_the_file_view_says_which_pane_shows_each_line(page):
    pg, _ = page
    pg.evaluate("window.repoluma.selectFile('shop/orders.py', 43)")
    wait(pg, "!!document.querySelector('.fc-ln.target')")
    labels = pg.eval_on_selector_all(".fc-owner", "els => els.map((e) => e.innerText)")
    # the file's header has no pane of its own; then each cited block, glue joining the one before
    assert labels == ["Ordering", "Max 50 lines per cart", "Place order", "Price the cart", "Place order"]
    # the line asked for is in view, and the header (where the file belongs) stays visible
    assert pg.evaluate("""(() => { const r = document.querySelector('.fc-ln.target').getBoundingClientRect();
      const c = document.querySelector('.file-code').getBoundingClientRect();
      return r.top >= c.top && r.bottom <= c.bottom; })()""")
    assert pg.locator(".file-view h2").is_visible()
    assert "belongs to" in pg.inner_text("#inspector").lower()


# ------------------------------------------------------------- data exchanges

ZOOM_TO_PAYMENT = """(() => { const a = window.repoluma;
  const r = (id) => { const b = a.layout.boxes.get(id); return { x: b.x, y: b.y, w: b.w * b.scale, h: b.h * b.scale }; };
  const p = r('place-order'), q = r('authorize-payment');
  const x = Math.min(p.x, q.x), y = Math.min(p.y, q.y);
  a.camera.set(a.camera.fitState({ x, y, w: Math.max(p.x + p.w, q.x + q.w) - x,
    h: Math.max(p.y + p.h, q.y + q.h) - y }, 0.05, a.insets())); })()"""

LABEL_AT = """(id) => { const a = window.repoluma, st = document.querySelector('#stage').getBoundingClientRect();
  const h = a.renderer.labelHits.find((h) => h.members.length === 1 && h.members[0].id === id);
  return h ? [st.left + (h.x0 + h.x1) / 2, st.top + (h.y0 + h.y1) / 2] : null; }"""


def test_data_label_opens_its_evidence_without_moving_the_map(page):
    pg, _ = page
    pg.evaluate(ZOOM_TO_PAYMENT)
    # zoom in until the label has room (it depends on the viewport and fonts)
    for _ in range(6):
        time.sleep(0.3)
        if pg.evaluate(LABEL_AT, "order-authorizes"):
            break
        pg.evaluate("window.repoluma.zoomBy(1.25)")
    x, y = pg.evaluate(LABEL_AT, "order-authorizes")
    before = cam(pg)
    pg.mouse.click(x, y)
    wait(pg, "window.repoluma.selection?.id === 'order-authorizes'")
    time.sleep(0.4)
    assert cam(pg) == before  # the camera stays where it was
    insp = pg.inner_text("#inspector").lower()  # section titles are upper-cased by CSS
    assert "payment token and total" in insp and "exchange" in insp
    assert "authorization" in insp and "decline reason" in insp
    # the exact call site, highlighted inside readable code
    wait(pg, "[...document.querySelectorAll('.ev-card.role-transfer')].some((c) => c.querySelector('.ev-mark'))")
    marked = pg.evaluate("[...document.querySelectorAll('.ev-mark')].map((m) => m.textContent).join('')")
    assert "self.payments.authorize(payment_token, total)" in marked
    # the other legs of the exchange are highlighted, more quietly
    assert pg.evaluate("[...window.repoluma.renderer.pairFocus].sort()") == ["authorization-returned", "payment-declined"]
    assert "r%3Aorder-authorizes" in pg.url or "r:order-authorizes" in pg.url
    summary = pg.evaluate("window.repoluma.relationshipSummary('order-authorizes')")
    assert "Exchange `payment-authorization` (input)" in summary
    assert '"self.payments.authorize(payment_token, total)" found at line' in summary
    # move to the result leg from the inspector, then back
    pg.locator(".legs button", has_text="authorization").click()
    wait(pg, "window.repoluma.selection?.id === 'authorization-returned'")
    assert "taken in" in pg.inner_text("#inspector").lower()
    pg.click(".insp-back")
    wait(pg, "window.repoluma.selection?.id === 'order-authorizes'")


def test_missing_evidence_is_said_not_replaced(page):
    pg, _ = page
    pg.evaluate("window.repoluma.select({ type: 'relationship', id: 'customer-returns' })")
    text = pg.inner_text("#inspector")
    assert "No site is cited" in text
    assert not pg.query_selector(".ev-card")


def test_lines_tab_reaches_every_line_by_keyboard(page):
    pg, _ = page
    pg.click("#sidebar [role=tab] >> text=Lines")
    assert pg.locator(".conn-item").count() == pg.evaluate("window.repoluma.model.relationships.length")
    assert "EXCHANGES (5)" in pg.inner_text("#tab-connections").upper()
    before = cam(pg)
    item = pg.locator(".conn-item[data-id='payment-declined']")
    item.focus()
    pg.keyboard.press("Enter")
    wait(pg, "window.repoluma.selection?.id === 'payment-declined'")
    assert cam(pg) == before
    assert "decline reason" in pg.inner_text("#inspector")


def test_overview_line_names_its_true_ends(page):
    pg, _ = page
    wait(pg, "(window.repoluma.renderer.hitSegs || []).length > 0")
    point = pg.evaluate("""(() => { const a = window.repoluma, st = document.querySelector('#stage').getBoundingClientRect();
      const h = a.renderer.hitSegs.find((h) => h.members.length === 1 && h.members[0].id === 'order-authorizes');
      if (!h) return null;
      let best = null;
      for (let i = 1; i < h.pts.length; i++) {
        const [p, q] = [h.pts[i - 1], h.pts[i]], len = Math.hypot(q[0] - p[0], q[1] - p[1]);
        if (!best || len > best[2]) best = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, len];
      }
      return [st.left + best[0], st.top + best[1]]; })()""")
    assert point, "the overview draws the payment input line"
    # at overview the line ends on the regions; its tooltip names the items it connects
    pg.mouse.move(*point)
    wait(pg, "!document.querySelector('#tip').hidden")
    tip = pg.inner_text("#tip")
    assert "Place order → Payment authorization" in tip


def test_renamed_selection_is_explained(page):
    pg, shop = page
    pg.evaluate("window.repoluma.select({ type: 'relationship', id: 'payment-declined' })")
    path = shop / ".repoluma/map.yaml"
    text = path.read_text()
    path.write_text(text.replace("id: payment-declined", "id: authorization-declined")
                    .replace("via: payment-declined", "via: authorization-declined"))
    wait(pg, "!window.repoluma.relById.has('payment-declined')", timeout=15)
    assert "NO LONGER IN THE MAP" in pg.inner_text("#inspector").upper()
    assert "payment-declined" in pg.inner_text("#inspector")


def test_exchange_is_one_route_drawn_as_two_lanes(page):
    pg, _ = page
    pg.evaluate(ZOOM_TO_PAYMENT)
    time.sleep(0.5)
    route = pg.evaluate("""(() => { const s = window.repoluma.layout.routes.find((s) => s.members.some((m) => m.id === 'order-authorizes'));
      return { pair: s.pair, ids: s.members.map((m) => m.id).sort() }; })()""")
    # input and result share one route; the error leg has a route of its own
    assert route == {"pair": True, "ids": ["authorization-returned", "order-authorizes"]}
    # each lane is its own click target, so the two directions are picked separately
    lanes = pg.evaluate("""window.repoluma.renderer.hitSegs.filter((h) => h.members.length === 1 &&
      ['order-authorizes', 'authorization-returned'].includes(h.members[0].id)).map((h) => h.members[0].id)""")
    assert {"order-authorizes", "authorization-returned"} <= set(lanes)
    for rid in ("order-authorizes", "authorization-returned"):
        x, y = pg.evaluate(LABEL_AT, rid) or (None, None)
        if x is None:
            continue  # the label had no room at this zoom; the lane is still clickable
        pg.mouse.click(x, y)
        wait(pg, f"window.repoluma.selection?.id === '{rid}'")


def test_lanes_of_a_pair_keep_clear_of_neighbouring_lines(selfmap):
    """Lanes take room: a lane keeps min(sep, 6 px) clear of another line, or as much as the
    two routes themselves have where the router had to put them closer (a crowded box side)."""
    pg = selfmap
    worst = pg.evaluate("""(async () => {
      const a = window.repoluma, frame = () => new Promise((r) => requestAnimationFrame(r));
      const fit = a.camera.fitState(a.layout.bounds, 0.05, a.insets());
      const b = a.layout.boxes.get('serve');
      const runs = (pts) => { const o = []; for (let i = 1; i < pts.length; i++) o.push([pts[i - 1], pts[i]]); return o; };
      // closest parallel distance between two polylines along a shared stretch (or Infinity)
      const gap = (P, Q) => { let d = Infinity;
        for (const [p, q] of runs(P)) for (const [r, s] of runs(Q)) for (const ax of [0, 1]) {
          const o = 1 - ax;
          if (Math.abs(p[o] - q[o]) > 0.01 || Math.abs(r[o] - s[o]) > 0.01) continue;
          const lo = Math.max(Math.min(p[ax], q[ax]), Math.min(r[ax], s[ax]));
          const hi = Math.min(Math.max(p[ax], q[ax]), Math.max(r[ax], s[ax]));
          if (hi - lo > 1) d = Math.min(d, Math.abs(p[o] - r[o]));
        }
        return d; };
      let worst = Infinity;
      for (const f of [1, 1.6, 2.5, 4]) {
        const z = fit.z * f;
        a.camera.set({ z, x: b.x - 40 / z, y: b.y - 60 / z });
        await frame(); await frame();
        const hs = a.renderer.hitSegs;
        for (let i = 0; i < hs.length; i++) for (let j = i + 1; j < hs.length; j++) {
          const g = hs[i], k = hs[j];
          if (g.route === k.route) continue; // the two lanes of one pair
          const drawn = gap(g.pts, k.pts);
          if (drawn === Infinity) continue;
          const allowed = Math.min(Math.min(g.sep, k.sep, 6), gap(g.center, k.center));
          worst = Math.min(worst, drawn - allowed);
        }
      }
      return worst; })()""")
    assert worst >= -0.05, worst



def test_pairs_between_regions_have_equal_wide_lanes(page):
    """Outside the regions every pair's lanes are equally far apart at a given zoom."""
    pg, _ = page
    spreads = pg.evaluate("""(async () => {
      const a = window.repoluma, frame = () => new Promise((r) => requestAnimationFrame(r));
      const fit = a.camera.fitState(a.layout.bounds, 0.05, a.insets());
      const out = [];
      for (const f of [1, 2, 4]) {
        a.camera.set({ ...fit, z: fit.z * f });
        await frame(); await frame();
        const widths = a.renderer.hitSegs.filter((h) => h.piece?.pair && h.piece.container == null)
          .map((h) => Math.min(...h.center.map((q) => Math.hypot(q[0] - h.pts[0][0], q[1] - h.pts[0][1]))));
        out.push([f, widths.length, Math.min(...widths), Math.max(...widths)]);
      }
      return out; })()""")
    for f, n, lo, hi in spreads:
        if n:
            assert hi - lo < 0.05, (f, lo, hi)
    assert any(n for _, n, _, _ in spreads), "the shop has pairs between its regions"
