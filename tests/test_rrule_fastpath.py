"""Unfiltered rules must take the closed-form path, not a per-candidate scan."""

import time
from datetime import datetime

from dateutil import rrule as upstream

from mojo_dateutil import rrule as mojo

START = datetime(2000, 1, 1, 9, 30)


def _fastest_generate(freq, count):
    rule = mojo.rrule(freq, dtstart=START, count=count)
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        rule._generate(0, count)
        best = min(best, time.perf_counter() - started)
    return best


def test_unfiltered_secondly_matches_upstream_and_stays_cheap():
    count = 250_000
    ours = list(mojo.rrule(mojo.SECONDLY, dtstart=START, count=count))
    assert ours == list(upstream.rrule(upstream.SECONDLY, dtstart=START, count=count))

    # A day scan needs count * 86,400 calendar steps and runs past a minute at
    # this size, so anything near a second means the fast path was lost.
    assert _fastest_generate(mojo.SECONDLY, count) < 1.0


def test_unfiltered_secondly_skip_window_is_absolute():
    count = 250_000
    theirs = list(upstream.rrule(upstream.SECONDLY, dtstart=START, count=count))
    for skip, capacity in ((0, 5), (10, 5), (count - 10, 10)):
        rule = mojo.rrule(mojo.SECONDLY, dtstart=START, count=count)
        assert rule._generate(skip, capacity) == theirs[skip:skip + capacity]
