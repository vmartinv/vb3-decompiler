# VB3 P-Code

Findings on the VB3 p-code format, derived from `VBRUN300.DLL` and from
compiling known-source test programs with a real VB3 compiler.
`tools/pcode_disasm.py` implements everything here.

## Executable layout

- **Segment 1**: fixed 25-byte bootstrap stub, identical in every VB3 exe.
- **Segment 2**: data.
- **Segment 3**: procedure table (records, below).
- **Segments 4+**: code. One segment per module/form that has code:
  standard modules first, then forms, each in project order. A unit's
  non-empty procedures are concatenated in source order, one record each.

### Procedure records (segment 3)

| offset | field |
|---|---|
| +0 | u16 tag (`0x16` event procedure, `0x26`/`0x28`/`0x3A`/… `Sub`/`Function`) |
| +24 | u16 code start offset within its code segment |
| +36 | u16 code end offset |
| +38 | code segment selector, filled by an NE `INTREF` relocation |

**The code segment is given by the NE relocation table of segment 3**:
one `INTREF` fixup chain per code segment, visiting +38 of every record
that belongs to it (on disk those bytes are the chain links). Validated
on `qrace.exe` (16 forms, 101 procedures) and 17 multi-module/multi-form
test builds: the records' ranges tile each code segment exactly.

## Threaded code

**Each 2-byte opcode is the little-endian near address of its handler in
`VBRUN300.DLL` segment 25** (the interpreter). The p-code IP is `ES:SI`.
Handlers read operands with `es:lodsw`/`es:lodsb` and end with
`es:lodsw; jmp ax`.

- **Operand lengths are derived from the handlers**: explore each
  handler's x86 paths and count the SI advance before dispatch. Branches
  load SI with `mov si, es:[si]`. Inline blobs are `lodsw; add si, ax`.
  Near helpers are summarized recursively, and `jmp [reg+table]`
  type-dispatch is followed (including `mov di, imm` … `jmp cs:[di]`).
  All call-family handlers converge on the call core at `0x62E4`, where
  their operands have been read.
- **Constraint solving** covers the rest: every procedure must decode to
  exactly its end offset, so an underivable length is the unique candidate
  that makes that work (ties: the shortest followed by a statement
  marker). One manual length remains (`0x36DF`).
- **Opcode ID**: the u16 immediately before each handler. It is shared by
  type-specialized variants of the same operation. For example, variable
  access handlers `2D21 2B15 4BA3 4BCC 4A6E 316D` all have ID `0x0B`.
- **Builtins**: handlers that `call` one of the trampolines
  `0x792B`…`0x796C` (followed by a 1-byte index), or far-jump via
  `0x79A1`/`0x79A6`/`0x79B0`, dispatch into the runtime library. They
  return to the dispatcher without touching SI.
- **Call operand**: `& 6` selects one of 4 table-segment selectors
  (`[0x1CCC + n]`), and `& 0xFFF8` is the record offset.

Validation: every procedure of `qrace.exe` (9,537 instructions, 144
handlers, 81 IDs) and of 82 test binaries decodes to exactly its record's
end offset. All non-`FOR` branch targets land on instruction boundaries.

## Named handlers

Operand notation: `slot` = i16 variable slot, `tgt` = u16 absolute
offset within the code segment.

| handler | name | operands | notes |
|---|---|---|---|
| `494B`, `4935`, `491F`, `4906`, … | STMT | — | one per statement (`a: b` is two); entry points into the yield countdown `dec ss:[0x278]`. The entry used varies with the line's layout, not its meaning |
| `4965` | LABEL | u32 | label definition; emitted as its own statement or folded into the previous one |
| `65D9` | RET | — | procedure exit |
| `0E5E`, `0E5B` | TRAP | — | runtime-error stubs emitted after `RET` |
| `37E5 37ED 37F8 37FE 3804 380A 3810 3816 381C 3822 3828` | PUSH_I2 0..10 | — | |
| `3834` | PUSH_I2 | u16 | |
| `389A` | PUSH_STR | u16 skip, u16 ?, u16 len, bytes, NUL, pad to even | skip counts the bytes after itself |
| `2D21` | LOAD | slot | local; first slot `0x1A`, +4 per variable, in first-use order |
| `2FD4` | STORE | slot | |
| `0EB0` | CVT_LIT | — | coerce pushed literal before Variant ops |
| `38D3` `38E1` `38EF` | ADD/SUB/MUL (I2) | — | both operands literals |
| `40DF` | ADD | — | Variant operands |
| `390B` | NEG | — | |
| `3B89` | DIV | — | `/`, float |
| `10F1` | CVT_R8 | — | |
| `4468 447A 448C 449E 44B0 44C2` | EQ NE LE LT GE GT | — | |
| `49CE` | CVT_BOOL | — | |
| `34B7` | JMP_FALSE | tgt | |
| `35FE` | JMP | tgt | |
| `35EC` | ENDIF | — | |
| `1B37` / `1B3E` | FOR / FOR_STEP | slot, tgt | tgt = the matching NEXT's operand word; handler skips it |
| `1E08` | NEXT | slot, tgt | back-edge to first body statement |
| `62E0` | CALL | u16 0, u16 record | `62DD`, `62A7` are variants (e.g. `Declare`d DLL calls) |
| `7E63` | RESUME | — | |
| `4A15` | PRINT | — | followed by argument push + finisher |

## Source-aligned corpus

`tools/align_source.py` pairs each statement of a compiled project with
its source line. Compiling VB3's own sample projects gives thousands of
aligned lines (see README: `restore_install.py`, `compile_project.py`).
Handlers are named from those pairs.

## Next steps

- Name the remaining handlers used by `qrace.exe`: read each one's x86 in
  segment 25, grouped by opcode ID, and confirm with compile-and-diff.
- Map builtin index bytes to VB builtins (`Rnd`, `Int`, `Str$`, …).
- Decode operands: the `0xC0xx` property operands (`4C09` family, likely
  `0xC000 | property index`), the variable-scope variants (ID `0x0B`), and
  `PUSH_STR`'s second field.
- Emit pseudo-BASIC from the listing.
