import os

import numpy as np
import pytest

from mojo_dateutil._lib import LIB, addr, i64, lib


def test_shared_library_is_loaded_from_dist():
    loaded = lib()
    assert os.path.basename(LIB) == "libmojo-dateutil.so"
    assert os.path.exists(LIB)
    assert loaded.mdu_parse_iso is not None
    assert loaded.mdu_rrule_generate is not None


def test_native_parser_rejects_null_buffers():
    assert lib().mdu_parse_iso(None, 1, None) < 0


def test_native_batch_parser_rejects_out_of_bounds_offsets():
    data = np.frombuffer(b"2024-01-01", dtype=np.uint8)
    offsets = np.array([0, len(data) + 1], dtype=np.int64)
    fields = np.empty((1, 9), dtype=np.int64)
    statuses = np.empty(1, dtype=np.int64)
    assert (
        lib().mdu_parse_iso_many(
            addr(data), addr(offsets), len(data), 1, addr(fields), addr(statuses)
        )
        < 0
    )


def test_addr_rejects_wrong_dtype_and_noncontiguous_arrays():
    with pytest.raises(TypeError):
        addr(np.array([1], dtype=np.int32))
    with pytest.raises(ValueError):
        addr(np.arange(8, dtype=np.int64)[::2])


def test_i64_rejects_values_that_ctypes_would_silently_narrow():
    assert i64(np.int64(42)) == 42
    with pytest.raises(OverflowError):
        i64(1 << 80)
    with pytest.raises(TypeError):
        i64(1.5)
