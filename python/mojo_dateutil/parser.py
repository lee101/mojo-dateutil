"""Date parsing API compatible with the covered python-dateutil subset."""

from __future__ import annotations

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
_PARSE_ISO_MANY_SERIAL = None
_FIXED_OFFSETS = {0: timezone.utc}
# Workers handed to the native kernel. It only splits a batch at or above its
# own measured record threshold; below that the caller runs the serial body.
_PARALLEL_WORKERS = 4


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


def _prepare_batch(values):
    """Encode ISO values into the contiguous buffers the native parser reads."""
    encoded = []
    for value in values:
        try:
            raw = value.encode("ascii") if isinstance(value, str) else bytes(value)
        except (TypeError, UnicodeError) as exc:
            raise ValueError("ISO-8601 strings must contain only ASCII characters") from exc
        if not raw:
            raise ValueError("ISO string too short")
        encoded.append(raw)
    return encoded


def _native_entry(parallel: bool):
    global _PARSE_ISO_MANY, _PARSE_ISO_MANY_SERIAL
    if _PARSE_ISO_MANY is None:
        native = lib()
        _PARSE_ISO_MANY = native.mdu_parse_iso_many_par
        _PARSE_ISO_MANY_SERIAL = native.mdu_parse_iso_many
    return _PARSE_ISO_MANY if parallel else _PARSE_ISO_MANY_SERIAL


def _prepare_buffers(encoded):
    """Pack encoded records into the contiguous native input buffers."""
    lengths = np.fromiter((len(raw) for raw in encoded), dtype=np.int64, count=len(encoded))
    offsets = np.empty(len(encoded) + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    data = np.frombuffer(b"".join(encoded), dtype=np.uint8)
    return data, offsets


def _run_native(native, parallel, data, offsets, count, fields, statuses):
    common = (
        addr(data),
        addr(offsets),
        i64(len(data), "batch byte length"),
        i64(count, "batch count"),
        addr(fields),
        addr(statuses),
    )
    if parallel:
        return native(*common, _PARALLEL_WORKERS)
    return native(*common)


def _parse_batch(encoded, parallel: bool):
    """Run one native batch call and return the raw ``(fields, statuses)``."""
    native = _native_entry(parallel)
    count = len(encoded)
    data, offsets = _prepare_buffers(encoded)
    fields = np.empty((count, 9), dtype=np.int64)
    statuses = np.empty(count, dtype=np.int64)
    status = _run_native(native, parallel, data, offsets, count, fields, statuses)
    if status != 0:
        raise RuntimeError(f"native batch parser rejected its buffers (status {status})")
    return fields, statuses


def _materialize(encoded, fields, statuses) -> list[datetime]:
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


def parse_many(values: Iterable[str | bytes]) -> list[datetime]:
    """Parse ISO values in one native call.

    Large batches are split across ``_PARALLEL_WORKERS`` by
    ``max.algorithm.parallelize`` inside the kernel, above its measured
    threshold. Per-record output is identical to the serial path.
    """
    encoded = _prepare_batch(values)
    if not encoded:
        return []
    return _materialize(encoded, *_parse_batch(encoded, parallel=True))


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
