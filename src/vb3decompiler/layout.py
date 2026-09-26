"""
Module list and data-image layout: where each module's image, init list and
slots are, and readers for slot values, array descriptors and constants.
"""
from __future__ import annotations

import re
import struct

from .dataimage import MOD_SIZE, GlobalImage, const_literal, word
from .model import OBJ_KINDS, pool_name
from .ne import vbx_entries
from .records import (
    DECL_DEFTYPE,
    DECL_FLAGS,
    DECLARE_ENTRY,
    DECLARE_FLAGS,
    OPTION_EXPLICIT,
    PROC_FLAGS,
    PROC_KIND,
    RECORD_SIZE,
    decl_record,
)
from .runtime import RECORD_FORM, SEG_IMAGE
from .symbols import CLASS_BY_KIND


def image_layout(image: bytes, nforms: int) -> dict:
    """RT_RCDATA 2 structure: chunks `u16 len, 00 00, 1E 00, ...`:
    global image; name pool (u16 size, 32-bucket hash table, entries `u16
    link, u8 flag, u8 len, name`; offsets from its start + 2); then the .bas
    module images, then the form images. The global image and each module
    image can be followed by an init list: `u16 len, u16 count, 1E 00`, count
    words (fixed-size arrays and String constants, see OPCODES.md); `lists`
    maps an image to its list."""
    first = re.search(rb"..\x00\x00\x1e\x00", image, re.S)
    out = dict(global_=first.start() if first else None, pool=None, modules=[], forms=[], lists={})
    if not first:
        return out
    # walk: chunks are `u16 len, u16, u16 0x1E`; the name pool (`u16 size, 0,
    # 0x1A`) sits between the global image's chunks and the modules
    chunks, c, pre = [], first.start(), True
    while c + 6 <= len(image):
        n, tag = struct.unpack_from("<H", image, c)[0], struct.unpack_from("<H", image, c + 4)[0]
        if tag == 0x1A and out["pool"] is None:
            out["pool"] = c
            c += 2  # the pool's size word doesn't cover its last 2 bytes (the next image's prefix)
            pre = True
        elif tag != 0x1E:
            if c + 8 <= len(image) and struct.unpack_from("<H", image, c + 6)[0] in (0x1E, 0x1A):
                c += 2  # the word before a module image (its declarations record - 4)
                pre = True
                continue
            break
        else:
            chunks.append((c, n, pre))
            pre = False
        c += 2 + n
    # an image (the global one, or after its 2-byte prefix) can be followed by
    # a chunk of (u8 type, u16 slot) triples, then by its init list
    prev, rest = None, []
    for c, n, pre in chunks:
        if pre:
            prev = c
            if c != first.start() and (out["pool"] is None or c > out["pool"]):
                rest.append(c)
        elif n == 4 + 2 * struct.unpack_from("<H", image, c + 2)[0] and prev is not None:
            out["lists"].setdefault(prev, c)  # an init list (4 bytes: empty)
    # a form image starts with 16 zero bytes, then the form's own record at 0x16
    is_form = [struct.unpack_from("<H", image, c)[0] >= 0x1A and not any(image[c + 6:c + 0x16])
               and image[c + 0x16] != 0 for c in rest]
    idx = [j for j, f in enumerate(is_form) if f][-nforms:] if nforms else []
    first = idx[0] if idx else len(rest)
    out["modules"] = rest[:first]
    out["forms"] = [(rest[j], out["lists"].get(rest[j])) for j in idx]
    return out


