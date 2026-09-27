"""ULID job ids — 26-char Crockford base32, lexicographically time-sortable.

A ULID is 128 bits: 48-bit millisecond timestamp + 80 bits of randomness,
encoded as 26 Crockford-base32 characters. Sorting ULIDs sorts by creation
time, which is exactly the drain order the queue wants — so the ``jobs/``
directory listing *is* the pending queue, in order, with no separate index.

ULIDs contain only ``[0-9A-HJKMNP-TV-Z]`` and so are a legal subset of the
``[A-Za-z0-9_.-]`` job-name charset validated on both the Python and Lua sides
(SI-7). We never accept a job file whose name is not a bare 26-char ULID.
"""

from __future__ import annotations

import os
import re
import time

# Crockford base32 alphabet (excludes I, L, O, U to avoid ambiguity).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_INDEX = {c: i for i, c in enumerate(_CROCKFORD)}

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_TIME_LEN = 10  # 48 bits -> 10 base32 chars
_RAND_LEN = 16  # 80 bits -> 16 base32 chars


def _encode(value: int, length: int) -> str:
    out = ["0"] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(out)


def new_ulid(now_ms: int | None = None) -> str:
    """Generate a fresh ULID. ``now_ms`` overridable for deterministic tests."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    now_ms &= (1 << 48) - 1
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    return _encode(now_ms, _TIME_LEN) + _encode(rand, _RAND_LEN)


def is_ulid(text: str) -> bool:
    return bool(text) and bool(ULID_RE.match(text))


def ulid_time_ms(ulid: str) -> int:
    """Extract the millisecond timestamp encoded in a ULID's first 10 chars."""
    if not is_ulid(ulid):
        raise ValueError(f"not a ULID: {ulid!r}")
    value = 0
    for c in ulid[:_TIME_LEN]:
        value = (value << 5) | _CROCKFORD_INDEX[c]
    return value
