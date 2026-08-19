"""Run a command with the recorder injected, then merge what came back."""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time

from .store import Func, Run, run_path, safe_name

_SHIM_DIR = os.path.dirname(os.path.abspath(__file__))


def _prepare_shim(scratch: str) -> str:
    """Materialise the injected import path and return it."""
    inject = os.path.join(scratch, "inject")
    os.makedirs(inject, exist_ok=True)
    shutil.copyfile(
        os.path.join(_SHIM_DIR, "_tracer.py"), os.path.join(inject, "_vein_tracer.py")
    )
    shutil.copyfile(
        os.path.join(_SHIM_DIR, "_sitecustomize.py"),
        os.path.join(inject, "sitecustomize.py"),
    )
    return inject


def build_env(
    base,
    inject: str,
    out_dir: str,
    roots: list[str],
    excludes: list[str],
    timing: bool = True,
    comprehensions: bool = False,
) -> dict[str, str]:
    env = dict(base)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = inject + (os.pathsep + existing if existing else "")
    env["VEIN_OUT_DIR"] = out_dir
    env["VEIN_ROOTS"] = os.pathsep.join(roots)
    env["VEIN_EXCLUDES"] = os.pathsep.join(excludes)
    env["VEIN_TIMING"] = "1" if timing else "0"
    env["VEIN_COMPREHENSIONS"] = "1" if comprehensions else "0"
    # Keep the traced project's __pycache__ clean of shim-influenced artefacts.
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _relative(path: str, root: str) -> str:
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # different drive on Windows
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def merge_parts(part_files: list[str], root: str):
    """Fold every process's raw recording into one function/edge table."""
    funcs: dict[tuple[str, str], Func] = {}
    order: list[tuple[str, str]] = []
    edges: dict[tuple, int] = {}
    processes = 0
    threads = 0

    for path in part_files:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        processes += 1
        threads += data.get("threads", 1)
        local: list[tuple[str, str]] = []
        for raw in data.get("functions", []):
            rel = _relative(raw["file"], root)
            key = (rel, raw["qualname"])
            func = funcs.get(key)
            if func is None:
                func = funcs[key] = Func(
                    file=rel, line=raw.get("line", 0), qualname=raw["qualname"]
                )
                order.append(key)
            func.calls += raw.get("calls", 0)
            func.self_ns += raw.get("self_ns", 0)
            func.cum_ns += raw.get("cum_ns", 0)
            local.append(key)
        for a, b, count in data.get("edges", []):
            if b >= len(local) or a >= len(local):
                continue
            src = "<entry>" if a < 0 else local[a]
            edges[(src, local[b])] = edges.get((src, local[b]), 0) + count

    ordered = [funcs[k] for k in order]
    ids = {k: i for i, k in enumerate(order)}
    id_edges = {
        (-1 if a == "<entry>" else ids[a], ids[b]): c for (a, b), c in edges.items()
    }
    return ordered, id_edges, processes, max(threads, 1)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by somebody else
        return True
    except OSError:
        return False
    return True


def _await_late_recordings(out_dir: str, timeout: float = 10.0) -> int:
    """Wait for descendants that are still writing their recording.

    ``subprocess.run`` returns when the *direct* child exits, but a forked
    grandchild -- a pool worker being shut down -- can still be mid-write.
    Collecting the directory at that moment dropped its recording, so the same
    command could report a different set of executed functions run to run.

    Each traced process leaves a ``live-<pid>`` marker until its part is
    published, so this waits on evidence rather than on a fixed delay: the
    common single-process case returns immediately, and a marker left behind by
    a process that died without cleaning up is discarded once its pid is gone.

    Returns the number of processes that died still owing a recording -- a pool
    worker killed outright during teardown runs no Python on its way out, so
    nothing can make it report. The count travels with the run so that
    ``vein dead`` can say its answer is incomplete instead of quietly calling
    live code dead.
    """
    lost = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            markers = [f for f in os.listdir(out_dir) if f.startswith("live-")]
        except OSError:
            return
        waiting = False
        for marker in markers:
            try:
                pid = int(marker[5:])
            except ValueError:
                continue
            if _pid_alive(pid):
                waiting = True
            else:
                # Died without publishing; nothing more is coming from it.
                lost += 1
                try:
                    os.unlink(os.path.join(out_dir, marker))
                except OSError:
                    pass
        if not waiting:
            return lost
        time.sleep(0.005)
    return lost


def record(
    command: list[str],
    *,
    root: str,
    name: str,
    excludes: list[str] | None = None,
    timing: bool = True,
    comprehensions: bool = False,
    quiet: bool = False,
):
    """Execute ``command`` under the recorder and persist the merged run."""
    name = safe_name(name)
    scratch = os.path.join(root, ".vein", "tmp", f"{name}-{os.getpid()}")
    out_dir = os.path.join(scratch, "parts")
    os.makedirs(out_dir, exist_ok=True)
    inject = _prepare_shim(scratch)

    env = build_env(
        os.environ,
        inject,
        out_dir,
        [root],
        excludes or [],
        timing=timing,
        comprehensions=comprehensions,
    )
    started = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    begin = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            env=env,
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL if quiet else None,
        )
        exit_code = proc.returncode
    except FileNotFoundError as exc:
        shutil.rmtree(scratch, ignore_errors=True)
        raise SystemExit(f"vein: cannot run {command[0]!r}: {exc.strerror}")
    except KeyboardInterrupt:
        exit_code = 130
    wall = time.perf_counter() - begin

    lost_processes = _await_late_recordings(out_dir)
    parts = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".json")
    )
    backend = ""
    if parts:
        try:
            with open(parts[0], encoding="utf-8") as fh:
                backend = json.load(fh).get("backend", "")
        except (OSError, json.JSONDecodeError):
            pass

    functions, edges, processes, threads = merge_parts(parts, root)
    run = Run(
        name=name,
        argv=list(command),
        cwd=os.getcwd(),
        root=root,
        started=started,
        wall_s=wall,
        exit_code=exit_code,
        processes=processes,
        lost_processes=lost_processes,
        threads=threads,
        backend=backend,
        timing=timing,
        functions=functions,
        edges=edges,
    )
    path = run.save(run_path(root, name))
    shutil.rmtree(scratch, ignore_errors=True)
    return run, path


def looks_like_python(command: list[str]) -> bool:
    """Best-effort check that the traced command will start a Python process."""
    if not command:
        return False
    exe = os.path.basename(command[0]).lower()
    if exe.startswith("python") or exe.endswith(".py"):
        return True
    # Console scripts installed by pip (pytest, ruff, django-admin, ...) are
    # Python too; look for a shebang pointing at an interpreter.
    resolved = shutil.which(command[0])
    if not resolved:
        return False
    try:
        with open(resolved, "rb") as fh:
            first = fh.readline(256)
    except OSError:
        return False
    return first.startswith(b"#!") and b"python" in first.lower()


def python_command(command: list[str]) -> list[str]:
    """Normalise a bare module invocation like ``-m pytest`` into a command."""
    if command and command[0] == "-m":
        return [sys.executable, *command]
    return command
