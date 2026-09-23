# VB3 P-Code

Findings on the VB3 p-code format, derived from `VBRUN300.DLL` and from
compiling known-source test programs with a real VB3 compiler.
`tools/pcode_disasm.py` implements everything here.

## Executable layout

- **Segment 1**: fixed 25-byte bootstrap stub, identical in every VB3 exe.
- **Segment 2**: data.
- **Segment 3**: procedure table (records, below).
- **Segments 4+**: code. One segment per module/form that has code:
  standard modules first, then forms, each in project order. Each
  non-empty procedure has one record. Records are in order of the
  procedure name's **first mention** in the file (its definition or an
  earlier call). Code layout within the segment uses a different order.

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
  that makes that work (ties: the smallest). One manual length remains (`0x36DF`).
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

## Handler names

`tools/opcodes.py` names every handler `qrace.exe` uses, from targeted
test projects (scope × type, operators/builtins/file I/O) and the sample
corpus. Names ending in `?` (2 in `qrace.exe`) are inferred from context.

- **Opcode IDs group operations**; handler variants within an ID carry the
  operand type/storage. For example ID `0x0B` load, `0x0C` store, `0x0E`/`0x0F`
  array load/store, `0x11`/`0x12` property get/set, `0xA3` add, `0xA6` `=`,
  `0xDE` `>=`.
- **Variables**: storage × type handler tables (local/ByVal, form/module/
  Static, `Global`, ByRef). Operand = slot, assigned in first-use order.
  4-byte loads serve both Long and String (far pointer).
- **Arrays**: operands `u16 dimension count, u16 array slot`, indices
  pushed first.
- **Controls/forms**: `CONTROL`/`FORM` push a reference (operand = slot,
  resolved to a name — see "Symbols" below);
  `PGET`/`PSET` operand `0xC0nn` = property `nn` **of that control's
  class**; `PGET_ME`/`PSET_ME` address the implicit form.
- **Methods**: `ARGS … OBJ OBJ_SELF [args] METHOD NARGS END_CALL`.
  `METHOD`'s operand byte 6 is a global method number (`0x0F` Show, `0x10`
  Hide, `0x13` SetFocus, `0x04` Refresh, `0x0C` Clear, `0x02` AddItem, …).
- **Statement markers**: several entry points into the yield countdown
  (`494B`, `4935`, …, and `48AF` which skips a u16); one per statement.
- **Builtins**: a stub is `call <trampoline>; u16 index` into the runtime
  library; handlers are named from the corpus (`Rnd`, `Int`, `Val`, …).

## Symbols: control and form references

A module's slots for controls and forms are initialized from its data
image in `RT_RCDATA` 2 (see `RESOURCE_FORMAT.md`):

- control slot: `u16 kind (0x40xx), u16 0x8000 | name index, u16 0`; the
  name index points into the form's name table.
- form/object slot: `u16 0x80NN, u16 global offset`. Forms have
  consecutive NN in project order from base `0x46` + one per VBX file and
  one per VBX control class (both listed in `RT_RCDATA` 1); built-ins:
  `0x32` Printer, `0x33` Screen, `0x34` Clipboard, `0x3D` App. A `FORM`
  slot without `0x80NN` is an object variable (`Dim x As Control`).
- `CTLARRAY_OF`/`SUBOBJ` operand `0x80nn` = control `nn` of the form
  pushed just before (`frmStatus!cmdTrain`).
- Control class: slot kind byte (`0x1C/1D` Label, `0x1E/1F` TextBox,
  `0x22/23` CommandButton, `0x42/43` Image, … odd = control array; table in
  the tool), or the class byte of the control's record in the form blob.

## Properties

`PGET`/`PSET` operand `0xC0nn` = entry `nn` of the object's class property
list. The lists are read from `VBRUN300.DLL`'s data segment: each class
has a MODEL (default name, class name, parent class, property list, event
list); list entries are `0xFFxx` (index `~w` into the master
standard-property table: Name, Index, hWnd, BackColor, …) or a pointer to
a class-specific PROPINFO (first word = name), terminated by 0. Verified
against the corpus (e.g. Label `0x18` AutoSize, TextBox `0x0B` Text,
ListBox `0x13` ListIndex). All 345 property accesses in `qrace.exe` are
named.

`pcode_disasm.py` resolves these. A segment's form comes from its event
procedures (below); its data image is the chunk resolving the most slots.
Against sample source (`tools/validate.py`): 1,299/1,313 references
correct, 5 wrong (`Forms` collection, one VBX), 9 object variables.

## Procedure names

Event procedures are bound in the form blob: each control record ends
with an event table `FF, u8 count (= the class's event count), count × u16`
where a non-zero entry is the handler's procedure record | 1. The owner is
the record ending with the table (`u8 flag, u16 length (excl. itself), u16
flags, u8 name index, …`, class byte at +7, or +9 for control-array
elements); tables outside control records are the form's. Event names
come from the class MODEL's event list in `VBRUN300.DLL` (entries `0xFFxx`
→ master event table Click, DblClick, DragDrop, …; or EVENTINFO
pointers). General `Sub`/`Function` names are not stored (record +4 is an
offset into a design-time name pool that isn't in the EXE).
Against sample source: 339/369 event procedures named correctly.

## Source-aligned corpus

`tools/align_source.py` pairs each statement of a compiled project with
its source line. Compiling VB3's own sample projects gives thousands of
aligned lines (see README: `restore_install.py`, `compile_project.py`).
Handlers are named from those pairs.

## Lifting to source

`tools/lift.py` lifts each statement back to BASIC on a symbolic expression
stack (operators, builtins, objects/properties, methods, calls, If/ElseIf/
Else/End If, Do/Loop, For/Next, Select Case, Exit/End/GoTo/On Error).
`lift.py score` compares every aligned corpus statement with its source
line (identifiers normalised): 3,001/3,326 match, 0 differ, 325 use
unsupported handlers (Print/graphics methods, file I/O, rarer builtins).

## Next steps

- Remaining naming errors on samples (`tools/validate.py`): 15 event
  procedures (mostly VBX controls in `loan`), `Forms` collection.
- Property names for custom (VBX) controls and `PGET_ME`/`PSET_ME`
  (implicit-form properties; operand not decoded).
- Lifter long tail (Print/file I/O, remaining builtins, UDT fields), then
  the source emitter + round-trip check: decompile each sample, recompile
  with the IDE, compare p-code per procedure.
- Pseudo-BASIC output: expression stack + control-flow structuring.
