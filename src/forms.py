"""
Compiled form layouts (per-form RT_RCDATA data blob, see RESOURCE_FORMAT.md)
decoded to control trees and .frm description text.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ne import form_names, rcdata
from runtime import PROP_STD, PROP_TYPES, Runtime


# Record class byte -> standard class.
CLASS_IDS = {
    0x00: "PictureBox", 0x01: "Label", 0x02: "TextBox", 0x03: "Frame", 0x04: "CommandButton",
    0x05: "CheckBox", 0x06: "OptionButton", 0x07: "ComboBox", 0x08: "ListBox", 0x09: "HScrollBar",
    0x0A: "VScrollBar", 0x0B: "Timer", 0x0D: "Form", 0x10: "DriveListBox", 0x11: "DirListBox",
    0x12: "FileListBox", 0x13: "Menu", 0x14: "MDIForm", 0x16: "Shape", 0x17: "Line", 0x18: "Image", 0x25: "Data",
}

# PROPINFO data types (DT_*)
DT_HSZ, DT_SHORT, DT_LONG, DT_BOOL, DT_COLOR, DT_ENUM, DT_REAL = 1, 2, 3, 4, 5, 6, 7
DT_XPOS, DT_XSIZE, DT_YPOS, DT_YSIZE, DT_PICTURE = 8, 9, 10, 11, 12
DT_HLSTR, DT_INDEX = 0x0D, 0x3D


@dataclass


class Control:
    cls: str
    name: str
    idx: int  # name-table index
    props: list = field(default_factory=list)  # (name, value) in record order
    children: list = field(default_factory=list)
    raw: bytes = b""


class FormDecoder:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.plists = rt.property_lists()

    def props(self, cls: str, b: bytes, pos: int, form: bool, prior: list = ()) -> tuple[list, int]:
        names, types = self.plists.get(cls, []), PROP_TYPES.get(cls, [])
        out: list = []
        while b[pos] != 0xFF:
            pid = b[pos]
            pos += 1
            name = names[pid] if pid < len(names) else f"#{pid:02x}"
            name = f"_{pid}" if name is None else name
            t = types[pid] if pid < len(types) else None
            if t is not None and t & 0x80:  # flag bit over the data type (e.g. Label.Alignment 0x86)
                t &= 0x7F
            std = PROP_STD.get(cls, [])
            if name == "Left" and (names[pid + 1:pid + 4] == ["Top", "Width", "Height"]
                                   or pid < len(std) and std[pid] and cls != "Timer"):
                size = 4 if form else 2  # Left, Top, Width, Height as one record
                vals = [int.from_bytes(b[pos + size * i:pos + size * (i + 1)], "little", signed=True)
                        for i in range(4)]
                out += [(k, v) for k, v in zip(("Left", "Top", "Width", "Height"), vals) if k in names]
                pos += 4 * size
                continue
            if name == "FontName":  # FontName, f32 FontSize, u8 style flags
                n = b[pos]
                out.append((name, b[pos + 1:pos + 1 + n].decode("latin-1")))
                pos += 1 + n
                out.append(("FontSize", struct.unpack_from("<f", b, pos)[0]))
                out.append(("_FontFlags", b[pos + 4]))
                pos += 5
                continue
            if name == "ScaleMode":  # u16 mode, [4 x f32 if user], u16 graphics flags
                mode = struct.unpack_from("<H", b, pos)[0]
                out.append((name, mode))
                pos += 2
                if mode == 0:
                    out += list(zip(("ScaleLeft", "ScaleTop", "_ScaleX", "_ScaleY"), struct.unpack_from("<4f", b, pos)))
                    pos += 16
                out.append(("_GFlags", struct.unpack_from("<H", b, pos)[0]))
                pos += 2
                continue
            if (cls, name) == ("PictureClip", "Location"):  # saved by the VBX: 4 x i32, text "x,y,a,b" from (a, b, x, y)
                a, b_, x, y = struct.unpack_from("<4i", b, pos)
                out.append((name, f"{x},{y},{a},{b_}"))
                pos += 16
                continue
            if (cls, name) == ("OLE", "OleObjectBlob") and b[pos] == 0x30:  # saved by the VBX: an empty
                out.append(("_OleObjectBlob", b[pos]))  # object's one byte, rewritten by the IDE
                pos += 1
                continue
            if t == DT_PICTURE:  # i32 size (-1: none), then the picture file
                n = struct.unpack_from("<i", b, pos)[0]
                out.append((name, b[pos + 4:pos + 4 + n] if n > 0 else None))
                pos += 4 + max(n, 0)
                continue
            if t == DT_HSZ and cls == "ComboBox" and name == "Text" and dict([*prior, *out]).get("Style") == 2:
                continue  # a drop-down list's Text is saved without a value
            if t in (DT_HSZ, DT_HLSTR):
                n = b[pos]
                out.append((name, b[pos + 1:pos + 1 + n].decode("latin-1")))
                pos += 1 + n
            elif t in (DT_BOOL, DT_ENUM):
                out.append((name, b[pos] if t == DT_ENUM else (-1 if b[pos] else 0)))
                pos += 1
            elif t in (DT_SHORT, DT_XPOS, DT_XSIZE, DT_YPOS, DT_YSIZE, DT_INDEX):
                size = 2 if t in (DT_SHORT, DT_INDEX) else 4
                out.append((name, int.from_bytes(b[pos:pos + size], "little", signed=True)))
                pos += size
            elif t in (DT_LONG, DT_COLOR):
                out.append((name, struct.unpack_from("<i", b, pos)[0]))
                pos += 4
            elif t == DT_REAL:
                out.append((name, struct.unpack_from("<f", b, pos)[0]))
                pos += 4
            else:
                out.append((name, f"?type{t} @{pos - 1:x}: {b[pos - 1:pos + 12].hex()}"))
                return out, None
        return out, pos + 1

    def record(self, b: bytes, pos: int, names: list[str]) -> tuple[Control, int]:
        """One control record at pos (u8 flag already consumed by caller):
        u16 length (from the flag byte), u16 flags (8000: array element),
        u8 name index, [u16 array index], u8 0, u8 class (FF: pstring class
        name follows), early properties, FF, properties, FF, then with code
        behind the form an event table: u8 count, u16 per event."""
        start = pos - 1
        ln, flags, idx = struct.unpack_from("<HHB", b, pos)
        pos += 5
        index = None
        if flags & 0x8000:  # control array element
            index = struct.unpack_from("<H", b, pos)[0]
            pos += 2
        cid = b[pos + 1]
        pos += 2
        if cid == 0xFF:  # custom control: class name follows
            cls = b[pos + 1:pos + 1 + b[pos]].decode("latin-1")
            pos += 1 + b[pos]
        else:
            cls = CLASS_IDS.get(cid, f"Class{cid:02X}")
        form = cls in ("Form", "MDIForm")
        c = Control(cls, names[idx] if idx < len(names) else f"#{idx}", idx)
        early, p = self.props(cls, b, pos, form)
        rest, p2 = self.props(cls, b, p, form, early) if p is not None else ([], None)
        c.props = early + rest
        end = start + ln
        if p2 is not None and p2 != end:  # event table: u8 count, u16 handler per event (length excludes the count)
            end = p2 + 1 + 2 * b[p2]
        c.raw = b[start:end]
        return c, end

    def decode(self, blob: bytes, names: list[str]) -> Control:
        """Records follow the form record, each after a flag byte (0: padding).
        Controls: 1 first child of the previous record, 3 sibling, 2 end of
        children. Menus: 5 first menu, 2 menu, 3 end of a menu level; a menu
        with unnamed property 7 set opens a level. 4 ends the form."""
        form, pos = self.record(blob, 9, names)
        stack: list[Control] = []
        last, menus = form, False
        while pos < len(blob):
            flag = blob[pos]
            if flag == 0:
                pos += 1
                continue
            if flag == 4:  # end of form
                break
            if flag == 5:
                menus, stack = True, [form]
            elif flag == 1:
                stack.append(last)
            elif flag == (3 if menus else 2):  # end of a level
                stack.pop()
                pos += 1
                continue
            c, pos = self.record(blob, pos + 1, names)
            stack[-1].children.append(c)
            last = c
            if menus and dict(c.props).get("_7"):
                stack.append(c)
        return form


def fmt_float(v: float) -> str:
    """Shortest decimal that rounds to the same f32."""
    want = struct.pack("<f", v)
    for k in range(10):
        t = f"{v:.{k}f}"
        if struct.pack("<f", float(t)) == want:
            t = t.rstrip("0").rstrip(".") if "." in t else t
            return t or "0"
    return repr(v)


SHORTCUTS = ([f"^{chr(65 + i)}" for i in range(26)] + [f"{m}{{F{i}}}" for m in ("", "^", "+", "^+") for i in range(1, 13)]
             + ["^{INSERT}", "+{INSERT}", "{DEL}", "+{DEL}", "%{BKSP}"])  # Menu.Shortcut 1, 2, ...


def text_props(c: Control, frx: bytearray, frx_name: str) -> list[tuple[str, str]]:
    """(name, value text) for a control's .frm description; pictures go to frx."""
    p = dict(c.props)
    form = c.cls in ("Form", "MDIForm")
    out = []
    for k, v in c.props:
        if k.startswith("_") or k in ("ScaleLeft", "ScaleTop") and p.get("ScaleMode") != 0:
            continue
        if form and k in ("Left", "Top", "Width", "Height"):
            continue  # the record holds the client rectangle (ClientLeft..ClientHeight follow)
        if c.cls == "OLE" and k == "TabIndex":
            continue  # the OLE 2 control rejects it in the text (the IDE assigns it)
        if isinstance(v, bytes) or v is None:
            if v is None:
                continue
            out.append((k, f"{frx_name}:{len(frx):04X}"))
            frx += struct.pack("<I", len(v)) + v
        elif isinstance(v, str):
            out.append((k, '"' + v.replace('"', '""') + '"'))
        elif isinstance(v, float):
            out.append((k, fmt_float(v)))
        elif k == "Shortcut" and c.cls == "Menu" and 0 < v <= len(SHORTCUTS):
            out.append((k, SHORTCUTS[v - 1]))
        elif k in ("BackColor", "ForeColor", "FillColor", "BorderColor"):
            out.append((k, f"&H{v & 0xFFFFFFFF:08X}&"))
        else:
            out.append((k, str(v)))
        if k == "FontSize":
            f = p["_FontFlags"]
            out += [("FontBold", str(-(f & 1))), ("FontItalic", str(-(f >> 1 & 1))),
                    ("FontStrikethru", str(-(f >> 3 & 1))), ("FontUnderline", str(-(f >> 2 & 1)))]
            if p.get("FontName") == "MS Sans Serif" and v == 8.25 and f & 0xF == 1:
                # a stored font with default values: an attribute the control drops (FontItalic
                # on a DirListBox) still makes the IDE store it; resetting it doesn't
                out[-3] = ("FontItalic", "-1")
    if "_GFlags" in p:
        g = p["_GFlags"]
        out += [("AutoRedraw", str(-(g >> 5 & 1))), ("FontTransparent", str(-(g >> 1 & 1)))]
        if p.get("ScaleMode") == 0:
            cw, ch = (p.get("ClientWidth"), p.get("ClientHeight")) if form else (None, None)
            if cw is not None:
                out += [("ScaleWidth", fmt_float(cw / p["_ScaleX"])), ("ScaleHeight", fmt_float(ch / p["_ScaleY"]))]
    return out


def form_text(c: Control, frx: bytearray, frx_name: str, depth: int = 0) -> list[str]:
    ind = "   " * depth
    out = [f"{ind}Begin {c.cls} {c.name}"]
    out += [f"{ind}   {k:<15s} =   {v}" for k, v in text_props(c, frx, frx_name)]
    for ch in c.children:
        out += form_text(ch, frx, frx_name, depth + 1)
    out.append(f"{ind}End")
    return out


def forms(exe: Path, rt: Runtime) -> list[Control]:
    res = rcdata(exe)
    rt.load_project_vbx(res)
    dec = FormDecoder(rt)
    blobs = [res[k] for k in sorted(res) if res[k][:2] == b"\xff\xcc"]
    return [dec.decode(b, n) for b, n in zip(blobs, form_names(res))]


def dump(c: Control, depth: int = 0) -> list[str]:
    ind = "   " * depth
    out = [f"{ind}Begin {c.cls} {c.name}"]
    for k, v in c.props:
        out.append(f"{ind}   {k} = {v!r}")
    for ch in c.children:
        out += dump(ch, depth + 1)
    out.append(f"{ind}End")
    return out
