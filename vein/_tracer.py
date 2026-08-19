"""Standalone in-process execution recorder.

This module is copied verbatim into the traced process's import path, so it
must not import anything from the ``vein`` package (or anything outside the
standard library). It is the only code that runs inside the user's program.

Design notes
------------
* On CPython 3.12+ we use :mod:`sys.monitoring` (PEP 669). The key trick is
  returning ``sys.monitoring.DISABLE`` from ``PY_START`` for code objects we
  do not care about: CPython then stops firing events for that code object
  entirely, so third-party and stdlib frames cost nothing after first sight.
* On older interpreters we fall back to ``sys.setprofile`` plus
  ``threading.setprofile`` for threads created later.
* Timing follows the usual profiler bookkeeping: a frame's *self* time is its
  elapsed time minus the elapsed time of its direct children, and *cumulative*
  time is only credited when the outermost frame of a recursive chain returns.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time

_MISSING = object()

# Comprehension frames are noise in a call graph; lambdas are real user code.
_SKIP_QUALNAME_PARTS = ("<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>")

_DEFAULT_EXCLUDES = (
    os.sep + ".vein" + os.sep,
    os.sep + "site-packages" + os.sep,
    os.sep + "dist-packages" + os.sep,
    os.sep + ".venv" + os.sep,
    os.sep + "venv" + os.sep,
    os.sep + ".tox" + os.sep,
    os.sep + "node_modules" + os.sep,
    os.sep + ".git" + os.sep,
)


class Recorder:
    """Accumulates the runtime call graph of the current process."""

    def __init__(self, roots, excludes=(), timing=True, comprehensions=False):
        self.roots = [os.path.realpath(r) for r in roots if r]
        self.excludes = tuple(excludes) + _DEFAULT_EXCLUDES
        self.timing = timing
        self.comprehensions = comprehensions

        self.funcs = []  # [file, line, qualname, calls, self_ns, cum_ns, depth]
        self.by_code = {}  # code object -> fid | None
        self.by_key = {}  # (file, line, qualname) -> fid
        self.edges = {}  # (caller_fid, callee_fid) -> count
        self.threads = set()
        self.max_depth = 0

        self._local = threading.local()
        # The injected shim lives beside this module; never trace ourselves.
        self._self_dir = os.path.dirname(os.path.realpath(__file__)) + os.sep
        self.started_ns = time.perf_counter_ns()

    # -- frame stacks -----------------------------------------------------

    def _stack(self):
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
            self.threads.add(threading.get_ident())
        return stack

    # -- filtering --------------------------------------------------------

    def _resolve(self, code):
        """Map a code object to a function id, or ``None`` if uninteresting."""
        filename = code.co_filename
        if not filename or filename[0] == "<":
            return None
        qualname = getattr(code, "co_qualname", None) or code.co_name
        if not self.comprehensions:
            for part in _SKIP_QUALNAME_PARTS:
                if part in qualname:
                    return None
        try:
            real = os.path.realpath(filename)
        except OSError:  # pragma: no cover - exotic filesystems
            return None
        if real.startswith(self._self_dir):
            return None
        for bad in self.excludes:
            if bad in real:
                return None
        for root in self.roots:
            if real == root or real.startswith(root + os.sep):
                break
        else:
            return None

        key = (real, code.co_firstlineno, qualname)
        fid = self.by_key.get(key)
        if fid is None:
            fid = len(self.funcs)
            self.by_key[key] = fid
            self.funcs.append([real, code.co_firstlineno, qualname, 0, 0, 0, 0])
        return fid

    def _fid(self, code):
        fid = self.by_code.get(code, _MISSING)
        if fid is _MISSING:
            fid = self._resolve(code)
            self.by_code[code] = fid
        return fid

    # -- event handling ---------------------------------------------------

    def push(self, fid):
        stack = self._stack()
        rec = self.funcs[fid]
        rec[3] += 1
        rec[6] += 1
        caller = stack[-1][0] if stack else -1  # -1 == process/thread entry
        edge = (caller, fid)
        self.edges[edge] = self.edges.get(edge, 0) + 1
        stack.append([fid, time.perf_counter_ns() if self.timing else 0, 0])
        if len(stack) > self.max_depth:
            self.max_depth = len(stack)

    def pop(self, fid):
        stack = self._stack()
        if not stack:
            return
        if stack[-1][0] != fid:
            # A frame we were not watching unwound in between; rewind to the
            # closest matching entry rather than corrupting the whole stack.
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == fid:
                    break
            else:
                return
            while len(stack) > i + 1:
                self._close(stack.pop(), stack)
        self._close(stack.pop(), stack)

    def _close(self, frame, stack):
        fid, start, child_ns = frame
        rec = self.funcs[fid]
        rec[6] -= 1
        if self.timing:
            elapsed = time.perf_counter_ns() - start
            rec[4] += elapsed - child_ns
            if rec[6] == 0:  # outermost frame of a (possibly recursive) chain
                rec[5] += elapsed
            if stack:
                stack[-1][2] += elapsed

    # -- forking ----------------------------------------------------------

    def reset(self):
        """Forget everything recorded so far.

        A forked child inherits the parent's counts. They are the parent's to
        report -- it writes its own recording -- so a child that kept them
        would make every merged call count double.
        """
        self.funcs = []
        self.by_code = {}
        self.by_key = {}
        self.edges = {}
        self.threads = set()
        self.max_depth = 0
        self._local = threading.local()
        self.started_ns = time.perf_counter_ns()

    # -- serialisation ----------------------------------------------------

    def snapshot(self):
        return {
            "pid": os.getpid(),
            "argv": list(sys.argv),
            "executable": sys.executable,
            "duration_ns": time.perf_counter_ns() - self.started_ns,
            "threads": len(self.threads) or 1,
            "max_depth": self.max_depth,
            "timing": self.timing,
            "functions": [
                {
                    "file": f[0],
                    "line": f[1],
                    "qualname": f[2],
                    "calls": f[3],
                    "self_ns": f[4],
                    "cum_ns": f[5],
                }
                for f in self.funcs
            ],
            "edges": [[a, b, c] for (a, b), c in self.edges.items()],
        }


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class _MonitoringBackend:
    """PEP 669 backend (CPython 3.12+)."""

    name = "sys.monitoring"

    def __init__(self, recorder):
        self.rec = recorder
        self.mon = sys.monitoring
        self.tool_id = self.mon.PROFILER_ID

    def start(self):
        mon, rec = self.mon, self.rec
        events = mon.events
        DISABLE = mon.DISABLE

        def on_start(code, offset):
            fid = rec._fid(code)
            if fid is None:
                return DISABLE
            rec.push(fid)

        def on_return(code, offset, retval):
            fid = rec.by_code.get(code)
            if fid is None:
                return DISABLE
            rec.pop(fid)

        def on_unwind(code, offset, exc):
            # PY_UNWIND is not a disableable event: returning DISABLE here
            # raises ValueError and detaches the callback mid-run.
            fid = rec.by_code.get(code)
            if fid is not None:
                rec.pop(fid)

        try:
            mon.use_tool_id(self.tool_id, "vein")
        except ValueError:  # pragma: no cover - another profiler is attached
            self.tool_id = None
            raise RuntimeError("another profiler already owns sys.monitoring")
        mon.register_callback(self.tool_id, events.PY_START, on_start)
        mon.register_callback(self.tool_id, events.PY_RETURN, on_return)
        mon.register_callback(self.tool_id, events.PY_UNWIND, on_unwind)
        mon.set_events(
            self.tool_id, events.PY_START | events.PY_RETURN | events.PY_UNWIND
        )

    def stop(self):
        if self.tool_id is None:
            return
        try:
            self.mon.set_events(self.tool_id, 0)
            self.mon.free_tool_id(self.tool_id)
        except Exception:  # pragma: no cover - interpreter tearing down
            pass


class _ProfileBackend:
    """``sys.setprofile`` backend for interpreters without PEP 669."""

    name = "sys.setprofile"

    def __init__(self, recorder):
        self.rec = recorder

    def _hook(self, frame, event, arg):
        rec = self.rec
        if event == "call":
            fid = rec._fid(frame.f_code)
            if fid is not None:
                rec.push(fid)
        elif event == "return":
            fid = rec.by_code.get(frame.f_code)
            if fid is not None:
                rec.pop(fid)

    def start(self):
        threading.setprofile(self._hook)
        sys.setprofile(self._hook)

    def stop(self):
        sys.setprofile(None)
        threading.setprofile(None)


def _make_backend(recorder):
    if hasattr(sys, "monitoring") and sys.version_info >= (3, 12):
        try:
            backend = _MonitoringBackend(recorder)
            backend.start()
            return backend
        except Exception:
            pass
    backend = _ProfileBackend(recorder)
    backend.start()
    return backend


# ---------------------------------------------------------------------------
# Entry point used by the injected sitecustomize
# ---------------------------------------------------------------------------

_ACTIVE = None

#: Captured before any wrapping, so nested forks cannot build a chain.
_REAL_OS_EXIT = os._exit


def _fatal_signals():
    """The signals that would kill us part-way through publishing a recording."""
    import signal

    return {
        sig
        for sig in (
            getattr(signal, name, None) for name in ("SIGTERM", "SIGHUP", "SIGQUIT")
        )
        if sig is not None
    }


def install():
    """Start recording, based on the ``VEIN_*`` environment variables."""
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    out_dir = os.environ.get("VEIN_OUT_DIR")
    if not out_dir:
        return None

    roots = [p for p in os.environ.get("VEIN_ROOTS", "").split(os.pathsep) if p]
    excludes = [p for p in os.environ.get("VEIN_EXCLUDES", "").split(os.pathsep) if p]
    timing = os.environ.get("VEIN_TIMING", "1") != "0"
    comps = os.environ.get("VEIN_COMPREHENSIONS", "0") == "1"

    _mark_live(out_dir)
    recorder = Recorder(roots, excludes, timing=timing, comprehensions=comps)
    backend = _make_backend(recorder)
    _ACTIVE = (recorder, backend)

    dumped = []

    def dump():
        # atexit, the signal handler and the os._exit wrapper can all fire;
        # write exactly once.
        if dumped:
            return
        dumped.append(True)

        # Hold off the fatal signals until the part file is published. A pool
        # worker gets SIGTERM twice -- once from Pool.terminate(), once from
        # multiprocessing's exit function -- and the second delivery re-enters
        # the handler, finds this flag already set, and kills the process while
        # the write above is still in flight. That stranded the recording as a
        # .tmp file and lost the worker's functions.
        import signal

        blocked = False
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, _fatal_signals())
            blocked = True
        except (AttributeError, ValueError, OSError):  # pragma: no cover
            pass

        backend.stop()
        try:
            data = recorder.snapshot()
            data["backend"] = backend.name
            os.makedirs(out_dir, exist_ok=True)
            name = "part-%d-%d.json" % (os.getpid(), time.time_ns())
            path = os.path.join(out_dir, name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
        except Exception:  # pragma: no cover - never break the traced program
            pass
        finally:
            _clear_live(out_dir)
            if blocked:
                try:
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, _fatal_signals())
                except (ValueError, OSError):  # pragma: no cover
                    pass

    atexit.register(dump)
    _install_signal_dump(dump)
    _install_fork_dump(recorder, dump, dumped, out_dir)
    return _ACTIVE


def _live_marker(out_dir, pid=None):
    return os.path.join(out_dir, "live-%d" % (pid if pid else os.getpid()))


def _mark_live(out_dir):
    """Announce that this process still owes a recording.

    The runner waits on these instead of guessing how long a worker needs.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(_live_marker(out_dir), "w"):
            pass
    except OSError:  # pragma: no cover - never break the traced program
        pass