class LayoutMixin:
    # --- modules ----------------------------------------------------------
    def value(self, base: int, slot: int, signed: bool = True) -> int:
        o = base + slot + 2
        if o + 2 > len(self.image):
            return 0
        return struct.unpack_from("<h" if signed else "<H", self.image, o)[0]

    def is_record(self, r: int) -> bool:
        return r in self.by_record or self.is_declare(r)

    def is_declare(self, r: int) -> bool:
        t = self.table
        return 0 <= r <= len(t) - RECORD_SIZE and r % 8 == 0 and t[r + PROC_FLAGS] == DECLARE_FLAGS \
            and t[r + PROC_KIND] in (1, 2) \
            and self.pool is not None and pool_name(self.image, self.pool, word(t, r + DECLARE_ENTRY)).isprintable()

    def module_list(self) -> list[dict]:
        """Every module (from the data images) with its code segment, if any."""
        if not self.image:
            raise ValueError(f"{self.exe}: no data images (RT_RCDATA 2): not a complete VB3 executable")
        lay = image_layout(self.image, len(self.forms))
        self.pool = lay["pool"]
        self.lists = lay["lists"]
        self.gimg_chunk = lay["global_"]
        self.gimg = GlobalImage(self.image, lay["global_"])
        mods = [dict(kind="bas", image=c, form=None, seg=None, start=0x06) for c in lay["modules"]]
        mods += [dict(kind="frm", image=c, form=self.forms[k][0], seg=None, start=0x1A, ctl=cl)
                 for k, (c, cl) in enumerate(lay["forms"])]
        for m in mods:
            rec = decl_record(self.image, m["image"])
            m["explicit"] = bool(word(self.table, rec + DECL_FLAGS) & OPTION_EXPLICIT)
            m["defint"] = word(self.table, rec + DECL_DEFTYPE) != 0xFFFF  # the samples' only DefType: DefInt A-Z
        decl_recs = sorted(decl_record(self.image, m["image"]) for m in mods)

        def owner(r: int) -> int:  # a module's records follow its declarations record
            return max((r0 for r0 in decl_recs if r0 < r), default=-1)

        for m in mods:  # Function/Declare slots: record offsets (sorted by name); a slot
            m["funcs"], s = [], m["start"]  # holding another module's procedure is a call's
            me = word(self.image, m["image"] - 2) + 4
            while self.is_record(r := self.value(m["image"], s, False)) and \
                    (r not in self.by_record or owner(r) == me):
                m["funcs"].append((s, self.value(m["image"], s, False)))
                s += 2
            m["decl_start"] = s
        segs = sorted({p.segment for p in self.procs})
        free = [m for m in mods if m["kind"] == "bas"]
        # a module's procedure records follow its declarations record in the table
        starts = sorted((word(self.image, m["image"] - 2) + 4, k) for k, m in enumerate(mods))
        for seg in segs:
            recs = {p.record for p in self.procs if p.segment == seg}
            own = {max((k for r0, k in starts if r0 < r), default=None) for r in recs}
            if len(own) == 1 and None not in own and mods[k := own.pop()]["seg"] is None:
                mods[k]["seg"] = seg
                SEG_IMAGE[seg] = mods[k]["image"]
                if mods[k] in free:
                    free.remove(mods[k])
                continue
            form = self.sym.seg_form.get(seg) or next((RECORD_FORM[r] for r in recs if r in RECORD_FORM), None)
            if form:
                m = next((m for m in mods if m["form"] == form), None)
            else:
                m = next((m for m in free if recs & {r for _, r in m["funcs"]}), None) or \
                    next((m for m in free if m["seg"] is None and not m["funcs"]), None)
            if m is not None:
                m["seg"] = seg
                SEG_IMAGE[seg] = m["image"]
                if m in free:
                    free.remove(m)
        for m in mods:  # forms whose code names no control (`Me.Text1` resolves by the form)
            if m["seg"] and m["form"]:
                self.sym.seg_form.setdefault(m["seg"], m["form"])
        return mods

    def global_desc(self, base: int, s: int) -> int:
        """Bytes of a Global array's descriptor at s in its declaring .bas
        (after the global offset): `0x4000 | dims` (fixed size; the bounds are
        in the global image) or 0 (dynamic), then `0xC000 | element type`;
        0 if none."""
        w, f = self.value(base, s, False), self.value(base, s + 2, False)
        fixed = w & 0xFF00 == 0x4000 and 1 <= w & 0xFF <= 60
        return 4 if (fixed or w == 0) and f >> 8 == 0xC0 and 0 <= f & 0xFF <= 9 else 0

    def init_list(self, chunk: int | None) -> set[int] | None:
        """The entries of the init list after the image at chunk, if any."""
        c = self.lists.get(chunk) if chunk is not None else None
        if c is None:
            return None
        return {word(self.image, c + 6 + 2 * k) for k in range(word(self.image, c + 2))}

    def is_global_slot(self, base: int, slot: int) -> bool:
        g = self.value(base, slot, False)
        return 6 <= g < self.globals_end and g % 2 == 0

    @property
    def globals_end(self) -> int:
        """End of the global variables: the global object table (`0x80NN, 0, 0`
        per form/object) follows them."""
        gl = self.gimg
        for g in range(6, gl.size, 2):
            if gl.w(g) >> 8 == 0x80 and gl.w(g + 2) == 0 and gl.w(g + 4) == 0 \
                    and all(gl.w(x) >> 8 == 0x80 for x in range(g, gl.size - 4, 6)):
                return g
        return gl.size

    def array_at(self, base: int, slot: int) -> tuple[str, bool, int] | None:
        """A fixed-size array's descriptor at slot: (element type, Static, type
        code). Flags word (+4): element type in the low byte (1 Integer ..
        7 String, 8 String * n, 9 object: length / object kind in the slot
        before), 0xC2 Static, 0xC1 module level."""
        w, f = word(self.image, base + slot + 4), word(self.image, base + slot + 6)
        if w & 0xFF00 != 0x4000 or not 1 <= w & 0xFF <= 60 or f >> 8 not in (0xC1, 0xC2) or not 1 <= f & 0xFF <= 9:
            return None
        t, x = f & 0xFF, self.value(base, slot - 2, False)
        name = {1: "Integer", 2: "Long", 3: "Single", 4: "Double", 5: "Currency", 6: "Variant", 7: "String"}.get(t)
        if t == 8:
            name = f"String * {x}"
        elif t == 9:
            fbase = 0x46 + len(vbx_entries(self.res.get(1, b"")))
            name = OBJ_KINDS.get(x) or CLASS_BY_KIND.get(x) or \
                (self.forms[x - fbase][0] if 0 <= x - fbase < len(self.forms) else "Control")
        return name, f >> 8 == 0xC2, t

    def array_dims(self, base: int, slot: int, absolute: int | None = None) -> tuple[str, int]:
        """An array's descriptor (inline, from slot + 2): word +2 is 0x4000 |
        dimensions for a fixed-size array, whose last words are (count, lower
        bound) per dimension, last dimension first; size 18 + 4 per dimension.
        A dynamic array reserves 8 dimensions (50 bytes)."""
        d = base + slot + 2 if absolute is None else absolute
        w = word(self.image, d + 2)
        if not w & 0x4000:
            return "", 50
        n = w & 0xFF
        pairs = [(word(self.image, d + 18 + 4 * j), word(self.image, d + 20 + 4 * j, True)) for j in range(n)]
        dims = [f"{lo + cnt - 1}" if lo == 0 else f"{lo} To {lo + cnt - 1}" for cnt, lo in reversed(pairs)]
        return ", ".join(dims), 18 + 4 * n

    def inline_const(self, base: int, slot: int, t: str, room: int, zero: bool = False) -> str | None:
        n = MOD_SIZE.get(t, 0)
        raw = self.image[base + slot + 2:base + slot + 2 + n]
        return const_literal(t, raw) if n and n <= room and (zero or any(raw)) else None
