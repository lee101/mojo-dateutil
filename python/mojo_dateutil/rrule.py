"""Recurrence rules with native bulk expansion."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import heapq
import re
from typing import Iterable, Iterator

import numpy as np

from ._lib import addr, i64, lib
from .parser import isoparse

YEARLY, MONTHLY, WEEKLY, DAILY, HOURLY, MINUTELY, SECONDLY = range(7)


class weekday:
    __slots__ = ("weekday", "n")

    def __init__(self, wkday: int, n: int | None = None):
        self.weekday = wkday
        self.n = n

    def __call__(self, n: int):
        if n == 0:
            raise ValueError("weekday ordinal must not be zero")
        return self.__class__(self.weekday, n)

    def __eq__(self, other):
        return isinstance(other, weekday) and (self.weekday, self.n) == (other.weekday, other.n)

    def __hash__(self):
        return hash((self.weekday, self.n))

    def __repr__(self):
        names = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
        return names[self.weekday] if self.n is None else f"{names[self.weekday]}({self.n:+d})"


MO, TU, WE, TH, FR, SA, SU = (weekday(i) for i in range(7))


def _items(value):
    if value is None:
        return ()
    if isinstance(value, (int, weekday)):
        return (value,)
    return tuple(value)


def _mask(values, lower: int, upper: int) -> int:
    result = 0
    for value in _items(values):
        value = int(value)
        if not lower <= value <= upper:
            raise ValueError(f"value {value} is outside [{lower}, {upper}]")
        result |= 1 << value
    return result


class rrule:
    def __init__(
        self,
        freq,
        dtstart=None,
        interval=1,
        wkst=None,
        count=None,
        until=None,
        bysetpos=None,
        bymonth=None,
        bymonthday=None,
        byyearday=None,
        byeaster=None,
        byweekno=None,
        byweekday=None,
        byhour=None,
        byminute=None,
        bysecond=None,
        cache=False,
    ):
        if freq not in range(7):
            raise ValueError("invalid frequency")
        if interval < 1:
            raise ValueError("interval must be greater than 0")
        if count is not None and count < 0:
            raise ValueError("count must be non-negative")
        unsupported = {
            "bysetpos": bysetpos,
            "byyearday": byyearday,
            "byeaster": byeaster,
            "byweekno": byweekno,
        }
        used = [name for name, value in unsupported.items() if value is not None]
        if used:
            raise NotImplementedError(f"unsupported recurrence clauses: {', '.join(used)}")
        self._freq = int(freq)
        self._dtstart = dtstart or datetime.now().replace(microsecond=0)
        if isinstance(self._dtstart, date) and not isinstance(self._dtstart, datetime):
            self._dtstart = datetime.combine(self._dtstart, time())
        self._dtstart = self._dtstart.replace(microsecond=0)
        self._interval = int(interval)
        self._count = count
        self._until = until
        if isinstance(until, date) and not isinstance(until, datetime):
            self._until = datetime.combine(until, time.max)
        if self._until is not None:
            aware_start = self._dtstart.tzinfo is not None
            aware_until = self._until.tzinfo is not None
            if aware_start != aware_until:
                raise ValueError("RRULE UNTIL values must have the same timezone awareness as DTSTART")
            if aware_start:
                self._until = self._until.astimezone(self._dtstart.tzinfo)
        self._wkst = int(wkst.weekday if isinstance(wkst, weekday) else (0 if wkst is None else wkst))
        if not 0 <= self._wkst <= 6:
            raise ValueError("wkst must be between 0 and 6")
        self._cache_enabled = bool(cache)
        self._cache: list[datetime] | None = None

        month_values = _items(bymonth)
        self._month_mask = _mask(month_values, 1, 12)
        monthdays = _items(bymonthday)
        self._monthday_pos = 0
        self._monthday_neg = 0
        for item in monthdays:
            item = int(item)
            if item == 0 or not -31 <= item <= 31:
                raise ValueError("bymonthday must be in 1..31 or -31..-1")
            if item > 0:
                self._monthday_pos |= 1 << (item - 1)
            else:
                self._monthday_neg |= 1 << (-item - 1)

        self._weekday_mask = 0
        nth = []
        for item in _items(byweekday):
            item = item if isinstance(item, weekday) else weekday(int(item))
            if item.n is None:
                self._weekday_mask |= 1 << item.weekday
            else:
                if not -5 <= item.n <= 5 or item.n == 0:
                    raise ValueError("weekday ordinal must be between -5 and 5")
                nth.extend((item.weekday, item.n))
        if nth and self._freq not in (MONTHLY, YEARLY):
            raise ValueError("ordinal weekdays are only valid for MONTHLY and YEARLY")
        if nth and self._freq == YEARLY and not month_values:
            raise NotImplementedError("ordinal BYDAY for YEARLY requires BYMONTH in this port")
        self._nth = np.array(nth or [0, 0], dtype=np.int64)
        self._nth_n = len(nth) // 2

        # RFC expansion defaults depend on frequency.
        if self._freq == YEARLY and not month_values and not monthdays and byweekday is None:
            self._month_mask = 1 << self._dtstart.month
        if self._freq in (YEARLY, MONTHLY) and not monthdays and byweekday is None:
            self._monthday_pos = 1 << (self._dtstart.day - 1)
        if self._freq == WEEKLY and byweekday is None:
            self._weekday_mask = 1 << self._dtstart.weekday()

        self._hour_mask = _mask(byhour, 0, 23)
        self._minute_mask = _mask(byminute, 0, 59)
        self._second_mask = _mask(bysecond, 0, 59)
        self._empty_time_filter = any(
            value is not None and not _items(value) for value in (byhour, byminute, bysecond)
        )
        if self._freq < HOURLY and byhour is None:
            self._hour_mask = 1 << self._dtstart.hour
        if self._freq < MINUTELY and byminute is None:
            self._minute_mask = 1 << self._dtstart.minute
        if self._freq < SECONDLY and bysecond is None:
            self._second_mask = 1 << self._dtstart.second
        time_masks = (self._hour_mask, self._minute_mask, self._second_mask)
        if all(mask and mask & (mask - 1) == 0 for mask in time_masks):
            hour, minute, second = (mask.bit_length() - 1 for mask in time_masks)
            self._fixed_delta = timedelta(hours=hour, minutes=minute, seconds=second)
        else:
            self._fixed_delta = None

    def _generate(self, skip: int, capacity: int) -> list[datetime]:
        if self._empty_time_filter or (self._count is not None and skip >= self._count):
            return []
        until = self._until or datetime.max.replace(tzinfo=self._dtstart.tzinfo)
        ordinals = np.empty(capacity, dtype=np.int64)
        seconds = np.empty(capacity, dtype=np.int64)
        start_seconds = self._dtstart.hour * 3600 + self._dtstart.minute * 60 + self._dtstart.second
        until_seconds = until.hour * 3600 + until.minute * 60 + until.second
        native_args = (
            self._freq,
            self._dtstart.toordinal(),
            start_seconds,
            self._interval,
            -1 if self._count is None else self._count,
            until.toordinal(),
            until_seconds,
            self._wkst,
            self._month_mask,
            self._monthday_pos,
            self._monthday_neg,
            self._weekday_mask,
            addr(self._nth),
            self._nth_n,
            self._hour_mask,
            self._minute_mask,
            self._second_mask,
            skip,
            capacity,
            addr(ordinals),
            addr(seconds),
        )
        n = lib().mdu_rrule_generate(
            *(i64(value, f"recurrence argument {index}") for index, value in enumerate(native_args))
        )
        if n < 0:
            raise RuntimeError(f"native recurrence generator rejected its buffers (status {n})")
        if n > capacity:
            raise RuntimeError(
                f"native recurrence generator returned {n} values for capacity {capacity}"
            )
        zone = self._dtstart.tzinfo
        if zone is None:
            if self._fixed_delta is not None:
                return [
                    datetime.fromordinal(int(day)) + self._fixed_delta
                    for day in ordinals[:n]
                ]
            return [
                datetime.fromordinal(int(day)) + timedelta(seconds=int(sec))
                for day, sec in zip(ordinals[:n], seconds[:n])
            ]
        return [
            datetime.combine(date.fromordinal(int(day)), time(), tzinfo=zone)
            + timedelta(seconds=int(sec))
            for day, sec in zip(ordinals[:n], seconds[:n])
        ]

    def __iter__(self) -> Iterator[datetime]:
        if self._cache is not None:
            return iter(self._cache)

        def generate():
            if self._count is not None:
                values = self._generate(0, self._count)
                if self._cache_enabled:
                    self._cache = values
                yield from values
                return
            skip = 0
            chunk_size = 4096
            cached = [] if self._cache_enabled else None
            while True:
                chunk = self._generate(skip, chunk_size)
                if not chunk:
                    break
                if cached is not None:
                    cached.extend(chunk)
                yield from chunk
                skip += len(chunk)
                if len(chunk) < chunk_size:
                    break
            if cached is not None:
                self._cache = cached

        return generate()

    def __getitem__(self, item):
        if isinstance(item, slice):
            if item.stop is None or (item.start is not None and item.start < 0):
                return list(self)[item]
            start = item.start or 0
            step = item.step or 1
            values = self._generate(0, item.stop)
            return values[start:item.stop:step]
        if item < 0:
            return list(self)[item]
        values = self._generate(item, 1)
        if not values:
            raise IndexError
        return values[0]

    def count(self):
        return len(list(self))

    def before(self, dt, inc=False):
        found = None
        for value in self:
            if value > dt or (value == dt and not inc):
                break
            found = value
        return found

    def after(self, dt, inc=False):
        for value in self:
            if value > dt or (inc and value == dt):
                return value
        return None

    def between(self, after, before, inc=False, count=1):
        result = []
        for value in self:
            if value < after or (value == after and not inc):
                continue
            if value > before or (value == before and not inc):
                break
            result.append(value)
            if count and len(result) >= count:
                break
        return result

    def xafter(self, dt, count=None, inc=False):
        yielded = 0
        for value in self:
            if value > dt or (inc and value == dt):
                yield value
                yielded += 1
                if count is not None and yielded >= count:
                    return


class rruleset:
    def __init__(self, cache=False):
        self._rrules = []
        self._rdates = []
        self._exrules = []
        self._exdates = set()
        self._cache = cache

    def rrule(self, rule):
        self._rrules.append(rule)

    def rdate(self, value):
        self._rdates.append(value)

    def exrule(self, rule):
        self._exrules.append(rule)

    def exdate(self, value):
        self._exdates.add(value)

    def __iter__(self):
        included = heapq.merge(*(iter(rule) for rule in self._rrules), iter(sorted(self._rdates)))
        excluded = heapq.merge(*(iter(rule) for rule in self._exrules), iter(sorted(self._exdates)))
        excluded_value = next(excluded, None)
        previous = None
        for value in included:
            while excluded_value is not None and excluded_value < value:
                excluded_value = next(excluded, None)
            if value != previous and value != excluded_value:
                yield value
            previous = value

    def count(self):
        return len(list(self))

    def before(self, dt, inc=False):
        return _before(self, dt, inc)

    def after(self, dt, inc=False):
        return _after(self, dt, inc)

    def between(self, after, before, inc=False, count=1):
        return _between(self, after, before, inc, count)

    def xafter(self, dt, count=None, inc=False):
        yield from _xafter(self, dt, count, inc)

    def __getitem__(self, item):
        return list(self)[item]


def _before(values, dt, inc=False):
    found = None
    for value in values:
        if value > dt or (value == dt and not inc):
            break
        found = value
    return found


def _after(values, dt, inc=False):
    for value in values:
        if value > dt or (inc and value == dt):
            return value
    return None


def _between(values, after, before, inc=False, count=1):
    result = []
    for value in values:
        if value < after or (value == after and not inc):
            continue
        if value > before or (value == before and not inc):
            break
        result.append(value)
        if count and len(result) >= count:
            break
    return result


def _xafter(values, dt, count=None, inc=False):
    yielded = 0
    for value in values:
        if value > dt or (inc and value == dt):
            yield value
            yielded += 1
            if count is not None and yielded >= count:
                return


_FREQ = {
    "YEARLY": YEARLY,
    "MONTHLY": MONTHLY,
    "WEEKLY": WEEKLY,
    "DAILY": DAILY,
    "HOURLY": HOURLY,
    "MINUTELY": MINUTELY,
    "SECONDLY": SECONDLY,
}
_WEEKDAYS = {"MO": MO, "TU": TU, "WE": WE, "TH": TH, "FR": FR, "SA": SA, "SU": SU}


def _parse_ints(value):
    return tuple(int(part) for part in value.split(","))


def _parse_weekdays(value):
    result = []
    for part in value.split(","):
        match = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", part)
        if not match:
            raise ValueError(f"invalid BYDAY value: {part}")
        item = _WEEKDAYS[match.group(2)]
        result.append(item(int(match.group(1))) if match.group(1) else item)
    return tuple(result)


def rrulestr(s, **kwargs):
    """Parse a single RFC 5545 RRULE (or DTSTART + RRULE) string."""
    dtstart = kwargs.pop("dtstart", None)
    forceset = kwargs.pop("forceset", False)
    compatible = kwargs.pop("compatible", False)
    cache = kwargs.pop("cache", False)
    unfold = kwargs.pop("unfold", False)
    if kwargs:
        raise TypeError(f"unexpected rrulestr options: {', '.join(kwargs)}")
    _ = compatible, unfold
    rules = []
    dates = []
    exdates = []
    for raw_line in str(s).strip().splitlines():
        line = raw_line.strip()
        if line.startswith("DTSTART"):
            dtstart = isoparse(line.split(":", 1)[1])
            continue
        if line.startswith("RDATE"):
            dates.extend(isoparse(v) for v in line.split(":", 1)[1].split(","))
            continue
        if line.startswith("EXDATE"):
            exdates.extend(isoparse(v) for v in line.split(":", 1)[1].split(","))
            continue
        if line.startswith("RRULE:"):
            line = line[6:]
        fields = dict(part.split("=", 1) for part in line.split(";"))
        options = {"dtstart": dtstart, "cache": cache, "freq": _FREQ[fields.pop("FREQ")]}
        mappings = {
            "INTERVAL": ("interval", int),
            "COUNT": ("count", int),
            "UNTIL": ("until", isoparse),
            "WKST": ("wkst", lambda x: _WEEKDAYS[x]),
            "BYMONTH": ("bymonth", _parse_ints),
            "BYMONTHDAY": ("bymonthday", _parse_ints),
            "BYDAY": ("byweekday", _parse_weekdays),
            "BYHOUR": ("byhour", _parse_ints),
            "BYMINUTE": ("byminute", _parse_ints),
            "BYSECOND": ("bysecond", _parse_ints),
        }
        for key, value in fields.items():
            if key not in mappings:
                raise NotImplementedError(f"unsupported RRULE field: {key}")
            name, convert = mappings[key]
            options[name] = convert(value)
        rules.append(rrule(**options))
    if len(rules) == 1 and not forceset and not dates and not exdates:
        return rules[0]
    result = rruleset(cache=cache)
    for rule in rules:
        result.rrule(rule)
    for value in dates:
        result.rdate(value)
    for value in exdates:
        result.exdate(value)
    return result
