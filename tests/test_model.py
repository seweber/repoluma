import jsonschema
import pytest
import yaml

from repoluma.model import json_schema, load_map, parse_lines, parse_yaml, validate_document

from .conftest import EXAMPLE, ROOT


def check(text):
    doc, err = parse_yaml(text)
    assert err is None, err
    return validate_document(doc)


def codes(result, severity=None):
    return {d.code for d in result.diagnostics if severity in (None, d.severity)}


MINIMAL = """
schema_version: 2
scope: {summary: test}
nodes:
  - {id: a, label: A, kind: capability}
  - id: b
    label: B
    kind: responsibility
    parent: a
"""


def test_minimal_map_is_clean():
    r = check(MINIMAL)
    assert not r.errors and not r.warnings
    nodes = {n["id"]: n for n in r.model["nodes"]}
    assert nodes["a"]["children"] == ["b"] and nodes["b"]["depth"] == 1


def test_example_and_self_map_validate_without_errors():
    for path in (EXAMPLE / ".repoluma/map.yaml", ROOT / ".repoluma/map.yaml"):
        r = load_map(path)
        assert r.model is not None
        assert not r.errors, [d.format() for d in r.errors]


def test_unknown_field_gets_did_you_mean_and_line():
    r = check(MINIMAL + "    sumary: typo\n")
    d = next(d for d in r.diagnostics if d.code == "unknown-field")
    assert d.severity == "warning" and "summary" in d.hint and d.line == 6 and d.record == "b"


def test_extension_fields_are_allowed():
    r = check(MINIMAL + "    x-owner: team-a\n")
    assert "unknown-field" not in codes(r)


def test_duplicate_ids_across_record_types():
    r = check(MINIMAL + "relationships:\n  - {id: a, from: a, to: b, kind: calls}\n")
    assert "duplicate-id" in codes(r, "error")


def test_unknown_parent_is_error_but_node_kept_at_top_level():
    r = check(MINIMAL.replace("parent: a\n", "parent: aa\n"))
    assert "unknown-parent" in codes(r, "error")
    assert {n["id"]: n["parent"] for n in r.model["nodes"]}["b"] is None


def test_containment_cycle_is_broken_and_reported():
    r = check("""
schema_version: 2
nodes:
  - {id: a, parent: c}
  - {id: b, parent: a}
  - {id: c, parent: b}
""")
    assert "containment-cycle" in codes(r, "error")
    # the result is a forest again: some node became a root
    assert any(n["parent"] is None for n in r.model["nodes"])


def test_relationship_with_unknown_endpoint_is_dropped_with_suggestion():
    r = check(MINIMAL + "relationships:\n  - {from: a, to: bb, kind: calls}\n")
    d = next(d for d in r.diagnostics if d.code == "unknown-endpoint")
    assert "'b'" in d.hint
    assert r.model["relationships"] == []


def test_relationship_default_id_and_type():
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, kind: relies_on}\n")
    rel = r.model["relationships"][0]
    assert rel["id"] == "b--relies_on--a" and rel["type"] == "dependency"
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, type: data, label: totals}\n")
    rel = r.model["relationships"][0]
    assert rel["id"] == "b--data--a" and (rel["from"], rel["to"]) == ("b", "a")
    assert not r.warnings


def test_unknown_kind_warns():
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, type: control, kind: cals}\n")
    d = next(d for d in r.diagnostics if d.code == "unknown-kind")
    assert "calls" in d.hint
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, kind: cals}\n")
    d = next(d for d in r.diagnostics if d.code == "missing-field")
    assert "'calls'" in d.hint


def test_relationship_types_are_checked():
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, type: dta}\n")
    assert "unknown-type" in codes(r, "error") and not r.model["relationships"]
    r = check(MINIMAL + "relationships:\n  - {from: b, to: a, type: data}\n")
    assert "data-without-label" in codes(r, "warning")
    # a version 1 verb in a version 2 map is read as before, with a warning
    r = check(MINIMAL + "relationships:\n  - {from: a, to: b, kind: reads}\n")
    rel = r.model["relationships"][0]
    assert "verb-kind" in codes(r, "warning")
    assert (rel["type"], rel["from"], rel["to"]) == ("data", "b", "a")


def test_schema_version_1_relationships_are_converted():
    r = check(
        MINIMAL.replace("schema_version: 2", "schema_version: 1")
        + """relationships:
  - {from: a, to: b, kind: reads}
  - {from: a, to: b, kind: subscribes}
  - {from: a, to: b, kind: writes}
  - {from: a, to: b, kind: calls}
  - {from: a, to: b, kind: tests}
"""
    )
    got = [(x["id"], x["type"], x["kind"], x["from"], x["to"]) for x in r.model["relationships"]]
    assert got == [
        ("a--reads--b", "data", None, "b", "a"),  # ids stay stable; data goes from b to a
        ("a--subscribes--b", "data", "event", "b", "a"),
        ("a--writes--b", "data", None, "a", "b"),
        ("a--calls--b", "control", "calls", "a", "b"),
        ("a--tests--b", "dependency", "tests", "a", "b"),
    ]
    assert "old-schema-version" in codes(r, "warning") and not r.errors


def test_bad_ids_and_schema_version():
    r = check("schema_version: 99\nnodes:\n  - {id: Not Valid}\n")
    assert {"unsupported-schema-version", "bad-id"} <= codes(r, "error")
    assert (
        "upgrade" in next(d for d in r.diagnostics if d.code == "unsupported-schema-version").hint
    )


def test_yaml_syntax_error_has_line():
    _, err = parse_yaml("schema_version: 2\nnodes:\n  - id: a\n    label: [unclosed\n")
    assert err.code == "yaml-syntax" and err.line is not None


