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
| +0 | u16 frame size (`0x16` + locals and return value) |
| +12 | u8 1 `Sub`, 2 `Function` |
| +13 | u8 return type: 1 Integer, 2 Long, 3 Single, 4 Double, 5 Currency, 6 Variant, 7 String |
| +14 | u8 `0x0C` for a `Declare` (DLL) record |
| +15 | u8 argument words (ByRef 2, ByVal Integer 1, ...) |
| +24 | u16 code start offset within its code segment |
| +36 | u16 code end offset |
| +38 | code segment selector, filled by an NE `INTREF` relocation |
| +40, +46 | `Declare`: DLL and function name (name pool offsets) |
| +50 | u16 source line count, including the comment block above it |

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
  `0x08` Forms, `0x32` Printer, `0x33` Screen, `0x34` Clipboard, `0x3D` App. A `FORM`
  slot without `0x80NN` is an object variable (`Dim x As Control`).
- `CTLARRAY_OF`/`SUBOBJ` operand `0x80nn` = control `nn` of the form
  pushed just before (`frmStatus!cmdTrain`); `0x00nn` = late-bound control
  (below); `0xC0FE` = `.Controls` (`SUBOBJ`) / `.Controls(i)`
  (`CTLARRAY_OF`). Separator as written: `4A57`/`4EA9` `!`, `4A63`/`4EB0`
  `.` (150/150 in the samples).
- Object arrays (`Forms(i)`, `Document(i)` of `Global Document() As New
  frmNotePad`): unnamed handlers with interpreter ID `0x0E` (`4CD5`,
  `4DEA`; store `0x0F`) and operand `u16 argc, u16 slot`.
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

