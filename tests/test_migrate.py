"""`repoluma migrate`: the mechanical part of moving a map to schema_version 3."""

from repoluma.cli import main
from repoluma.migrate import migrate_text
from repoluma.model import parse_yaml, validate_document

from .conftest import write

V2 = """\
# a comment that must survive
schema_version: 2
scope: {summary: "evidence: kept in prose"}
nodes:
  - id: area
    kind: capability
  - {id: a, parent: area, evidence: observed}
  - id: b
    parent: area
    evidence: inferred   # trailing comment
  - {id: other, kind: capability}
  - {id: c, parent: other}
relationships:
  - {id: ask, from: a, to: b, type: data, label: q, evidence: observed}
  - {id: answer, from: b, to: a, type: data, label: r, evidence: observed}
  - {id: wide, from: a, to: other, type: data, label: w, evidence: inferred}
"""


def test_migrate_renames_and_keeps_everything_else():
    new, notes, decisions = migrate_text(V2)
    assert new.startswith("# a comment that must survive\nschema_version: 3\n")
    assert "evidence: inferred   # trailing comment" not in new
    assert "certainty: inferred   # trailing comment" in new
    assert '"evidence: kept in prose"' in new  # values are not touched
    assert "evidence -> certainty (5 fields)" in notes
    model = validate_document(parse_yaml(new)[0]).model
    assert model["schema_version"] == 3
    assert [r["certainty"] for r in model["relationships"]] == ["observed", "observed", "inferred"]
    # nothing is paired or retargeted; the decisions are listed instead
    assert model["exchanges"] == []
    assert any(d.startswith("wide") and "container other" in d for d in decisions)
    assert any(d.startswith("ask / answer") and "exchange" in d for d in decisions)
    assert any(d.startswith("ask (") and "without a cited site" in d for d in decisions)


def test_migrate_refuses_other_versions():
    new, _, decisions = migrate_text(V2.replace("schema_version: 2", "schema_version: 3"))
    assert new is None and "only schema_version 2" in decisions[0]


def test_migrate_command_writes_a_new_file_unless_in_place(tmp_path, capsys):
    path = write(tmp_path, ".repoluma/map.yaml", V2)
    assert main(["migrate", "--root", str(tmp_path)]) == 0
    assert path.read_text() == V2
    out = tmp_path / ".repoluma/map.v3.yaml"
    assert "schema_version: 3" in out.read_text()
    assert "decisions need you" in capsys.readouterr().out
    assert main(["migrate", "--root", str(tmp_path)]) == 1  # will not overwrite the output
    assert main(["migrate", "--root", str(tmp_path), "--in-place"]) == 0
    assert "schema_version: 3" in path.read_text()
