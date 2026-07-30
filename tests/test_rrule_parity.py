from datetime import datetime, timedelta, timezone

import pytest
from dateutil import rrule as upstream

from mojo_dateutil import rrule as mojo


START = datetime(2023, 1, 31, 12, 34, 56)


def _upstream_options(options):
    converted = dict(options)
    if "byweekday" in converted:
        values = converted["byweekday"]
        values = values if isinstance(values, tuple) else (values,)
        weekdays = (upstream.MO, upstream.TU, upstream.WE, upstream.TH, upstream.FR, upstream.SA, upstream.SU)
        converted["byweekday"] = tuple(
            weekdays[value.weekday](value.n) if value.n is not None else weekdays[value.weekday]
            for value in values
        )
    if isinstance(converted.get("wkst"), mojo.weekday):
        converted["wkst"] = converted["wkst"].weekday
    return converted


@pytest.mark.parametrize(
    "freq, options",
    [
        (mojo.YEARLY, {"count": 12}),
        (mojo.YEARLY, {"count": 20, "bymonthday": (1, -1)}),
        (mojo.YEARLY, {"count": 10, "bymonth": (2, 8), "bymonthday": (29, -1)}),
        (mojo.YEARLY, {"count": 12, "bymonth": 5, "byweekday": mojo.MO(2)}),
        (mojo.MONTHLY, {"count": 20}),
        (mojo.MONTHLY, {"count": 20, "bymonthday": (1, -1)}),
        (mojo.MONTHLY, {"count": 20, "byweekday": (mojo.MO, mojo.FR)}),
        (mojo.MONTHLY, {"count": 20, "byweekday": (mojo.MO(1), mojo.FR(-1))}),
        (
            mojo.WEEKLY,
            {"count": 20, "interval": 2, "wkst": mojo.SU, "byweekday": (mojo.MO, mojo.TU)},
        ),
        (
            mojo.DAILY,
            {"count": 20, "interval": 3, "bymonth": (1, 2), "byweekday": (mojo.MO, mojo.FR), "byhour": (1, 13)},
        ),
        (mojo.HOURLY, {"count": 20, "interval": 3, "byminute": (10, 40), "bysecond": 2}),
        (mojo.MINUTELY, {"count": 20, "interval": 7, "bysecond": (0, 30)}),
        (mojo.SECONDLY, {"count": 20, "interval": 13, "byminute": (0, 1)}),
    ],
)
def test_recurrences_match_upstream(freq, options):
    got = list(mojo.rrule(freq, dtstart=START, **options))
    expected = list(upstream.rrule(freq, dtstart=START, **_upstream_options(options)))
    assert got == expected


def test_until_is_inclusive():
    until = START + timedelta(days=10)
    got = list(mojo.rrule(mojo.DAILY, dtstart=START, until=until, interval=2))
    expected = list(upstream.rrule(upstream.DAILY, dtstart=START, until=until, interval=2))
    assert got == expected
    assert got[-1] == until


