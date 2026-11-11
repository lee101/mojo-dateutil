"""The parallel batch path must return exactly what the serial path returns."""

import numpy as np
import pytest

from dateutil import parser as upstream

from mojo_dateutil import parser
from mojo_dateutil._lib import addr, i64, lib

# Covers the serial side of the native threshold, the boundary itself and a
# real parallel split. Kernel level, so the whole file stays fast.
BATCH_SIZES = (1024, 524_287, 524_288, 1_100_000)


def _values(count):
    pool = [
        "2024-02-29T12:34:56.123456+05:30",
        "2024-W09-4T06:07:08Z",
        "2024-060T24:00-03:00",
        "1999-12-31T23:59:59.999999",
    ]
    return [pool[i % len(pool)] for i in range(count)]


def _native(encoded, workers=None):
    """Run one native batch entry point; ``workers=None`` is the serial call."""
    lengths = np.fromiter((len(raw) for raw in encoded), dtype=np.int64, count=len(encoded))
    offsets = np.empty(len(encoded) + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(lengths, out=offsets[1:])
    data = np.frombuffer(b"".join(encoded), dtype=np.uint8)
    fields = np.empty((len(encoded), 9), dtype=np.int64)
    statuses = np.empty(len(encoded), dtype=np.int64)
    args = (
        addr(data),
        addr(offsets),
        i64(len(data)),
        i64(len(encoded)),
        addr(fields),
        addr(statuses),
    )
    if workers is None:
        status = lib().mdu_parse_iso_many(*args)
    else:
        status = lib().mdu_parse_iso_many_par(*args, workers)
    assert status == 0
    return fields, statuses


@pytest.mark.parametrize("count", BATCH_SIZES)
def test_parallel_batch_matches_serial_bytes(count):
    encoded = [value.encode("ascii") for value in _values(count)]
    serial_fields, serial_statuses = _native(encoded)
    par_fields, par_statuses = _native(encoded, workers=4)
    assert np.array_equal(par_fields, serial_fields)
    assert np.array_equal(par_statuses, serial_statuses)
    assert not serial_statuses.any()


@pytest.mark.parametrize("workers", [0, 1, 2, 3, 4, 8])
def test_worker_count_does_not_change_results(workers):
    encoded = [value.encode("ascii") for value in _values(524_288)]
    serial_fields, _ = _native(encoded)
    fields, _ = _native(encoded, workers=workers)
    assert np.array_equal(fields, serial_fields)


def test_parse_many_parallel_matches_upstream():
    values = _values(524_288)
    assert parser.parse_many(values) == [upstream.isoparse(value) for value in values]


def test_parallel_batch_reports_error_index_in_any_chunk():
    count = 524_288
    values = _values(count)
    values[count - 7] = "2024-02-30"  # in the last parallel chunk
    with pytest.raises(ValueError, match=f"index {count - 7}"):
        parser.parse_many(values)
