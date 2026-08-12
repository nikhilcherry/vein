"""On-disk layout for recorded runs.

Everything lives under ``.vein/`` at the project root::

    .vein/
      runs/<name>.json      merged, canonical recording
      tmp/<name>-<pid>/     per-run scratch: injected shim + raw parts

A run file is deliberately plain JSON with stable integer function ids so that
``vein diff`` can compare two recordings made months apart.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

VEIN_DIR = ".vein"
FORMAT_VERSION = 1

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Turn an arbitrary run label into something safe for a filename."""
    cleaned = _SAFE_NAME.sub("-", name.strip()).strip("-.")
    return cleaned or "run"


def vein_dir(root: str) -> str:
    return os.path.join(root, VEIN_DIR)


def runs_dir(root: str) -> str:
    return os.path.join(vein_dir(root), "runs")


def run_path(root: str, name: str) -> str:
    return os.path.join(runs_dir(root), safe_name(name) + ".json")


def list_runs(root: str) -> list[str]:
    """Run names, most recently recorded first."""
    directory = runs_dir(root)
    if not os.path.isdir(directory):
        return []
    names = [f[:-5] for f in os.listdir(directory) if f.endswith(".json")]
    return sorted(
        names,
        key=lambda n: os.path.getmtime(os.path.join(directory, n + ".json")),
        reverse=True,
    )


@dataclass
class Func:
    """One function as observed at runtime."""

    file: str
    line: int
    qualname: str
    calls: int = 0
    self_ns: int = 0
    cum_ns: int = 0

    @property
    def key(self) -> tuple[str, str]:
        """Identity used across runs: file path + qualified name.

        The definition line is deliberately excluded -- moving a function
        within a file should not read as "removed and added" in a diff.
        """
        return (self.file, self.qualname)

    @property
    def label(self) -> str:
        return f"{self.file}:{self.qualname}"

    def to_json(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "qualname": self.qualname,
            "calls": self.calls,
            "self_ns": self.self_ns,
            "cum_ns": self.cum_ns,
        }


@dataclass
class Run:
    """A merged recording of one ``vein run`` invocation."""

    name: str
    argv: list[str] = field(default_factory=list)
    cwd: str = ""
    root: str = ""
    started: str = ""
    wall_s: float = 0.0
    exit_code: int = 0
    processes: int = 1
    threads: int = 1
    backend: str = ""
    timing: bool = True
    functions: list[Func] = field(default_factory=list)
    edges: dict[tuple[int, int], int] = field(default_factory=dict)

    # -- derived views ----------------------------------------------------

    def index(self) -> dict[tuple[str, str], Func]:
        return {f.key: f for f in self.functions}

    def by_file(self) -> dict[str, list[Func]]:
        grouped: dict[str, list[Func]] = {}
        for func in self.functions:
            grouped.setdefault(func.file, []).append(func)
        for funcs in grouped.values():
            funcs.sort(key=lambda f: f.line)
        return grouped

    def total_calls(self) -> int:
        return sum(f.calls for f in self.functions)

    def total_self_ns(self) -> int:
        return sum(f.self_ns for f in self.functions)

    def edge_keys(self) -> dict[tuple[str, str], int]:
        """Edges expressed with stable string labels instead of ids."""
        out: dict[tuple[str, str], int] = {}
        for (a, b), count in self.edges.items():
            src = "<entry>" if a < 0 else self.functions[a].label
            dst = self.functions[b].label
            out[(src, dst)] = out.get((src, dst), 0) + count
        return out

    def callers_of(self) -> dict[int, list[tuple[int, int]]]:
        out: dict[int, list[tuple[int, int]]] = {}
        for (a, b), count in self.edges.items():
            out.setdefault(b, []).append((a, count))
        return out

    def callees_of(self) -> dict[int, list[tuple[int, int]]]:
        out: dict[int, list[tuple[int, int]]] = {}
        for (a, b), count in self.edges.items():
            out.setdefault(a, []).append((b, count))
        return out

    # -- serialisation ----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "vein": FORMAT_VERSION,
            "name": self.name,
            "argv": self.argv,
            "cwd": self.cwd,
            "root": self.root,
            "started": self.started,
            "wall_s": round(self.wall_s, 6),
            "exit_code": self.exit_code,
            "processes": self.processes,
            "threads": self.threads,
            "backend": self.backend,
            "timing": self.timing,
            "functions": [f.to_json() for f in self.functions],
            "edges": [[a, b, c] for (a, b), c in sorted(self.edges.items())],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Run":
        version = data.get("vein")
        if version != FORMAT_VERSION:
            raise ValueError(
                f"unsupported recording format {version!r} "
                f"(this build of vein reads format {FORMAT_VERSION})"
            )
        run = cls(
            name=data.get("name", "run"),
            argv=data.get("argv", []),
            cwd=data.get("cwd", ""),
            root=data.get("root", ""),
            started=data.get("started", ""),
            wall_s=data.get("wall_s", 0.0),
            exit_code=data.get("exit_code", 0),
            processes=data.get("processes", 1),
            threads=data.get("threads", 1),
            backend=data.get("backend", ""),
            timing=data.get("timing", True),
        )
        run.functions = [
            Func(
                file=f["file"],
                line=f.get("line", 0),
                qualname=f.get("qualname", "?"),
                calls=f.get("calls", 0),
                self_ns=f.get("self_ns", 0),
                cum_ns=f.get("cum_ns", 0),
            )
            for f in data.get("functions", [])
        ]
        run.edges = {(a, b): c for a, b, c in data.get("edges", [])}
        return run

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh, indent=1)
        os.replace(tmp, path)
        return path


def load_run(root: str, name: str) -> Run:
    """Load a run by name, by path, or by unambiguous prefix."""
    for path in (name, run_path(root, name)):
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return Run.from_json(json.load(fh))
    known = list_runs(root)
    matches = [n for n in known if n.startswith(safe_name(name))]
    if len(matches) == 1:
        return load_run(root, matches[0])
    if len(matches) > 1:
        raise LookupError(f"run {name!r} is ambiguous: {', '.join(sorted(matches))}")
    hint = f" (known runs: {', '.join(known)})" if known else " (no runs recorded yet)"
    raise LookupError(f"no run named {name!r}{hint}")
