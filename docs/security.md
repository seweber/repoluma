# Security model

repoluma displays untrusted content (repository source and agent-written map text) and
runs a local HTTP server. The rules:

**Viewing never executes the repository.** Python symbols are located with
`ast.parse` (no import). Other languages use regular expressions. Git is run with
`core.fsmonitor=false`, `core.hooksPath=/dev/null`, no external diff, no optional
locks, and no terminal prompts; a test checks that a hostile `core.fsmonitor` does not
run.

**File access is confined.** Paths must be relative without `..`; they are resolved
with symlinks and must remain inside the root. Default exclusions cover VCS
directories, dependency and cache directories, `.env*`, private keys and certificates,
credential files, and databases; maps can add `source.exclude` globs. Binary files
(NUL bytes) and files over 2 MB are refused. The server only serves files that the map
references or owns (`owns`), plus code files that still need an owner (so the viewer can
show them as "not yet mapped"), at most 2,000 lines per request. Excluded files are never
listed, owned or served.

**The server is local and guarded.** It binds `127.0.0.1` by default (`--host` is an
explicit opt-in with a warning). Every request must carry a `Host` header naming the
server (defeats DNS rebinding). API requests need a per-process random token that only
the served page carries; requests with a foreign `Origin` are refused; only `GET` and
`HEAD` are accepted. HTML responses carry a strict Content-Security-Policy
(`default-src 'self'`, no inline script, no eval, no framing).

**Display is escaped.** The viewer builds all DOM from text nodes (`textContent`);
labels, summaries and source never pass through `innerHTML`. Canvas text is inert.
YAML is parsed with a safe loader that cannot construct Python objects.

Tests: `tests/test_server.py` (host, token, origin, method, traversal, only referenced
or owned source), `tests/test_sources.py` (confinement, symlinks, exclusions, binary, size),
`tests/test_freshness.py` (git configuration cannot execute), `tests/test_model.py`
(YAML object construction refused).
