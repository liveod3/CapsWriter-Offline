"""Failure policy shared by the result sender and recognition worker."""

import math


SCHEDULING_RESUME_GRACE = 5.0


class ResultDeliveryError(RuntimeError):
    """The shared recognition channel cannot safely continue; restart is required."""


def positive_timeout(config, name, default):
    value = getattr(config, name, default)
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) and value > 0 else default
