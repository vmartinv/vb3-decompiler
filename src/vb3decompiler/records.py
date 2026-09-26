"""
Field offsets in the procedure table's 56-byte records (segment 3): see
OPCODES.md "Procedure records" and "Source recovery".
"""
from __future__ import annotations

from .dataimage import word

# Procedure record (a Sub/Function, or an event procedure)
PROC_FRAME = 0          # u16 22 + frame bytes (locals and return value)
PROC_POOL_NAME = 4      # u16 name's offset in the compile-time name pool (shared by same-named procedures)
PROC_NUMBERED = 10      # u16 count of numbered locals
PROC_KIND = 12          # u8 1 Sub, 2 Function
PROC_RET_TYPE = 13      # u8 return type (model.RET_TYPE)
PROC_FLAGS = 14         # u8 bit 7 Static Sub/Function; 0x0C marks a Declare record
PROC_ARG_WORDS = 15     # u8 argument words (ByRef 2, ByVal Integer 1, ...)
PROC_VAR_TABLE = 18     # u16 offset in the IDE's per-module variable table
PROC_CODE_START = 24    # u16 code start within its code segment
PROC_CODE_END = 36      # u16 code end
PROC_SEGMENT = 38       # code segment selector, filled by an NE INTREF relocation (a fixup chain through the records)
PROC_LINES = 50         # u16 source line count, the comment block above it included

PROC_STATIC = 0x80      # PROC_FLAGS: Static Sub/Function
DECLARE_FLAGS = 0x0C    # PROC_FLAGS of a Declare record

# Declare record (a procedure record with PROC_FLAGS == DECLARE_FLAGS)
DECLARE_PARAMS = 24     # u16 cumulative offset in the module's Declare/Type table
DECLARE_DLL = 40        # u16 DLL name (name pool offset)
DECLARE_ENTRY = 46      # u16 entry name (name pool offset)

# Declarations record (one per module)
DECL_ITEMS_END = 12     # u16 image end + 2 per item
DECL_FLAGS = 18         # u16 1 Option Base 1, 0x40 Option Explicit, 0x800 Option Compare
DECL_COMPARE = 20       # u16 Option Compare: 1 Text, 0 Binary
DECL_DEFTYPE = 44       # u16 DefType table offset, 0xFFFF if none
DECL_TYPES_START = 46   # u16 where the Types start in the table Declare records' +24 index, 0xFFFF if none
DECL_LINES = 50         # u16 line count (declarations, DefType statements)

OPTION_EXPLICIT = 0x40  # DECL_FLAGS

RECORD_SIZE = 56


def decl_record(image: bytes, chunk: int) -> int:
    """A module's declarations record: the word before its image chunk, + 4."""
    return word(image, chunk - 2) + 4
