"""
Module declarations recovered from the data images (RT_RCDATA 2): Types,
Global variables and constants, module variables and constants, Declares.
Used by decompile.py. Layout rules: see ../OPCODES.md, "Source recovery".

Global image (the first chunk): global offset g is at chunk + 2 + g.
  g 4: head of the Type chain; a Type is `name, next Type, size, first
       field`; a field is `name, next field, type, offset` (+ a length word
       before it for fixed-length strings). Names point into the IDE's name
       table (not stored), so Types and fields get synthetic names; FIELD_*
       operands are field record offsets.
  globals follow from g 6, in declaration order, values inline.
Module image: slot s holds its value (or a variable's storage) from s + 2.
  Functions and Declares first (record offsets, sorted by name), then the
  declarations in text order: a Global is a 2-byte slot holding its global
  offset; a module variable/constant is stored inline (size = its type's).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

TYPE_CODE = {1: "I", 2: "L", 3: "S", 4: "D", 5: "C", 6: "V", 7: "T"}  # Type fields, records
TYPE_NAME = {"I": "Integer", "L": "Long", "S": "Single", "D": "Double", "C": "Currency", "T": "String",
             "V": "Variant"}
MOD_SIZE = {"I": 2, "L": 4, "S": 4, "D": 8, "C": 8, "T": 4, "V": 16}


def word(d: bytes, o: int, signed: bool = False) -> int:
    return struct.unpack_from("<h" if signed else "<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0


@dataclass
class Field:
    g: int  # record offset (FIELD_* operand)
    type: int
    offset: int
    length: int = 0

    def decl(self, types: dict) -> str:
        if self.type == 8:
            return f"F{self.g:X} As String * {self.length}"
        t = types.get(self.type)
        return f"F{self.g:X} As " + (t.name if t else TYPE_NAME.get(TYPE_CODE.get(self.type, "V"), "Variant"))


@dataclass
class TypeDef:
    g: int
    size: int
    fields: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"T{self.g:X}"

    def lines(self, types: dict) -> list[str]:
        return [f"Type {self.name}"] + [f"    {f.decl(types)}" for f in self.fields] + ["End Type"]


class GlobalImage:
    def __init__(self, image: bytes, base: int | None):
        self.d, self.base = image, base
        self.size = word(image, base) if base is not None else 0
        self.types: dict[int, TypeDef] = {}
        self.field_type: dict[int, TypeDef] = {}  # field record -> its Type
        self.type_extent: list[tuple[int, int]] = []
        if base is None:
            return
        t, seen = self.w(4), set()
        while t and t not in seen and t < self.size:
            seen.add(t)
            td = TypeDef(t, self.w(t + 4))
            f, end = self.w(t + 6), t + 8
            while f and f < self.size and len(td.fields) < 256:
                typ = self.w(f + 4)
                fl = Field(f, typ, self.w(f + 6), self.w(f - 2) if typ == 8 else 0)
                td.fields.append(fl)
                self.field_type[f] = td
                end = max(end, f + 8)
                f = self.w(f + 2)
            self.types[t] = td
            self.type_extent.append((min([t] + [x.g - (2 if x.type == 8 else 0) for x in td.fields]), end))
            t = self.w(t + 2)

    def w(self, g: int, signed: bool = False) -> int:
        return word(self.d, self.base + 2 + g, signed)

    def raw(self, g: int, n: int) -> bytes:
        o = self.base + 2 + g
        return self.d[o:o + n]

    def in_type(self, g: int) -> bool:
        return any(a <= g < b for a, b in self.type_extent)

    def by_size(self, size: int) -> TypeDef | None:
        hits = [t for t in self.types.values() if t.size == size]
        return hits[0] if len(hits) == 1 else None


def const_literal(t: str, raw: bytes) -> str | None:
    """VB literal for a constant's stored value, typed so that it compiles
    to the same type."""
    try:
        if t == "I":  # the most negative value has no decimal literal
            v = struct.unpack("<h", raw[:2])[0]
            return str(v) if v > -32768 else "&H8000"
        if t == "L":
            v = struct.unpack("<i", raw[:4])[0]
            return f"{v}&" if v >= 0 else f"&H{v & 0xFFFFFFFF:X}&"
        if t == "S":
            return f"{struct.unpack('<f', raw[:4])[0]!r}!"
        if t == "D":
            return f"{struct.unpack('<d', raw[:8])[0]!r}#"
        if t == "C":
            return f"{struct.unpack('<q', raw[:8])[0] / 10000!r}@"
    except struct.error:
        return None
    return None
