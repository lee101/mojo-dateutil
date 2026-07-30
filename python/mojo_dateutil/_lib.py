"""ctypes bridge to the Mojo date kernels."""

from __future__ import annotations

import ctypes
import operator
import os
import subprocess

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.environ.get("MOJO_DATEUTIL_LIB", os.path.join(ROOT, "dist", "libmojo-dateutil.so"))
I = ctypes.c_int64

_SIGNATURES = {
    "mdu_parse_iso": ([ctypes.c_char_p, I, ctypes.POINTER(I)], I),
    "mdu_parse_iso_many": ([I] * 6, I),
    "mdu_rrule_generate": ([I] * 21, I),
}

_library: ctypes.CDLL | None = None
_I64_MIN = -(1 << 63)
_I64_MAX = (1 << 63) - 1


def i64(value, name: str = "value") -> int:
    """Return an integer only when it can cross the signed Int64 ABI exactly."""
    try:
        value = operator.index(value)
    except TypeError as exc:
        raise TypeError(f"{name} must be an integer") from exc
    if not _I64_MIN <= value <= _I64_MAX:
        raise OverflowError(f"{name} does not fit in a signed 64-bit integer")
    return value


def build() -> str:
    subprocess.run(["bash", os.path.join(ROOT, "build", "build.sh")], check=True)
    return LIB


def lib() -> ctypes.CDLL:
    global _library
    if _library is None:
        if not os.path.exists(LIB):
            build()
        _library = ctypes.CDLL(LIB)
        for name, (argtypes, restype) in _SIGNATURES.items():
            fn = getattr(_library, name)
            fn.argtypes = argtypes
            fn.restype = restype
    return _library


def addr(array: np.ndarray) -> int:
    if not array.flags.c_contiguous:
        raise ValueError("native buffers must be C-contiguous")
    if array.dtype != np.dtype(np.int64) and array.dtype != np.dtype(np.uint8):
        raise TypeError("native buffers must use int64 or uint8 elements")
    return i64(array.ctypes.data, "buffer address")