def test_aware_recurrence_matches_upstream():
    start = datetime(2024, 1, 1, 8, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    options = {"count": 50, "byweekday": (mojo.MO, mojo.WE, mojo.FR)}
    assert list(mojo.rrule(mojo.DAILY, dtstart=start, **options)) == list(
        upstream.rrule(upstream.DAILY, dtstart=start, **_upstream_options(options))
    )


def test_query_methods_match_upstream():
    ours = mojo.rrule(mojo.DAILY, dtstart=START, count=30, interval=2)
    theirs = upstream.rrule(upstream.DAILY, dtstart=START, count=30, interval=2)
    pivot = START + timedelta(days=11)
    assert ours[7] == theirs[7]
    assert ours[3:12:2] == theirs[3:12:2]
    with pytest.raises(ValueError):
        theirs[:-3]
    with pytest.raises(ValueError):
        ours[:-3]
    assert ours.before(pivot) == theirs.before(pivot)
    assert ours.before(pivot, inc=True) == theirs.before(pivot, inc=True)
    assert ours.after(pivot) == theirs.after(pivot)
    assert ours.between(START, pivot, inc=True, count=0) == theirs.between(
        START, pivot, inc=True, count=0
    )
    assert list(ours.xafter(pivot, count=4)) == list(theirs.xafter(pivot, count=4))
    assert ours.count() == theirs.count()


def test_rrulestr_matches_upstream():
    text = "DTSTART:20240101T090000\nRRULE:FREQ=WEEKLY;COUNT=20;INTERVAL=2;BYDAY=MO,WE,FR"
    assert list(mojo.rrulestr(text)) == list(upstream.rrulestr(text))


def test_rrulestr_forceset_and_dates():
    text = (
        "DTSTART:20240101T090000\n"
        "RRULE:FREQ=DAILY;COUNT=5\n"
        "RDATE:20240110T090000\n"
        "EXDATE:20240103T090000"
    )
    assert list(mojo.rrulestr(text, forceset=True)) == list(upstream.rrulestr(text, forceset=True))


def test_rrulestr_multiple_rules_match_upstream():
    text = (
        "DTSTART:20240101T090000\n"
        "RRULE:FREQ=DAILY;COUNT=3\n"
        "RRULE:FREQ=WEEKLY;COUNT=3;BYDAY=FR"
    )
    assert list(mojo.rrulestr(text)) == list(upstream.rrulestr(text))


def test_rruleset_union_exclusion_matches_upstream():
    ours = mojo.rruleset()
    theirs = upstream.rruleset()
    for target, module in ((ours, mojo), (theirs, upstream)):
        target.rrule(module.rrule(module.DAILY, dtstart=START, count=8))
        target.rdate(START + timedelta(days=20))
        target.exdate(START + timedelta(days=3))
    assert list(ours) == list(theirs)


def test_cache_does_not_change_results():
    rule = mojo.rrule(mojo.MONTHLY, dtstart=START, count=40, byweekday=mojo.FR(-1), cache=True)
    first = list(rule)
    assert list(rule) == first


def test_monthly_weekday_fast_path_order_and_skip():
    options = {
        "count": 300,
        "interval": 2,
        "bymonth": (1, 3, 11),
        "byweekday": (mojo.MO, mojo.WE, mojo.FR),
        "byhour": (9, 17),
        "byminute": (5, 30),
        "bysecond": (0, 45),
    }
    ours = mojo.rrule(mojo.MONTHLY, dtstart=START, **options)
    theirs = upstream.rrule(upstream.MONTHLY, dtstart=START, **_upstream_options(options))
    assert list(ours) == list(theirs)
    assert ours[137] == theirs[137]


@pytest.mark.parametrize("name", ["byhour", "byminute", "bysecond"])
def test_empty_time_filter_matches_upstream(name):
    options = {name: ()}
    start = datetime(9999, 1, 1)
    until = datetime(9999, 1, 31)
    assert list(mojo.rrule(mojo.DAILY, dtstart=start, until=until, **options)) == list(
        upstream.rrule(upstream.DAILY, dtstart=start, until=until, **options)
    )


def test_lazy_exrule_matches_upstream():
    ours = mojo.rruleset()
    theirs = upstream.rruleset()
    for target, module in ((ours, mojo), (theirs, upstream)):
        target.rrule(module.rrule(module.DAILY, dtstart=START))
        target.exrule(module.rrule(module.WEEKLY, dtstart=START))
    assert list(ours.xafter(START, count=20, inc=True)) == list(
        theirs.xafter(START, count=20, inc=True)
    )


@pytest.mark.parametrize(
    "option",
    [
        {"bysetpos": 1},
        {"byyearday": 100},
        {"byeaster": 0},
        {"byweekno": 3},
    ],
)
def test_unsupported_clauses_are_explicit(option):
    with pytest.raises(NotImplementedError):
        mojo.rrule(mojo.YEARLY, dtstart=START, count=2, **option)
