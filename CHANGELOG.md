# Changelog

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
