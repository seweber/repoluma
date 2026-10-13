import os

import pytest

from repoluma.sources import Repository, SourceError

from .conftest import write


@pytest.fixture
def repo(tmp_path):
    write(
        tmp_path,
        "pkg/svc.py",
        '''
        import functools

        LIMIT = 5


        class Service:
            """Doc."""

            @functools.cache
            def run(self, x):
                return x + LIMIT

            def stop(self):
                pass


        def helper():
            return 1


        if True:
            def twice():
                return 1
        else:
            def twice():
                return 2
        ''',
    )
    write(
        tmp_path,
        "web/app.ts",
        """
        export interface Cart {
          lines: number;
        }

        export async function submit(cart: Cart): Promise<void> {
          if (cart.lines === 0) {
            throw new Error("empty");
          }
        }

        class Widget {
          render(): string {
            return "x";
          }
        }
        """,
    )
    write(
        tmp_path,
        "srv/main.go",
        """
        package main

        func (s *Server) Handle(w Writer) {
        	s.count++
        }
        """,
    )
    write(tmp_path, "docs/README.md", "# Title\n\n## Install\n\nrun it\n\n## Use\n\nmore\n")
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "x"\n\n[project.scripts]\nx = "x:main"\n\n[tool.ruff]\n',
    )
    write(tmp_path, ".env", "SECRET=1\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
    return Repository(tmp_path)


def res(repo, **ref):
    return repo.resolve(
        {"path": ref.pop("path"), "symbol": ref.get("symbol"), "lines": ref.get("lines")}
    )


def test_python_method_with_decorator(repo):
    r = res(repo, path="pkg/svc.py", symbol="Service.run")
    assert (r.status, r.start, r.end, r.method) == ("resolved", 9, 11, "python-ast")


def test_python_constant_and_class(repo):
    assert res(repo, path="pkg/svc.py", symbol="LIMIT").start == 3
    r = res(repo, path="pkg/svc.py", symbol="Service")
    assert (r.start, r.end) == (6, 14)


def test_python_ambiguous_then_disambiguated_by_lines(repo):
    r = res(repo, path="pkg/svc.py", symbol="twice")
    assert r.status == "ambiguous" and len(r.candidates) == 2
    r = res(repo, path="pkg/svc.py", symbol="twice", lines=[25, 26])
    assert r.status == "resolved" and r.start == 25


def test_python_missing_symbol_suggests_qualified_name(repo):
    r = res(repo, path="pkg/svc.py", symbol="stop")
    assert r.status == "missing_symbol" and "Service.stop" in r.message


def test_python_never_resolves_to_a_different_symbol(repo):
    assert res(repo, path="pkg/svc.py", symbol="Service.runn").status == "missing_symbol"


def test_moved_when_hint_disagrees(repo):
    r = res(repo, path="pkg/svc.py", symbol="helper", lines=[1, 2])
    assert r.status == "moved" and r.start == 17


def test_lines_only_and_out_of_range(repo):
    assert res(repo, path="pkg/svc.py", lines=[2, 4]).status == "lines"
    assert res(repo, path="pkg/svc.py", lines=[200, 210]).status == "out_of_range"
    assert res(repo, path="pkg/svc.py").status == "file"


def test_heuristics_for_other_languages(repo):
    r = res(repo, path="web/app.ts", symbol="submit")
    assert (r.status, r.start, r.end) == ("heuristic", 5, 9)
    r = res(repo, path="web/app.ts", symbol="Widget.render")
    assert (r.status, r.start, r.end) == ("heuristic", 12, 14)
    r = res(repo, path="srv/main.go", symbol="Handle")
    assert (r.start, r.end) == (3, 5)
    r = res(repo, path="docs/README.md", symbol="Install")
    assert (r.start, r.end) == (3, 5)
    r = res(repo, path="pyproject.toml", symbol="project.scripts")
    assert (r.start, r.end) == (4, 5)
    assert res(repo, path="web/app.ts", symbol="nothere").status == "missing_symbol"


@pytest.mark.parametrize(
    "path,status",
    [
        (".env", "excluded"),
        ("blob.bin", "binary"),
        ("nope.py", "missing_file"),
        ("../outside.py", "invalid_path"),
        ("/etc/passwd", "invalid_path"),
        ("pkg", "not_a_file"),
    ],
)
def test_confinement_and_exclusions(repo, path, status):
    with pytest.raises(SourceError) as exc:
        repo.read(path)
    assert exc.value.status == status


def test_symlink_escaping_root_is_refused(tmp_path, repo):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    os.symlink(outside, tmp_path / "link.txt")
    with pytest.raises(SourceError) as exc:
        repo.read("link.txt")
    assert exc.value.status == "outside_root"


def test_symlink_to_excluded_file_is_refused(tmp_path, repo):
    os.symlink(tmp_path / ".env", tmp_path / "innocent.txt")
    with pytest.raises(SourceError) as exc:
        repo.read("innocent.txt")
    assert exc.value.status == "excluded"


def test_user_excludes(tmp_path):
    write(tmp_path, "gen/out.py", "x = 1\n")
    with pytest.raises(SourceError):
        Repository(tmp_path, ["gen/*"]).read("gen/out.py")


def test_large_file_refused(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 2_100_000)
    with pytest.raises(SourceError) as exc:
        Repository(tmp_path).read("big.txt")
    assert exc.value.status == "too_large"


def test_cache_invalidates_on_change(tmp_path):
    p = write(tmp_path, "a.py", "def f():\n    return 1\n")
    repo = Repository(tmp_path)
    assert repo.excerpt("a.py", 1, 5)["lines"][1] == "    return 1"
    p.write_text("def f():\n    return 2  # longer\n")
    assert repo.excerpt("a.py", 1, 5)["lines"][1] == "    return 2  # longer"


def test_overload_stubs_do_not_make_a_symbol_ambiguous(tmp_path):
    write(
        tmp_path,
        "m.py",
        """
        from typing import overload


        class R:
            @overload
            def get(self, k: int) -> int: ...
            @overload
            def get(self, k: str) -> str: ...
            def get(self, k):
                return k
        """,
    )
    write(tmp_path, "s.pyi", "from typing import overload\n@overload\ndef f(x: int) -> int: ...\n")
    repo = Repository(tmp_path)
    r = repo.resolve({"path": "m.py", "symbol": "R.get"})
    assert (r.status, r.start, r.end) == ("resolved", 9, 10)
    assert repo.resolve({"path": "s.pyi", "symbol": "f"}).status == "resolved"


def test_focus_must_occur_exactly_once_in_the_range(tmp_path):
    write(tmp_path, "m.py", '''
        def place(token, total):
            check(total)
            result = pay(
                token,   total)
            check(total)
            return result


        def other():
            pay(token, total)
    ''')
    r = Repository(tmp_path)

    def anchors(*focus, symbol="place"):
        ref = {"path": "m.py", "symbol": symbol, "focus": list(focus)}
        return r.resolve_focus(ref, r.resolve(ref))

    found, = anchors("pay( token, total)")  # whitespace and line breaks normalized
    assert found["status"] == "found" and (found["start"], found["end"]) == (3, 4)
    assert found["cols"] == [13, 23]  # from "pay(" on line 3 to after "total)" on line 4
    twice, = anchors("check(total)")
    assert twice["status"] == "ambiguous" and twice["candidates"] == [2, 5]
    # text outside the ref's range is reported, never used instead
    gone, = anchors("other()")
    assert gone["status"] == "missing" and gone["elsewhere"] == [9]
    none, = anchors("pay(token)")
    assert none["status"] == "missing" and none.get("elsewhere") is None
    broken, = anchors("x", symbol="nope")
    assert broken["status"] == "unresolved"
