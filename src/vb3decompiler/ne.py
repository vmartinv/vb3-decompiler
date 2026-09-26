"""
NE (Windows 3.x New Executable) structure of a VB3 exe and VBRUN300.DLL:
segments with their relocations, resources (RT_RCDATA), the procedure
table (segment 3) and the form/VBX lists of RT_RCDATA 1. See OPCODES.md
"Procedure -> segment resolution" and RESOURCE_FORMAT.md.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Resource table
# ---------------------------------------------------------------------------

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


PROC_TABLE_SEGMENT = 3    # 1-based, in every VB3 exe tested

# ---------------------------------------------------------------------------
# NE parsing (segments + relocations)
# ---------------------------------------------------------------------------


@dataclass


class Reloc:
    src_type: int
    flags: int
    offset: int  # head of the fixup chain (non-additive) or the single site
    target1: int
    target2: int

    @property
    def kind(self) -> int:
        return self.flags & 3  # 0 INTREF, 1 IMPORD, 2 IMPNAME, 3 OSFIXUP

    @property
    def additive(self) -> bool:
        return bool(self.flags & 4)


@dataclass


class Segment:
    index: int
    length: int
    flags: int
    data: bytes
    relocs: list[Reloc] = field(default_factory=list)

    def fixup_sites(self, r: Reloc) -> list[int]:
        """All patched offsets for a relocation (walks the in-segment chain)."""
        if r.additive:
            return [r.offset]
        sites, o = [], r.offset
        while o != 0xFFFF and len(sites) < 0x10000:
            sites.append(o)
            (o,) = struct.unpack_from("<H", self.data, o)
        return sites


def parse_ne(path: Path) -> list[Segment]:
    raw = path.read_bytes()
    (ne,) = struct.unpack_from("<H", raw, 0x3C)
    segtab = ne + struct.unpack_from("<H", raw, ne + 0x22)[0]
    (shift,) = struct.unpack_from("<H", raw, ne + 0x32)
    (count,) = struct.unpack_from("<H", raw, ne + 0x1C)
    segs = []
    for i in range(count):
        off, length, flags, _ = struct.unpack_from("<HHHH", raw, segtab + 8 * i)
        length = length or 0x10000
        start = off << shift
        data = raw[start:start + length] if off else b""
        seg = Segment(i + 1, length, flags, data)
        if flags & 0x100 and off:
            p = start + length
            (n,) = struct.unpack_from("<H", raw, p)
            for k in range(n):
                seg.relocs.append(Reloc(*struct.unpack_from("<BBHHH", raw, p + 2 + 8 * k)))
        segs.append(seg)
    return segs


# ---------------------------------------------------------------------------
# Procedure table
# ---------------------------------------------------------------------------


@dataclass


class Proc:
    record: int  # offset of the procedure record in segment 3
    tag: int
    segment: int
    start: int
    end: int


def find_procs(segs: list[Segment]) -> list[Proc]:
    table = segs[PROC_TABLE_SEGMENT - 1]
    procs = []
    for r in table.relocs:
        if r.kind != 0 or r.additive:
            continue
        for site in table.fixup_sites(r):
            rec = site - 38
            (tag,) = struct.unpack_from("<H", table.data, rec)
            (start,) = struct.unpack_from("<H", table.data, rec + 24)
            (end,) = struct.unpack_from("<H", table.data, rec + 36)
            procs.append(Proc(rec, tag, r.target1, start, end))
    return sorted(procs, key=lambda p: (p.segment, p.start))


# ---------------------------------------------------------------------------
# Resources of a VB3 exe: RT_RCDATA blobs, the project directory (RT_RCDATA 1)
# ---------------------------------------------------------------------------


def rcdata(path: Path) -> dict[int, bytes]:
    """RT_RCDATA resources by id (1: project directory, 2: data images, then forms)."""
    return {r.res_id: r.data for r in NEFile(path).iter_resources() if r.type_name == "RT_RCDATA"}


def vbx_entries(res1: bytes) -> list[str]:
    """VBX files and the control classes they add, from the project directory:
    printable strings after the first `*.VBX` that aren't `*.FRM`."""
    strs = [m.group(0).decode("latin-1") for m in re.finditer(rb"[\x20-\x7e]{3,}", res1)]
    first = next((i for i, t in enumerate(strs) if t.upper().endswith(".VBX")), None)
    return [] if first is None else [t for t in strs[first:] if not t.upper().endswith((".FRM", ".BAS"))]


def form_names(res: dict[int, bytes]) -> list[list[str]]:
    """Name tables (form name, then one entry per control name, indexed by the
    controls' name index; empty entries are deleted controls), in project
    order: each form is a data blob (FF CC) followed by its name table."""
    out, ids = [], sorted(res)
    for a, b in zip(ids, ids[1:]):
        if res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc":
            names, pos, d = [], 0, res[b]
            while pos < len(d):  # zero-length entries = deleted controls
                names.append(d[pos + 1:pos + 1 + d[pos]].decode("latin-1"))
                pos += 1 + d[pos]
            out.append(names)
    return out
