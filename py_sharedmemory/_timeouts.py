"""Timeout arguments shared by both endpoints."""

import math
import threading


def normalize_timeout(timeout: float | None) -> float | None:
    """Return `None` for waits too long to schedule, such as `math.inf`; reject NaN.

    Lock and pipe timeouts overflow past `threading.TIMEOUT_MAX`, which is under 50 days
    on Windows, so longer waits become "no timeout".
    """
    if timeout is None:
        return None
    if math.isnan(timeout):
        raise ValueError("timeout must be a number of seconds, not NaN")
    return None if timeout > threading.TIMEOUT_MAX else timeout
