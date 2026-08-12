"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, runner, static_scan, term
from .diff import diff_runs, edge_text
from .store import list_runs, load_run, safe_name

USAGE = """vein — see what your code actually does when it runs

  vein run -- pytest tests/            record a run
  vein list                            show recorded runs
  vein show <run>                      summarise a run in the terminal
  vein dead                            functions no recorded run ever executed
  vein diff <before> <after>           what changed at runtime between two runs
  vein report <run>                    write a self-contained HTML report
"""


def find_root(start: str | None = None) -> str:
    """Project root: nearest ancestor with a repo/project marker, else cwd."""
    here = os.path.abspath(start or os.getcwd())
    markers = (".git", "pyproject.toml", "setup.py", "setup.cfg", ".vein")
    path = here
    while True:
        for marker in markers:
            if os.path.exists(os.path.join(path, marker)):
                return path
        parent = os.path.dirname(path)
        if parent == path:
            return here
        path = parent


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split ``vein run -n x -- cmd ...`` at the ``--`` separator."""
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    # Tolerate a missing separator: everything from the first bare token on
    # is the command to run.
    for i, token in enumerate(argv):
        if not token.startswith("-"):
            return argv[:i], argv[i:]
    return argv, []


def _default_name(command: list[str]) -> str:
    parts = []
    for token in command:
        base = os.path.basename(token)
        if token.startswith("-"):
            continue
        if base in ("python", "python3") or base.startswith("python3."):
            continue
        parts.append(base)
        if len(parts) == 2:
            break
    return safe_name("-".join(parts) or "run")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_run(args, command: list[str]) -> int:
    if not command:
        print("vein run: nothing to run (try: vein run -- pytest)", file=sys.stderr)
        return 2
    root = find_root(args.root)
    command = runner.python_command(command)
    if not runner.looks_like_python(command):
        print(
            term.paint(
                f"vein: {command[0]!r} does not look like a Python program; "
                "recording anyway (child Python processes will still be traced)",
                "yellow",
            ),
            file=sys.stderr,
        )
    name = args.name or _default_name(command)

    print(term.paint(f"vein: recording {' '.join(command)} → {name}", "grey"))
    run, path = runner.record(
        command,
        root=root,
        name=name,
        excludes=args.exclude or [],
        timing=not args.no_time,
        comprehensions=args.comprehensions,
    )

    if not run.functions:
        print(
            term.paint(
                "vein: no project code was executed. Is the code under "
                f"{root}? (use --root to point elsewhere)",
                "yellow",
            ),
            file=sys.stderr,
        )
    else:
        files = len({f.file for f in run.functions})
        print(
            term.paint("vein: ", "grey")
            + term.paint(str(len(run.functions)), "bold")
            + f" functions in {files} files, "
            + term.paint(term.count(run.total_calls()), "bold")
            + f" calls, {run.processes} process(es) → "
            + term.paint(os.path.relpath(path, os.getcwd()), "cyan")
        )
    return run.exit_code


def cmd_list(args) -> int:
    root = find_root(args.root)
    names = list_runs(root)
    if not names:
        print("no runs recorded yet — try: vein run -- pytest")
        return 1
    rows = []
    for name in names:
        try:
            run = load_run(root, name)
        except (ValueError, LookupError):
            continue
        rows.append(
            [
                term.paint(name, "bold"),
                run.started or "-",
                f"{run.wall_s:.2f}s",
                str(len(run.functions)),
                term.count(run.total_calls()),
                term.truncate(" ".join(run.argv), 40),
            ]
        )
    print(
        term.table(
            rows, ["RUN", "STARTED", "WALL", "FUNCS", "CALLS", "COMMAND"], "llrrrl"
        )
    )
    return 0


def cmd_show(args) -> int:
    root = find_root(args.root)
    try:
        run = load_run(root, args.run)
    except (LookupError, ValueError) as exc:
        print(f"vein: {exc}", file=sys.stderr)
        return 1

    print(term.rule(f"run {run.name}"))
    print(
        f"  command   {' '.join(run.argv)}\n"
        f"  started   {run.started}   wall {run.wall_s:.2f}s   exit {run.exit_code}\n"
        f"  observed  {len(run.functions)} functions in "
        f"{len({f.file for f in run.functions})} files, "
        f"{term.count(run.total_calls())} calls, "
        f"{run.processes} process(es), {run.threads} thread(s)"
    )
    if not run.functions:
        return 0

    metrics = {
        "self": lambda f: f.self_ns,
        "cum": lambda f: f.cum_ns,
        "calls": lambda f: f.calls,
    }
    key = metrics[args.by]
    ranked = sorted(run.functions, key=key, reverse=True)[: args.limit]
    peak = max(key(f) for f in ranked) or 1

    print()
    print(term.rule(f"top {len(ranked)} by {args.by}"))
    rows = [
        [
            term.paint(term.bar(key(f) / peak, 10), "cyan"),
            term.count(f.calls),
            term.duration(f.self_ns),
            term.duration(f.cum_ns),
            term.truncate(f"{f.file}:{f.line}", 44),
            term.paint(term.truncate(f.qualname, 34), "bold"),
        ]
        for f in ranked
    ]
    print(term.table(rows, ["", "CALLS", "SELF", "CUM", "FILE", "FUNCTION"], "lrrrll"))
    return 0


def _executed_keys(root: str, names: list[str]) -> tuple[set, list[str]]:
    """Union of every function observed across the given (or all) runs."""
    names = names or list_runs(root)
    executed: set = set()
    used: list[str] = []
    for name in names:
        try:
            run = load_run(root, name)
        except (LookupError, ValueError) as exc:
            print(f"vein: {exc}", file=sys.stderr)
            continue
        used.append(run.name)
        executed.update(f.key for f in run.functions)
    return executed, used


def cmd_dead(args) -> int:
    root = find_root(args.root)
    executed, used = _executed_keys(root, args.runs)
    if not used:
        print(
            "vein: no runs to compare against — record one first "
            "(vein run -- pytest)",
            file=sys.stderr,
        )
        return 1

    definitions = static_scan.scan_project(root, tuple(args.exclude or ()))
    dead = static_scan.dead_functions(
        definitions, executed, include_registered=args.include_registered
    )

    if args.json:
        print(
            json.dumps(
                {
                    "runs": used,
                    "defined": len(definitions),
                    "executed": len(definitions) - len(dead),
                    "dead": [
                        {
                            "file": d.file,
                            "line": d.line,
                            "qualname": d.qualname,
                            "kind": d.kind,
                            "lines": d.lines,
                        }
                        for d in dead
                    ],
                },
                indent=2,
            )
        )
        return 1 if dead and args.strict else 0

    covered = len(definitions) - len(dead)
    share = covered / len(definitions) * 100 if definitions else 100.0
    print(term.rule(f"dead code vs {len(used)} run(s): {', '.join(used)}"))
    print(
        f"  {covered}/{len(definitions)} defined functions executed "
        f"({share:.0f}%), {len(dead)} never ran "
        f"({sum(d.lines for d in dead)} lines)"
    )
    if not dead:
        print(term.paint("  nothing unused — every function ran at least once", "green"))
        return 0

    print()
    rows = [
        [
            term.paint(str(d.lines), "yellow"),
            d.kind,
            term.truncate(f"{d.file}:{d.line}", 48),
            term.paint(term.truncate(d.qualname, 40), "bold"),
        ]
        for d in dead[: args.limit]
    ]
    print(term.table(rows, ["LINES", "KIND", "FILE", "FUNCTION"], "rlll"))
    if len(dead) > args.limit:
        print(term.paint(f"  … and {len(dead) - args.limit} more", "grey"))
    print()
    print(
        term.paint(
            "  Dead here means: not executed by these recordings. Record more "
            "entry points before deleting anything.",
            "grey",
        )
    )
    return 1 if args.strict else 0


def cmd_diff(args) -> int:
    root = find_root(args.root)
    try:
        before = load_run(root, args.before)
        after = load_run(root, args.after)
    except (LookupError, ValueError) as exc:
        print(f"vein: {exc}", file=sys.stderr)
        return 2

    result = diff_runs(before, after, ignore_modules=args.ignore_imports)

    if args.json:
        print(
            json.dumps(
                {
                    "before": before.name,
                    "after": after.name,
                    "identical": result.identical,
                    "headline": result.headline(),
                    "only_before": [f.label for f in result.only_a],
                    "only_after": [f.label for f in result.only_b],
                    "call_deltas": [
                        {
                            "function": f"{d.file}:{d.qualname}",
                            "before": d.before,
                            "after": d.after,
                        }
                        for d in result.call_deltas
                    ],
                    "edges_only_before": [list(e) for e in result.edges_only_a],
                    "edges_only_after": [list(e) for e in result.edges_only_b],
                },
                indent=2,
            )
        )
        return 1 if args.strict and not result.identical else 0

    print(term.rule(f"diff {before.name} → {after.name}"))
    tone = "green" if result.identical and result.call_counts_identical else "yellow"
    print("  " + term.paint(result.headline(), tone, "bold"))
    print(
        term.paint(
            f"  {result.shared} shared functions, {result.shared_edges} shared "
            f"call paths",
            "grey",
        )
    )

    limit = args.limit
    _section(
        f"only in {after.name}",
        [f"{term.paint('+', 'green')} {f.label}  ({term.count(f.calls)} calls)" for f in result.only_b],
        limit,
    )
    _section(
        f"only in {before.name}",
        [f"{term.paint('-', 'red')} {f.label}  ({term.count(f.calls)} calls)" for f in result.only_a],
        limit,
    )
    _section(
        f"call paths only in {after.name}",
        [f"{term.paint('+', 'green')} {edge_text(e)}" for e in result.edges_only_b],
        limit,
    )
    _section(
        f"call paths only in {before.name}",
        [f"{term.paint('-', 'red')} {edge_text(e)}" for e in result.edges_only_a],
        limit,
    )

    if result.call_deltas and not args.structure_only:
        print()
        print(term.rule("call count changes"))
        rows = []
        for delta in result.call_deltas[:limit]:
            arrow = "↑" if delta.delta > 0 else "↓"
            colour = "green" if delta.delta > 0 else "red"
            rows.append(
                [
                    term.paint(f"{arrow}{abs(delta.delta)}", colour),
                    f"{delta.before} → {delta.after}",
                    term.truncate(delta.file, 40),
                    term.paint(term.truncate(delta.qualname, 34), "bold"),
                ]
            )
        print(term.table(rows, ["DELTA", "CALLS", "FILE", "FUNCTION"], "rrll"))
        if len(result.call_deltas) > limit:
            print(term.paint(f"  … and {len(result.call_deltas) - limit} more", "grey"))

    if args.strict:
        changed = not result.identical or (
            not args.structure_only and not result.call_counts_identical
        )
        return 1 if changed else 0
    return 0


def cmd_report(args) -> int:
    from . import report as report_module

    root = find_root(args.root)
    try:
        run = load_run(root, args.run)
    except (LookupError, ValueError) as exc:
        print(f"vein: {exc}", file=sys.stderr)
        return 1

    definitions = []
    if not args.no_dead:
        definitions = static_scan.scan_project(root, tuple(args.exclude or ()))
    html = report_module.render_report(
        run, definitions, include_dead=not args.no_dead
    )

    out = args.output or f"vein-{run.name}.html"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    size = os.path.getsize(out) / 1024
    print(
        term.paint("vein: ", "grey")
        + f"report written to "
        + term.paint(out, "cyan")
        + term.paint(f"  ({size:.0f} KB, no external assets)", "grey")
    )
    if args.open:
        import webbrowser

        webbrowser.open("file://" + os.path.abspath(out))
    return 0


def _section(title: str, lines: list[str], limit: int) -> None:
    if not lines:
        return
    print()
    print(term.rule(f"{title} ({len(lines)})"))
    for line in lines[:limit]:
        print("  " + line)
    if len(lines) > limit:
        print(term.paint(f"  … and {len(lines) - limit} more", "grey"))


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vein",
        description="see what your code actually does when it runs",
        usage=USAGE,
    )
    parser.add_argument("--version", action="version", version=f"vein {__version__}")
    parser.add_argument("--no-color", action="store_true", help="disable colour output")

    # Shared flags, accepted before *or* after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None, help="project root")
    common.add_argument("--no-color", action="store_true", help="disable colour output")

    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="record a command's execution", parents=[common])
    p_run.add_argument("-n", "--name", help="name for this recording")
    p_run.add_argument(
        "-x", "--exclude", action="append", help="substring of paths to ignore"
    )
    p_run.add_argument(
        "--no-time", action="store_true", help="skip timing (lower overhead)"
    )
    p_run.add_argument(
        "--comprehensions", action="store_true", help="include comprehension frames"
    )

    sub.add_parser("list", help="list recorded runs", parents=[common])

    p_show = sub.add_parser(
        "show", help="summarise a run in the terminal", parents=[common]
    )
    p_show.add_argument("run", nargs="?", default=None, help="run name (default: latest)")
    p_show.add_argument("-n", "--limit", type=int, default=20, help="rows to show")
    p_show.add_argument(
        "--by",
        choices=("self", "cum", "calls"),
        default="self",
        help="ranking metric (default: self time)",
    )

    p_dead = sub.add_parser(
        "dead",
        help="functions defined in the project that no recorded run executed",
        parents=[common],
    )
    p_dead.add_argument(
        "runs", nargs="*", help="runs to count as coverage (default: all of them)"
    )
    p_dead.add_argument(
        "-x", "--exclude", action="append", help="substring of paths to ignore"
    )
    p_dead.add_argument("-n", "--limit", type=int, default=30, help="rows to show")
    p_dead.add_argument("--json", action="store_true", help="machine-readable output")
    p_dead.add_argument(
        "--strict", action="store_true", help="exit 1 when dead code is found (CI)"
    )
    p_dead.add_argument(
        "--include-registered",
        action="store_true",
        help="also report decorator-registered callbacks (routes, fixtures, …)",
    )

    p_diff = sub.add_parser(
        "diff",
        help="compare what two recordings actually executed",
        parents=[common],
    )
    p_diff.add_argument("before", help="baseline run")
    p_diff.add_argument("after", help="run to compare against the baseline")
    p_diff.add_argument("-n", "--limit", type=int, default=15, help="rows per section")
    p_diff.add_argument("--json", action="store_true", help="machine-readable output")
    p_diff.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if execution changed (behaviour gate for CI)",
    )
    p_diff.add_argument(
        "--structure-only",
        action="store_true",
        help="ignore call-count changes; compare only functions and call paths",
    )
    p_diff.add_argument(
        "--ignore-imports",
        action="store_true",
        help="ignore module-level <module> frames (import order noise)",
    )

    p_report = sub.add_parser(
        "report",
        help="write a self-contained HTML report for a run",
        parents=[common],
    )
    p_report.add_argument("run", nargs="?", default=None, help="run name (default: latest)")
    p_report.add_argument("-o", "--output", help="output path (default: vein-<run>.html)")
    p_report.add_argument("--open", action="store_true", help="open it in a browser")
    p_report.add_argument(
        "-x", "--exclude", action="append", help="substring of paths to ignore"
    )
    p_report.add_argument(
        "--no-dead", action="store_true", help="skip the never-executed functions"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command: list[str] = []
    if argv and argv[0] == "run":
        head, command = _split_command(argv[1:])
        argv = ["run", *head]

    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "no_color", False):
        term.set_color(False)

    if args.command == "run":
        return cmd_run(args, command)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "show":
        if args.run is None:
            runs = list_runs(find_root(args.root))
            if not runs:
                print("no runs recorded yet — try: vein run -- pytest", file=sys.stderr)
                return 1
            args.run = runs[0]
        return cmd_show(args)
    if args.command == "dead":
        return cmd_dead(args)
    if args.command == "diff":
        return cmd_diff(args)
    if args.command == "report":
        if args.run is None:
            runs = list_runs(find_root(args.root))
            if not runs:
                print("no runs recorded yet — try: vein run -- pytest", file=sys.stderr)
                return 1
            args.run = runs[0]
        return cmd_report(args)

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
