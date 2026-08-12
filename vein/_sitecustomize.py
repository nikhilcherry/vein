"""Injected startup shim.

``site.py`` imports a module named ``sitecustomize`` if one is importable, and
by that point ``PYTHONPATH`` entries are already on ``sys.path``. Dropping a
copy of this file into a scratch directory at the front of ``PYTHONPATH`` is
therefore enough to trace an arbitrary command -- and every Python subprocess
it spawns -- without touching the target project at all.

The file is copied next to ``_vein_tracer.py`` by ``vein.runner``.
"""

import os
import sys

try:
    import _vein_tracer

    _vein_tracer.install()
except Exception:  # pragma: no cover - tracing must never break the program
    pass


def _chain():
    """Import any *other* sitecustomize the user already had installed."""
    here = os.path.dirname(os.path.abspath(__file__))
    saved = sys.path[:]
    try:
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
        del sys.modules["sitecustomize"]
        import sitecustomize  # noqa: F401
    except Exception:
        pass
    finally:
        sys.path[:] = saved


_chain()
