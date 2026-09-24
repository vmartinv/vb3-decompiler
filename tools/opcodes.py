"""
Names of VB3 p-code handlers (= their addresses in VBRUN300.DLL segment 25).

Sources: the generated scope x type test project (variables, arrays, calls)
and the source-aligned sample corpus (tools/corpus.py). A trailing "?" marks
names inferred from context only (not yet seen in an aligned source line).

Suffixes give the operand type: V Variant, I Integer, L Long, S Single,
D Double, C Currency, T String, R8 floating (FPU), B Boolean.
Variable storage: LOC local/ByVal param (frame), MOD form/module/Static
(module data segment), GLB Global (global segment), REF ByRef param.
"""

_T = "VILSDCT"


def _row(prefix: str, handlers: str) -> dict[int, str]:
    """handlers: 7 hex handler addresses in V I L S D C T order ('-' = none)."""
    out = {}
    for t, h in zip(_T, handlers.split()):
        if h != "-":
            out[int(h, 16)] = f"{prefix}.{t}"
    return out


NAMES: dict[int, str] = {
    # --- statements / flow ---------------------------------------------
    0x48AF: "STMT_X",            # statement marker with a u16 operand
    0x4965: "LABEL",
    0x65D9: "RET", 0x0E5E: "TRAP", 0x0E5B: "TRAP",
    0x0E35: "END",
    0x34B7: "JF", 0x3500: "JF.I",           # jump if false (block If / loops)
    0x34B4: "IF1_JF", 0x3503: "IF1_JF.I",   # single-line If ... Then ...
    0x34FD: "ELSEIF_JF", 0x34B1: "ELSEIF_JF", 0x34DF: "ELSEIF_JF", 0x34E5: "JF", 0x34FA: "DO_WHILE_JF",
    0x34AE: "DO_WHILE_JF", 0x350F: "DO_UNTIL_JT", 0x34AB: "LOOP_UNTIL_JF", 0x35F8: "LOOP",
    0x35FE: "JMP", 0x35EC: "ENDIF",
    0x1F41: "GOTO", 0x1F4A: "EXIT", 0x1FC6: "GOSUB", 0x1FE2: "RETURN",
    0x1F3E: "CASE",                          # u16: next Case / End Select
    0x0D09: "SELECT.I", 0x0CC8: "SELECT.V", 0x0CF8: "SELECT.R8", 0x0DA1: "END_SELECT", 0x0DA4: "CASE_ELSE",
    0x396A: "CASE_VAL.I", 0x440D: "CASE_VAL.V", 0x3C14: "CASE_VAL.R8", 0x3DC0: "CASE_VAL.T",
    0x0D4D: "CASE_EQ.I", 0x0D2F: "CASE_EQ.V", 0x0D39: "CASE_EQ.R8", 0x0D57: "CASE_EQ.T",  # u16 next Case, u16 body
    0x0D83: "END_SELECT", 0x0D76: "CASE_ELSE", 0x0DAD: "END_SELECT", 0x0CFF: "SELECT.T",
    0x4403: "CASE_IS.GT", 0x43F2: "CASE_IS.LT",   # `Case Is > v`
    0x1B37: "FOR", 0x1B3E: "FOR_STEP", 0x1A7E: "FOR.I", 0x1AA6: "FOR.L",
    0x1E08: "NEXT", 0x1C8A: "NEXT.I", 0x1D08: "NEXT.L", 0x1C87: "NEXT_NOVAR", 0x1E0B: "NEXT_NOVAR", 0x35E9: "DO", 0x0D73: "END_SELECT",
    0x7EB6: "ON_ERROR_GOTO", 0x7E63: "RESUME", 0x7E44: "RESUME_LABEL", 0x7E5D: "RESUME_NEXT",
    0x3761: "LOCAL",                          # `On Local Error`
    0x1D9F: "EXIT_DO", 0x1DA2: "EXIT_FOR", 0x3512: "LOOP_WHILE_JT", 0x35B8: "DO_UNTIL_JT",
    0x079D: "DIM_BOUND", 0x077A: "ARRAY_REF",  # u16 2*values (0x8000: whole array), u16 slot
    0x0768: "ARRAY_REF_LB",                     # `lower To upper` per dimension: u16 2*dims, u16 slot
    0x0816: "REDIM", 0x0813: "REDIM_PRESERVE", 0x0AEF: "UBOUND",
    # graphics / Print methods: `obj GFX pieces... END`; value methods: `obj GFX_FN args FN`
    0x2137: "GFX", 0x2130: "GFX_FN",
    0x22F6: "PT", 0x22E9: "PT", 0x231E: "PT_TO", 0x2328: "PT_STEP_TO", 0x2332: "PT_STEP_TO",
    0x26C7: "LINE", 0x26C1: "LINE_C",            # operand: 0 / 1 B / 2 BF
    0x23DA: "CIRCLE_C", 0x23E3: "CIRCLE", 0x27BE: "PSET_C", 0x27C4: "PSET_P",  # PSet (x, y) [, color]
    0x27E8: "SCALE",
    0x296C: "TEXTWIDTH", 0x28FF: "TEXTHEIGHT", 0x2A69: "POINT",
    0x3764: "DEBUG", 0x2124: "PRINT_BEGIN",
    0x6132: "PRINT_NL", 0x60A4: "PRINT_NL", 0x60DE: "PRINT_NL", 0x6012: "PRINT_COMMA",
    0x0DEA: "RET_SLOT", 0x67A2: "BYVAL",                        # reserves a call's return value (method used as a value)
    0x4A08: "PUSH_NOTHING", 0x4F3A: "IS", 0x4A3A: "TYPEOF_IS",
    # --- calls ---------------------------------------------------------
    0x62E0: "CALL", 0x62DD: "CALL", 0x62A7: "CALL_FN",
    0x67B1: "ARGS",                          # opens an argument frame
    0x4FC3: "OBJ", 0x3767: "OBJ_SELF",
    0x6819: "ARG_MISSING", 0x6AD5: "ARG_TEMP",   # ARG_TEMP: by-value temporary for a ByRef parameter
    0x4B61: "METHOD",                        # operand byte 6 = method number
    0x4FFC: "NARGS", 0x376A: "END_CALL",
    0x6A63: "ARG_STR", 0x6A72: "ARGS_FREE", 0x6A02: "ARG_V", 0x6823: "ARG_S", 0x6834: "ARG_D",
    0x320F: "ADDR_LOC.V", 0x3200: "ADDR_LOC", 0x3237: "ADDR_LOC.T",
    # --- objects -------------------------------------------------------
    0x4A6E: "CONTROL", 0x4AA7: "FORM", 0x4A12: "ME", 0x4A15: "ME_IMPLICIT",
    0x4A7F: "OBJVAR",
    0x4BA3: "PGET_ME", 0x4C14: "PSET_ME",    # property of the implicit form
    0x4C09: "PGET", 0x4C72: "PSET",          # operand 0xC0nn: class property nn
    0x4A63: "SUBOBJ", 0x4A57: "SUBOBJ", 0x4CA8: "CTLARRAY", 0x4EB0: "CTLARRAY_OF", 0x4EA9: "CTLARRAY_OF",  # 4A57/4EA9 `!`, 4A63/4EB0 `.`
    0x4EC7: "PGET_IDX", 0x4EDD: "PSET_IDX",   # indexed property: u16 index count, u16 0xC0nn
    0x3357: "OLE_CALL",                      # OLE Automation method: u16 argc, u16 name (RT_RCDATA 3 offset)
    0x4A23: "UNLOAD", 0x4A2A: "LOAD",
    0x316D: "ADDR.GLB", 0x4F69: "SET_OBJ", 0x33BE: "SET_OBJ",
    # --- literals ------------------------------------------------------
    **{a: f"PUSH.I {n}" for n, a in enumerate(
        [0x37E5, 0x37ED, 0x37F8, 0x37FE, 0x3804, 0x380A, 0x3810, 0x3816, 0x381C, 0x3822, 0x3828])},
    0x3834: "PUSH.I", 0x3831: "PUSH.I", 0x388A: "PUSH.L", 0x388D: "PUSH.L",
    0x3788: "PUSH.R8 0", 0x3791: "PUSH.R8 1", 0x379A: "PUSH.R8 2", 0x37A7: "PUSH.R8 3",
    0x37AE: "PUSH.R8 4", 0x37B5: "PUSH.R8 5", 0x37BC: "PUSH.R8 6", 0x37C3: "PUSH.R8 7", 0x37CA: "PUSH.R8 8", 0x37D8: "PUSH.R8 10",
    0x387A: "PUSH.R8", 0x389A: "PUSH.T",
    0x37E2: "PUSH.B False", 0x383D: "PUSH.B True",
    # --- conversions ---------------------------------------------------
    0x0EB0: "CVT.I>V", 0x0E8B: "CVT.I>R8", 0x0E7B: "CVT.I>L", 0x0F1C: "CVT.L>V",
    0x0F2E: "CVT.S>R8?", 0x0F67: "CVT.R8>I", 0x0F7B: "CVT.R8>L", 0x0F49: "CVT.R8",
    0x1050: "CVT.V>I", 0x10A3: "CVT.V>T", 0x10F1: "CVT.S>V", 0x1102: "CVT.R8>V",
    0x11BB: "CVT.Ttmp>V", 0x11C3: "CVT.T>V", 0x49CE: "PAREN", 0x106D: "CVT.V>S",
    0x67EA: "ARGS_DLL", 0x67A8: "ARGS_DLL", 0x1972: "ARG_T_BYREF", 0x699D: "ARG_PAREN",
    # --- operators -----------------------------------------------------
    0x40DF: "ADD.V", 0x38D3: "ADD.I", 0x3B6E: "ADD.R8", 0x3D47: "ADD.T",
    0x416A: "SUB.V", 0x38E1: "SUB.I",
    0x4160: "MUL.V", 0x38EF: "MUL.I", 0x3B80: "MUL.R8",
    0x4241: "DIV.V", 0x3B89: "DIV.R8",
    0x390B: "NEG",
    0x4468: "EQ.V", 0x396D: "EQ.I", 0x3DF6: "EQ.T", 0x3C17: "EQ.R8", 0x3B51: "EQ.L",
    0x447A: "NE.V", 0x3980: "NE.I", 0x3DFF: "NE.T", 0x3C2A: "NE.R8", 0x3CC4: "NE.C", 0x3B61: "NE.L",
    0x7805: "LIKE",
    0x448C: "LE.V", 0x3993: "LE.I", 0x3C63: "LE.R8",
    0x449E: "LT.V", 0x39CC: "LT.I", 0x3C76: "LT.R8",
    0x44B0: "GE.V", 0x39A6: "GE.I", 0x3C50: "GE.R8",
    0x44C2: "GT.V", 0x39B9: "GT.I", 0x3C3D: "GT.R8",
    0x42FD: "AND.V", 0x39E7: "AND.I",
    0x4312: "OR.V", 0x39F2: "OR.I",
    0x42D1: "NOT.V", 0x39DC: "NOT.I", 0x393E: "IDIV.L", 0x3AB5: "AND.L",
    0x3A4D: "SUB.L", 0x3A3A: "ADD.L", 0x3B2A: "LE.L", 0x3AC6: "OR.L", 0x3B42: "GT.L", 0x3924: "MOD.L",
    0x3B77: "SUB.R8", 0x3AD7: "XOR.L", 0x3B1E: "LT.L",
    0x4720: "NEG",
    0x42B1: "MOD.V", 0x3918: "MOD.I", 0x4276: "IDIV.V", 0x38FF: "IDIV.I",
    0x4255: "POW.V", 0x3B92: "POW.R8", 0x77B8: "CONCAT",
    0x4290: "XOR.V", 0x39FE: "XOR.I", 0x42A3: "EQV.V", 0x3A0A: "EQV.I", 0x42E7: "IMP.V", 0x3A17: "IMP.I",
    # --- builtins ------------------------------------------------------
    0x19F5: "Rnd", 0x743A: "Randomize",
    0x3BCA: "Int.R8", 0x4738: "Int.V", 0x3A24: "Int.I", 0x4744: "Fix.V", 0x472C: "Abs.V", 0x104D: "CInt",
    0x7582: "Len", 0x756A: "Left$", 0x74C9: "Chr$", 0x74C3: "Asc", 0x769C: "UCase$", 0x775F: "Format$.1",
    0x7677: "Str$", 0x760A: "Str$.I", 0x76A2: "Val",
    0x52BA: "Shell", 0x4FA6: "OBJ_FREE",  # OBJ_FREE: epilogue release of a local object variable
    0x2A0D: "QBColor", 0x7EC5: "Error$", 0x19CE: "Err",
    0x52AF: "Timer", 0x1A78: "Now", 0x750A: "InStr", 0x75BC: "Mid$",
    0x537B: "ChDir", 0x5381: "ChDrive", 0x2A73: "Cls", 0x742E: "Beep",
    0x37DF: "ARG_MISSING", 0x3844: "ARG_MISSING", 0x52F4: "MsgBox", 0x5308: "MsgBox.fn",
    0x5291: "Time", 0x52D8: "InputBox$", 0x1480: "IsDate", 0x148A: "CVDate", 0x7766: "Format$",
    0x10A0: "CStr", 0x5340: "DoEvents", 0x1A6A: "Minute", 0x28DA: "RGB", 0x75F5: "Trim$",
    0x7594: "Len.T",
    # --- file I/O ------------------------------------------------------
    0x376D: "FILENUM", 0x373B: "OPEN",        # operand: 1 Input, 2 Output
    0x3631: "CLOSE", 0x375B: "PRINT#", 0x3740: "OPEN_LEN",   # Open ... Len = n
    0x3692: "INPUT#", 0x3698: "INPUT_ITEM.I", 0x36C2: "INPUT_ITEM.V", 0x36B6: "INPUT_ITEM.T",
    0x368C: "INPUT_END", 0x36DF: "INPUT_ITEM_FIELD?",
    0x3662: "GET#", 0x367E: "PUT#",                 # operand: record length
    0x3654: "GET#_NOREC", 0x3670: "PUT#_NOREC", 0x374F: "SEEK", 0x19D7: "ERR_SET",
    # --- user-defined types -------------------------------------------
    0x0709: "AADDR.GLB",                     # address of an array element
    0x6CE6: "FIELD_ADDR", 0x6FB6: "FIELD_ADDR.T",  # operand: field offset
    0x70D5: "FIELD_GET.T",
    0x6D00: "FIELD_SET.I", 0x6BD2: "FIELD_GET.I", 0x6D9B: "FIELD_SET.V", 0x6C6C: "FIELD_GET.V",
    0x70EE: "FIELD_SET.T", 0x6C52: "FIELD_GET.D", 0x6D1C: "FIELD_SET.L", 0x6D7F: "FIELD_SET.D",
    0x6BF1: "FIELD_GET.L", 0x31B0: "LOAD.UDT", 0x31EE: "LOAD.UDT_LOC",
}

