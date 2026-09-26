"""dataimage: constant literals and the global image's Type chain."""
from __future__ import annotations

import struct

import pytest

from vb3decompiler.dataimage import GlobalImage, const_literal, word


@pytest.mark.parametrize("t, raw, text", [
    ("I", struct.pack("<h", 5), "5"),
    ("I", struct.pack("<h", -3), "-3"),
    ("I", struct.pack("<h", -32768), "&H8000"),  # no decimal literal for it
    ("L", struct.pack("<i", 70000), "70000&"),
    ("L", struct.pack("<i", -1), "&HFFFFFFFF&"),
    ("S", struct.pack("<f", 1.5), "1.5!"),
    ("D", struct.pack("<d", 0.1), "0.1#"),
    ("C", struct.pack("<q", 12345), "1.2345@"),
    ("T", b"\0\0\0\0", None),
    ("D", b"\0", None),  # truncated
])
def test_const_literal(t, raw, text):
    assert const_literal(t, raw) == text


def test_word():
    assert word(b"\x34\x12\xff\xff", 0) == 0x1234
    assert word(b"\x34\x12\xff\xff", 2) == 0xFFFF
    assert word(b"\x34\x12\xff\xff", 2, signed=True) == -1


def global_image(words: dict[int, int], size: int = 0x60, base: int = 4) -> bytes:
    """A global image chunk at `base` (u16 size, then global offsets from
    base + 2) with the given words set."""
    d = bytearray(base + 2 + size)
    struct.pack_into("<H", d, base, size)
    for g, v in words.items():
        struct.pack_into("<H", d, base + 2 + g, v)
    return bytes(d)


def test_global_image_types():
    t, f1, f2, f3 = 0x10, 0x18, 0x22, 0x2A
    image = global_image({
        4: t,  # head of the Type chain
        t + 2: 0, t + 4: 12, t + 6: f1,  # next Type, size, first field
        f1 + 2: f2, f1 + 4: 1, f1 + 6: 0,  # Integer at 0
        f2 - 2: 5, f2 + 2: f3, f2 + 4: 8, f2 + 6: 2,  # String * 5 at 2
        f3 + 2: 0 | 1, f3 + 4: 2, f3 + 6: 8,  # Long at 8, the last field (next 0), an array (| 1)
        f3 + 12: 1, f3 + 20: 3, f3 + 22: 1,  # 1 dimension: 3 elements from 1
    })
    gl = GlobalImage(image, 4)
    assert list(gl.types) == [t]
    assert gl.types[t].lines(gl.types) == ["Type T10", "    F18 As Integer", "    F22 As String * 5",
                                           "    F2A(1 To 3) As Long", "End Type"]
    assert gl.field_type[f2] is gl.types[t]
    assert gl.type_extent == [(t, f3 + 8 + 12 + 4)]
    assert gl.in_type(f1) and not gl.in_type(0x50)
    assert gl.by_size(12) is gl.types[t] and gl.by_size(4) is None
    assert gl.w(f2 + 6) == 2 and gl.raw(t + 4, 2) == b"\x0c\x00"


def test_global_image_without_types():
    gl = GlobalImage(global_image({}), 4)
    assert gl.types == {} and gl.type_extent == []
    assert GlobalImage(b"", None).size == 0
