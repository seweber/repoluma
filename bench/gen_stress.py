"""Generate synthetic repositories + maps for performance measurement.

    uv run python bench/gen_stress.py OUTDIR

Shapes (engineering limits only, not semantic usefulness):
  broad   - 40 top-level areas x 50 children (2,040 nodes), 2,000 relationships
  deep    - branching 3, depth 7 (3,279 nodes), 3,000 relationships
  dense   - 300 nodes, 6,000 relationships
  longsrc - 200 nodes, each referencing a 400-line function in 5,000-line files
  huge    - 10,000 nodes in 4 levels, 10,000 relationships
"""

import random
import sys
from pathlib import Path

import yaml

KINDS = ["calls", "reads", "writes", "publishes", "relies_on", "depends_on"]


def write_sources(root: Path, files: int, funcs_per_file: int, body_lines: int) -> list[tuple]:
    symbols = []
    for f in range(files):
        lines = [f'"""synthetic module {f}"""', ""]
        for g in range(funcs_per_file):
            lines.append(f"def func_{g}(x):")
            lines.append(f'    """function {g} of module {f}"""')
            for i in range(body_lines):
                lines.append(f"    x = x + {i}  # step {i}")
            lines.append("    return x")
            lines.append("")
            symbols.append((f"src/mod_{f}.py", f"func_{g}"))
        p = root / "src" / f"mod_{f}.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n")
    return symbols


def build(name: str, root: Path, nodes: list[dict], rels: int, rng, symbols, flows=1):
    leaf_ids = [n["id"] for n in nodes if not any(m.get("parent") == n["id"] for m in nodes)]
    for i, n in enumerate(nodes):
        if n["id"] in leaf_ids:
            path, sym = symbols[i % len(symbols)]
            n["refs"] = [{"path": path, "symbol": sym}]
            n["evidence"] = rng.choice(["observed", "inferred", "inferred", "needs_review"])
    ids = [n["id"] for n in nodes]
    relationships = []
    for i in range(rels):
        a, b = rng.sample(ids, 2)
        relationships.append({"id": f"r{i}", "from": a, "to": b, "kind": rng.choice(KINDS)})
    flow_list = []
    for f in range(flows):
        steps = [
            {"node": nid, "label": f"step {k}"} for k, nid in enumerate(rng.sample(leaf_ids, 8))
        ]
        steps.append({"node": steps[3]["node"], "kind": "failure", "after": "s3", "label": "fails"})
        flow_list.append({"id": f"flow-{f}", "label": f"Synthetic flow {f}", "steps": steps})
    doc = {
        "schema_version": 1,
        "repository": {"name": f"stress-{name}"},
        "scope": {"summary": f"Synthetic {name} map for performance measurement"},
        "nodes": nodes,
        "relationships": relationships,
        "flows": flow_list,
    }
    (root / ".repoluma").mkdir(parents=True, exist_ok=True)
    (root / ".repoluma" / "map.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, width=200))
    return len(nodes), len(relationships)


def tree(prefix, depth, branching, parent=None, out=None, level=0):
    out = [] if out is None else out
    for i in range(branching[level] if isinstance(branching, list) else branching):
        nid = f"{prefix}{i}" if not parent else f"{parent}.{i}"
        out.append(
            {
                "id": nid,
                "label": f"Area {nid} with a moderately long label",
                "kind": "capability" if level == 0 else "responsibility",
                "summary": f"Synthetic node {nid}: does a thing that matters for the stress test.",
                **({"parent": parent} if parent else {}),
            }
        )
        if level + 1 < depth:
            tree(prefix, depth, branching, nid, out, level + 1)
    return out


def main(outdir: str) -> None:
    out = Path(outdir)
    rng = random.Random(42)
    shapes = {
        "broad": lambda r: (tree("a", 2, [40, 50]), 2000, write_sources(r, 20, 50, 8)),
        "deep": lambda r: (tree("d", 7, 3), 3000, write_sources(r, 20, 50, 8)),
        "dense": lambda r: (tree("x", 2, [15, 19]), 6000, write_sources(r, 10, 40, 8)),
        "longsrc": lambda r: (tree("l", 2, [10, 19]), 200, write_sources(r, 10, 12, 400)),
        "huge": lambda r: (tree("h", 4, [10, 10, 10, 9]), 10000, write_sources(r, 50, 100, 6)),
    }
    for name, make in shapes.items():
        root = out / name
        root.mkdir(parents=True, exist_ok=True)
        nodes, rels, symbols = make(root)
        n, r = build(name, root, nodes, rels, rng, symbols)
        print(f"{name}: {n} nodes, {r} relationships -> {root}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bench/out")