# Variables: load (id 0x0B) / store (id 0x0C) by storage x type.
NAMES |= _row("LOAD.LOC", "2d21 2ca5 2cc4 2cf0 2d01 2cd7 -")
NAMES |= _row("LOAD.MOD", "2bc1 2b50 2b6c 2b94 2ba3 2b7d -")
NAMES |= _row("LOAD.GLB", "2b15 2a86 2aa3 2ace 2ae9 2ab7 -")
NAMES |= _row("LOAD.REF", "2c80 2bf3 2c0d 2c4d 2c68 2c2a -")
NAMES |= _row("STORE.LOC", "2fd4 2f3b 2f4b 2f77 2f8a 2f5e 2f9d")
NAMES |= _row("STORE.MOD", "2e8e 2ecc 2ee5 2f0d 2f1e 2ef6 2e6b")
NAMES |= _row("STORE.GLB", "2e25 2d55 2d6f 2daf 2dcc 2d8c 2dfb")
NAMES |= _row("STORE.REF", "303d 30a5 30bf 30ff 311c 30dc 3139")
# Arrays (operands: u16 dimension count, u16 array slot): id 0x0E / 0x0F.
NAMES |= _row("ALOAD.GLB", "05f8 006a 0129 018a 0307 02ab 0425")
NAMES |= _row("ASTORE.GLB", "064a 00cc 01e9 024a 03c4 0366 0481")
NAMES |= _row("ALOAD.MOD", "0609 007e 013d 019e 031b 02bf 0439")
NAMES |= _row("ASTORE.MOD", "065b 00e0 01fd 025e 03d8 037a 0495")
# 4-byte loads are shared by Long and String (a far pointer): note both.
for _h in (0x2CC4, 0x2B6C, 0x2AA3):
    NAMES[_h] = NAMES[_h][:-1] + "L/T"

