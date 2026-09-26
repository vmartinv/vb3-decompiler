#!/usr/bin/env python3
"""
Parser for Windows 3.x "NE" (New Executable) files, aimed at VB3-compiled
executables. See ../RESOURCE_FORMAT.md for the format notes this produced,
developed against Quibble Race (https://github.com/vmartinv/qrace).

Standard library only, no third-party dependencies, so anyone can run the
extraction pipeline without installing anything beyond Python itself.

Usage:
    python3 tools/ne_parser.py <path-to-exe> [--out DIR]
"""
from __future__ import annotations

import sys
import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ne import PREDEFINED_TYPES, NamedResource, NEFile  # noqa: E402,F401

# Predefined NE resource type IDs (high bit of rtTypeID set => integer type,
# low byte is one of these). See MSDN "NE Executable Format" / Wine's
# include/winnt.h RT_* constants.


def parse_string_table(resources: list[NamedResource]) -> dict[int, str]:
    """
    RT_STRING resources are stored in bundles of up to 16 strings each.
    A resource with integer id N holds strings numbered (N-1)*16 .. N*16-1.
    Each string in the bundle is BYTE-length-prefixed (not null terminated);
    zero-length entries are unused slots.
    """
    out: dict[int, str] = {}
    for res in resources:
        if res.type_name != "RT_STRING" or res.res_id is None:
            continue
        base_id = (res.res_id - 1) * 16
        pos = 0
        data = res.data
        for i in range(16):
            if pos >= len(data):
                break
            length = data[pos]
            pos += 1
            if length:
                text = data[pos : pos + length].decode("latin-1")
                out[base_id + i] = text
                pos += length
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("exe", type=Path)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "extraction",
        help="extraction/ output directory (default: repo's extraction/)",
    )
    args = ap.parse_args()

    ne = NEFile(args.exe)
    print(f"NE header at 0x{ne.ne_header_offset:x}")
    print(f"  linker version: {ne.ver}.{ne.rev}")
    print(f"  target OS (exetyp): {ne.exetyp} (2=Windows)")
    print(f"  segments: {ne.cseg}, module refs: {ne.cmod}")
    print(f"  resource table at file offset 0x{ne.rsrctab_abs:x}")

    resources = list(ne.iter_resources())
    print(f"\nFound {len(resources)} resource entries:")

    by_type: dict[str, list[NamedResource]] = {}
    for res in resources:
        by_type.setdefault(res.type_name, []).append(res)

    for type_name, items in sorted(by_type.items()):
        print(f"  {type_name}: {len(items)} entries")

    strings_out = args.out / "strings"
    resources_out = args.out / "resources"
    forms_out = args.out / "forms"
    for d in (strings_out, resources_out, forms_out):
        d.mkdir(parents=True, exist_ok=True)

    # Strings: committed to the repo (recovered text, not binary asset data).
    string_table = parse_string_table(resources)
    (strings_out / "string_table.json").write_text(
        json.dumps(string_table, indent=2, ensure_ascii=False, sort_keys=True)
    )
    print(f"\nWrote {len(string_table)} strings -> {strings_out / 'string_table.json'}")

    # Everything else: raw bytes, gitignored, for local inspection /
    # figuring out the VB3 custom form resource format.
    manifest = []
    for res in resources:
        if res.type_name == "RT_STRING":
            continue  # already handled above
        if res.res_id is not None:
            label = str(res.res_id)
        else:
            label = "".join(c if c.isalnum() else "_" for c in (res.res_name or ""))
        fname = f"{res.type_name}_{label}.bin"
        dest = resources_out if res.type_name in PREDEFINED_TYPES.values() else forms_out
        (dest / fname).write_bytes(res.data)
        manifest.append(
            {
                "type": res.type_name,
                "id": res.res_id,
                "name": res.res_name,
                "offset": res.offset,
                "length": res.length,
                "file": str((dest / fname).relative_to(args.out)),
            }
        )

    (args.out / "resource_manifest.json").write_text(
        json.dumps(manifest, indent=2)
    )
    print(f"Wrote {len(manifest)} raw resource files, manifest -> extraction/resource_manifest.json")


if __name__ == "__main__":
    main()
