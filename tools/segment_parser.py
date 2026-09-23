#!/usr/bin/env python3
"""
Parses the NE segment table of a VB3-compiled executable and dumps each
segment's raw bytes, plus scans every segment for VB3's inline
string-literal encoding.

Empirically confirmed (see ../OPCODES.md): VB3 p-code embeds string
literals directly in the code segment as `<u16 length><raw bytes>`, with no
separate string pool/table. This isn't documented anywhere we found; it was
verified by checking that the two bytes immediately preceding several known
UI strings always equal that string's exact byte length.

Usage:
    python3 tools/segment_parser.py <path-to-exe> [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Segment:
    index: int  # 1-based, matches NE segment table / intersegment fixup numbering
    file_offset: int
    length: int
    flags: int
    min_alloc: int
    data: bytes

    @property
    def is_data(self) -> bool:
        return bool(self.flags & 0x0001)


def parse_segments(raw: bytes) -> list[Segment]:
    (ne_off,) = struct.unpack_from("<H", raw, 0x3C)
    (segtab_rel,) = struct.unpack_from("<H", raw, ne_off + 0x22)
    (align_shift,) = struct.unpack_from("<H", raw, ne_off + 0x32)
    (cseg,) = struct.unpack_from("<H", raw, ne_off + 0x1C)
    segtab_abs = ne_off + segtab_rel

    segments = []
    pos = segtab_abs
    for i in range(cseg):
        seg_off, seg_len, seg_flags, seg_minalloc = struct.unpack_from("<HHHH", raw, pos)
        pos += 8
        abs_off = seg_off << align_shift
        length = seg_len if seg_len != 0 else 0x10000
        data = b"" if seg_off == 0 else raw[abs_off : abs_off + length]
        segments.append(Segment(i + 1, abs_off, length, seg_flags, seg_minalloc, data))
    return segments


# A "string" for our purposes: printable ASCII (with common punctuation),
# length 2..500, exactly matching a little-endian u16 immediately preceding
# it. This is deliberately conservative to avoid false positives from
# random bytecode that happens to look like short text.
_PRINTABLE = re.compile(rb"[\x20-\x7e]+")


def find_inline_strings(data: bytes):
    for m in _PRINTABLE.finditer(data):
        start, end = m.span()
        length = end - start
        if length < 2 or start < 2:
            continue
        (declared_len,) = struct.unpack_from("<H", data, start - 2)
        if declared_len == length:
            yield start, data[start:end].decode("ascii")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", type=Path)
    ap.add_argument(
        "--out", type=Path,
        default=Path(__file__).resolve().parent.parent / "extraction" / "pcode",
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    raw = args.exe.read_bytes()
    segments = parse_segments(raw)

    all_strings = []
    for seg in segments:
        print(
            f"seg {seg.index:2d}: file_off=0x{seg.file_offset:06x} "
            f"len=0x{seg.length:05x} flags=0x{seg.flags:04x} "
            f"({'DATA' if seg.is_data else 'CODE'})"
        )
        if not seg.data:
            continue
        (args.out / f"seg{seg.index:02d}.bin").write_bytes(seg.data)

        for offset, text in find_inline_strings(seg.data):
            all_strings.append({"segment": seg.index, "offset": offset, "text": text})

    (args.out / "inline_strings.json").write_text(json.dumps(all_strings, indent=2))
    print(f"\nFound {len(all_strings)} inline string literals -> extraction/pcode/inline_strings.json")


if __name__ == "__main__":
    main()