# METHOD operand byte 6 = method number (global across classes), from the corpus.
METHODS = {
    0x02: "AddItem", 0x03: "RemoveItem", 0x04: "Refresh", 0x0C: "Clear", 0x0E: "Arrange",
    0x0F: "Show", 0x10: "Hide", 0x11: "EndDoc", 0x12: "NewPage", 0x13: "SetFocus",
    0x14: "Drag", 0x15: "Move", 0x16: "ZOrder", 0x17: "Close", 0x18: "Delete",
    0x19: "CommitTrans", 0x1A: "CreateDynaset", 0x1B: "ExecuteSQL", 0x1C: "Rollback",
    0x1D: "AddNew", 0x1E: "Edit", 0x1F: "MoveFirst", 0x20: "MoveLast", 0x21: "MoveNext",
    0x22: "MovePrevious", 0x24: "BeginTrans", 0x25: "Update", 0x26: "Append",
    0x27: "FieldSize", 0x28: "GetChunk", 0x2D: "OpenTable", 0x2F: "ListTables",
    0x32: "CreateSnapshot", 0x33: "OpenQueryDef", 0x34: "CreateQueryDef", 0x39: "Execute",
    0x3A: "Seek", 0x3B: "Clone", 0x07: "LinkExecute", 0x35: "FindFirst",
    0x01: "LinkSend", 0x05: "LinkPoke", 0x06: "LinkRequest", 0x08: "GetText", 0x0A: "SetText",
}

