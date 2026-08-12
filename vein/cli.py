"""Command line interface."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, runner, term
from .store import list_runs, load_run, safe_name

USAGE = """vein — see what your code actually does when it runs

  vein run -- pytest tests/            record a run
  vein list                            show recorded runs
  vein show <run>                      summarise a run in the terminal
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

    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
