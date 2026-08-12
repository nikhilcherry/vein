# How vein works

## Getting code into the process

`vein run` never asks you to modify the program under test. It writes two
files into a scratch directory and puts that directory at the front of
`PYTHONPATH`:

```
.vein/tmp/<run>-<pid>/inject/
  sitecustomize.py    imported automatically by CPython's site.py at startup
  _vein_tracer.py     the recorder, standard library only
```

CPython builds `sys.path` from `PYTHONPATH` *before* `site.py` runs, and
`site.py` then does `import sitecustomize`. That ordering is the entire trick.
Two consequences follow:

* Any Python process started with that environment is traced, including
  subprocesses the command spawns. Their recordings are merged into one graph.
* A `sitecustomize` the user already had is still imported — ours removes its
  own directory from `sys.path` and re-imports, so we chain rather than shadow.

## Recording

On CPython 3.12+ the recorder uses `sys.monitoring` (PEP 669) with three
events: `PY_START`, `PY_RETURN`, `PY_UNWIND`.

The important detail is filtering. `PY_START` returns
`sys.monitoring.DISABLE` for any code object outside the project root, and
CPython then stops delivering events for that code object entirely. Third-party
and stdlib frames therefore cost one callback each, ever — not one per call.

`PY_UNWIND` is not a disableable event; returning `DISABLE` from it raises
`ValueError` and detaches the callback mid-run. It returns `None` instead.

Older interpreters fall back to `sys.setprofile` plus `threading.setprofile`.

## Timing

Each thread keeps a stack of `[function_id, start_ns, children_ns]`. On return:

* **self** += elapsed − children_ns
* the parent's children_ns += elapsed
* **cumulative** += elapsed, but only when the outermost frame of a recursive
  chain returns, so recursion is not counted many times over

If a frame we were not watching unwinds in between, the stack is rewound to the
closest matching entry rather than left corrupt.

## Getting data back out

Every process dumps its slice on exit via `atexit`, written to a temp file and
`os.replace`d into place so a reader never sees a partial file.

`atexit` does not run on `SIGTERM`, so the recorder also installs handlers for
`SIGTERM`/`SIGHUP`/`SIGQUIT` that flush first and then chain to whatever
handler the program had. Without this, tracing a server — the thing you most
want a call graph of — would produce nothing when you stop it. The dump is
idempotent, since both paths can fire.

Nothing in the recorder may raise into the traced program: the dump is wrapped
in a bare `except Exception`. A failed recording is acceptable; a crashed
program is not.

## Identity

A function is identified across runs by `(relative file path, qualname)` —
deliberately *not* including the definition line. Moving a function within its
file is not a behaviour change, and a diff that reported it as one would be
noise. Edges are identified by the pair of those labels.

Paths are stored relative to the project root, so a recording made in CI is
comparable with one made on a laptop.

## Overhead

Measured on this repository (`bench/overhead.sh`):

| workload | baseline | traced | traced, `--no-time` |
|---|---|---|---|
| 800k calls of a 1-line function | 0.02s | 0.81s | 0.62s |
| this project's pytest suite | 1.01s | 1.20s | — |

The first row is the pathological case and the honest worst case: functions
small enough that two monitoring callbacks dwarf the work. The second is what
normal code looks like — roughly 20%. Use `--no-time` when you only care about
structure (which is all `dead` and `diff --structure-only` need).

## What it does not do

* Only Python frames are recorded. C extensions appear as time inside their
  Python caller.
* Coverage is per *function*, not per line. `vein dead` answers "did this ever
  run", not "which branches ran".
* Generators and coroutines are recorded at resume granularity, so their self
  time is spread across resumes.
* A recording proves a function *did* run. It can never prove one is
  unreachable — only that the entry points you recorded did not reach it.