def _clear_live(out_dir):
    try:
        os.unlink(_live_marker(out_dir))
    except OSError:  # pragma: no cover
        pass


def _install_fork_dump(recorder, dump, dumped, out_dir):
    """Make forked children record their own work, and write it before dying.

    The shim reaches a new process through ``PYTHONPATH`` and ``sitecustomize``,
    which only run on *exec*. A forked child inherits an already-installed
    tracer instead, so it traces faithfully -- and then throws the result away,
    because multiprocessing workers, ``Process`` targets and raw ``os.fork``
    users all leave through ``os._exit``, which by design skips ``atexit``.

    The visible symptom was that a function called only inside a worker was
    reported as never having run, which is precisely the claim this tool exists
    to get right.
    """
    if not hasattr(os, "register_at_fork"):  # pragma: no cover - non-POSIX
        return

    def after_in_child():
        recorder.reset()
        del dumped[:]  # the parent may have dumped; this child still owes one
        _mark_live(out_dir)
        os._exit = _child_exit

    def _child_exit(status):
        dump()
        _REAL_OS_EXIT(status)

    os.register_at_fork(after_in_child=after_in_child)


def _install_signal_dump(dump):
    """Also write the recording when the process is terminated by a signal.

    ``atexit`` does not run on SIGTERM, so without this a traced server -- the
    exact thing you most want a call graph of -- would produce nothing when
    stopped. Any handler the program had already installed is still called.
    """
    import signal

    names = ("SIGTERM", "SIGHUP", "SIGQUIT")
    for name in names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            previous = signal.getsignal(sig)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            continue

        def handler(signum, frame, previous=previous):
            dump()
            if callable(previous):
                return previous(signum, frame)
            if previous == signal.SIG_IGN:
                return None
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):  # pragma: no cover - not the main thread
            pass
