"""Cart assembly, with a fast path that only some runs take."""

from . import pricing


def line_total(item):
    total = pricing.base_price(item)
    return pricing.bulk_discount(total, item["qty"])


def checkout(items, tier="none", fast=False):
    if fast:
        total = sum(i["qty"] * i["unit"] for i in items)
    else:
        total = sum(line_total(i) for i in items)
        total = pricing.loyalty_discount(total, tier)
    return round(pricing.tax(total), 2)


def audit(items):
    """Only used by the slow path in main."""
    return {"lines": len(items), "units": sum(i["qty"] for i in items)}
