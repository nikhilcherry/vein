# Changelog

## Unreleased

### Fixed
- **Forked child processes are recorded.** `sitecustomize` on `PYTHONPATH` only
  runs on *exec*, so an exec'd subprocess was traced but a **fork** was not: the
  child inherited a live tracer, recorded faithfully, and then left through
  `os._exit`, which by design skips `atexit`. Everything it observed was thrown
  away. `multiprocessing.Pool`, `multiprocessing.Process` and raw `os.fork` all
  lost 100% of their children's work, so a function called only inside a worker
  was reported by `vein dead` as never having run — the one claim this tool
  exists to get right. Children now re-arm on fork and publish before exiting.
- **A worker no longer dies part-way through writing its recording.** A pool
  worker is sent SIGTERM twice — once by `Pool.terminate()`, once by
  multiprocessing's exit function. The second delivery re-entered the handler,
  found the write already flagged as done, and killed the process while the file
  was still being written, stranding it as a `.tmp`. Fatal signals are now held
  off until the recording is published.
- **`vein run` waits for children that are still writing.** `subprocess.run`
  returns when the *direct* child exits, and the parts directory was collected
  at that moment — before forked grandchildren had finished. The same command
  could report a different set of executed functions run to run. Each traced
  process now leaves a marker until its recording lands, so the wait is on
  evidence rather than a fixed delay, and the single-process case is unaffected.

  Measured on a `multiprocessing.Pool` program, functions that only run in a
  worker went from falsely reported dead in ~50% of runs to 0 of 25.

### Added
- Runs record `lost_processes`, and `vein dead` warns when it is non-zero. A
  worker killed outright during pool teardown runs no Python on its way out and
  so can never report; rather than fold that silently into a dead list, the
  report now says the recording is incomplete. Also in `--json` output.

## 0.1.0 — 2026-08-12

First release.

### Commands
- `vein run -- CMD` — record what a command actually executes, including every
  Python subprocess it spawns.
- `vein show` — rank functions by self time, cumulative time, or call count.
- `vein list` — recorded runs, newest first.
- `vein dead` — functions defined in the project that no recording ever
  executed. `--strict` makes it a CI gate.
- `vein diff BEFORE AFTER` — functions and call paths unique to each run, plus
  call-count deltas. `--strict` gates on "execution did not change".
- `vein report` — one self-contained HTML page, no external assets.
- `vein paths FUNCTION` — how a function was reached, edge counts included.

### Implementation
- `sitecustomize` on `PYTHONPATH` as the injection point, so the traced project
  is never modified and subprocesses are traced for free.
- `sys.monitoring` (PEP 669) on 3.12+, returning `DISABLE` for code outside the
  project so non-project frames cost one callback ever; `sys.setprofile`
  fallback below 3.12.
- Recordings flush on `SIGTERM`/`SIGHUP`/`SIGQUIT` as well as normal exit, so
  long-running processes can be traced and then stopped.
- Zero runtime dependencies.

### Known limits
- Function-level, not line-level. C extensions are attributed to their Python
  caller. See `docs/DESIGN.md`.
