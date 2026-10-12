"""Local viewer server (standard library only).

Security model, in order:

* binds to loopback by default; anything else is an explicit ``--host`` choice;
* every request must carry a ``Host`` header naming this server (DNS-rebinding guard);
* API requests must carry the per-process token (``X-Repoluma-Token``), which only the
  served page knows, and cross-origin ``Origin`` headers are rejected;
* source is read through :class:`repoluma.sources.Repository` (confined, excluded, read-only);
* responses are JSON or packaged static assets; the page escapes all map/source text.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .model import iter_refs
from .sources import Repository, SourceError
from .workspace import Workspace

STATIC_TYPES = {
    ".js": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}


def _web_file(name: str) -> bytes | None:
    try:
        node = resources.files("repoluma").joinpath("web")
        for part in name.strip("/").split("/"):
            if part in ("", ".", ".."):
                return None
            node = node.joinpath(part)
        return node.read_bytes() if node.is_file() else None
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None


class ViewerState:
    """Caches the last analysis; recomputed when the map or a referenced file changes."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.lock = threading.Lock()
        self._analysis: dict[str, Any] | None = None
        self._tokens: dict[str, str] = {}
        self._ref_paths: set[str] = set()
        self.repo = Repository(workspace.root)

    def _tokens_now(self) -> dict[str, str]:
        return self.ws.watch_tokens(sorted(self._ref_paths), self.repo)

    def analysis(self, force: bool = False) -> dict[str, Any]:
        with self.lock:
            if force or self._analysis is None or self._tokens_now() != self._tokens:
                self._analysis = self.ws.analyze()
                model = self._analysis.get("model")
                self.repo = Repository(self.ws.root, model["source"]["exclude"] if model else [])
                self._ref_paths = {r["path"] for _, _, r in iter_refs(model)} if model else set()
                self._tokens = self._tokens_now()
            return self._analysis

    def status(self) -> dict[str, Any]:
        """Cheap change detection for polling: a digest of map + referenced-file stats."""
        if self._analysis is None:
            self.analysis()
        with self.lock:
            tokens = self._tokens_now()
        digest = hashlib.sha1(json.dumps(tokens, sort_keys=True).encode()).hexdigest()[:16]
        return {"digest": digest, "tokens": tokens}

    def source(self, rel: str, start: int, end: int) -> dict[str, Any]:
        self.analysis()
        if rel not in self._ref_paths:
            raise SourceError(
                "not_referenced",
                f"{rel} is not referenced by the map; only referenced files are served",
            )
        return self.repo.excerpt(rel, start, end)


def make_handler(state: ViewerState, token: str, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"repoluma/{__version__}"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet by default
            if getattr(self.server, "verbose", False):
                super().log_message(fmt, *args)

        # ---------------------------------------------------------- plumbing
        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if ctype.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
                    " connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any):
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, code: str, message: str):
            self._json(status, {"error": code, "message": message})

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return host in allowed_hosts

        # ---------------------------------------------------------- routing
        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method", "read-only server")

        do_PUT = do_DELETE = do_PATCH = do_POST

        def do_GET(self):
            if not self._host_ok():
                self._error(HTTPStatus.FORBIDDEN, "host", "unexpected Host header")
                return
            url = urlsplit(self.path)
            path = url.path
            if path.startswith("/api/"):
                origin = self.headers.get("Origin")
                if origin is not None and urlsplit(origin).netloc.lower() not in allowed_hosts:
                    self._error(HTTPStatus.FORBIDDEN, "origin", "cross-origin request refused")
                    return
                supplied = self.headers.get("X-Repoluma-Token", "")
                if not secrets.compare_digest(supplied, token):
                    self._error(HTTPStatus.FORBIDDEN, "token", "missing or wrong token")
                    return
                try:
                    self._api(path, parse_qs(url.query))
                except (BrokenPipeError, ConnectionResetError):
                    return  # the page went away mid-response (navigation, closed tab)
                except Exception as exc:  # noqa: BLE001 - keep the viewer alive; report it
                    self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal", repr(exc))
                return
            if path in ("/", "/index.html"):
                page = _web_file("index.html")
                if page is None:
                    self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "assets", "viewer assets missing")
                    return
                page = page.replace(b"__REPOLUMA_TOKEN__", token.encode())
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
                return
            if path.startswith("/static/"):
                name = path[len("/static/") :]
                data = _web_file(name)
                if data is None:
                    self._error(HTTPStatus.NOT_FOUND, "not-found", "no such asset")
                    return
                ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
                ctype = STATIC_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "text/plain"
                if ctype.startswith("text/") or ctype.endswith("json"):
                    ctype += "; charset=utf-8"
                self._send(HTTPStatus.OK, data, ctype)
                return
            self._error(HTTPStatus.NOT_FOUND, "not-found", "no such page")

        def _api(self, path: str, query: dict[str, list[str]]):
            if path == "/api/bootstrap":
                force = query.get("force", ["0"])[0] == "1"
                self._json(HTTPStatus.OK, state.analysis(force=force))
            elif path == "/api/status":
                self._json(HTTPStatus.OK, state.status())
            elif path == "/api/source":
                rel = query.get("path", [""])[0]
                try:
                    start = int(query.get("start", ["1"])[0])
                    end = int(query.get("end", [str(start + 199)])[0])
                except ValueError:
                    self._error(HTTPStatus.BAD_REQUEST, "bad-range", "start/end must be integers")
                    return
                try:
                    self._json(HTTPStatus.OK, state.source(rel, start, end))
                except SourceError as exc:
                    self._json(
                        HTTPStatus.OK, {"path": rel, "error": exc.status, "message": exc.message}
                    )
            else:
                self._error(HTTPStatus.NOT_FOUND, "not-found", "no such endpoint")

    return Handler


def create_server(workspace: Workspace, host: str = "127.0.0.1", port: int = 0):
    """Bind the viewer server. Returns (httpd, url, token); call httpd.serve_forever()."""
    state = ViewerState(workspace)
    token = secrets.token_urlsafe(24)
    # Bind first so port 0 resolves, then compute the allowed Host values.
    httpd = ThreadingHTTPServer((host, port), make_handler(state, token, set()))
    httpd.daemon_threads = True
    httpd.verbose = False
    real_port = httpd.server_address[1]
    hosts = {f"127.0.0.1:{real_port}", f"localhost:{real_port}", f"[::1]:{real_port}"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        hosts.add(f"{host}:{real_port}")
    httpd.RequestHandlerClass = make_handler(state, token, hosts)
    httpd.state = state
    display_host = "127.0.0.1" if host in ("0.0.0.0", "127.0.0.1") else host
    return httpd, f"http://{display_host}:{real_port}/", token


def serve(
    workspace: Workspace,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    verbose: bool = False,
) -> None:
    import webbrowser

    httpd, url, _ = create_server(workspace, host, port)
    httpd.verbose = verbose
    print(f"repoluma {__version__} viewing {workspace.root}")
    print(
        f"  map:    {workspace.map_display}"
        + ("" if workspace.map_path.exists() else "  (not created yet: the viewer will guide you)")
    )
    print(f"  viewer: {url}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"  WARNING: listening on {host}; anyone who can reach this address can open the "
            "viewer and read the source files the map references."
        )
    print(
        "  Ctrl+C to stop. The page refreshes itself when the map or referenced files change;"
        " restart this command after upgrading or editing repoluma itself.",
        flush=True,
    )
    httpd.state.analysis()  # warm the cache so the first page load is quick
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
