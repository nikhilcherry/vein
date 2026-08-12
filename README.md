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

## Status

Working today: `run`, `list`, `show`, `dead`.
In progress: `diff` (what changed at runtime between two runs) and a
self-contained HTML report.

## License

MIT