def test_yaml_does_not_construct_python_objects():
    _, err = parse_yaml("a: !!python/object/apply:os.system ['true']\n")
    assert err is not None


def test_flow_steps_numbering_branching_and_errors():
    r = check(
        MINIMAL
        + """
flows:
  - id: f
    steps:
      - {node: a, label: one}
      - {id: two, node: b, label: two}
      - {node: a, label: fail, kind: failure, after: two}
      - {node: a, label: three}
      - {node: zz, label: bad, after: nope}
"""
    )
    steps = r.model["flows"][0]["steps"]
    assert [s["id"] for s in steps] == ["s1", "two", "s3", "s4", "s5"]
    assert steps[1]["after"] == "s1"
    assert steps[3]["after"] == "two"  # failure branch does not advance the main path
    assert {"unknown-step-node", "unknown-step-after"} <= codes(r, "error")


def test_flow_steps_are_linked_to_relationships():
    r = check(
        """
schema_version: 2
scope: {summary: test}
nodes:
  - {id: x, label: X, kind: capability}
  - {id: p, label: P, kind: responsibility, parent: x}
  - {id: q, label: Q, kind: responsibility, parent: x}
  - {id: y, label: Y, kind: capability}
  - {id: r, label: R, kind: responsibility, parent: y}
  - {id: lone, label: Lone, kind: capability}
relationships:
  - {id: xy, from: x, to: y, kind: calls}
  - {id: pq, from: p, to: q, kind: calls}
flows:
  - id: f
    steps:
      - {id: start, node: p, label: start}
      - {id: exact, node: q, label: exact relationship}
      - {id: outer, node: r, label: via the containing nodes}
      - {id: none, node: lone, label: no relationship}
      - {id: wrong, node: r, label: via elsewhere, via: pq}
      - {id: named, node: p, label: named, via: xy}
"""
    )
    links = {s["id"]: s["link"] for s in r.model["flows"][0]["steps"]}
    assert links == {
        "start": None,
        "exact": "pq",
        "outer": "xy",
        "none": None,
        "wrong": None,
        "named": "xy",
    }
    assert {"step-without-relationship", "step-via-mismatch"} <= codes(r, "warning")


def test_flow_steps_continue_from_where_the_flow_is():
    """A step that jumps over a node the work passes through is reported (the viewer
    would dim that node); a coordinator's calls and an actor starting anew are no jumps."""
    r = check(
        """
schema_version: 2
scope: {summary: test}
nodes:
  - {id: file, label: File, kind: resource}
  - {id: parse, label: Parse, kind: component}
  - {id: check, label: Check, kind: component}
  - {id: hub, label: Hub, kind: component}
  - {id: user, label: User, kind: actor}
relationships:
  - {id: read, from: file, to: parse, type: data}
  - {id: parsed, from: parse, to: check, type: data}
  - {id: hub-file, from: hub, to: file, type: data}
  - {id: hub-check, from: hub, to: check, type: data}
  - {id: user-hub, from: user, to: hub, type: control, kind: triggers}
flows:
  - id: skips
    steps:
      - {id: a, node: file, label: a}
      - {id: b, node: check, label: b, via: parsed}
  - id: through
    steps:
      - {id: a, node: file, label: a}
      - {id: b, node: parse, label: b, via: read}
      - {id: c, node: check, label: c, via: parsed}
  - id: coordinated
    steps:
      - {id: a, node: file, label: a, via: hub-file}
      - {id: b, node: check, label: b, via: hub-check}
      - {id: c, node: hub, label: back at the coordinator}
  - id: restarted
    steps:
      - {id: a, node: check, label: a}
      - {id: b, node: hub, label: b, via: user-hub}
"""
    )
    gaps = [d for d in r.diagnostics if d.code == "step-via-gap"]
    assert [(d.record, d.location) for d in gaps] == [("skips", "flows[0].steps[1].via")]
    assert gaps[0].severity == "warning"
    links = {f["id"]: [s["link"] for s in f["steps"]] for f in r.model["flows"]}
    # the jumping step is still drawn on its relationship; only the gap is reported
    assert links["skips"] == [None, "parsed"]
    # without via, the coordinator's end of the previous step's call is where the flow is
    assert links["coordinated"] == ["hub-file", "hub-check", "hub-check"]
    assert links["restarted"] == [None, "user-hub"]


def test_ref_forms():
    r = check(MINIMAL + "    refs: ['x.py#f', 'y.py:3-9', 'z.md']\n")
    refs = r.model["nodes"][1]["refs"]
    assert [(x["path"], x["symbol"], x["lines"]) for x in refs] == [
        ("x.py", "f", None),
        ("y.py", None, [3, 9]),
        ("z.md", None, None),
    ]


@pytest.mark.parametrize("path", ["/etc/passwd", "../x.py", "a/../../b", "a\\b.py", "dir/", "C:/x"])
def test_bad_ref_paths_rejected(path):
    r = check(MINIMAL + f"    refs: [{{path: '{path}'}}]\n")
    assert "bad-ref-path" in codes(r, "error")


@pytest.mark.parametrize(
    "value,expected",
    [
        (12, (12, 12)),
        ("3-9", (3, 9)),
        ([4, 5], (4, 5)),
        ("9-3", None),
        (0, None),
        ("x", None),
    ],
)
def test_parse_lines(value, expected):
    assert parse_lines(value)[0] == expected


def test_json_schema_agrees_with_examples():
    schema = json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    for path in (EXAMPLE / ".repoluma/map.yaml", ROOT / ".repoluma/map.yaml"):
        jsonschema.validate(yaml.safe_load(path.read_text()), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"schema_version": 1, "nodes": [{"id": "a", "lable": "x"}]}, schema)