Validation against sample source (`tools/validate.py`): 1,393/1,393
property accesses named correctly (generated tests: 24/24). Object class
is tracked per instruction: control class from the form blob's control
record (records chained by `start + 1 + length`; class byte at +7, +9 for
array elements, VBX class name after `0xFF`); `Form` or `MDIForm` (the
form's own event-table size) for forms, `Me` and `Forms(i)`; the
built-in's own list for Printer/Screen/…; `ActiveForm` → Form, `Recordset`
→ Dynaset. `0xC0FD` = `.Count` of a collection (`Forms`, `Controls`).
`PSET_IDX`/`PGET_IDX` with `0x80nn` = control-array element with its
default property (`Form1.FieldBoxes(3) = x`).

`PGET_ME`/`PSET_ME` (unqualified name in form code) take a data-image slot:
`u16 0x40xx, u16 0xC0nn` = the form's property `nn` (`Left`, `Width`), or
a control record = that control's default property (`ReadOut = "0."`).
102/102 in the samples.

Late-bound properties (on `As Control`/`As Form` variables) use operand
`0x00nn`: `nn` numbers such properties in first-use order across the
project, and `RT_RCDATA` 1 stores, per class, each one's index in that
class's property list (`58 <class#> 00 00, kind, kind, 47 00 00 | 47 03
00 <VBX name>, u16 n, n × number, n × index`); names follow from the lists.
Late-bound control names (`frmMDI.ActiveForm.Text1`, `Frm!lstForms`) share
the numbering; each form's `RT_RCDATA` 1 entry (`… NAME.FRM\0`, project
order) ends with `u16 n, n × number, n × name-table index`.

OLE Automation (`As Object` variables) is late-bound by name: `RT_RCDATA`
3 (present only when OLE is used) is `u16 length` + NUL-terminated member
names, and `PGET`/`PSET 0x00nn`, `PGET_IDX`/`PSET_IDX u16 argc, u16 name`
and `OLE_CALL` (`3357`: `u16 argc, u16 name`, statement call) take the byte
offset of the name. No server or OLE DLL is needed to name them.

Object-variable declared class, from the variable's data-image record
(`kind` 1 = Form, 4 = Control, else a control class kind): module-level
`kind, 0, 0`; local `kind, frame, frame` (negative offsets); parameter
`kind, bp offset` (positive, e.g. `01 00 0a 00` = first of two `As Form`
parameters); a global used from another module (loaded by `FORM`) `kind,
global offset`. `As New frmX` variables and arrays: `0x80NN` (the form's
NN), then the frame or global offset.

`pcode_disasm.py` resolves these. A segment's form comes from its event
procedures (below); its data image is the chunk resolving the most slots.
Against sample source (`tools/validate.py`): 1,458/1,458 references
correct, plus 9 object variables.

## Procedure names

Event procedures are bound in the form blob: each control record ends
with an event table `FF, u8 count (= the class's event count), count × u16`
where a non-zero entry is the handler's procedure record | 1. The owner is
the record ending with the table (`u8 flag, u16 length (excl. itself), u16
flags, u8 name index, …`, class byte at +7, or +9 for control-array
elements); tables outside control records are the form's. Event names
come from the class MODEL's event list in `VBRUN300.DLL` (entries `0xFFxx`
→ master event table Click, DblClick, DragDrop, …; or EVENTINFO
pointers). VBX controls (class byte `0xFF` followed by the class name)
use the MODEL in their `.VBX` (same layout, standard entries via VBRUN300's
master tables); VBX files are located by the names in `RT_RCDATA` 1. General `Sub`/`Function` names are not stored (record +4 is an
offset into a design-time name pool that isn't in the EXE).
Against sample source: 369/369 event procedures named correctly.

## Source-aligned corpus

`tools/align_source.py` pairs each statement of a compiled project with
its source line. Compiling VB3's own sample projects gives thousands of
aligned lines (see README: `restore_install.py`, `compile_project.py`).
Handlers are named from those pairs.

## Lifting to source

`tools/lift.py` lifts each statement back to BASIC on a symbolic expression
stack (operators, builtins, objects/properties, methods, calls, If/ElseIf/
Else/End If, Do/Loop, For/Next, Select Case, Exit/End/GoTo/On Error).
`lift.py infer` proposes semantics for unknown handlers by searching
(function/statement, name from the source line, arity) for the reading
that makes the corpus lines lift exactly; accepted readings go in
`opcodes.SEM`. `lift.py score` compares every aligned corpus statement with its source
line (identifiers normalised): 3,335/3,335 match (samples + generated
tests), 0 differ, 0 unsupported.

## Source recovery

`tools/decompile.py` rebuilds the project from the exe; `tools/roundtrip.py`
recompiles it in the IDE next to the original source and compares p-code
per procedure (`tools/pcode_diff.py` shows instruction diffs). **All 483
procedures of the 22 samples recompile p-code-identical** (form layouts
still copied from the original source).

- **RT_RCDATA 2 layout**: a header (with the String constants' texts,
  `u16 size, u16 len, text, 0`, in declaration order), the global image,
  the name pool (`u16 size, 0, 0x1A`, 32-bucket hash, entries `u16 link,
  u8, u8 len, name`, offsets from its start + 2; DLL/`Declare` names), then
  each .bas image, then each form image (16 zero bytes, form record at
  0x16) and its control list. Chunks are `u16 len, u16, u16 0x1E`; the word
  before a module's chunk + 4 is its declarations record (+18 flags: 0x40
  Option Explicit, 0x800 Option Compare Text, 0x8000 the text contains a
  tab; +30 name-table size, +34 the same in 16-byte units; +44 DefType
  table or 0xFFFF; +50 line count).
- **Slots**: one numbering per module, in compile order (source text
  order, but an assignment's target after its expression): Functions and
  Declares first (record offsets, sorted by name), the declarations
  section, then each procedure: its parameters, return value, locals,
  and the controls/forms/globals/constants it's first to use. A slot's
  value starts 2 bytes past it (control/object records at the operand).
  Sizes: control and form-property records 6, form/object records 4,
  local Variant 4 (second word: its number), local array 6, others 2;
  inline module storage by type (I 2, L/S/T 4, D/C 8, V 16, Type size + 2,
  arrays 18 + 4 per dimension, dynamic 50).
- **Locals**: value = BP offset (< 0), String number (odd: Strings and
  Variants share one numbering 1, 3, ...), or 0 when unused (an unused
  Variant still takes 16 bytes of frame). Parameters: even BP offsets >= 6,
  4 argument bytes each ByRef; unused ones hold 0 too.
- **Declarations**: a `Global` is a slot holding its global offset
  (`Global x As <class>/<form>`: `kind or form number, global offset`;
  `x() As New frmX`: class reference + `0x80NN, global offset`); array
  descriptors give fixed bounds (`0x4000 | dims`, then `(count, lower)`
  per dimension, last first). A `Global Const` used in another module gets
  a copy slot there (first use); equal-valued constants need distinct names
  (one slot each). Records follow text order, so `Declare`s are emitted in
  record order, in the module whose records surround theirs.
- **Global image**: offset 4 heads the Type chain (`name, next, size,
  first field`; field `name, next, type, offset`, fixed strings with a
  length word before; FIELD_* operands are field record offsets); globals
  follow in declaration order with constant values inline (String: a
  descriptor); the global object table (`0x80NN, 0, 0`) ends it.
- **Names not stored**: general Sub/Function (code layout and Function
  slots are sorted by name, so synthetic names are fitted between the
  stored ones), variables, Types/fields, labels.
- **Encoded source details**: statement markers give the indentation
  column (table in `decompile.py`; `48AF` + u16 for 25+, `4958` a `:`
  statement; a marker before a `LABEL` is a blank line); `49CE` explicit
  parentheses; literal radix (`3831`/`388A` hex, `3834`/`388D` decimal);
  a type suffix at a use (`b%`) selects another handler entry (ID `| type
  << 10`); `0768` `ReDim a$(...)`; `Left(` vs `Left$(` by `CVT.Ttmp>V`.
- **Name-table size** (+30): 349 + Σ(len + 4) over the module's unique
  identifiers (case-insensitive), computable from the source (all 61
  sample modules fit). Counted: variables, constants, parameters,
  procedure names (events included), labels (numeric too), properties,
  referenced controls/forms, `Screen`/`App`/`Printer`/`Clipboard`,
  object type names (`As Form`, `As Control`, `As CommandButton`),
  `Compare` (Option Compare), `BF` (and `B`, which also adds `BF`), Type
  and field names, Declare names (not the Alias). Not counted: keywords,
  builtin functions/statements (Rnd, QBColor, Dir, Err, Timer, ...,
  `Left`/`Width` only as functions/statements), methods (Show, Move,
  Line, Print, EndDoc, SetText, FindFirst, MoveNext, ...), `Me`,
  `Debug`, type suffixes, intrinsic types. Identifiers are at most 40
  characters. The project record at table offset 12 (+30) sums global
  names similarly.
- **Print** (all targets alike): one op per item type (% & ! # @ $ V)
  and following separator: newline / `;` / `,`. `0x610A` ends a
  statement without newline (trailing separator, empty Print). Tab(n) /
  Spc(n) have their own ops, followed by untyped `;`/`,` ops, which also
  stand alone (`Print , x`).
- **Code segment → module**: a module's procedure records follow its
  declarations record (the word before its image + 4) in the table, so
  each segment belongs to the module whose record precedes its records.
- **Compile-time name pool** (procedure record +4): the offset of the
  procedure's name in the IDE's project-wide name pool (same layout as
  the exe's pool: 90-byte header, entries `len + 4`). Order: per module,
  code modules (`.bas`/`.glo`) first, then forms, each in project order:
  the module file's **full path** as the IDE saw it (under Wine
  `Z:\home\...\NAME.FRM`), then every procedure/Declare name the module
  enters first, at its definition or a call statement (first word or
  after `Call`; a Function called inside an expression isn't entered
  there). Names are shared: the same name in two modules has one entry.
  Controls, variables, comments and form properties don't take entries.
  The exe keeps only the Declare names, but the offsets give each unstored
  name's length and each module path's length (`Decompiler.pool_lengths`;
  the original build directory's length follows from any form boundary).
  The model predicts all 483 sample records. The project record (table
  offset 12, +30) is the pool end + 259 + the global names.
- Unused `Const`s are not neutral padding: each changes
  declarations-record +6/+10/+14/+36.
- **Event signatures** from EVENTINFO (1 Integer, 3 Single, 6 String,
  8 Control, + Index); **Declare parameters** from call-site conversions.

## Next steps

- Whole-exe identity: data images still differ in 6 samples (names
  of Types/fields, symbol-hash order). Procedure names now take their
  original lengths from the name pool; still to size: variable/label
  names (per-module name table +30, table offset 76 record +30), the last
  pool entry, Declare aliases.
- DefType tables beyond `DefInt A-Z` (only form seen in the samples).
- Batteries (`tools/battery.py`, 717/718): types 68/69 (object vars:
  free order, see below); builtins, expressions, print, statements,
  names, deftype, controls complete.
- Form blob class bytes: 03 Frame, 0A VScrollBar, 16 Shape, 17 Line,
  25 Data (besides those in formblob.CLASS_IDS). A stored font with
  default values comes from an attribute the control drops (FontItalic
  on a DirListBox): emitting it reproduces the record.
- Type array fields: field "next" | 1, descriptor after the field (see
  vbdecl.py). FIELD_ALOAD/ASTORE per element type: ID 0x13 / 0x14.
- REDIM_AS second word: the text column of `As`; generated names are
  resized to put it there.
- Print item ops per type (`;` / end of line): V 6085/60A4, I 6045/60DE,
  L 604B/608F, S 6059/609D, D 6069/60AE, C 607F/60B5, T 6104/6132.
- Labels: LABEL 4965 `u32` = FFFFFFFF (named) or `FFFF, line number`;
  48BE adds a word, the spaces before a statement on the label's line.
  A statement marker after the label = the statement on its own line.
- A function call written with its suffix (`F%(1)`) uses a suffixed
  entry of CALL_FN (ID | type << 10, e.g. 62A1 `%`, 6298 `$`), like
  variable accesses; so does `F% = x` in its body. The record doesn't
  distinguish `Function F% ()` from `Function F () As Integer`.
- OPEN/OPEN_LEN word: low byte mode, high byte Access (bits 0-1:
  Read/Write/Read Write) and lock (bits 4-6: 4 Shared, 3 Lock Read,
  2 Lock Write, 1 Lock Read Write).
- `String * n`: 3264/3275 its address (module or Static / local) for
  LSet/RSet and ByRef arguments; 6AE2/6B1B copy a ByRef argument to a
  temp and back. 6AC5: ARG_TEMP for 4-byte values.
- Epilogue frees (OBJ_FREE 4FA6, 505F, Type 6F79) follow the IDE's
  local symbol table (VB.EXE seg53:3476, iterator 806A/8097): 8 buckets
  for a procedure (16 at module level), walked in order, each chain in
  insertion order. Bucket = (name-table offset >> 1) & 7 (seg53:6DB8):
  a name's offset in the module's name table (entries 4 + length bytes,
  allocated at first appearance in the module text). So free order is
  fixed by the lengths and first-mention order of all earlier names.
- Unused parameters have no slot: their count comes from the callers,
  their layout from the BP frame (`6 + 2 * argwords`).
- Procedure/Declare records (56 bytes) follow the module's declarations
  record (`word(image, m.image-2)+4`) and are allocated in first-mention
  text order: a Sub call statement (62DD bare, 62E0 `Call` keyword)
  allocates the callee's record, a function call in an expression
  (62A7) doesn't. Code layout is sorted by name, case-insensitively.
- A module's leading Function/Declare slots can hold another module's
  procedure record: an external call slot allocated at first use.
- Declarations record +44: 4/8 if any DefType, 0xFFFF if none (flag
  only). +18 flags: 0x40 Option Explicit, 0x800 Option Compare Text.
