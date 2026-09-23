#!/usr/bin/env python3
"""
Decodes the fixed-format header that begins every VB3 compiled-form
RT_RCDATA "data blob", recovering each form's window caption.

Confirmed structure (see ../RESOURCE_FORMAT.md), 14-byte fixed
prefix + variable tag list + Pascal-string caption:

    offset 0:  u16 0xFFCC          -- constant magic
    offset 2:  u16 0x002C          -- constant (purpose unknown)
    offset 4:  u32                 -- varies per form, no correlation found
                                       with blob length or control count
    offset 8:  u8  0x00            -- constant
    offset 9:  u8                  -- varies, purpose unknown
    offset 10: u16 0x0003          -- constant
    offset 12: u16 0x0000          -- constant
    offset 14: (u8 tag, u8 0x00)*  -- variable-length tag list, terminated
                                       by (0xFF, 0x00). Tag values seen so
                                       far: 0, 38, 39, 40, 50. Meaning
                                       unknown -- possibly form-level
                                       boolean property flags.
    then:      Pascal string (u8 length + ASCII bytes) -- the form's
               .Caption property.

Usage:
    python3 tools/parse_form_headers.py <path-to-exe>
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ne_parser import NEFile  # noqa: E402


def read_pstrings(data: bytes, n: int = 64) -> list[str]:
    out = []
    pos = 0
    while pos < len(data) and len(out) < n:
        length = data[pos]
        if length == 0 or pos + 1 + length > len(data):
            break
        chunk = data[pos + 1 : pos + 1 + length]
        if not all(0x20 <= b < 0x7F for b in chunk):
            break
        out.append(chunk.decode("ascii"))
        pos += 1 + length
    return out


def decode_form_header(data: bytes):
    """Returns (tags, caption) or (None, None) if data doesn't start with
    the expected magic / doesn't have a sane terminated tag list."""
    if data[0:2] != b"\xff\xcc":
        return None, None
    pos = 14
    tags = []
    while data[pos] != 0xFF or data[pos + 1] != 0x00:
        tags.append(data[pos])
        pos += 2
        if pos > 64:
            return None, None
    pos += 2
    caplen = data[pos]
    caption = data[pos + 1 : pos + 1 + caplen].decode("ascii", errors="replace")
    return tags, caption


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", type=Path)
    args = ap.parse_args()

    ne = NEFile(args.exe)
    rcdata = [r for r in ne.iter_resources() if r.type_name == "RT_RCDATA"]
    rcdata.sort(key=lambda r: r.res_id)

    # Each form is a data blob (FF CC magic) immediately followed by its
    # name table, in project-directory order.
    i = 0
    while i < len(rcdata):
        res = rcdata[i]
        is_blob = res.data[:2] == b"\xff\xcc"
        if is_blob and i + 1 < len(rcdata) and rcdata[i + 1].length <= 256:
            names = read_pstrings(rcdata[i + 1].data)
            tags, caption = decode_form_header(res.data)
            form = names[0] if names else "?"
            ncontrols = len(names) - 1 if names else 0
            print(
                f"id{res.res_id:2d}/{rcdata[i + 1].res_id:<2d} {form:<14s} "
                f"caption={caption!r:<32s} tags={tags} controls={ncontrols} "
                f"blob={len(res.data):,}B"
            )
            i += 2
        else:
            label = f"({res.length}B)"
            print(f"id{res.res_id:<2d} unpaired: {label}")
            i += 1

if __name__ == "__main__":
    main()
