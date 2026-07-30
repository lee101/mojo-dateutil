"""Small dateutil.tz-compatible fixed-offset subset."""

from __future__ import annotations

from datetime import timedelta, timezone, tzinfo

UTC = timezone.utc


def tzutc() -> tzinfo:
    return UTC


def tzoffset(name: str | None, offset: int | timedelta) -> tzinfo:
    seconds = int(offset.total_seconds()) if isinstance(offset, timedelta) else int(offset)
    return timezone(timedelta(seconds=seconds), name=name)
