"""Benchmarks against python-dateutil. Run only through ``pixi run bench``."""

from __future__ import annotations

from datetime import datetime
import os
import platform
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))

from dateutil import parser as py_parser  # noqa: E402
from dateutil import rrule as py_rrule  # noqa: E402
from mojo_dateutil import parser as mojo_parser  # noqa: E402
from mojo_dateutil import rrule as mojo_rrule  # noqa: E402


def best_time(fn, repeat=5):
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def main():
    start = datetime(2000, 1, 1, 9, 30)
    iso_values = [
        f"2024-{month:02d}-{day:02d}T12:34:56.123456+05:30"
        for month in range(1, 13)
        for day in range(1, 29)
    ]
    batch_values = [iso_values[i % len(iso_values)] for i in range(100_000)]

    cases = [
        (
            "isoparse 100k ISO datetimes",
            lambda: [mojo_parser.isoparse(iso_values[i % len(iso_values)]) for i in range(100_000)],
            lambda: [py_parser.isoparse(iso_values[i % len(iso_values)]) for i in range(100_000)],
        ),
        (
            "parse_many 100k ISO datetimes",
            lambda: mojo_parser.parse_many(batch_values),
            lambda: [py_parser.isoparse(value) for value in batch_values],
        ),
        (
            "SECONDLY, 250k occurrences",
            lambda: list(mojo_rrule.rrule(mojo_rrule.SECONDLY, dtstart=start, count=250_000)),
            lambda: list(py_rrule.rrule(py_rrule.SECONDLY, dtstart=start, count=250_000)),
        ),
        (
            "DAILY, 100k occurrences",
            lambda: list(mojo_rrule.rrule(mojo_rrule.DAILY, dtstart=start, count=100_000)),
            lambda: list(py_rrule.rrule(py_rrule.DAILY, dtstart=start, count=100_000)),
        ),
        (
            "MONTHLY weekdays, 50k",
            lambda: list(
                mojo_rrule.rrule(
                    mojo_rrule.MONTHLY,
                    dtstart=start,
                    count=50_000,
                    byweekday=(mojo_rrule.MO, mojo_rrule.WE, mojo_rrule.FR),
                )
            ),
            lambda: list(
                py_rrule.rrule(
                    py_rrule.MONTHLY,
                    dtstart=start,
                    count=50_000,
                    byweekday=(py_rrule.MO, py_rrule.WE, py_rrule.FR),
                )
            ),
        ),
    ]

    print(f"Machine: {cpu_name()} ({platform.system()} {platform.machine()}, Python {platform.python_version()})")
    print()
    print("| case | mojo-dateutil | python-dateutil | ratio |")
    print("| --- | ---: | ---: | ---: |")
    for name, ours, theirs in cases:
        ours()
        theirs()
        mojo_seconds, mojo_result = best_time(ours, repeat=3)
        python_seconds, python_result = best_time(theirs, repeat=3)
        assert mojo_result == python_result
        ratio = python_seconds / mojo_seconds
        label = "faster" if ratio >= 1 else "slower"
        print(
            f"| {name} | {mojo_seconds * 1000:.2f} ms | "
            f"{python_seconds * 1000:.2f} ms | {ratio:.2f}x {label} |"
        )


if __name__ == "__main__":
    main()
