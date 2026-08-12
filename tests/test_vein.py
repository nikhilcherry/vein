"""Tests for vein.

The end-to-end tests actually launch a Python subprocess under the recorder,
because the injection path (sitecustomize on PYTHONPATH) is the one part that
cannot be verified in-process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from vein import cli, runner, static_scan, term
from vein.diff import diff_runs
from vein.store import Func, Run, load_run, run_path, safe_name

PROGRAM = textwrap.dedent(
    """
    def leaf(n):
        return n * 2

    def middle(n):
        return leaf(n) + leaf(n + 1)

    def never_called():
        return "unused"

    def main(fast=False):
        if fast:
            return leaf(1)
        total = 0
        for i in range(3):
            total += middle(i)
        return total

    if __name__ == "__main__":
        import sys
        print(main("--fast" in sys.argv))
    """
)


@pytest.fixture
def project(tmp_path):
    """A tiny traceable project on disk."""
    (tmp_path / "app.py").write_text(PROGRAM)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    return tmp_path


def record(project, name, args=()):
    return runner.record(
        [sys.executable, str(project / "app.py"), *args],
        root=str(project),
        name=name,
        quiet=True,
    )


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_safe_name_strips_path_separators():
    assert safe_name("tests/unit run!") == "tests-unit-run"
    assert safe_name("") == "run"
    assert safe_name("../../etc/passwd") == "etc-passwd"


def test_run_round_trips_through_json(tmp_path):
    run = Run(
        name="demo",
        argv=["python", "app.py"],
        started="2026-08-12T00:00:00Z",
        functions=[Func("app.py", 1, "main", calls=2, self_ns=10, cum_ns=20)],
        edges={(-1, 0): 2},
    )
    path = run.save(run_path(str(tmp_path), "demo"))
    assert os.path.exists(path)
    loaded = load_run(str(tmp_path), "demo")
    assert loaded.name == "demo"
    assert loaded.functions[0].qualname == "main"
    assert loaded.edges == {(-1, 0): 2}
    assert loaded.total_calls() == 2


def test_load_run_rejects_a_future_format(tmp_path):
    target = run_path(str(tmp_path), "future")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w") as fh:
        json.dump({"vein": 99, "name": "future"}, fh)
    with pytest.raises(ValueError, match="unsupported recording format"):
        load_run(str(tmp_path), "future")


def test_load_run_reports_known_runs_when_missing(tmp_path):
    Run(name="alpha").save(run_path(str(tmp_path), "alpha"))
    with pytest.raises(LookupError, match="alpha"):
        load_run(str(tmp_path), "zeta")


def test_edge_keys_use_stable_labels():
    run = Run(
        name="x",
        functions=[Func("a.py", 1, "one"), Func("a.py", 5, "two")],
        edges={(-1, 0): 1, (0, 1): 3},
    )
    assert run.edge_keys() == {
        ("<entry>", "a.py:one"): 1,
        ("a.py:one", "a.py:two"): 3,
    }


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------


def test_records_the_functions_that_ran(project):
    run, _ = record(project, "slow")
    names = {f.qualname for f in run.functions}
    assert {"main", "middle", "leaf"} <= names
    assert "never_called" not in names
    assert run.exit_code == 0

    leaf = next(f for f in run.functions if f.qualname == "leaf")
    assert leaf.calls == 6  # middle calls leaf twice, three iterations
    assert leaf.file == "app.py"  # recorded relative to the project root


def test_records_the_call_graph(project):
    run, _ = record(project, "slow")
    edges = run.edge_keys()
    assert ("app.py:main", "app.py:middle") in edges
    assert ("app.py:middle", "app.py:leaf") in edges
    assert edges[("app.py:middle", "app.py:leaf")] == 6


def test_does_not_record_the_injected_shim(project):
    run, _ = record(project, "slow")
    assert not [f for f in run.functions if "sitecustomize" in f.file]
    assert not [f for f in run.functions if ".vein" in f.file]


def test_traces_child_processes(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "child.py").write_text("def work():\n    return 1\n\nwork()\n")
    (tmp_path / "parent.py").write_text(
        "import os, subprocess, sys\n"
        "def spawn():\n"
        "    subprocess.run([sys.executable, os.path.join("
        "os.path.dirname(__file__), 'child.py')], check=True)\n"
        "spawn()\n"
    )
    run, _ = runner.record(
        [sys.executable, str(tmp_path / "parent.py")],
        root=str(tmp_path),
        name="both",
        quiet=True,
    )
    names = {f.qualname for f in run.functions}
    assert "spawn" in names
    assert "work" in names, "the child process should have been traced too"
    assert run.processes >= 2


def test_scratch_directory_is_cleaned_up(project):
    record(project, "slow")
    tmp = project / ".vein" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_traced_program_still_behaves_normally(project):
    """Tracing must not change stdout or the exit code."""
    plain = subprocess.run(
        [sys.executable, str(project / "app.py")], capture_output=True, text=True
    )
    run, _ = record(project, "slow")
    assert plain.stdout.strip() == "18"
    assert run.exit_code == plain.returncode == 0


def test_build_env_prepends_the_shim_to_pythonpath():
    env = runner.build_env(
        {"PYTHONPATH": "/existing"}, "/inject", "/out", ["/root"], []
    )
    assert env["PYTHONPATH"].split(os.pathsep)[0] == "/inject"
    assert "/existing" in env["PYTHONPATH"]
    assert env["VEIN_OUT_DIR"] == "/out"
    assert env["VEIN_ROOTS"] == "/root"


def test_merge_parts_folds_processes_together(tmp_path):
    parts = []
    for i in range(2):
        payload = {
            "threads": 1,
            "functions": [
                {
                    "file": str(tmp_path / "a.py"),
                    "line": 1,
                    "qualname": "f",
                    "calls": 2,
                    "self_ns": 5,
                    "cum_ns": 9,
                }
            ],
            "edges": [[-1, 0, 2]],
        }
        path = tmp_path / f"part-{i}.json"
        path.write_text(json.dumps(payload))
        parts.append(str(path))
    functions, edges, processes, threads = runner.merge_parts(parts, str(tmp_path))
    assert processes == 2
    assert threads == 2
    assert len(functions) == 1
    assert functions[0].calls == 4  # summed across processes
    assert edges == {(-1, 0): 4}


# ---------------------------------------------------------------------------
# static scan / dead code
# ---------------------------------------------------------------------------


def test_scan_finds_methods_and_nested_functions(tmp_path):
    (tmp_path / "m.py").write_text(
        textwrap.dedent(
            """
            class Thing:
                def method(self):
                    def inner():
                        return 1
                    return inner()

                @property
                def value(self):
                    return 2

            async def fetch():
                return None
            """
        )
    )
    found = {d.qualname: d for d in static_scan.scan_project(str(tmp_path))}
    assert found["Thing.method"].kind == "method"
    assert found["Thing.value"].kind == "property"
    assert found["fetch"].kind == "async"
    assert "Thing.method.<locals>.inner" in found


def test_scan_survives_a_syntax_error(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n")
    (tmp_path / "fine.py").write_text("def ok():\n    return 1\n")
    names = {d.qualname for d in static_scan.scan_project(str(tmp_path))}
    assert names == {"ok"}


def test_dead_code_is_what_never_ran(project):
    record(project, "slow")
    run = load_run(str(project), "slow")
    definitions = static_scan.scan_project(str(project))
    dead = static_scan.dead_functions(definitions, {f.key for f in run.functions})
    assert [d.qualname for d in dead] == ["never_called"]


def test_registered_callbacks_are_not_reported_as_dead(tmp_path):
    (tmp_path / "web.py").write_text(
        "import app\n\n@app.route('/')\ndef home():\n    return 1\n\n"
        "def plain():\n    return 2\n"
    )
    definitions = static_scan.scan_project(str(tmp_path))
    dead = static_scan.dead_functions(definitions, set())
    assert [d.qualname for d in dead] == ["plain"]
    both = static_scan.dead_functions(definitions, set(), include_registered=True)
    assert {d.qualname for d in both} == {"home", "plain"}


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_of_a_run_against_itself_is_identical(project):
    record(project, "slow")
    run = load_run(str(project), "slow")
    result = diff_runs(run, run)
    assert result.identical
    assert result.call_counts_identical
    assert "identical execution" in result.headline()


def test_diff_finds_the_branch_that_differs(project):
    record(project, "slow")
    record(project, "fast", ["--fast"])
    slow = load_run(str(project), "slow")
    fast = load_run(str(project), "fast")
    result = diff_runs(slow, fast)

    assert not result.identical
    assert "middle" in {f.qualname for f in result.only_a}
    assert ("app.py:main", "app.py:middle") in result.edges_only_a
    assert "execution changed" in result.headline()


def test_diff_reports_call_count_changes_on_shared_functions():
    a = Run(name="a", functions=[Func("x.py", 1, "f", calls=2)], edges={(-1, 0): 2})
    b = Run(name="b", functions=[Func("x.py", 1, "f", calls=5)], edges={(-1, 0): 5})
    result = diff_runs(a, b)
    assert result.identical  # same functions, same edges
    assert not result.call_counts_identical
    assert result.call_deltas[0].delta == 3
    assert "different volume" in result.headline()


def test_diff_ignores_a_function_moving_within_its_file():
    a = Run(name="a", functions=[Func("x.py", 10, "f", calls=1)], edges={(-1, 0): 1})
    b = Run(name="b", functions=[Func("x.py", 90, "f", calls=1)], edges={(-1, 0): 1})
    assert diff_runs(a, b).identical


def test_ignore_modules_drops_import_frames():
    a = Run(
        name="a",
        functions=[Func("x.py", 1, "<module>", calls=1), Func("x.py", 4, "f", calls=1)],
        edges={(-1, 0): 1, (0, 1): 1},
    )
    b = Run(name="b", functions=[Func("x.py", 4, "f", calls=1)], edges={(-1, 0): 1})
    assert not diff_runs(a, b).identical
    # Contracting the module frame re-attaches f to the entry point on both
    # sides, rather than leaving it orphaned on one.
    assert diff_runs(a, b, ignore_modules=True).identical


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------


def test_split_command_at_the_separator():
    assert cli._split_command(["-n", "x", "--", "pytest", "-q"]) == (
        ["-n", "x"],
        ["pytest", "-q"],
    )


def test_split_command_without_a_separator():
    assert cli._split_command(["pytest", "-q"]) == ([], ["pytest", "-q"])


def test_default_name_skips_the_interpreter():
    assert cli._default_name(["python3", "-m", "pytest"]) == "pytest"
    assert cli._default_name(["/usr/bin/python3", "app.py"]) == "app.py"


def test_find_root_walks_up_to_the_project_marker(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("")
    assert cli.find_root(str(nested)) == str(tmp_path)


def test_diff_command_exit_codes(project, monkeypatch):
    record(project, "slow")
    record(project, "fast", ["--fast"])
    monkeypatch.chdir(project)
    assert cli.main(["diff", "slow", "slow", "--strict", "--no-color"]) == 0
    assert cli.main(["diff", "slow", "fast", "--strict", "--no-color"]) == 1


def test_dead_command_strict_exit_code(project, monkeypatch):
    record(project, "slow")
    monkeypatch.chdir(project)
    assert cli.main(["dead", "--strict", "--no-color"]) == 1


def test_show_reports_a_missing_run(project, monkeypatch, capsys):
    monkeypatch.chdir(project)
    assert cli.main(["show", "nope", "--no-color"]) == 1
    assert "no run named" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# term
# ---------------------------------------------------------------------------


def test_duration_units():
    assert term.duration(500) == "500ns"
    assert term.duration(1_500) == "1.5µs"
    assert term.duration(2_000_000) == "2.0ms"
    assert term.duration(3_000_000_000) == "3.00s"


def test_count_units():
    assert term.count(999) == "999"
    assert term.count(2_500) == "2.5k"
    assert term.count(3_000_000) == "3.0M"


def test_bar_is_a_fixed_width():
    assert len(term.bar(0.0, 10)) == 10
    assert len(term.bar(1.0, 10)) == 10
    assert term.bar(1.0, 4) == "████"


def test_table_aligns_around_ansi_escapes():
    term.set_color(True)
    try:
        rendered = term.table([[term.paint("a", "red"), "1"]], ["X", "Y"], "lr")
    finally:
        term.set_color(False)
    assert "\033[31m" in rendered
    assert len(rendered.splitlines()) == 2


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_is_self_contained(project, tmp_path):
    from vein.report import render_report

    record(project, "slow")
    run = load_run(str(project), "slow")
    page = render_report(run, static_scan.scan_project(str(project)))

    assert page.startswith("<!doctype html>")
    # No external asset may be fetched: the page must work offline, from disk.
    for pattern in ("<script src=", "<link rel=\"stylesheet\"", "@import", "//cdn"):
        assert pattern not in page
    assert "const DATA = {" in page
    assert "never_called" in page  # dead functions are shown inline


def test_report_payload_marks_dead_functions(project):
    from vein.report import build_payload

    record(project, "slow")
    run = load_run(str(project), "slow")
    functions, edges = build_payload(
        run, static_scan.scan_project(str(project)), include_dead=True
    )
    dead = {f["qualname"] for f in functions if f["dead"]}
    assert dead == {"never_called"}
    assert all(f["id"] == i for i, f in enumerate(functions))
    assert edges, "the call graph should not be empty"


def test_report_escapes_html_in_names(tmp_path):
    from vein.report import render_report

    run = Run(
        name="x<script>",
        argv=["python", "<evil>"],
        functions=[Func("a<b>.py", 1, "f", calls=1)],
    )
    page = render_report(run)
    assert "<script>alert" not in page
    assert "&lt;evil&gt;" in page


def test_report_command_writes_a_file(project, monkeypatch, tmp_path):
    record(project, "slow")
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "r.html"
    assert cli.main(
        ["report", "slow", "--root", str(project), "-o", str(out), "--no-color"]
    ) == 0
    assert out.stat().st_size > 4000


def test_recording_survives_sigterm(tmp_path):
    """A server killed with SIGTERM must still leave a usable recording."""
    import signal
    import time

    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    (tmp_path / "server.py").write_text(
        "import time\n"
        "def serve():\n"
        "    print('up', flush=True)\n"
        "    while True:\n"
        "        time.sleep(0.05)\n"
        "serve()\n"
    )
    scratch = tmp_path / "scratch"
    out_dir = scratch / "parts"
    out_dir.mkdir(parents=True)
    inject = runner._prepare_shim(str(scratch))
    env = runner.build_env(
        os.environ, inject, str(out_dir), [str(tmp_path)], []
    )
    proc = subprocess.Popen(
        [sys.executable, str(tmp_path / "server.py")],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "up"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:  # pragma: no cover - only on a hang
            proc.kill()

    deadline = time.time() + 5
    parts = []
    while time.time() < deadline and not parts:
        parts = [str(p) for p in out_dir.glob("*.json")]
        time.sleep(0.05)
    assert parts, "SIGTERM should still have flushed a recording"
    functions, _, _, _ = runner.merge_parts(parts, str(tmp_path))
    assert "serve" in {f.qualname for f in functions}


def test_paths_walks_back_to_the_entry_point(project):
    record(project, "slow")
    run = load_run(str(project), "slow")
    [leaf] = run.find("leaf")
    chains = run.paths_to(leaf)
    assert chains, "leaf is called, so it must have at least one path"
    for chain in chains:
        assert chain[-1] == leaf
        names = [run.functions[i].qualname for i in chain]
        assert "middle" in names or "main" in names


def test_paths_does_not_loop_on_recursion(tmp_path):
    run = Run(
        name="r",
        functions=[Func("a.py", 1, "f"), Func("a.py", 5, "g")],
        edges={(-1, 0): 1, (0, 1): 1, (1, 0): 1},  # f -> g -> f
    )
    chains = run.paths_to(0, limit=5)
    assert chains and all(len(c) <= 3 for c in chains)


def test_find_prefers_an_exact_qualname_match():
    run = Run(
        name="r",
        functions=[Func("a.py", 1, "run"), Func("a.py", 5, "run_again")],
    )
    assert run.find("run") == [0]
    assert set(run.find("run_")) == {1}
