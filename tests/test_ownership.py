"""File ownership: every code file has exactly one owning node (see repoluma.ownership)."""

import json

import pytest

from repoluma.cli import main
from repoluma.ownership import glob_regex, is_required, specificity
from repoluma.workspace import Workspace

from .conftest import write


def analyze(root, nodes, source=""):
    write(root, ".repoluma/map.yaml", f"""
        schema_version: 2
        scope: {{summary: test}}
        {source}
        nodes:
        {nodes}
    """)
    a = Workspace.open(root).analyze(with_git=False)
    return a, {d["code"]: d for d in a["diagnostics"]}


@pytest.fixture
def repo(tmp_path):
    write(tmp_path, "app/core/engine.py", "def run():\n    return 1\n")
    write(tmp_path, "app/core/util.py", """
        def slug(text):
            return text.lower()


        class Money:
            def add(self, other):
                return self

            def times(self, n):
                return self
    """)
    write(tmp_path, "app/web/page.ts", "export function render() {\n  return 1;\n}\n")
    write(tmp_path, "app/web/style.css", "body { margin: 0; }\n")
    write(tmp_path, "tests/test_engine.py", "def test_run():\n    pass\n")
    write(tmp_path, "docs/notes.md", "# Notes\n")
    write(tmp_path, ".github/ci.sh", "echo hi\n")
    write(tmp_path, "README.md", "# App\n")
    return tmp_path


def owner(a, path):
    return a["ownership"]["files"][path]["owner"]


def test_globs_match_files_and_directories():
    assert glob_regex("src/").match("src/a/b.py") and not glob_regex("src/").match("srcx/a.py")
    assert glob_regex("src/**").match("src/a.py") and glob_regex("src/**").match("src/a/b/c.py")
    assert glob_regex("src/*.py").match("src/a.py") and not glob_regex("src/*.py").match("src/a/b.py")
    assert glob_regex("**/test_*.py").match("test_a.py") and glob_regex("**/test_*.py").match("x/y/test_a.py")
    assert glob_regex("src/[ab].py").match("src/a.py") and not glob_regex("src/[ab].py").match("src/c.py")


def test_the_most_specific_pattern_wins(repo):
    # exact path > longer fixed prefix > shorter; a region's broad pattern takes the rest
    assert specificity("app/core/engine.py") > specificity("app/core/*.py") > specificity("app/**")
    assert specificity("app/core/*.py") > specificity("app/core/")
    a, diags = analyze(repo, """
          - {id: app, kind: area, owns: ["app/"]}
          - {id: core, kind: area, owns: ["app/core/*.py"]}
          - {id: engine, kind: component, owns: [app/core/engine.py]}
    """)
    assert owner(a, "app/core/engine.py") == "engine"
    assert owner(a, "app/core/util.py") == "core"
    assert owner(a, "app/web/page.ts") == "app" and owner(a, "app/web/style.css") == "app"
    assert not a["ownership"]["unowned"] and "unowned-file" not in diags
    assert a["ownership"]["counts"] == {"files": 9, "required": 4, "owned_required": 4, "owned": 4}


def test_owns_belongs_on_top_level_nodes(repo):
    a, diags = analyze(repo, """
          - {id: app, kind: area}
          - {id: core, parent: app, kind: area}
          - {id: engine, parent: core, kind: component, owns: [app/core/engine.py]}
    """)
    d = diags["owns-not-top-level"]
    assert d["severity"] == "error" and d["record"] == "engine" and "'app'" in d["hint"]
    assert owner(a, "app/core/engine.py") == "app"  # counted for its area meanwhile


def test_equally_specific_claims_are_a_conflict(repo):
    a, diags = analyze(repo, """
          - {id: one, kind: area, owns: ["app/"]}
          - {id: two, kind: area, owns: ["app/**"]}
    """)
    d = diags["ownership-conflict"]
    assert d["severity"] == "error" and "'one'" in d["message"] and "'two'" in d["message"]
    assert owner(a, "app/core/util.py") == "one"  # still shown somewhere, deterministically


