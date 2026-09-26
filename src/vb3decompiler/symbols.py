"""
Symbol recovery for a compiled exe: control and form names/classes behind
control slots, procedure (event) names, object variable classes, late-bound
member names and OLE names. See OPCODES.md "Symbols".
"""
from __future__ import annotations

import re
import struct

from .ne import Segment, find_procs, form_names, vbx_entries
from .opcodes import NAMES
from .runtime import (
    Insn,
    Runtime,
    decode,
)

# Class byte in a form blob's control record (confirmed values only).
CLASS_BY_BLOB = {0x00: "PictureBox", 0x01: "Label", 0x02: "TextBox", 0x04: "CommandButton",
                 0x05: "CheckBox", 0x06: "OptionButton", 0x07: "ComboBox", 0x08: "ListBox", 0x09: "HScrollBar",
                 0x0B: "Timer", 0x10: "DriveListBox", 0x11: "DirListBox", 0x12: "FileListBox",
                 0x13: "Menu", 0x18: "Image", 0x03: "Frame", 0x0A: "VScrollBar", 0x16: "Shape", 0x17: "Line",
                 0x25: "Data"}  # 0xFF: VBX custom control
_CTL_RECORD = re.compile(rb"[\x01\x03](..)\x00\x00(.)\x00(.)\xff", re.S)
_CTL_RECORD_ANY = re.compile(rb"(?=[\x01-\x03]..(?:\x00\x00.\x00.|\x00\x80..\x00\x00.)"
                             rb"(?:[\x00-\x2f\xff]?\xff|(?<=\xff)[\x01-\x20][A-Za-z]))", re.S)


