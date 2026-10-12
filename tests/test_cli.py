import json

from repoluma.cli import main

from .conftest import write


def test_init_creates_placeholder_and_never_overwrites(tmp_path, capsys):
    assert main(["init", "--root", str(tmp_path)]) == 0
    map_path = tmp_path / ".repoluma" / "map.yaml"
    assert map_path.exists() and (tmp_path / ".repoluma" / ".gitignore").exists()
    out = capsys.readouterr().out
    assert "repoluma guide" in out and "repoluma validate" in out
    map_path.write_text("schema_version: 1\n# mine\n")
    assert main(["init", "--root", str(tmp_path)]) == 0
    assert map_path.read_text() == "schema_version: 1\n# mine\n"
    assert "nothing was changed" in capsys.readouterr().out
    # the placeholder itself is a valid (empty) map
    map_path.unlink()
    main(["init", "--root", str(tmp_path)])
    assert main(["validate", "--root", str(tmp_path)]) == 0


def test_validate_reports_and_fails_on_errors(tmp_path, capsys):
    write(
        tmp_path,
        ".repoluma/map.yaml",
        """
        schema_version: 2
        scope: {summary: s}
        nodes:
          - {id: a, label: A, refs: [{path: missing.py, symbol: f}]}
        """,
    )
    assert main(["validate", "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "ref-missing-file" in out and "map.yaml:4" in out and "FAILED" in out
    assert main(["validate", "--root", str(tmp_path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["errors"] == 1
    assert data["diagnostics"][0]["record"] == "a"


def test_validate_example_ok(shop, capsys):
    assert main(["validate", "--root", str(shop)]) == 0
    assert "22/22 source refs resolve" in capsys.readouterr().out


def test_strict_turns_warnings_into_failure(tmp_path):
    write(
        tmp_path,
        ".repoluma/map.yaml",
        "schema_version: 2\nscope: {summary: s}\nnodes:\n  - {id: a, labl: x}\n",
    )
    assert main(["validate", "--root", str(tmp_path)]) == 0
    assert main(["validate", "--root", str(tmp_path), "--strict"]) == 1


def test_guide_and_skill_export(tmp_path, capsys):
    assert main(["guide"]) == 0
    assert "schema_version: 2" in capsys.readouterr().out
    assert main(["guide", "--format", "skill", "--write", str(tmp_path)]) == 0
    skill = (tmp_path / "SKILL.md").read_text()
    assert skill.startswith("---\nname: repoluma-map")
    assert main(["guide", "--format", "skill", "--write", str(tmp_path)]) == 1  # no clobber


def test_schema_is_json(capsys):
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "repoluma map"


def test_example_copies_a_valid_repository(tmp_path, capsys):
    dest = tmp_path / "ex"
    assert main(["example", str(dest)]) == 0
    assert main(["validate", "--root", str(dest)]) == 0
    assert main(["example", str(dest)]) == 1  # refuses non-empty destination
