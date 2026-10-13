import http.client
import json
import threading
import time

import pytest

from repoluma.server import create_server
from repoluma.workspace import Workspace


@pytest.fixture
def server(shop):
    httpd, _, token = create_server(Workspace.open(shop))
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    port = httpd.server_address[1]
    yield port, token, shop
    httpd.shutdown()
    httpd.server_close()


def request(port, path, headers=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    conn.request(method, path, headers=h)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    return r.status, dict(r.getheaders()), body


def api(port, token, path, **headers):
    status, _, body = request(port, path, {"X-Repoluma-Token": token, **headers})
    return status, json.loads(body)


def test_index_carries_token_and_csp(server):
    port, token, _ = server
    status, headers, body = request(port, "/")
    assert status == 200 and token.encode() in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    status, headers, body = request(port, "/static/js/main.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")


def test_static_traversal_refused(server):
    port, *_ = server
    assert request(port, "/static/../__init__.py")[0] == 404
    assert request(port, "/static/%2e%2e/__init__.py")[0] == 404


def test_api_requires_token(server):
    port, token, _ = server
    assert request(port, "/api/bootstrap")[0] == 403
    assert request(port, "/api/bootstrap", {"X-Repoluma-Token": token + "x"})[0] == 403
    status, data = api(port, token, "/api/bootstrap")
    assert status == 200 and len(data["model"]["nodes"]) > 10


def test_dns_rebinding_host_refused(server):
    port, token, _ = server
    status, _, _ = request(port, "/", {"Host": f"evil.example:{port}"})
    assert status == 403
    status, _, _ = request(port, "/api/status", {"Host": "evil.example", "X-Repoluma-Token": token})
    assert status == 403


def test_cross_origin_refused(server):
    port, token, _ = server
    status, _ = api(port, token, "/api/status", Origin="http://evil.example")
    assert status == 403
    status, _ = api(port, token, "/api/status", Origin=f"http://127.0.0.1:{port}")
    assert status == 200


def test_read_only(server):
    port, token, _ = server
    assert request(port, "/api/status", {"X-Repoluma-Token": token}, method="POST")[0] == 405


def test_source_endpoint_serves_only_referenced_or_owned_files(server):
    port, token, shop = server
    status, data = api(port, token, "/api/source?path=shop/orders.py&start=1&end=5")
    assert status == 200 and data["lines"][0].startswith('"""')
    # owned, not referenced: shown in the file view
    status, data = api(port, token, "/api/source?path=shop/__init__.py&start=1&end=5")
    assert status == 200 and data["lines"]
    (shop / "notes.txt").write_text("not on the map\n")
    (shop / ".env").write_text("SECRET=1\n")
    for bad in ("notes.txt", ".env", "../etc/passwd", ".repoluma/map.yaml", "/etc/passwd"):
        status, data = api(port, token, f"/api/source?path={bad}")
        assert data.get("error") in ("not_referenced",), (bad, data)


def test_status_digest_changes_when_referenced_file_changes(server):
    port, token, shop = server
    _, s1 = api(port, token, "/api/status")
    time.sleep(0.01)
    p = shop / "shop" / "orders.py"
    p.write_text(p.read_text() + "\n# edit\n")
    _, s2 = api(port, token, "/api/status")
    assert s1["digest"] != s2["digest"]
    # unreferenced files do not trigger reloads
    q = shop / "shop" / "__init__.py"
    q.write_text(q.read_text() + "\n# edit\n")
    _, s3 = api(port, token, "/api/status")
    assert s3["digest"] == s2["digest"]


def test_status_digest_changes_when_files_are_added(server, monkeypatch):
    import repoluma.server

    monkeypatch.setattr(repoluma.server, "LISTING_INTERVAL", 0)
    port, token, shop = server
    _, s1 = api(port, token, "/api/status")
    (shop / "shop" / "coupons.py").write_text("RATE = 0\n")
    _, s2 = api(port, token, "/api/status")
    assert s1["digest"] != s2["digest"]
    _, boot = api(port, token, "/api/bootstrap")
    assert boot["ownership"]["unowned"] == ["shop/coupons.py"]


def test_bootstrap_without_map(tmp_path):
    ws = Workspace.open(tmp_path)
    data = ws.analyze()
    assert data["model"] is None and data["map_exists"] is False
    assert data["diagnostics"][0]["code"] == "missing-map"


def test_failing_browser_helper_prints_one_line(monkeypatch, capsys, tmp_path):
    from repoluma.server import open_browser_quietly

    helper = tmp_path / "browser.sh"
    helper.write_text("#!/bin/sh\necho 'Error: connect ECONNREFUSED' >&2\nexit 1\n")
    helper.chmod(0o755)
    monkeypatch.setenv("BROWSER", str(helper))
    open_browser_quietly("http://127.0.0.1:1/")
    out = capsys.readouterr()
    assert "ECONNREFUSED" not in out.out + out.err
    assert "open http://127.0.0.1:1/ yourself" in out.out
    helper.write_text("#!/bin/sh\nexit 0\n")
    open_browser_quietly("http://127.0.0.1:1/")
    assert capsys.readouterr().out == ""


def test_keep_alive_serves_whole_responses_on_one_connection(tmp_path):
    """Large responses arrive complete, several on one connection (HTTP/1.1)."""
    import http.client
    import threading

    from repoluma.server import create_server
    from repoluma.workspace import Workspace

    httpd, _, _ = create_server(Workspace.open(tmp_path))
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        port = httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        for path in ("/static/app.css", "/static/js/main.js", "/static/js/ui.js"):
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200 and resp.version == 11
            assert len(body) == int(resp.getheader("Content-Length")) > 16384
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
