"""The owner's wall clock. Quiet hours and other decisions made on the laptop (a Linux box that may run in UTC) must follow
where the OWNER is, not where the host thinks it is. Default America/New_York; JARVIS_TZ overrides it with an IANA name.

zoneinfo needs the tz database, which a Windows Python often lacks (no `tzdata` package), so for the default zone there is a
built-in US rule (DST from the second Sunday of March 02:00 to the first Sunday of November 02:00). An unknown zone name
falls back to the host's own local time, which is what the code did before this module existed.
"""
import os
from datetime import datetime, timedelta, timezone

DEFAULT_TZ = "America/New_York"


def tz_name() -> str:
    return (os.getenv("JARVIS_TZ") or "").strip() or DEFAULT_TZ


def _nth_sunday(year: int, month: int, n: int) -> datetime:
    first = datetime(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _us_eastern(utc: datetime) -> datetime:
    """Naive Eastern wall time for a naive UTC datetime."""
    year = utc.year
    dst_start = _nth_sunday(year, 3, 2) + timedelta(hours=2 + 5)      # 02:00 EST == 07:00 UTC
    dst_end = _nth_sunday(year, 11, 1) + timedelta(hours=2 + 4)       # 02:00 EDT == 06:00 UTC
    return utc - timedelta(hours=4 if dst_start <= utc < dst_end else 5)


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database, or a bad name
        return None


def now() -> datetime:
    """Naive datetime in the owner's timezone (naive, like datetime.now(), so callers compare it the same way)."""
    name = tz_name()
    zone = _zone(name)
    if zone is not None:
        return datetime.now(zone).replace(tzinfo=None)
    if name == DEFAULT_TZ:
        return _us_eastern(datetime.now(timezone.utc).replace(tzinfo=None))
    return datetime.now()
