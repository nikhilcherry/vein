"""Compare two recordings.

This is the part static tooling cannot do. Two questions it answers:

* *"My refactor was supposed to preserve behaviour."*  Record before and
  after; if the executed function set and the call edges are identical, the
  program really did take the same path.
* *"Why does this flag change the result?"*  Record with and without it; the
  edges that appear on only one side are the branch that matters.

Functions are matched on ``(file, qualname)`` and edges on the pair of those
labels, so a function moving within its file is not reported as a change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .store import Func, Run


@dataclass
class CallDelta:
    """A function in both runs, invoked a different number of times."""

    file: str
    qualname: str
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before

    @property
    def ratio(self) -> float:
        if self.before == 0:
            return float("inf") if self.after else 1.0
        return self.after / self.before


@dataclass
class RunDiff:
    """The structural difference between two recordings."""

    a: Run
    b: Run
    only_a: list[Func] = field(default_factory=list)
    only_b: list[Func] = field(default_factory=list)
    call_deltas: list[CallDelta] = field(default_factory=list)
    shared: int = 0
    edges_only_a: list = field(default_factory=list)
    edges_only_b: list = field(default_factory=list)
    shared_edges: int = 0

    @property
    def identical(self) -> bool:
        """True when both runs ran the same functions over the same edges."""
        return not (
            self.only_a or self.only_b or self.edges_only_a or self.edges_only_b
        )

    @property
    def call_counts_identical(self) -> bool:
        return not self.call_deltas

    def headline(self) -> str:
        if self.identical and self.call_counts_identical:
            return "identical execution: same functions, same call paths, same counts"
        if self.identical:
            return (
                "same call graph, different volume: "
                f"{len(self.call_deltas)} function(s) called a different "
                "number of times"
            )
        parts = []
        if self.only_b:
            parts.append(f"+{len(self.only_b)} functions")
        if self.only_a:
            parts.append(f"-{len(self.only_a)} functions")
        if self.edges_only_b:
            parts.append(f"+{len(self.edges_only_b)} call paths")
        if self.edges_only_a:
            parts.append(f"-{len(self.edges_only_a)} call paths")
        return "execution changed: " + ", ".join(parts)


def _is_module_edge(edge) -> bool:
    return any(part.endswith(":<module>") for part in edge)


def diff_runs(a: Run, b: Run, *, ignore_modules: bool = False) -> RunDiff:
    """Diff ``a`` (before) against ``b`` (after)."""
    index_a = a.index()
    index_b = b.index()

    def keep(key) -> bool:
        return not (ignore_modules and key[1] == "<module>")

    keys_a = {k for k in index_a if keep(k)}
    keys_b = {k for k in index_b if keep(k)}

    only_a = [index_a[k] for k in sorted(keys_a - keys_b)]
    only_b = [index_b[k] for k in sorted(keys_b - keys_a)]
    shared = sorted(keys_a & keys_b)

    deltas = []
    for key in shared:
        before, after = index_a[key].calls, index_b[key].calls
        if before != after:
            deltas.append(
                CallDelta(file=key[0], qualname=key[1], before=before, after=after)
            )
    deltas.sort(key=lambda d: (-abs(d.delta), d.file, d.qualname))

    edges_a = set(a.edge_keys())
    edges_b = set(b.edge_keys())
    if ignore_modules:
        edges_a = {e for e in edges_a if not _is_module_edge(e)}
        edges_b = {e for e in edges_b if not _is_module_edge(e)}

    return RunDiff(
        a=a,
        b=b,
        only_a=only_a,
        only_b=only_b,
        call_deltas=deltas,
        shared=len(shared),
        edges_only_a=sorted(edges_a - edges_b),
        edges_only_b=sorted(edges_b - edges_a),
        shared_edges=len(edges_a & edges_b),
    )


def _short(label: str) -> str:
    if label == "<entry>":
        return "«entry»"
    file, _, qualname = label.rpartition(":")
    module = file.rsplit("/", 1)[-1]
    if module.endswith(".py"):
        module = module[:-3]
    return f"{module}.{qualname}" if module else qualname


def edge_text(edge) -> str:
    """Render an edge as ``caller → callee`` using short names."""
    src, dst = edge
    return f"{_short(src)} → {_short(dst)}"
