"""Toy pricing rules used by the vein examples."""


def base_price(item):
    return item["qty"] * item["unit"]


def bulk_discount(total, qty):
    if qty >= 10:
        return total * 0.9
    return total


def loyalty_discount(total, tier):
    if tier == "gold":
        return total * 0.85
    if tier == "silver":
        return total * 0.95
    return total


def legacy_coupon(total, code):
    """Never called any more — vein dead should find this."""
    return total - 5 if code else total


def tax(total, rate=0.18):
    return total * (1 + rate)