def test_parts_of_a_file_belong_to_other_nodes(repo):
    a, _ = analyze(repo, """
          - {id: core, kind: area, owns: ["app/"]}
          - {id: text, kind: component, owns: ["app/core/util.py#slug"]}
          - {id: money, kind: data, owns: ["app/core/util.py#Money"]}
          - {id: scaling, kind: rule, owns: ["app/core/util.py#Money.times"]}
    """)
    assert not [d for d in a["diagnostics"] if d["severity"] == "error" and d["code"] != "unexplained-code"]
    rec = a["ownership"]["files"]["app/core/util.py"]
    assert rec["owner"] == "core"
    assert [(p["node"], p["start"], p["end"]) for p in rec["parts"]] == [
        ("text", 1, 2), ("money", 5, 10), ("scaling", 9, 10)]


def test_wrong_or_duplicate_parts_are_errors(repo):
    _, diags = analyze(repo, """
          - {id: core, kind: area, owns: ["app/", "app/core/util.py#nope"]}
          - {id: x, kind: area, owns: ["app/core/util.py#slug"]}
          - {id: y, kind: area, owns: ["app/core/util.py#slug"]}
    """)
    assert diags["owns-missing-symbol"]["severity"] == "error"
    assert diags["ownership-conflict"]["record"] == "y"


def test_code_files_without_owner_are_reported_with_a_suggestion(repo):
    a, diags = analyze(repo, """
          - {id: web, kind: area, owns: ["app/web/"]}
          - id: core
            kind: area
          - id: engine
            parent: core
            kind: component
            refs: [{path: app/core/engine.py, symbol: run}]
          - id: tools
            parent: core
            kind: component
            refs: [{path: app/core/util.py, symbol: slug}]
    """)
    assert a["ownership"]["unowned"] == ["app/core/engine.py", "app/core/util.py"]
    d = diags["unowned-file"]
    assert d["severity"] == "warning" and "2 code files in app/core/" in d["message"]
    assert "consider owns on 'core'" in d["hint"]  # the area the citing nodes are in


def test_tests_docs_and_tooling_need_no_owner(repo):
    assert not is_required("tests/test_engine.py") and not is_required("docs/notes.md")
    assert not is_required(".github/ci.sh") and not is_required("README.md")
    assert is_required("app/core/util.py") and is_required("app/web/style.css")
    assert not is_required("app/core/util.py", ignore=["app/core/"])
    assert is_required("docs/notes.md", require=["docs/"])
    a, _ = analyze(repo, "  - {id: all, kind: area, owns: ['app/']}",
                       source="source: {ownership: {require: [README.md]}}")
    assert a["ownership"]["unowned"] == ["README.md"]


def test_patterns_that_match_nothing_and_bad_patterns(repo):
    _, diags = analyze(repo, """
          - {id: a, kind: area, owns: ["app/", "app/gone.py"]}
    """)
    assert diags["owns-matches-nothing"]["severity"] == "warning"
    for bad in ("/abs.py", "../up.py", "**", "app/*.py#Symbol", "app\\\\x.py"):
        _, diags = analyze(repo, f"  - {{id: a, kind: area, owns: ['{bad}']}}")
        assert diags["bad-owns"]["severity"] == "error", bad


def test_a_directory_written_without_slash_owns_its_files(repo):
    a, _ = analyze(repo, "  - {id: a, kind: area, owns: [app/web]}")
    assert owner(a, "app/web/page.ts") == "a"


