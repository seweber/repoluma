"""Stale-reference behaviour in an isolated git checkout."""

from repoluma.workspace import Workspace

from .conftest import git, write

MAP = """
schema_version: 2
scope:
  summary: s
  reviewed_revision: {rev}
nodes:
  - id: calc
    label: Calculate
    refs: [{{path: app/calc.py, symbol: total}}]
  - id: fmt
    label: Format
    refs: [{{path: app/fmt.py, symbol: render}}]
"""


def setup_repo(tmp_path):
    write(tmp_path, "app/calc.py", "def total(xs):\n    return sum(xs)\n")
    write(tmp_path, "app/fmt.py", "def render(x):\n    return str(x)\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    rev = git(tmp_path, "rev-parse", "HEAD").strip()
    write(tmp_path, ".repoluma/map.yaml", MAP.format(rev=rev))
    return rev


def test_changed_since_review_and_renamed_symbol(tmp_path):
    rev = setup_repo(tmp_path)
    ws = Workspace.open(tmp_path)
    a = ws.analyze()
    assert a["git"]["head"] == rev
    assert a["refs"]["app/calc.py#total"]["changed_since_review"] is False

    # edit one file: it is flagged as changed since review, the other is not
    write(
        tmp_path,
        "app/calc.py",
        "def total(xs):\n    # now ignores None\n    return sum(x for x in xs if x is not None)\n",
    )
    a = ws.analyze()
    calc = a["refs"]["app/calc.py#total"]
    assert calc["status"] == "resolved" and calc["changed_since_review"] is True
    assert (calc["start"], calc["end"]) == (1, 3)
    assert a["refs"]["app/fmt.py#render"]["changed_since_review"] is False
    assert "app/calc.py" in a["git"]["dirty"]

    # rename the symbol: reported as an error, never silently re-targeted
    write(tmp_path, "app/fmt.py", "def render_value(x):\n    return str(x)\n")
    a = ws.analyze()
    assert a["refs"]["app/fmt.py#render"]["status"] == "missing_symbol"
    assert any(d["code"] == "ref-missing-symbol" and d["record"] == "fmt" for d in a["diagnostics"])

    # move the file: missing_file
    (tmp_path / "app/calc.py").rename(tmp_path / "app/calculate.py")
    a = ws.analyze()
    assert a["refs"]["app/calc.py#total"]["status"] == "missing_file"


def test_unknown_reviewed_revision_is_reported(tmp_path):
    setup_repo(tmp_path)
    text = (tmp_path / ".repoluma/map.yaml").read_text()
    rev = text.split("reviewed_revision: ")[1].split()[0]
    (tmp_path / ".repoluma/map.yaml").write_text(text.replace(rev, "deadbeef"))
    a = Workspace.open(tmp_path).analyze()
    assert any(d["code"] == "unknown-reviewed-revision" for d in a["diagnostics"])


def test_git_config_cannot_run_commands(tmp_path):
    """A hostile repository config (fsmonitor hook) must not execute while viewing."""
    setup_repo(tmp_path)
    marker = tmp_path / "pwned"
    git(tmp_path, "config", "core.fsmonitor", f"touch {marker}; false")
    Workspace.open(tmp_path).analyze()
    assert not marker.exists()


def test_focus_text_since_review(tmp_path):
    write(tmp_path, "app/pay.py", "def pay(token, total):\n    return gateway.charge(token, total)\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    rev = git(tmp_path, "rev-parse", "HEAD").strip()
    write(tmp_path, ".repoluma/map.yaml", f"""
        schema_version: 3
        scope: {{summary: s, reviewed_revision: {rev}}}
        nodes:
          - {{id: a, label: A}}
          - {{id: b, label: B}}
        relationships:
          - id: charge
            from: a
            to: b
            type: data
            label: token and total
            certainty: observed
            refs:
              - {{path: app/pay.py, symbol: pay, role: transfer, focus: "gateway.charge(token, total)"}}
              - {{path: app/pay.py, symbol: pay, role: receive, focus: "def pay(token, total, retries=1):"}}
    """)

    def anchors():
        a = Workspace.open(tmp_path).analyze()
        refs = a["model"]["relationships"][0]["refs"]
        return [x["anchors"][0] for x in refs], {d["code"] for d in a["diagnostics"]}

    (first, second), codes = anchors()
    assert first["since_review"] == "unchanged" and second["status"] == "missing"
    # the file changes: the old statement is still there, the new signature is new
    write(tmp_path, "app/pay.py",
          "def pay(token, total, retries=1):\n    return gateway.charge(token, total)\n")
    (first, second), codes = anchors()
    assert (first["status"], first["since_review"]) == ("found", "text_unchanged")
    assert (second["status"], second["since_review"]) == ("found", "text_new")
    # the statement moves into a helper: reported where it is now, not followed there
    write(tmp_path, "app/pay.py", "def pay(token, total, retries=1):\n    return _go(token, total)\n\n\n"
          "def _go(token, total):\n    return gateway.charge(token, total)\n")
    (first, second), codes = anchors()
    assert first["status"] == "missing" and first["elsewhere"] == [6] and "focus-missing" in codes
    # deleted entirely
    write(tmp_path, "app/pay.py", "def pay(token, total, retries=1):\n    return None\n")
    (first, _), _ = anchors()
    assert first["status"] == "missing" and not first.get("elsewhere")
