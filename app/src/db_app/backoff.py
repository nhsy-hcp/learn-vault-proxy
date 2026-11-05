"""Jittered exponential backoff."""

from __future__ import annotations

import random


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Delay before retry ``attempt`` (1-based): equal jitter on ``base * 2**(attempt-1)``."""
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    half = ceiling / 2
    return half + random.uniform(0, half)  # noqa: S311 - jitter, not cryptography