def test_where_names_the_owner_of_a_file_or_line(repo, capsys):
    analyze(repo, """
          - {id: core, kind: area, label: Core, owns: ["app/"]}
          - {id: money, kind: data, label: Money, owns: ["app/core/util.py#Money"]}
          - {id: adding, parent: money, kind: rule, label: Adding, refs: ["app/core/util.py#Money.add"]}
    """)
    assert main(["where", "--root", str(repo), str(repo / "app/core/util.py") + ":7"]) == 0
    out = capsys.readouterr().out
    assert "owner    Money  (money, data)" in out and "shown in Adding" in out
    assert main(["where", "--root", str(repo), "--json", str(repo / "app/core/util.py")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["owner"] == "core" and data["parts"][0]["node"] == "money"
    assert main(["where", "--root", str(repo), str(repo / "tests/test_engine.py")]) == 1
    assert "needs no owner" in capsys.readouterr().out


def test_every_line_of_an_owned_file_is_shown_in_its_area(repo):
    a, _ = analyze(repo, """
          - {id: core, kind: area, owns: ["app/core/", "app/web/page.ts"]}
          - id: money
            parent: core
            kind: data
            refs: ["app/core/util.py#Money"]
          - {id: web, kind: area, owns: ["app/web/style.css"], refs: ["app/core/util.py#slug"]}
    """)
    rest = {(r["area"], r["path"]): r["segments"] for r in a["ownership"]["rest"]}
    # util.py: slug is cited from another area, so it stays here; Money is folded into
    # its pane; the blank lines between them join the fold
    segs = [(g["kind"], g["start"], g["end"], g.get("node")) for g in rest[("core", "app/core/util.py")]]
    assert segs == [("code", 1, 4, None), ("shown", 5, 10, "money")]
    # files nobody cites are shown whole; every owned file appears
    assert rest[("core", "app/core/engine.py")] == [{"kind": "code", "start": 1, "end": 2}]
    assert ("web", "app/web/style.css") in rest and ("core", "app/web/page.ts") in rest
    assert a["ownership"]["files"]["app/core/util.py"]["lines"] == 10


def test_code_without_owner_is_shown_in_the_unmapped_region(repo):
    a, _ = analyze(repo, "  - {id: web, kind: area, owns: ['app/web/']}")
    areas = {(r["area"], r["path"]) for r in a["ownership"]["rest"]}
    assert ("~unmapped", "app/core/util.py") in areas and ("~unmapped", "app/core/engine.py") in areas
    assert not any(p.startswith("tests/") for _, p in areas)  # needs no owner: not shown


def test_code_no_node_shows_is_an_error_but_glue_needs_none(tmp_path):
    write(tmp_path, "app/orders.py", """
        \"\"\"Orders.\"\"\"

        import uuid
        from dataclasses import dataclass

        __all__ = ["Order", "place"]


        @dataclass
        class Order:
            \"\"\"An order.\"\"\"

            def total(self):
                return 1

            def cancel(self):
                return 2


        def place():
            # a comment
            return Order()


        if __name__ == "__main__":
            place()
    """)
    write(tmp_path, "web/app.js", """
        import {
          a,
          b,
        } from "./x.js";

        export class Cart {
          add(item) {
            return item;
          }
        }

        function helper() {
          return 1;
        }
    """)
    a, diags = analyze(tmp_path, """
          - {id: shop, kind: area, owns: [app/, web/]}
          - {id: totals, parent: shop, kind: rule, refs: ["app/orders.py#Order.total", "web/app.js#Cart.add"]}
    """)
    msgs = sorted(d["message"] for d in a["diagnostics"] if d["code"] == "unexplained-code")
    # imports, docstrings, __all__, the __main__ block and comments need no node; the class
    # header is context for its cited method; what is left is real code and must be shown
    assert len(msgs) == 2
    assert "Order.cancel, place" in msgs[0] and "app/orders.py" in msgs[0]
    assert "web/app.js" in msgs[1] and "helper" in msgs[1] and "Cart" not in msgs[1]
    assert diags["unexplained-code"]["severity"] == "error" and diags["unexplained-code"]["record"] == "shop"
    a, diags = analyze(tmp_path, """
          - {id: shop, kind: area, owns: [app/, web/]}
          - {id: orders, parent: shop, kind: component, refs: [app/orders.py, web/app.js]}
    """)
    assert "unexplained-code" not in diags and not a["ownership"]["rest"]  # whole files count
