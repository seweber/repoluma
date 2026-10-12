"""The maintained self-map must stay valid and fully resolvable."""

from repoluma.sources import DISPLAYABLE
from repoluma.workspace import Workspace

from .conftest import ROOT


def test_self_map_is_strictly_clean_and_every_reference_displays():
    analysis = Workspace.open(ROOT).analyze(with_coverage=False)
    problems = [d for d in analysis["diagnostics"] if d["severity"] in ("error", "warning")]
    assert not problems, problems
    bad = {k: r["status"] for k, r in analysis["refs"].items() if r["status"] not in DISPLAYABLE}
    assert not bad, bad


def test_self_map_covers_every_python_module():
    analysis = Workspace.open(ROOT).analyze(with_coverage=False)
    referenced = {k.split("#")[0].split(":")[0] for k in analysis["refs"]}
    modules = {p.relative_to(ROOT).as_posix() for p in (ROOT / "src" / "repoluma").glob("*.py")}
    missing = modules - referenced - {"src/repoluma/__main__.py"}
    assert not missing, f"modules not described by the self-map: {sorted(missing)}"
