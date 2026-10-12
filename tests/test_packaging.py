"""The built wheel must contain everything `uvx repoluma` needs (no frontend toolchain)."""

import shutil
import subprocess
import zipfile

import pytest

from .conftest import ROOT


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not available")
def test_wheel_contains_viewer_guide_and_example(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("repoluma-*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    for required in (
        "repoluma/web/index.html",
        "repoluma/web/app.css",
        "repoluma/web/js/main.js",
        "repoluma/web/js/render.js",
        "repoluma/guide/AUTHORING.md",
        "repoluma/examples/shop/.repoluma/map.yaml",
        "repoluma/examples/shop/shop/orders.py",
    ):
        assert required in names, required
    assert not any("__pycache__" in n for n in names)
    entry = next(n for n in names if n.endswith("entry_points.txt"))
    assert "repoluma = repoluma.cli:main" in zipfile.ZipFile(wheel).read(entry).decode()
