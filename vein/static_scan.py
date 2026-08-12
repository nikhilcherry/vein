"""Static inventory of what a project *defines*.

Pairing this with a recording answers the question dead-code linters cannot:
not "is this symbol referenced anywhere?" but "did it ever actually run?".
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass

SKIP_DIRS = {
    ".git",
    ".vein",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "site-packages",
    "dist",
    "build",
}


@dataclass(frozen=True)
class Definition:
    """A function (or method) defined in the project's source."""

    file: str
    line: int
    end_line: int
    qualname: str
    kind: str  # function | method | async | property
    decorators: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.file, self.qualname)

    @property
    def lines(self) -> int:
        return max(1, self.end_line - self.line + 1)


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


class _Collector(ast.NodeVisitor):
    def __init__(self, relpath: str):
        self.relpath = relpath
        self.stack: list[str] = []
        self.found: list[Definition] = []

    def _qual(self, name: str) -> str:
        return ".".join([*self.stack, name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node, kind: str) -> None:
        decorators = tuple(_decorator_name(d) for d in node.decorator_list)
        if "property" in decorators:
            kind = "property"
        elif self.stack and kind == "function":
            kind = "method"
        self.found.append(
            Definition(
                file=self.relpath,
                line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
                qualname=self._qual(node.name),
                kind=kind,
                decorators=decorators,
            )
        )
        # Nested functions carry a "<locals>" segment in co_qualname at
        # runtime; mirror that here so the two views line up.
        self.stack.append(node.name + ".<locals>")
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, "async")


def scan_file(path: str, relpath: str) -> list[Definition]:
    try:
        with open(path, "rb") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=relpath)
    except (OSError, SyntaxError, ValueError):
        return []
    collector = _Collector(relpath)
    collector.visit(tree)
    return collector.found


def iter_python_files(root: str, excludes: tuple[str, ...] = ()):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            full = os.path.join(dirpath, filename)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if any(pattern in rel for pattern in excludes):
                continue
            yield full, rel


def scan_project(root: str, excludes: tuple[str, ...] = ()) -> list[Definition]:
    """Every function defined under ``root``, in file order."""
    out: list[Definition] = []
    for full, rel in iter_python_files(root, excludes):
        out.extend(scan_file(full, rel))
    return sorted(out, key=lambda d: (d.file, d.line))


# Decorators that hand a function to a framework to call later. Such code can
# legitimately never run in a given recording, so flagging it is usually a
# false positive.
REGISTERED = {
    "abstractmethod",
    "app",
    "cli",
    "command",
    "delete",
    "fixture",
    "get",
    "given",
    "hookimpl",
    "on_event",
    "overload",
    "patch",
    "post",
    "put",
    "receiver",
    "register",
    "route",
    "setup",
    "signal",
    "step",
    "task",
    "teardown",
    "then",
    "websocket",
    "when",
}


def is_probably_registered(definition: Definition) -> bool:
    return any(d in REGISTERED for d in definition.decorators)


def dead_functions(
    definitions: list[Definition],
    executed: set,
    include_registered: bool = False,
) -> list[Definition]:
    """Definitions with no matching runtime observation, largest first."""
    out = []
    for definition in definitions:
        if definition.key in executed:
            continue
        if not include_registered and is_probably_registered(definition):
            continue
        out.append(definition)
    return sorted(out, key=lambda d: (-d.lines, d.file, d.line))
