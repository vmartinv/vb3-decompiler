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
    0x34FD: "ELSEIF_JF",
    0x35FE: "JMP", 0x35EC: "ENDIF",
    0x1F41: "GOTO", 0x1F4A: "EXIT", 0x1FC6: "GOSUB", 0x1FE2: "RETURN",
    0x1F3E: "CASE",                          # u16: next Case / End Select
    0x0D09: "SELECT.I", 0x0DA1: "END_SELECT", 0x0DA4: "END_SELECT",
    0x1B37: "FOR", 0x1B3E: "FOR_STEP", 0x1A7E: "FOR.I",
    0x1E08: "NEXT", 0x1C8A: "NEXT.I",
    0x7EB6: "ON_ERROR_GOTO", 0x7E63: "RESUME",
    # --- calls ---------------------------------------------------------
    0x62E0: "CALL", 0x62DD: "CALL", 0x62A7: "CALL_FN",
    0x67B1: "ARGS",                          # opens an argument frame
    0x4FC3: "OBJ", 0x3767: "OBJ_SELF",
    0x6819: "ARG_MISSING", 0x6AD5: "ARG_MISSING",
    0x4B61: "METHOD",                        # operand byte 6 = method number
    0x4FFC: "NARGS", 0x376A: "END_CALL",
    0x6A63: "ARG_STR", 0x6A72: "ARGS_FREE", 0x6A02: "ARG_V", 0x6823: "ARG_S", 0x6834: "ARG_D",
    0x320F: "ADDR_LOC.V", 0x3200: "ADDR_LOC", 0x3237: "ADDR_LOC.T",
    # --- objects -------------------------------------------------------
    0x4A6E: "CONTROL", 0x4AA7: "FORM", 0x4A12: "ME", 0x4A15: "ME_IMPLICIT",
    0x4A7F: "OBJVAR",
    0x4BA3: "PGET_ME", 0x4C14: "PSET_ME",    # property of the implicit form
    0x4C09: "PGET", 0x4C72: "PSET",          # operand 0xC0nn: class property nn
    0x4A63: "SUBOBJ", 0x4CA8: "CTLARRAY", 0x4EB0: "CTLARRAY_OF",
    0x4A23: "UNLOAD", 0x4A2A: "LOAD",
    0x316D: "SET", 0x4F69: "SET_END?",
    # --- literals ------------------------------------------------------
    **{a: f"PUSH.I {n}" for n, a in enumerate(
        [0x37E5, 0x37ED, 0x37F8, 0x37FE, 0x3804, 0x380A, 0x3810, 0x3816, 0x381C, 0x3822, 0x3828])},
    0x3834: "PUSH.I", 0x3831: "PUSH.I",
    0x3788: "PUSH.R8 0", 0x3791: "PUSH.R8 1", 0x379A: "PUSH.R8 2", 0x37A7: "PUSH.R8 3",
    0x37AE: "PUSH.R8 4", 0x37B5: "PUSH.R8 5?", 0x37D8: "PUSH.R8 10",
    0x387A: "PUSH.R8", 0x389A: "PUSH.T",
    0x37E2: "PUSH.B False", 0x383D: "PUSH.B True",
    # --- conversions ---------------------------------------------------
    0x0EB0: "CVT.I>V", 0x0E8B: "CVT.I>R8", 0x0E7B: "CVT.I>L", 0x0F1C: "CVT.L>V",
    0x0F2E: "CVT.S>R8?", 0x0F67: "CVT.R8>I", 0x0F7B: "CVT.R8>L", 0x0F49: "CVT.R8",
    0x1050: "CVT.V>I", 0x10A3: "CVT.V>T", 0x10F1: "CVT.S>V", 0x1102: "CVT.R8>V",
    0x11BB: "CVT.Ttmp>V", 0x11C3: "CVT.T>V", 0x49CE: "CVT.>B",
    # --- operators -----------------------------------------------------
    0x40DF: "ADD.V", 0x38D3: "ADD.I", 0x3B6E: "ADD.R8", 0x3D47: "ADD.T",
    0x416A: "SUB.V", 0x38E1: "SUB.I",
    0x4160: "MUL.V", 0x38EF: "MUL.I", 0x3B80: "MUL.R8",
    0x4241: "DIV.V", 0x3B89: "DIV.R8",
    0x390B: "NEG",
    0x4468: "EQ.V", 0x396D: "EQ.I", 0x3DF6: "EQ.T", 0x3C17: "EQ.R8", 0x3B51: "EQ.L",
    0x447A: "NE.V", 0x3980: "NE.I", 0x3DFF: "NE.T", 0x3C2A: "NE.R8",
    0x448C: "LE.V", 0x3993: "LE.I", 0x3C63: "LE.R8",
    0x449E: "LT.V", 0x39CC: "LT.I", 0x3C76: "LT.R8",
    0x44B0: "GE.V", 0x39A6: "GE.I", 0x3C50: "GE.R8",
    0x44C2: "GT.V", 0x39B9: "GT.I", 0x3C3D: "GT.R8",
    0x42FD: "AND.V", 0x39E7: "AND.I",
    0x4312: "OR.V", 0x39F2: "OR.I",
    0x42D1: "NOT.V", 0x39DC: "NOT.I",
    # --- builtins ------------------------------------------------------
    0x19F5: "Rnd", 0x743A: "Randomize",
    0x3BCA: "Int.R8", 0x4738: "Int.V",
    0x7677: "Str$", 0x760A: "Str$.I", 0x76A2: "Val",
    0x52BA: "Shell", 0x2A0D: "QBColor", 0x7EC5: "Error$", 0x19CE: "Err",
    0x52AF: "Timer", 0x1A78: "Now", 0x750A: "InStr", 0x75BC: "Mid$",
    0x537B: "ChDir", 0x5381: "ChDrive", 0x2A73: "Cls", 0x742E: "Beep",
    0x37DF: "MSGBOX_ARGS", 0x3844: "MSGBOX_ARGS", 0x52F4: "MsgBox",
    # --- file I/O ------------------------------------------------------
    0x376D: "FILENUM", 0x373B: "OPEN",        # operand: 1 Input, 2 Output
    0x3631: "CLOSE", 0x375B: "PRINT#", 0x6132: "PRINT#_ITEM",
    0x3692: "INPUT#?", 0x36DF: "INPUT#_FIELD?",
    # --- user-defined types -------------------------------------------
    0x0709: "ARRAY_ELEM_ADDR.UDT", 0x6D00: "FIELD_SET?", 0x6BD2: "FIELD_GET?",
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
