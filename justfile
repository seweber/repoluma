# Common development commands (https://just.systems). All run the local checkout.

# validate the self-map, lint, and run the tests
check:
    uv run repoluma validate --strict
    uv run ruff check .
    uv run pytest -q

# view this repository with the current checkout
serve *ARGS:
    uv run repoluma serve {{ARGS}}

# view the bundled example
example:
    uv run repoluma serve --root src/repoluma/examples/shop

# generate stress maps and measure (needs a Playwright Chromium)
bench:
    uv run python bench/gen_stress.py bench/out
    uv run python bench/run.py src/repoluma/examples/shop . bench/out/broad bench/out/deep bench/out/dense bench/out/longsrc bench/out/huge --json bench/results.local.json
