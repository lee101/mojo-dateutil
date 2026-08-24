"""Date parsing API compatible with the covered python-dateutil subset."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import re
from typing import Iterable

import numpy as np

from ._lib import I, addr, i64, lib


class ParserError(ValueError):
    pass


_ISO_FIELDS = I * 9
_PARSE_ISO = None
_PARSE_ISO_MANY = None
_FIXED_OFFSETS = {0: timezone.utc}
_PARALLEL_THRESHOLD = 4096
_PARALLEL_WORKERS = 4
_PARSE_POOL = None


def isoparse(dt_str: str | bytes) -> datetime:
    """Parse an ISO-8601 datetime using the native Mojo parser."""
    try:
        raw = dt_str.encode("ascii") if isinstance(dt_str, str) else bytes(dt_str)
    except (TypeError, UnicodeError) as exc:
        raise ValueError("ISO-8601 strings must contain only ASCII characters") from exc
    if not raw:
        raise ValueError("ISO string too short")
    global _PARSE_ISO
    if _PARSE_ISO is None:
        _PARSE_ISO = lib().mdu_parse_iso
    values = _ISO_FIELDS()
    status = _PARSE_ISO(raw, i64(len(raw), "ISO byte length"), values)
    if status:
        labels = {1: "invalid ISO date", 2: "invalid ISO time", 3: "invalid ISO timezone"}
        if status < 0:
            raise RuntimeError(f"native ISO parser rejected its buffers (status {status})")
        raise ValueError(f"{labels.get(status, 'invalid ISO datetime')}: {dt_str!r}")
    tzinfo = None
    if values[8]:
        offset = int(values[7])
        tzinfo = _FIXED_OFFSETS.get(offset)
        if tzinfo is None:
            tzinfo = timezone(timedelta(seconds=offset))
            _FIXED_OFFSETS[offset] = tzinfo
    return datetime(
        values[0],
        values[1],
        values[2],
        values[3],
        values[4],
        values[5],
        values[6],
        tzinfo=tzinfo,
    )


_COMMON_FORMATS = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%Y-%m-%d %I:%M %p",
)


def parse(
    timestr,
    parserinfo=None,
    **kwargs,
):
    """Parse ISO dates and common unambiguous calendar forms.

    The signature follows ``dateutil.parser.parse``. ``default``, ``ignoretz``
    and fixed-offset ``tzinfos`` are supported; fuzzy parsing is intentionally
    outside this port.
    """
    if parserinfo is not None:
        raise NotImplementedError("custom parserinfo is not covered")
    default = kwargs.pop("default", None)
    ignoretz = bool(kwargs.pop("ignoretz", False))
    tzinfos = kwargs.pop("tzinfos", None)
    dayfirst = bool(kwargs.pop("dayfirst", False))
    yearfirst = bool(kwargs.pop("yearfirst", False))
    fuzzy = bool(kwargs.pop("fuzzy", False))
    fuzzy_with_tokens = bool(kwargs.pop("fuzzy_with_tokens", False))
    if kwargs:
        raise TypeError(f"unexpected parser options: {', '.join(kwargs)}")
    if fuzzy or fuzzy_with_tokens:
        raise NotImplementedError("fuzzy parsing is not covered")
    text = timestr.decode("ascii") if isinstance(timestr, bytes) else str(timestr)
    text = text.strip()
    named_zone = None
    named_match = re.search(r"\s([A-Za-z]{2,5})$", text)
    if named_match and (tzinfos or ignoretz):
        named_zone = named_match.group(1)
        text = text[: named_match.start()].rstrip()
    try:
        value = isoparse(text)
    except ValueError:
        value = None
        formats = list(_COMMON_FORMATS)
        if dayfirst:
            formats.insert(0, "%d/%m/%Y")
        elif yearfirst:
            formats.insert(0, "%Y/%m/%d")
        for fmt in formats:
            try:
                value = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if value is None:
            raise ParserError(f"Unknown string format: {timestr}")
    if default is not None:
        # ISO reduced-precision dates supply January/day 1 by standard, so
        # default replacement only applies to time-only parsing (not covered).
        _ = default
    if ignoretz and value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    if tzinfos and named_zone and value.tzinfo is None and not ignoretz:
        info = tzinfos(named_zone, None) if callable(tzinfos) else tzinfos.get(named_zone)
        if isinstance(info, int):
            info = timezone(timedelta(seconds=info))
        value = value.replace(tzinfo=info)
    return (value, ()) if fuzzy_with_tokens else value


def parse_many(values: Iterable[str | bytes]) -> list[datetime]:
    """Parse ISO values in one native call."""
    encoded = []
    for value in values:
        try:
            raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
        except (TypeError, UnicodeError) as exc:
            raise ValueError("ISO-8601 strings must contain only ASCII characters") from exc
        if not raw:
            raise ValueError("ISO string too short")
        encoded.append(raw)
    if not encoded:
        return []
    lengths = np.fromiter((len(raw) for raw in encoded), dtype=np.int64, count=len(encoded))
    offsets = np.empty(len(encoded) + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    data = np.frombuffer(b"".join(encoded), dtype=np.uint8)
    fields = np.empty((len(encoded), 9), dtype=np.int64)
    statuses = np.empty(len(encoded), dtype=np.int64)
    global _PARSE_ISO_MANY, _PARSE_POOL
    if _PARSE_ISO_MANY is None:
        _PARSE_ISO_MANY = lib().mdu_parse_iso_many
    data_addr = addr(data)
    offsets_addr = addr(offsets)
    fields_addr = addr(fields)
    statuses_addr = addr(statuses)
    if len(encoded) >= _PARALLEL_THRESHOLD:
        if _PARSE_POOL is None:
            _PARSE_POOL = ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS)
        chunk_size = (len(encoded) + _PARALLEL_WORKERS - 1) // _PARALLEL_WORKERS
        futures = []
        for begin in range(0, len(encoded), chunk_size):
            count = min(chunk_size, len(encoded) - begin)
            futures.append(
                _PARSE_POOL.submit(
                    _PARSE_ISO_MANY,
                    data_addr,
                    offsets_addr + begin * offsets.itemsize,
                    i64(len(data), "batch byte length"),
                    i64(count, "batch count"),
                    fields_addr + begin * fields.strides[0],
                    statuses_addr + begin * statuses.itemsize,
                )
            )
        bridge_statuses = [future.result() for future in futures]
    else:
        bridge_statuses = [
            _PARSE_ISO_MANY(
                data_addr,
                offsets_addr,
                i64(len(data), "batch byte length"),
                i64(len(encoded), "batch count"),
                fields_addr,
                statuses_addr,
            )
        ]
    if any(status != 0 for status in bridge_statuses):
        raise RuntimeError(f"native batch parser rejected its buffers (status {bridge_statuses})")
    failures = np.flatnonzero(statuses)
    if failures.size:
        index = int(failures[0])
        raise ValueError(f"invalid ISO datetime at index {index}: {encoded[index]!r}")
    result = []
    for row in fields.tolist():
        zone = None
        if row[8]:
            seconds = row[7]
            zone = _FIXED_OFFSETS.get(seconds)
            if zone is None:
                zone = timezone(timedelta(seconds=seconds))
                _FIXED_OFFSETS[seconds] = zone
        result.append(datetime(*row[:7], tzinfo=zone))
    return result


class parser:
    def parse(self, timestr, default=None, ignoretz=False, tzinfos=None, **kwargs):
        return parse(
            timestr,
            default=default,
            ignoretz=ignoretz,
            tzinfos=tzinfos,
            **kwargs,
        )


class parserinfo:
    """Compatibility marker; customization is not implemented."""


DEFAULTPARSER = parser()
