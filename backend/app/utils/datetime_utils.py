"""Shared datetime utilities for ARIA backend."""

from datetime import datetime

# Off-hours window: 6 PM (18:00) to 8 AM (08:00) UTC, or weekends
_OFF_HOURS_START: int = 18
_OFF_HOURS_END: int = 8


def is_off_hours(dt: datetime) -> bool:
    """Return True if dt falls between 6 PM–8 AM UTC or on a weekend.

    Args:
        dt: Datetime to evaluate (should be UTC-aware or UTC-naive UTC time).

    Returns:
        True if the datetime is outside standard working hours.
    """
    hour = dt.hour
    weekday = dt.weekday()  # 5=Saturday, 6=Sunday
    if weekday >= 5:
        return True
    return hour >= _OFF_HOURS_START or hour < _OFF_HOURS_END
