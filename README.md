# mojo-dateutil

`mojo-dateutil` ports the compute-heavy parts of
[`python-dateutil`](https://github.com/dateutil/dateutil) date parsing and
recurrence expansion to Mojo. Its Python modules follow the upstream
`dateutil.parser` and `dateutil.rrule` names and signatures for the covered
subset, while one shared native library does the calendar work.

The useful fast paths are ISO-8601 parsing and bounded recurrence expansion.
Single-value parsing uses bytes and a small ctypes field buffer directly, while
`parse_many()` keeps its NumPy buffers zero-copy and parallelizes sufficiently
large batches.

## Install

```bash
pixi install
pixi run build
pixi run test
```

The build produces `dist/libmojo-dateutil.so`. Python also builds it on first
import when it is absent. `MOJO_DATEUTIL_LIB` can point the wrapper at a
prebuilt copy.

## Usage

```python
from datetime import datetime
from mojo_dateutil import parser
from mojo_dateutil.rrule import MONTHLY, MO, FR, rrule, rrulestr

instant = parser.isoparse("2026-07-30T14:20:00+02:00")
batch = parser.parse_many([
    "2026-07-30T14:20:00Z",
    "2026-07-31T14:20:00Z",
])

paydays = rrule(
    MONTHLY,
    dtstart=datetime(2026, 1, 1, 9),
    count=12,
    byweekday=(MO(1), FR(-1)),
)
print(instant)
print(list(paydays)[:4])

same_rule = rrulestr(
    "DTSTART:20260101T090000\n"
    "RRULE:FREQ=MONTHLY;COUNT=12;BYDAY=1MO,-1FR"
)
```

## Coverage

### Parsing

- `parser.isoparse()` for calendar, ordinal, and ISO week dates in basic or
  extended form
- ISO times through microseconds, decimal fractions, `24:00`, `Z`, and signed
  hour/minute offsets
- any ASCII one-byte separator between a complete date and time, matching
  upstream
- `parser.parse()` for full ISO dates plus common named-month and numeric
  calendar forms
- `parser.parser`, the non-customizable `parser.parserinfo` compatibility
  marker, `ParserError`, and the additional native batch API
  `parser.parse_many()`
- fixed-offset `tz.UTC`, `tzutc()`, and `tzoffset()`

### Recurrence rules

- `rrule()` with `YEARLY`, `MONTHLY`, `WEEKLY`, `DAILY`, `HOURLY`,
  `MINUTELY`, and `SECONDLY`
- `dtstart`, `interval`, `wkst`, `count`, `until`, `bymonth`,
  positive/negative `bymonthday`, plain and ordinal `byweekday`, `byhour`,
  `byminute`, and `bysecond`
- lazy iteration, indexing/slicing, `count`, `before`, `after`, `between`, and
  `xafter`
- `rrulestr()` for `DTSTART`, one or more `RRULE`, `RDATE`, and `EXDATE` lines
- `rruleset()` unions and exclusions

The supported paths have parity tests against installed `python-dateutil`
2.9.0.post0, including invalid parser inputs, every ASCII one-byte datetime
separator, recurrence filters, query methods, rule sets, and edge cases.

## Not covered

This is not the complete `python-dateutil` package. Generic natural-language
and fuzzy parsing, locale/custom `parserinfo`, named time-zone databases,
relativedelta, Easter helpers, and the uncommon recurrence clauses
`BYSETPOS`, `BYYEARDAY`, `BYWEEKNO`, and `BYEASTER` are not implemented.
Ordinal `BYDAY` on a yearly rule currently requires `BYMONTH`. Unsupported
recurrence clauses raise `NotImplementedError` instead of silently changing a
rule.

Unbounded rules are chunked lazily. Bounded `count` rules expand in one native
call and are the optimized path.

## Benchmarks

Measured with `pixi run bench` on an Intel Xeon E5-2697 v4 at 2.30 GHz,
Linux x86-64, Python 3.13.14. These are real best-of-three results from the
same inputs; ratios below 1 mean Mojo is slower.

| case | mojo-dateutil | python-dateutil | ratio |
| --- | ---: | ---: | ---: |
| `isoparse` 100k ISO datetimes | 307.22 ms | 937.38 ms | 3.05x faster |
| `parse_many` 100k ISO datetimes | 840.56 ms | 926.17 ms | 1.10x faster |
| `SECONDLY`, 250k occurrences | 365.94 ms | 705.55 ms | 1.93x faster |
| `DAILY`, 100k occurrences | 88.33 ms | 231.75 ms | 2.62x faster |
| `MONTHLY` weekdays, 50k | 44.05 ms | 112.10 ms | 2.54x faster |

The parser clears native result fields a SIMD vector at a time with a scalar
tail. Batches of at least 4,096 records use four independent native calls over
disjoint slices of the same NumPy allocations; smaller batches stay serial.
Monthly weekday rules advance directly by eligible months instead of repeating
civil-date transforms for every scanned day. Recurrences with one fixed time
also reuse a single `timedelta` while materializing Python objects.

There is no GPU path. These kernels are short, branch-heavy parsing and
calendar operations with low arithmetic intensity, followed by unavoidable
Python `datetime` materialization. Device transfer and launch overhead would
dominate, so CPU remains the only execution device and no GPU dependency is
added.

## How it works

`src/dateutil.mojo` is one compilation unit containing the ISO state machine,
Gregorian ordinal conversions, RFC recurrence filters, and three C ABI
exports. `build/build.sh` compiles it with `mojo build --emit shared-lib`.

The Python wrapper owns every allocation. Single parsing passes the encoded
bytes and a nine-field ctypes array directly. Bulk text is a contiguous `uint8`
view; batch boundaries, parsed fields, recurrence ordinals, and seconds-of-day
are contiguous `int64` arrays. ctypes passes buffer addresses and scalar sizes
without copying payloads. Mojo reconstructs each address as
`UnsafePointer[..., AnyOrigin[mut=True]]`, fills caller-owned output, and
returns a count or status. Python then creates standard `datetime` objects, so
callers receive ordinary interoperable values rather than custom native
objects.

## Development

```bash
pixi run build
pixi run test
pixi run bench
```

The benchmark task holds a machine-wide lock; run it through Pixi rather than
calling `bench/bench.py` directly.

MIT licensed.
