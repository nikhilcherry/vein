"""A deliberately call-heavy workload: the worst case for a call tracer."""

import sys
import time


def leaf(n):
    return n & 7


def middle(n):
    return leaf(n) + leaf(n + 1) + leaf(n + 2)


def top(n):
    total = 0
    for i in range(n):
        total += middle(i)
    return total


if __name__ == "__main__":
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    start = time.perf_counter()
    top(rounds)
    print(f"{time.perf_counter() - start:.4f}")
