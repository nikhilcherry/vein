"""Allow ``python -m vein``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
