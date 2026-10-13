# Developing repoluma (with repoluma)

## Setup

```sh
uv sync          # creates the dev environment with an editable install of this checkout
```

(The devcontainer runs `uv sync --locked` on creation.) There is no frontend build:
the viewer is plain ES modules and CSS in `src/repoluma/web/`, shipped as package data.

## Run the checkout on its own map

```sh
uv run repoluma validate     # schema + every source reference in .repoluma/map.yaml
uv run repoluma serve        # opens this checkout's viewer on this repository
```

`uv run` uses the editable install in the project environment, so this always runs
the code in your working tree, never a published release. (`just serve` and
`just check` are shortcuts, see the `justfile`.)

## What refreshes when

| you changed | what to do |
| --- | --- |
| `.repoluma/map.yaml` or any file it references | nothing: the open page reloads within ~2 s and keeps your place |
| viewer assets (`src/repoluma/web/**`) | reload the browser tab (assets are read from the source tree) |
| Python code (`src/repoluma/*.py`) | restart `uv run repoluma serve` |

The server never writes into the repository, so its own activity cannot trigger a
reload loop. Change detection compares `stat` tokens of the map and referenced files.

## The development loop

1. Use the map to look at the part you are changing (search with `/`, follow a flow,
   read the code in place).
2. Change the code. If you rename, move or change the meaning of something the map
   describes, update the map in the same change; keep ids stable. New code files need a
   place on the map (`owns`); `repoluma where <file>` shows where a file belongs.
3. `uv run repoluma validate` (CI-equivalent: `just check`). Moved symbols show up as
   `ref-missing-symbol` / `ref-moved`.
4. Follow the affected flow in the viewer from overview to code and check that the
   explanation is still true. Reference validity does not prove the prose is right.

The self-map is deliberately small: user-facing capabilities, their implementation
boundaries, contracts (map format, HTTP API, reference resolution), the security
rules, and three flows. It is maintained with the same guide (`repoluma guide`) that
external agents use and is checked by `tests/test_model.py` (no errors) and
`tests/test_selfmap.py` (strict: no warnings, every reference displayable).

## Tests

```sh
uv run pytest            # Python: model, sources, server, CLI, freshness, self-map, packaging
uv run ruff check .
```

End-to-end browser checks and measurements need a Chromium for the locked Playwright
version (a dev dependency). The dev container has Chromium's shared libraries and fonts in
the image (an apt list in `.devcontainer/Dockerfile`, independent of the playwright
version) and installs the browser on first start; elsewhere:

```sh
uv run playwright install --with-deps chromium   # --with-deps needs root (apt)
uv run python bench/run.py src/repoluma/examples/shop .
```

Without a browser, `tests/test_e2e_browser.py` is skipped, not failed. If a newer
Chromium needs another library, the skip message names it; add it to the apt list.

## Packaging check

`tests/test_packaging.py` builds the wheel and checks that the viewer assets, guide and
example are inside it. To try the real `uvx` path from a clean environment:

```sh
uv build
uvx --from dist/repoluma-*.whl repoluma example /tmp/rl-example
uvx --from dist/repoluma-*.whl repoluma serve --root /tmp/rl-example
```

## Fixtures

`tests/fixtures/provenance` is a tiny repository with a schema_version 3 map for the
cases the shop does not have: an object configured by setters, a setter with side
effects, and shared mutable state. The authoring guide's worked examples are copied from
it and from the shop; `tests/test_guide.py` fails if they drift apart.
