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

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

# Predefined NE resource type IDs (high bit of rtTypeID set => integer type,
# low byte is one of these). See MSDN "NE Executable Format" / Wine's
# include/winnt.h RT_* constants.
PREDEFINED_TYPES = {
    1: "RT_CURSOR",
    2: "RT_BITMAP",
    3: "RT_ICON",
    4: "RT_MENU",
    5: "RT_DIALOG",
    6: "RT_STRING",
    7: "RT_FONTDIR",
    8: "RT_FONT",
    9: "RT_ACCELERATOR",
    10: "RT_RCDATA",
    11: "RT_MESSAGETABLE",
    12: "RT_GROUP_CURSOR",
    14: "RT_GROUP_ICON",
    15: "RT_NAMETABLE",
    16: "RT_VERSION",
}


@dataclass
class NamedResource:
    type_id: int
    type_name: str  # resolved PREDEFINED_TYPES name, or "TYPE_<n>"/string name
    res_id: int | None  # integer id, if rnID had the high bit set
    res_name: str | None  # string name, if not an integer id
    offset: int  # absolute file offset of raw resource data
    length: int
    data: bytes = field(repr=False)


class NEFile:
    def __init__(self, path: Path):
        self.path = path
        self.raw = path.read_bytes()
        self._parse_mz_header()
        self._parse_ne_header()

    def _parse_mz_header(self):
        if self.raw[0:2] != b"MZ":
            raise ValueError("Not an MZ/NE executable (missing 'MZ' signature)")
        (self.ne_header_offset,) = struct.unpack_from("<H", self.raw, 0x3C)

    def _parse_ne_header(self):
        off = self.ne_header_offset
        sig = self.raw[off : off + 2]
        if sig != b"NE":
            raise ValueError(f"No 'NE' signature at offset 0x{off:x} (got {sig!r})")

        # Field layout per the NE header spec (all offsets relative to `off`
        # unless noted). We only unpack what we actually need for resource
        # extraction; segment/entry table parsing can be added later if the
        # p-code disassembly phase needs it.
        (
            self.ver,
            self.rev,
            self.enttab,
            self.cbenttab,
            self.crc,
            self.flags,
            self.autodata,
            self.heap,
            self.stack,
            self.csip,
            self.sssp,
            self.cseg,
            self.cmod,
            self.cbnrestab,
            self.segtab,
            self.rsrctab,
            self.restab,
            self.modtab,
            self.imptab,
            self.nrestab,
            self.cmovent,
            self.align_shift,
            self.cres,
            self.exetyp,
            self.flagsothers,
        ) = struct.unpack_from("<BBHHIHHHHIIHHHHHHHHIHHHBB", self.raw, off + 2)

        self.rsrctab_abs = off + self.rsrctab
        self.restab_abs = off + self.restab

    def iter_resources(self):
        """Yield NamedResource for every entry in the resource table."""
        pos = self.rsrctab_abs
        (align_shift,) = struct.unpack_from("<H", self.raw, pos)
        pos += 2

        while True:
            type_id, res_count, _reserved = struct.unpack_from("<HHI", self.raw, pos)
            pos += 8
            if type_id == 0:
                break  # terminator TYPEINFO

            if type_id & 0x8000:
                numeric_type = type_id & 0x7FFF
                type_name = PREDEFINED_TYPES.get(numeric_type, f"TYPE_{numeric_type}")
            else:
                # type_id is an offset (relative to rsrctab_abs) to a
                # length-prefixed name string identifying a custom type.
                type_name = self._read_pstring(self.rsrctab_abs + type_id)

            for _ in range(res_count):
                rn_offset, rn_length, rn_flags, rn_id, _handle, _usage = (
                    struct.unpack_from("<HHHHHH", self.raw, pos)
                )
                pos += 12

                if rn_id & 0x8000:
                    res_id = rn_id & 0x7FFF
                    res_name = None
                else:
                    res_id = None
                    res_name = self._read_pstring(self.rsrctab_abs + rn_id)

                abs_offset = rn_offset << align_shift
                length = rn_length << align_shift
                if length == 0:
                    # Genuine empty/reserved slot left by the linker (seen at
                    # least once in qrace.exe, alongside a gap in the id
                    # sequence) -- nothing to extract.
                    continue
                data = self.raw[abs_offset : abs_offset + length]

                yield NamedResource(
                    type_id=type_id & 0x7FFF if type_id & 0x8000 else type_id,
                    type_name=type_name,
                    res_id=res_id,
                    res_name=res_name,
                    offset=abs_offset,
                    length=length,
                    data=data,
                )

    def _read_pstring(self, pos: int) -> str:
        length = self.raw[pos]
        return self.raw[pos + 1 : pos + 1 + length].decode("latin-1")


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
