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


def test_source_endpoint_serves_only_referenced_files(server):
    port, token, _ = server
    status, data = api(port, token, "/api/source?path=shop/orders.py&start=1&end=5")
    assert status == 200 and data["lines"][0].startswith('"""')
    for bad in ("shop/__init__.py", "../etc/passwd", ".repoluma/map.yaml", "/etc/passwd"):
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


def test_bootstrap_without_map(tmp_path):
    ws = Workspace.open(tmp_path)
    data = ws.analyze()
    assert data["model"] is None and data["map_exists"] is False
    assert data["diagnostics"][0]["code"] == "missing-map"
