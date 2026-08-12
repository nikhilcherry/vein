"""Run the toy shop: `python -m examples.shop.main [--fast]`."""

import sys

from . import cart

ITEMS = [
    {"qty": 12, "unit": 3.5},
    {"qty": 2, "unit": 19.0},
    {"qty": 7, "unit": 1.25},
]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    fast = "--fast" in argv
    total = cart.checkout(ITEMS, tier="gold", fast=fast)
    if not fast:
        print("audit:", cart.audit(ITEMS))
    print(f"total: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
