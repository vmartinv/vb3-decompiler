"""
Shared vocabulary of the decompiler: type codes and names, variable and
procedure records (Var, ProcInfo), and small p-code helpers (handler
families, statement columns, label numbers).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .ne import Proc
from .opcodes import NAMES
from .runtime import Runtime


SUFFIX = {"I": "%", "L": "&", "S": "!", "D": "#", "C": "@", "T": "$", "V": ""}


class _TypeNames(dict):
    def __missing__(self, t: str) -> str:  # "F<n>": fixed-length String
        if t.startswith("F") and t[1:].isdigit():
            return f"String * {t[1:]}"
        raise KeyError(t)


TYPE_NAME = _TypeNames({"I": "Integer", "L": "Long", "S": "Single", "D": "Double", "C": "Currency", "T": "String",
                        "V": "Variant"})

RET_TYPE = {1: "I", 2: "L", 3: "S", 4: "D", 5: "C", 6: "V", 7: "T"}  # procedure record +13

EVENT_TYPE = {1: "Integer", 2: "Long", 3: "Single", 4: "Double", 5: "Currency", 6: "String", 8: "Control"}

EVENT_PARAMS = {  # conventional parameter names (the IDE's templates)
    "DragDrop": "Source X Y", "DragOver": "Source X Y State", "KeyDown": "KeyCode Shift",
    "KeyUp": "KeyCode Shift", "KeyPress": "KeyAscii", "MouseDown": "Button Shift X Y",
    "MouseUp": "Button Shift X Y", "MouseMove": "Button Shift X Y", "LinkError": "LinkErr",
    "LinkOpen": "Cancel", "Unload": "Cancel", "QueryUnload": "Cancel UnloadMode",
    "LinkExecute": "CmdStr Cancel", "Error": "DataErr Response", "Validate": "Action Save",
    "Collapse": "ListIndex", "Expand": "ListIndex", "PictureClick": "ListIndex",
    "PictureDblClick": "ListIndex", "Updated": "Code", "Click": "Value",  # (OLE; Threed's SSCheck/SSOption)
}

LABEL, LABEL_WIDE = 0x4965, 0x48BE  # LABEL_WIDE: + spaces before the statement on its line

OBJ_KINDS = {1: "Form", 2: "MDIForm", 4: "Control", 0x14: "Object"}  # object variable kinds besides control classes

VAR_FAMILIES = ("LOAD", "STORE", "ADDR_LOC", "ALOAD", "ASTORE", "ADDR", "AADDR")

SIZE_TYPES = {2: "I", 4: "L", 8: "D", 16: "V"}  # filler declarations for unused slots


def label_number(operand: bytes) -> int:
    """A LABEL's line number (second word; 0xFFFF for a named label, returned as 0xFFFFFFFF).
    The first word is 0xFFFF unless a Resume / Erl refers to the label."""
    num = struct.unpack_from("<H", operand, 2)[0]
    return 0xFFFFFFFF if num == 0xFFFF else num


@dataclass


class Var:
    slot: int
    scope: str  # MOD / LOC / REF / GLB
    votes: dict = field(default_factory=dict)  # type letter -> count
    array: bool = False
    procs: list = field(default_factory=list)  # procedures (layout index) referencing it
    stored: bool = False
    udt: bool = False
    udt_type: int | None = None  # Type of an array's elements
    fixed: bool = False  # String * n (length in the slot before)
    obj: str | None = None  # object variable's class (As Control, As frmX, ...)
    glob: int | None = None  # global offset, for a reference to a global object array
    copy_type: str | None = None  # a Global Const's copy: its type (its size in the slots)

    def type(self) -> str:
        if not self.votes:
            return "V"
        return max(self.votes, key=lambda t: (self.votes[t], t != "L"))


@dataclass


class ProcInfo:
    proc: Proc
    insns: list
    notes: list
    name: str = ""
    event: bool = False
    function: bool = False
    ret: str = "V"
    argwords: int = 0
    params: list = field(default_factory=list)  # (slot, text)
    ret_slot: int | None = None
    callees: list = field(default_factory=list)  # called records, in text order


def var_access(name: str) -> tuple[str, str, bool] | None:
    """Handler name -> (scope, type letters, array) for variable accesses."""
    parts = name.split(".")
    fam = parts[0]
    if fam not in VAR_FAMILIES:
        return None
    if fam == "ADDR_LOC":
        return "LOC", parts[1] if len(parts) > 1 else "", False
    if fam in ("ADDR", "AADDR"):
        return (parts[1] if len(parts) > 1 else "GLB"), "F" if parts[2:] == ["F"] else "", fam == "AADDR"
    if len(parts) < 2:
        return None
    return parts[1], parts[2] if len(parts) > 2 else "", fam in ("ALOAD", "ASTORE")


SUFFIX_OF_ID = {1: "%", 2: "&", 3: "!", 4: "#", 5: "@", 7: "$"}  # interpreter ID >> 10

TYPE_OF_SUFFIX = {"%": "I", "&": "L", "!": "S", "#": "D", "@": "C", "$": "T"}


def plain_handler(rt: Runtime, op: int) -> tuple[str | None, str]:
    """A variable access or function call written with a type suffix (`b% = 3`, `F%(1)`) uses another
    entry point of the plain handler (usually 3 bytes before it) whose
    interpreter ID carries the suffix type: ID = plain ID | type << 10.
    Returns (plain handler name, suffix)."""
    if op in NAMES:
        return NAMES[op], ""
    oid = rt.opcode_id(op)
    if oid is None:
        return None, ""
    if oid >> 10 in SUFFIX_OF_ID:  # 3-byte entries falling through to the plain handler after them first
        for k in [*range(3, 22, 3), *sorted(range(-16, 17), key=abs)]:
            n = NAMES.get(op + k)
            if n and (n.split(".")[0] in VAR_FAMILIES or n == "CALL_FN") and rt.opcode_id(op + k) == oid & 0x3FF:
                return n, SUFFIX_OF_ID[oid >> 10]
    fam = ID_CLASS.get(oid & 0xFF)  # otherwise by the ID's class; scope from the slot (".X")
    return (f"{fam}.X", SUFFIX_OF_ID.get(oid >> 10, "")) if fam else (None, "")


ID_CLASS = {0x0B: "LOAD", 0x0C: "STORE", 0x0E: "ALOAD", 0x0F: "ASTORE",  # interpreter ID low byte
            0x13: "FIELD_ALOAD", 0x14: "FIELD_ASTORE"}


def mod_name(slot: int) -> str:
    """Synthetic module variable name; `mE` would be the keyword Me."""
    return f"m{slot:X}" if slot != 0xE else "m0E"


def pool_name(image: bytes, pool: int, off: int) -> str:
    p = pool + 2 + off
    return image[p + 4:p + 4 + image[p + 3]].decode("latin-1")


# Statement markers encode the line's indentation (the IDE regenerates the
# text from p-code): entry point -> column, from compiling lines at columns
# 0..40; 48AF takes the column as a u16 operand (25 and up); 4958 is a
# statement after `:` on the same line.
STMT_COLUMN = {op: c for c, op in enumerate([
    0x494B, 0x4948, 0x4945, 0x4942, 0x4935, 0x4932, 0x492F, 0x492C, 0x491F, 0x491C, 0x4919, 0x4916,
    0x4906, 0x4903, 0x4900, 0x48FD, 0x48F0, 0x48EA, 0x48E7, 0x48E4, 0x48D7, 0x48D4, 0x48D1, 0x48CE,
    0x48ED])}

STMT_WIDE, STMT_SAME_LINE = 0x48AF, 0x4958


def stmt_column(rt: Runtime, op: int, operand: bytes = b"") -> int | None:
    if op == STMT_WIDE and len(operand) >= 2:
        return struct.unpack_from("<H", operand)[0]
    return STMT_COLUMN.get(op)


# Print items (`;` / end of line) per value type: the type of what is printed
PRINT_TYPE = {0x6085: "V", 0x6045: "I", 0x604B: "L", 0x6059: "S", 0x6069: "D", 0x607F: "C", 0x6104: "T",
              0x60A4: "V", 0x60DE: "I", 0x608F: "L", 0x609D: "S", 0x60AE: "D", 0x60B5: "C", 0x6132: "T"}


def lt_hint(nxt: str) -> str:
    """Long or String for a shared 4-byte load, from the handler consuming it."""
    if nxt.startswith("CVT."):
        src = nxt[4:].split(">")[0]
        return src if src in ("L", "T") else ""
    if nxt in ("ARG_STR", "CONCAT"):
        return "T"
    parts = nxt.split(".")
    if len(parts) == 2 and parts[0] not in ("LOAD", "STORE", "ALOAD", "ASTORE", "ADDR_LOC") and parts[1] in ("L", "T"):
        return parts[1]  # ADD.T, EQ.L, ...
    return ""