# Lifter semantics for handlers that are plain builtins/statements:
# op -> (kind, name, arity); kind: "fn" function, "kw" keyword statement,
# "push" constant expression, "pass" no source effect. Proposed by
# `lift.py infer` (search over the aligned corpus) and kept only where
# they make the corpus lines lift exactly.
SEM: dict[int, tuple[str, str, int]] = {
    0x53CC: ("fn", "Dir$", 1), 0x53C6: ("fn", "Dir$", 0), 0x5314: ("fn", "DoEvents", 0),
    0x75EF: ("fn", "Right$", 2), 0x3613: ("fn", "FreeFile", 0), 0x75B4: ("fn", "Mid$", 2),
    0x7690: ("fn", "String$", 2), 0x7684: ("fn", "String", 2), 0x3958: ("fn", "Abs", 1),
    0x7570: ("fn", "Len", 1), 0x7610: ("fn", "Str$", 1), 0x4841: ("fn", "IsNumeric", 1),
    0x3BE8: ("fn", "Sgn", 1), 0x74E1: ("fn", "Hex$", 1), 0x75D4: ("fn", "Oct$", 1),
    0x1043: ("fn", "CDbl", 1), 0x7535: ("fn", "InStr", 3),
    0x3625: ("fn", "LOF", 1), 0x361F: ("fn", "Loc", 1), 0x3619: ("fn", "Input$", 2),
    0x1A18: ("fn", "DateValue", 1), 0x105D: ("fn", "CLng", 1), 0x10D4: ("fn", "CCur", 1),
    0x100F: ("fn", "CDbl", 1), 0x75AE: ("fn", "LTrim$", 1), 0x75FE: ("fn", "RTrim$", 1),
    0x3302: ("fn", "CreateObject", 1), 0x53D7: ("kw", "FileCopy", 2),
    0x3607: ("fn", "EOF", 1), 0x7604: ("fn", "Space$", 1), 0x762E: ("fn", "Str$", 1),
    0x5387: ("fn", "CurDir$", 0), 0x0EED: ("pass", "", 0), 0x3BB9: ("fn", "Sqr", 1),
    0x7428: ("kw", "Error", 1), 0x52B5: ("fn", "Shell", 1), 0x4FA6: ("pass", "", 0),
    0x0E9A: ("fn", "CCur", 1), 0x6895: ("pass", "", 0), 0x6942: ("pass", "", 0),
    0x1156: ("pass", "", 0), 0x0ED9: ("pass", "", 0),
    0x1094: ("pass", "", 0), 0x1060: ("pass", "", 0), 0x0EC5: ("pass", "", 0),
    0x34A8: ("kw", "While", 1), 0x35F5: ("kw", "Wend", 0), 0x53FB: ("kw", "Kill", 1),
    0x199D: ("pass", "", 0), 0x10D7: ("pass", "", 0),
}