def blob_classes(res: dict[int, bytes]) -> dict[tuple[str, str], str]:
    """(form, control) -> class from each form blob's control records:
    `u8 flag 1-3, u16 length, u16 flags, u8 name index, u8 element, ...,
    class` with the class byte at +7 (+9 for control-array elements,
    flags & 0x8000); VBX controls (0xFF) name their class next."""
    out, ids = {}, sorted(res)
    for a, b in zip(ids, ids[1:]):
        if not (res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc"):
            continue
        d, names = res[a], form_names({a: res[a], b: res[b]})[0]
        starts, todo = set(), [m.start() for m in _CTL_RECORD_ANY.finditer(d)]
        while todo:  # follow the record chain (start + 1 + length, 00 separators)
            q = todo.pop()
            if q in starts or q + 8 > len(d) or d[q] not in (1, 2, 3):
                continue
            starts.add(q)
            nxt = q + 1 + struct.unpack_from("<H", d, q + 1)[0]  # length counts from after the flag
            while nxt < len(d) and d[nxt] == 0:
                nxt += 1
            todo.append(nxt)
        for q in sorted(starts):
            flags, idx = struct.unpack_from("<H", d, q + 3)[0], d[q + 5]
            if flags & ~0x8000 or not 0 < idx < len(names) or not names[idx]:
                continue
            at = q + (9 if flags & 0x8000 else 7)
            if at + 1 >= len(d):
                continue
            cb = d[at]
            cls = CLASS_BY_BLOB.get(cb)
            if cb == 0xFF:
                cls = d[at + 2:at + 2 + d[at + 1]].decode("latin-1")
            if cls:
                out.setdefault((names[0], names[idx]), cls)
    return out


def _slot_refs(segs: list[Segment], rt: Runtime) -> dict[int, dict[int, str]]:
    """code segment -> {slot: 'control' | 'form'} for the references its code makes."""
    out: dict[int, dict[int, str]] = {}
    for p in find_procs(segs):
        for i in decode(rt, segs[p.segment - 1].data, p)[0]:
            kind = {"CONTROL": "control", "CTLARRAY": "control", "CTLARRAY_GET": "control", "CTLARRAY_SET": "control",
                    "FORM": "form",
                    "OBJVAR": "objvar", "PGET_ME": "meprop", "PSET_ME": "meprop"}.get(NAMES.get(i.op))
            if kind is None and is_objarr(rt, i):
                kind = "objarr"
            if kind and not (kind == "objarr" and p.segment in out
                             and out[p.segment].get(struct.unpack_from("<H", i.operand, 2)[0]) not in (None, "objarr")):
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                out.setdefault(p.segment, {})[slot] = kind
    return out


def is_objarr(rt: Runtime, i: Insn) -> bool:
    """Unnamed array-element load/store (`argc, slot`) — e.g. `Forms(i)`,
    `Document(i)` of `Global Document() As New frmNotePad`."""
    return i.op not in NAMES and len(i.operand) == 4 and (rt.opcode_id(i.op) or 0) & 0xFF in (0x0E, 0x0F)


def objvar_kind(kind: str, w0: int, w1: int, w2: int) -> int | None:
    """Declared class kind of an object variable's data-image record, or None.
    Module-level `kind, 0, 0`; local `kind, frame offset, frame offset`
    (negative); parameter `kind, bp offset` (positive); a global used from
    another module (loaded by FORM) `kind, global offset`. Kind 1 = Form,
    4 = Control (generic, late-bound), else a control class kind."""
    ok = lambda k: k in (1, 4) or k in CLASS_BY_KIND
    if kind == "form" and 0 < w1 < 0x8000 and not w1 & 1 and ok(w0):
        return w0
    if kind not in ("objvar", "control"):
        return None
    if w1 == w2 == 0 and ok(w0):
        return w0
    if kind == "objvar" and w1 >= 0xFF00 and w2 >= 0xFF00 and ok(w0):
        return w0
    if kind == "objvar" and 6 <= w1 < 0x100 and not w1 & 1 and ok(w0):
        return w0
    return None


def resolve_symbols(segs: list[Segment], rt: Runtime, res: dict[int, bytes], st: Symbols) -> dict[int, dict[int, str]]:
    """code segment -> {slot: name} for control and form references.

    RT_RCDATA 2 holds each module's initial data image as a chunk
    `u16 length, 00 00, 1E 00, ...`; slot offsets count from the chunk
    start. A control slot holds `u16 kind, u16 0x8000|name index, u16 0`
    (name index into the form's name table); a form slot holds
    `u16 0x8000|NN, u16 global offset`, NN = base + the form's project
    index, base = the smallest NN in the global per-form run
    (`NN 80 00 00 00 00` x forms). Each segment's image is the first chunk
    (after the previous segment's) where every referenced slot is valid.
    Fills st's resolved tables (classes, objvar types, Me properties,
    segment and record forms, form classes)."""
    forms = form_names(res)
    d = res.get(2, b"")
    form_base, global_at = None, None
    for m in re.finditer(rb"(?:[\x00-\xff]\x80\x00\x00\x00\x00)+", d):
        nn = [m.group(0)[i] for i in range(0, len(m.group(0)), 6)]
        if global_at is None and any(x >= 0x40 for x in nn):
            global_at = m.start()  # global object table: lives in the global data block
        for k in range(len(nn) - len(forms) + 1):
            w = nn[k:k + len(forms)]
            if forms and min(w) >= 0x40 and sorted(w) == list(range(min(w), min(w) + len(forms))):
                form_base = min(w)
                break
        if form_base is not None:
            break
    if form_base is None:
        # Global object numbers: forms follow 0x46 + one per VBX file and one
        # per VBX control class (both listed in the project directory, RT_RCDATA 1).
        form_base = 0x46 + len(vbx_entries(res.get(1, b"")))
    chunks = [m.start() for m in re.finditer(rb"(?=..\x00\x00\x1e\x00)", d, re.S)]
    # The global data block (holding the global object table) is no module's image.
    chunks = [c for c in chunks
              if global_at is None or not c <= global_at < c + 2 + struct.unpack_from("<H", d, c)[0]]
    refs = _slot_refs(segs, rt)
    code_segs = sorted({p.segment for p in find_procs(segs)})

    def names_at(base: int, seg: int, fi: int) -> dict[int, str] | None:
        out, types, mep = {}, {}, {}
        for slot, kind in refs[seg].items():
            if base + slot + 6 > len(d):
                return None
            w0, w1, w2 = struct.unpack_from("<HHH", d, base + slot)
            if kind in ("objvar", "objarr") and w0 >> 8 == 0x80 and form_base is not None:
                # `As New frmX` / form-typed: `0x8000|NN, frame or global offset`
                k = (w0 & 0xFF) - form_base
                if 0 <= k < len(forms):
                    types[slot] = forms[k][0]
                elif kind == "objarr" and (w0 & 0xFF) in BUILTIN_OBJECTS:
                    types[slot] = BUILTIN_OBJECTS[w0 & 0xFF]
                continue
            if kind == "objarr":
                continue
            if kind == "meprop":  # PGET_ME/PSET_ME: form property `u16 0x40xx, u16 0xC0nn`,
                if w0 >> 8 == 0x40 and w1 >> 8 == 0xC0:  # or a control (its default property)
                    mep[slot] = w1 & 0xFF
                    continue
                kind = "control"
            tk = objvar_kind(kind, w0, w1, w2)
            if tk is not None:
                types[slot] = {1: "Form", 4: "Control"}.get(tk) or CLASS_BY_KIND[tk]
                continue
            if kind == "objvar":
                continue  # untyped (As Control/Form generic) or not in this image
            # the form's object property (ActiveForm, Controls)
            if kind == "control" and w0 >> 8 in (0x40, 0x60) and w1 >> 8 == 0xC0:
                mep[slot] = w1 & 0xFF
                continue
            if kind == "control":
                idx = w1 & 0x7FFF
                if not (w1 & 0x8000 and w2 == 0 and w0 >> 8 == 0x40) or fi < 0 \
                        or idx >= len(forms[fi]) or not forms[fi][idx]:
                    return None
                out[slot] = forms[fi][idx]
                st.resolved_classes[(forms[fi][0], forms[fi][idx])] = CLASS_BY_KIND.get(w0 & 0xFF, "?")
            else:
                if w0 >> 8 != 0x80:
                    continue  # object variable (Dim x As Control/Form), not a form
                k = (w0 & 0xFF) - form_base if form_base is not None else -1
                out[slot] = forms[k][0] if 0 <= k < len(forms) \
                    else BUILTIN_OBJECTS.get(w0 & 0xFF, f"obj#{w0 & 0xFF:#x}")
        st.objvar_types[seg] = types  # last evaluated candidate; the chosen one is re-evaluated
        st.meprops[seg] = mep
        return {**out, **{-k - 1: t for k, t in types.items()}} if out or types or mep else None

    # Segments are modules (no controls) then forms with code, in project
    # order; forms without code have no segment, so each segment's form is
    # the next one whose name table fits its control references.
    proc_names(segs, rt, res, st.record_form, st.form_class)
    form_index = {t[0]: k for k, t in enumerate(forms)}
    seg_known = {}
    for p in find_procs(segs):
        if p.record in st.record_form:
            seg_known[p.segment] = form_index.get(st.record_form[p.record])
    result, ci, fi = {}, 0, 0
    for seg in code_segs:
        if seg in seg_known and seg_known[seg] is not None:
            fi = seg_known[seg]
        if seg not in refs:
            continue
        uses_controls = "control" in refs[seg].values()
        cands = [seg_known[seg]] if seg_known.get(seg) is not None else range(fi, len(forms))
        for f in (cands if uses_controls else [-1]):
            scored = [(sum(k >= 0 for k in g), len(g), -j, j, g) for j in range(ci, len(chunks))
                      if (g := names_at(chunks[j], seg, f)) is not None]
            got = max(scored)[3:] if scored else None  # most names, then typed variables, earliest
            if got:
                names_at(chunks[got[0]], seg, f)  # leave st.objvar_types for the chosen image
                got = (got[0], {k: v for k, v in got[1].items() if k >= 0})
            if got:
                ci, result[seg] = got[0] + 1, got[1]
                if f >= 0:
                    st.seg_form[seg] = forms[f][0]
                    fi = f + 1
                break
    return result


_CTL_HEADER = re.compile(rb"[\x01\x03](..)\x00\x00(.)(.)(.)\xff", re.S)


def proc_names(segs: list[Segment], rt: Runtime, res: dict[int, bytes], record_form: dict[int, str] | None = None,
               form_class: dict[str, str] | None = None) -> dict[int, str]:
    """Procedure record -> event procedure name (`control_Event`,
    `Form_Event`). Each form blob's control records end with an event table:
    `FF, u8 count (= the class's event count), count x u16` where a
    non-zero entry is the handler's procedure record offset | 1. The owning
    control is the record ending with the table (`u8 flag 1/2/3, u16
    length, u16 flags, u8 name index, ...`, class at +7, or +9 for a
    control-array element, flags & 0x8000); tables outside any control
    record are the form's own. Procedures not found here are general
    Sub/Function procedures (their names aren't stored). Fills record_form
    (procedure record -> form) and form_class (form -> Form | MDIForm)."""
    record_form = {} if record_form is None else record_form
    form_class = {} if form_class is None else form_class
    records = {p.record for p in find_procs(segs)}
    rt.load_project_vbx(res)
    events = rt.event_lists()
    counts = {len(v) for v in events.values()}
    out, ids = {}, sorted(res)
    for a, b in zip(ids, ids[1:]):
        if not (res[a][:2] == b"\xff\xcc" and res[b][:2] != b"\xff\xcc"):
            continue
        d, names = res[a], form_names({a: res[a], b: res[b]})[0]
        for p in range(1, len(d) - 2):
            n = d[p]
            if d[p - 1] != 0xFF or n not in counts or p + 1 + 2 * n > len(d):
                continue
            ents = struct.unpack_from(f"<{n}H", d, p + 1)
            if not any(ents) or not all(e == 0 or (e & 1 and e & ~1 in records) for e in ents):
                continue
            end = p + 1 + 2 * n
            def is_hdr(q: int) -> bool:  # a control record's header (not bytes inside another's properties)
                if d[q] not in (1, 2, 3, 5) or q + 2 + struct.unpack_from("<H", d, q + 1)[0] not in (end, end + 1):
                    return False
                at = q + (9 if struct.unpack_from("<H", d, q + 3)[0] & 0x8000 else 7)
                return d[q + 5] < len(names) and bool(names[d[q + 5]]) and at < len(d) \
                    and (d[at] in CLASS_BY_BLOB or d[at] == 0xFF)
            # (VBX records hold bitmaps)
            hdr = next((q for q in range(p - 3, max(0, p - 0x10000), -1) if is_hdr(q)), None)
            if hdr is not None:
                flags = struct.unpack_from("<H", d, hdr + 3)[0]
                idx = d[hdr + 5]
                at = hdr + (9 if flags & 0x8000 else 7)  # array element: + u8 elem, u16
                cb = d[at]
                ctl = names[idx] if idx < len(names) and names[idx] else f"ctl#{idx}"
                cls = CLASS_BY_BLOB.get(cb)
                if cb == 0xFF:  # VBX control: class name follows as a Pascal string
                    cls = d[at + 2:at + 2 + d[at + 1]].decode("latin-1")
            else:  # the form's own table: Form or MDIForm, by event count
                ctl = cls = "MDIForm" if len(events.get("MDIForm", [])) == n != len(events.get("Form", [])) else "Form"
                form_class[names[0]] = cls
            evs = events.get(cls, [])
            if len(evs) != n:  # unknown class (e.g. a VBX control): don't guess names
                evs = []
            for k, e in enumerate(ents):
                if e:
                    out.setdefault(e & ~1, f"{ctl}_{evs[k] if k < len(evs) else f'Event{k}'}")
                    record_form[e & ~1] = names[0]
    return out


# Slot kind byte -> control class (even: single control, odd: control array).
CLASS_BY_KIND = {k: c for c, ks in {
    "PictureBox": (0x1A, 0x1B), "Label": (0x1C, 0x1D), "TextBox": (0x1E, 0x1F),
    "Frame": (0x20, 0x21), "CommandButton": (0x22, 0x23), "CheckBox": (0x24, 0x25),
    "OptionButton": (0x26, 0x27), "ComboBox": (0x28, 0x29), "ListBox": (0x2A, 0x2B),
    "HScrollBar": (0x2C, 0x2D), "VScrollBar": (0x2E, 0x2F), "Timer": (0x30, 0x31),
    "DriveListBox": (0x34, 0x35), "DirListBox": (0x36, 0x37), "FileListBox": (0x38, 0x39),
    "Menu": (0x3B, 0x3C), "Shape": (0x3E, 0x3F), "Line": (0x40, 0x41),
    "Image": (0x42, 0x43), "Data": (0x44, 0x45)}.items() for k in ks}

# Class of objects returned by object-valued properties.
OBJECT_PROPERTY_CLASS = {"Recordset": "Dynaset", "ActiveForm": "Form", "ActiveControl": None}

# FORM also pushes VB's built-in objects; their NN (outside the form range):
BUILTIN_OBJECTS: dict[int, str] = {0x08: "Forms", 0x32: "Printer", 0x33: "Screen", 0x34: "Clipboard", 0x3D: "App"}


def late_bound_names(res1: bytes, rt: Runtime) -> dict[int, str]:
    """Late-bound property number -> name. Properties used through object
    variables (`Dim c As Control`) are numbered in first-use order, and the
    project directory (RT_RCDATA 1) stores, per class, that class's
    property-list index for each of them: `58 <class#> 00 00, kind, kind,
    47 00 00 | 47 03 00 <pstr VBX class>, u16 n, n x number, n x index`.
    The names follow from the classes' property lists."""
    props = rt.property_lists()
    out: dict[int, str] = {}
    for m in re.finditer(rb"\x58(.)\x00\x00(..)(..)\x47(\x00\x00|\x03\x00)", res1, re.S):
        q, cls = m.end(), None
        if m.group(4) == b"\x03\x00":  # VBX class: Pascal-ish name (length includes NUL)
            ln = res1[q]
            cls = res1[q + 1:q + ln].rstrip(b"\0").decode("latin-1")
            q += 1 + ln
        else:
            cls = CLASS_BY_KIND.get(struct.unpack_from("<H", m.group(2))[0])
        if q + 2 > len(res1) or cls not in props:
            continue
        (n,) = struct.unpack_from("<H", res1, q)
        if not 0 < n < 64 or q + 2 + 4 * n > len(res1):
            continue
        nums = struct.unpack_from(f"<{n}H", res1, q + 2)
        idxs = struct.unpack_from(f"<{n}H", res1, q + 2 + 2 * n)
        for num, idx in zip(nums, idxs):
            if idx < len(props[cls]) and props[cls][idx]:
                out.setdefault(num, props[cls][idx])
    return out


def late_bound_controls(res1: bytes, forms: list[list[str]]) -> dict[int, str]:
    """Late-bound control names (`frmMDI.ActiveForm.Text1`: SUBOBJ 0x00nn)
    share the late-bound numbering; each form's directory entry (`...
    NAME.FRM\0`, forms in project order) ends with `u16 n, n x number,
    n x index into the form's name table`."""
    out: dict[int, str] = {}
    for k, m in enumerate(re.finditer(rb"[\x20-\x7e]+\.FRM\x00", res1, re.I)):
        q = m.end()
        if k >= len(forms) or q + 2 > len(res1):
            break
        (n,) = struct.unpack_from("<H", res1, q)
        if not 0 < n < 64 or q + 2 + 4 * n > len(res1):
            continue
        nums = struct.unpack_from(f"<{n}H", res1, q + 2)
        idxs = struct.unpack_from(f"<{n}H", res1, q + 2 + 2 * n)
        for num, idx in zip(nums, idxs):
            if idx < len(forms[k]) and forms[k][idx]:
                out.setdefault(num, forms[k][idx])
    return out


def ole_names(res3: bytes) -> dict[int, str]:
    """OLE Automation member names (late-bound on `As Object` variables):
    RT_RCDATA 3 is `u16 length, NUL-terminated names`; PGET/PSET 0x00nn and
    PGET_IDX/PSET_IDX/OLE_CALL name operands are byte offsets into it."""
    out, q = {}, 2
    end = min(len(res3), 2 + struct.unpack_from("<H", res3)[0]) if len(res3) >= 2 else 0
    while q < end and res3[q]:
        z = res3.index(b"\0", q)
        out[q] = res3[q:z].decode("latin-1")
        q = z + 1
    return out


class Symbols:
    """Names for one executable's control/form references and properties."""

    def __init__(self, rt: Runtime, segs: list[Segment], res: dict[int, bytes]):
        self.rt = rt
        # filled by resolve_symbols
        self.resolved_classes: dict[tuple[str, str], str] = {}  # (form, control) -> class, as resolved
        self.seg_form: dict[int, str] = {}  # code segment -> its form, as resolved
        self.record_form: dict[int, str] = {}  # procedure record -> form (from event tables)
        self.objvar_types: dict[int, dict[int, str]] = {}  # code segment -> {slot: declared class}
        self.meprops: dict[int, dict[int, int]] = {}  # code segment -> {PGET_ME slot: property index}
        self.form_class: dict[str, str] = {}  # form -> Form | MDIForm (from its own event table)
        self.controls = resolve_symbols(segs, rt, res, self)
        self.tables = {t[0]: t for t in form_names(res)}
        self.late = late_bound_names(res.get(1, b""), rt)
        self.ole = ole_names(res.get(3, b""))
        self.late_controls = late_bound_controls(res.get(1, b""), form_names(res))
        self.classes = dict(blob_classes(res))  # blob records first (authoritative)
        for key, cls in self.resolved_classes.items():
            if key[0] in self.tables:
                self.classes.setdefault(key, cls)

    def annotate(self, seg: int, insns: list[Insn]) -> list[str]:
        """Per instruction: symbolic text ('' if none) — control/form names,
        `form!control`, `Class.Property` for PGET/PSET."""
        out, other, cls = [], None, None
        sym = self.controls.get(seg, {})
        form_of_seg = self.seg_form.get(seg)
        for i in insns:
            n = NAMES.get(i.op)
            text = ""
            if n in ("CONTROL", "CTLARRAY", "CTLARRAY_GET", "CTLARRAY_SET", "FORM") and i.operand:
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                text = sym.get(slot, "")
                idx = self.meprops.get(seg, {}).get(slot) if n in ("CONTROL", "CTLARRAY") else None
                props = self.rt.property_lists().get(self.form_class.get(form_of_seg, "Form"), [])
                if not text and idx == 0xFE:
                    text = "Controls"  # the form's control collection
                elif not text and idx is not None and idx < len(props) and props[idx]:
                    text = props[idx]  # an object-valued property of the form itself: `ActiveForm`
            elif n in ("PGET", "PSET", "PGET_IDX", "PSET_IDX") and len(i.operand) >= 2:
                nn = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                props = self.rt.property_lists().get(cls, []) if cls else []
                if nn == 0xC0FD:  # collection (Forms, Controls)
                    text = f"{cls or '?'}.Count"
                elif nn >> 8 == 0x80 and other and (nn & 0xFF) < len(other):
                    text = f"{other[0]}!{other[nn & 0xFF]}"  # control array element: default property
                elif nn >> 8 == 0xC0 and (nn & 0xFF) < len(props):
                    text = f"{cls}.{props[nn & 0xFF]}"
                elif nn >> 8 == 0 and nn in self.ole and cls != "Control":  # OLE Automation
                    text = f"Object.{self.ole[nn]}"
                elif nn >> 8 == 0 and nn in self.late:  # late-bound (object variable)
                    text = f"?.{self.late[nn]}"
            elif n in ("PGET_ME", "PSET_ME") and i.operand:  # implicit form: `Left`; control: `Label1`
                slot = struct.unpack_from("<H", i.operand)[0]
                idx = self.meprops.get(seg, {}).get(slot)
                text = sym.get(slot, "")
                fcls = self.form_class.get(form_of_seg, "Form")
                props = self.rt.property_lists().get(fcls, [])
                if idx is not None and idx < len(props) and props[idx]:
                    text = f"{fcls}.{props[idx]}"
            elif n == "OLE_CALL" and len(i.operand) >= 4:
                text = f"Object.{self.ole.get(struct.unpack_from('<H', i.operand, 2)[0], '?')}"
            elif n in ("CTLARRAY_OF", "SUBOBJ") and len(i.operand) >= 2:
                idx = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                if idx == 0xC0FE:
                    text = "Form.Controls"
                elif idx >> 8 == 0 and idx in self.late_controls:  # late-bound control
                    text = f"?!{self.late_controls[idx]}"
                elif other and idx & 0xC000 != 0xC000 and (idx & 0x3FFF) < len(other):
                    text = f"{other[0]}!{other[idx & 0x3FFF]}"
                elif idx & 0xC000 == 0xC000 and cls:
                    props = self.rt.property_lists().get(cls, [])
                    text = f"{cls}.{props[idx & 0xFF]}" if (idx & 0xFF) < len(props) else ""
            out.append(text)
            # object class for the next property access
            if n == "FORM":
                target = text
                other = self.tables.get(target)
                typed = self.objvar_types.get(seg, {}).get(
                    struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]) if i.operand else None
                cls = typed or (self.form_class.get(target, "Form") if target in self.tables else target)
            elif (n in ("CONTROL", "CTLARRAY", "CTLARRAY_GET", "CTLARRAY_SET", "OBJVAR")
                  or n is None and is_objarr(self.rt, i)) and i.operand:
                slot = struct.unpack_from("<H", i.operand, len(i.operand) - 2)[0]
                typed = self.objvar_types.get(seg, {}).get(slot)
                if typed in self.tables:  # As New frmX (variable or array element)
                    cls, other = self.form_class.get(typed, "Form"), self.tables[typed]
                elif n is None and (typed or sym.get(slot)) == "Forms":  # Forms(i)
                    cls, other = "Form", None
                else:
                    cls = typed or (self.classes.get((form_of_seg, text)) if text else None) \
                        or OBJECT_PROPERTY_CLASS.get(text)
                    other = self.tables.get(form_of_seg) if typed == "Form" else None
            elif n in ("CTLARRAY_OF", "SUBOBJ"):
                f, _, c = text.partition("!")
                if c:
                    cls = self.classes.get((f, c))
                elif text == "Form.Controls" or text.startswith("?!"):  # .Controls / .Controls(i) / late-bound
                    cls = "Controls" if n == "SUBOBJ" and not text.startswith("?!") else "Control"
                else:  # object-valued property (e.g. Data.Recordset)
                    cls = OBJECT_PROPERTY_CLASS.get(text.split(".")[-1])
                other = None
            elif n in ("ME", "ME_IMPLICIT"):
                cls = self.form_class.get(form_of_seg, "Form")
                other = self.tables.get(form_of_seg)
            elif n in ("OBJ", "OBJ_SELF", "PGET", "PGET_IDX") or (n or "").startswith(("LOAD", "ALOAD")):
                if n not in ("OBJ", "OBJ_SELF"):
                    cls = None  # object of unknown class (variable, property result)
                    other = None
        return out
