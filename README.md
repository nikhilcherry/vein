# vein

**Static analysis tells you what *could* run. `vein` tells you what *did*.**

`vein` records the real call graph of a Python program — every function that
actually executed, who called it, how often, and how long it took — by
injecting a recorder into the process without touching a line of your code.

```bash
vein run -- pytest tests/       # record what your test suite really exercises
vein show                       # read it back in the terminal
```

Zero dependencies. Pure standard library. One file per recording.

## Install

```bash
pip install -e .        # or: pipx install .
```

Requires Python 3.9+. On 3.12+ it uses `sys.monitoring` (PEP 669) and disables
events for code outside your project, so third-party frames cost nothing.

## Use it

```
vein run [-n NAME] [--root DIR] [-x PATTERN] [--no-time] -- COMMAND ...
vein list
vein show [RUN] [--by self|cum|calls] [-n ROWS]
vein dead [RUN ...] [--strict] [--json]
vein diff BEFORE AFTER [--strict] [--structure-only] [--json]
vein report [RUN] [-o FILE] [--open]
```

Recordings land in `.vein/runs/<name>.json` — plain JSON, safe to commit or
throw away.

```console
$ vein run -n slow -- python -m examples.shop.main
vein: 12 functions in 4 files, 19 calls, 1 process(es) → .vein/runs/slow.json

$ vein show slow
── run slow ───────────────────────────────────────────────────────────
  command   python -m examples.shop.main
  observed  12 functions in 4 files, 19 calls, 1 process(es)

── top 6 by self ──────────────────────────────────────────────────────
            CALLS     SELF      CUM  FILE                      FUNCTION
██████████      1  173.2µs  505.8µs  examples/shop/main.py:1   <module>
███████▏        1  125.4µs  130.9µs  examples/shop/cart.py:1   <module>
████▌           1   78.0µs  137.3µs  examples/shop/cart.py:11  checkout
```

## Find code that never runs

`vein dead` walks your source with `ast`, builds the full inventory of
functions you *define*, and subtracts everything any recording actually
executed. Not "is this symbol referenced?" — "did this ever run?".

```console
$ vein dead
── dead code vs 2 run(s): fast, slow ─────────────────────────────────
  8/9 defined functions executed (89%), 1 never ran (3 lines)

LINES  KIND      FILE                         FUNCTION
    3  function  examples/shop/pricing.py:22  legacy_coupon
```

Coverage is the **union of every run**, so a function exercised by any one
recorded entry point counts as live. Functions registered by decorator
(`@app.route`, `@pytest.fixture`, `@abstractmethod`, …) are skipped by default
because a framework may call them later; pass `--include-registered` to see
them. `--strict` exits 1, which makes it a CI gate.

## Diff two runs

The flagship. Record twice, then ask what actually changed:

```console
$ vein run -n before -- python -m examples.shop.main
$ vein run -n after  -- python -m examples.shop.main --fast
$ vein diff before after
── diff before → after ───────────────────────────────────────────────
  execution changed: -5 functions, -5 call paths
  7 shared functions, 7 shared call paths

── only in before (5) ────────────────────────────────────────────────
  - examples/shop/cart.py:line_total  (3 calls)
  - examples/shop/pricing.py:bulk_discount  (3 calls)
  ...
── call paths only in before (5) ─────────────────────────────────────
  - cart.checkout → cart.line_total
  - cart.line_total → pricing.bulk_discount
```

Two things this is very good at:

**Proving a refactor was behaviour-preserving.** Record before, record after,
and if `vein diff` says *identical execution: same functions, same call paths,
same counts*, the program genuinely took the same route. `--strict` exits 1 on
any change, so it works as a CI gate:

```yaml
- run: vein run -n base -- pytest -q          # on the base commit
- run: vein run -n head -- pytest -q          # on the PR
- run: vein diff base head --strict --structure-only
```

**Explaining a flag, an env var, or a bug.** The call paths present on only one
side *are* the branch that differs. No stepping through a debugger to find
where two runs part ways.

Functions are matched on `(file, qualname)`, so moving a function within its
file is not reported as a change. `--ignore-imports` drops module-level frames
when import order is noisy.

## Read it in a browser

```bash
vein report --open
```

One HTML file, no external assets, ~30 KB for a mid-sized run. Files grouped by
cost, functions that never ran shown greyed out in place, and a side panel that
walks the call graph — click a function to see who called it and what it called,
then click through.

## How it works

`vein run` writes two tiny files into a scratch directory and puts it at the
front of `PYTHONPATH`:

* `sitecustomize.py` — CPython's `site.py` imports this automatically at
  interpreter startup, *after* `PYTHONPATH` is on `sys.path`. That is the hook.
  Any `sitecustomize` you already had is chained afterwards.
* `_vein_tracer.py` — the recorder itself, stdlib-only.

Because the recorder rides on an environment variable, **every Python
subprocess your command spawns is traced too**, and their recordings are merged
into one graph.

On exit each process dumps its slice of the graph; `vein` folds them together
and deletes the scratch directory.

## Overhead

| workload | baseline | traced |
|---|---|---|
| this project's pytest suite | 1.01s | 1.20s |
| 800k calls of a one-line function | 0.02s | 0.81s |

The second row is the honest worst case: functions small enough that the
tracing callbacks dwarf the actual work. Normal code costs roughly 20%. Pass
`--no-time` to skip timing entirely when you only care about structure — which
is all `dead` and `diff --structure-only` need. Run `bench/overhead.sh` to
measure it on your own machine.

Tracing a long-running process? Stop it however you like: `vein` flushes the
recording on `SIGTERM` as well as on normal exit, so `vein run -- ./serve` then
Ctrl-C (or `kill`) still gives you the graph.

## Status

`run`, `list`, `show`, `dead`, `diff`, `report` all work today.

vein records its own test suite in CI and diffs two consecutive runs, so the
tool is exercised by the tool.

## Design notes

[`docs/DESIGN.md`](docs/DESIGN.md) covers the injection trick, the PEP 669
filtering, the timing bookkeeping, and — importantly — what vein cannot tell
you.

## License

MIT
