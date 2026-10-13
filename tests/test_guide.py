"""The authoring guide's examples parse and agree with the maps they are taken from."""

import re

from repoluma.model import load_map, parse_yaml, validate_document

from .conftest import ROOT

GUIDE = ROOT / "src" / "repoluma" / "guide" / "AUTHORING.md"


def blocks():
    return re.findall(r"```yaml\n(.*?)```", GUIDE.read_text(encoding="utf-8"), re.DOTALL)


def test_every_yaml_block_parses():
    assert len(blocks()) >= 8
    for text in blocks():
        _, err = parse_yaml(text)
        assert err is None, (err.message, text[:200])


def test_the_format_example_is_a_valid_v3_map():
    full = [b for b in blocks() if b.startswith("schema_version:")]
    assert full
    for text in full:
        r = validate_document(parse_yaml(text)[0])
        assert not r.errors and not r.warnings, [d.format() for d in r.diagnostics if d.severity != "info"]
        assert r.model["schema_version"] == 3
        assert r.model["exchanges"][0]["legs"] == ["order-authorizes", "authorization-returned"]


def test_worked_examples_are_copied_from_real_maps():
    seen = 0
    for text in blocks():
        m = re.match(r"# from: (\S+)\n", text)
        if not m:
            continue
        model = load_map(ROOT / m.group(1) / ".repoluma/map.yaml").model
        real = {r["id"]: r for r in model["relationships"]}
        doc = parse_yaml(text)[0]
        wrapper = {"schema_version": 3, "nodes": model["nodes"], **doc}
        for r in validate_document(_plain(wrapper)).model["relationships"]:
            assert r["id"] in real, r["id"]
            mine, theirs = _comparable(r), _comparable(real[r["id"]])
            assert mine == theirs, r["id"]
            seen += 1
    assert seen >= 9


def _plain(doc):
    # nodes from a normalized model back to authored form (enough for endpoint checks)
    doc = dict(doc)
    doc["nodes"] = [{"id": n["id"], "parent": n["parent"], "kind": n["kind"]} for n in doc["nodes"]]
    return doc


def _comparable(r):
    keep = ("from", "to", "type", "kind", "label", "summary", "exchange", "leg", "certainty", "notes")
    refs = [{k: x[k] for k in ("path", "symbol", "role", "focus")} for x in r["refs"]]
    return {k: r[k] for k in keep}, refs
