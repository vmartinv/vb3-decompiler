#!/usr/bin/env python3
"""
Scan the raw RT_RCDATA blobs already extracted by ne_parser.py for embedded
standard Windows BMP files (VB3 stores FRX picture data as a small
VB-specific wrapper header immediately followed by a complete, standalone
BITMAPFILEHEADER + BITMAPINFOHEADER + pixel data stream) and slice them out
as real, independently-openable .bmp files.

Usage:
    python3 tools/extract_bitmaps.py [--in DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path


def find_bitmaps(data: bytes):
    """Yield (offset, length) for each valid embedded BMP found in data."""
    start = 0
    while True:
        idx = data.find(b"BM", start)
        if idx == -1:
            return
        # BITMAPFILEHEADER: 'BM', bfSize(u32), reserved(u32), bfOffBits(u32)
        if idx + 14 <= len(data):
            bf_size, _res1, _res2, bf_off_bits = struct.unpack_from("<2xIHHI", data, idx)
            # Sanity check: declared file size must fit in the remaining
            # buffer and bfOffBits must point somewhere sane before it.
            if 0 < bf_size <= len(data) - idx and 14 <= bf_off_bits <= bf_size:
                yield idx, bf_size
                start = idx + bf_size
                continue
        start = idx + 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--in", dest="in_dir", type=Path,
        default=Path(__file__).resolve().parent.parent / "extraction" / "resources",
    )
    ap.add_argument(
        "--out", dest="out_dir", type=Path,
        default=Path(__file__).resolve().parent.parent / "extraction" / "resources" / "bitmaps",
    )
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for blob_path in sorted(args.in_dir.glob("RT_RCDATA_*.bin")):
        data = blob_path.read_bytes()
        for i, (offset, length) in enumerate(find_bitmaps(data)):
            out_name = f"{blob_path.stem}_bmp{i}.bmp"
            (args.out_dir / out_name).write_bytes(data[offset : offset + length])
            total += 1
            print(f"{blob_path.name}: bitmap at 0x{offset:x}, {length} bytes -> {out_name}")

    print(f"\nExtracted {total} bitmaps -> {args.out_dir}")


if __name__ == "__main__":
    main()
