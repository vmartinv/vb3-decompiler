# VB3 P-Code Opcodes

Visual Basic 3.0 compiles to p-code (bytecode interpreted by `VBRUN300.DLL`
at runtime), not native x86. No usable public reference for VB3's specific
p-code encoding was found anywhere (see "Prior art" below) — everything on
this page is original empirical work, derived by compiling small
known-source test programs through a real VB3 compiler and diffing the
resulting bytecode. Treat it as a research log, not a finished spec: gaps
and open questions are called out explicitly rather than guessed at.

## Prior art checked (negative results)

- ["VISUAL BASIC REVERSED - A decompiling approach"](https://sandsprite.com/vb-reversing/files/VISUAL%20BASIC%20REVERSED.pdf)
  (AndreaGeddon) — not applicable. Covers VB6 apps compiled to *native
  x86*, not p-code at all.
- [vb-decompiler.org's p-code decompiling article](https://www.vb-decompiler.org/pcode_decompiling.htm)
  and its linked [opcode table](https://www.vb-decompiler.org/vb_pcode_table.htm)
  — real p-code documentation, but explicitly **VB5/6 (32-bit) only**, and
  confirmed incompatible: VB5/6 string literals are Unicode, referenced
  indirectly via an offset into a `ProcTable.DataConst` table; VB3 embeds
  ASCII strings directly inline in the instruction stream with a length
  prefix (see below) — no indirection at all. VB3 is an older, 16-bit,
  real/protected-mode segmented interpreter — a different VM generation.
  Do not assume VB5/6 opcode values carry over to VB3.
- A partially-complete VB3-to-VB6 decompiler exists
  ([Ron R. Dodi's port of DoDi's VB3 decompiler](https://github.com/Planet-Source-Code/ron-r-dodi-s-vb3-decompiler-has-been-decompiled-and-converterd-to-vb6-90-complete__1-38543),
  ~90% complete per its own README) with a real compiled disassembler
  binary, `VBDIS3E.exe` — a genuine 32-bit PE executable, not a 16-bit
  stub. Initially looked promising: its decompiled VB6 source
  (`MODULE12/15/17.BAS` etc.) has a numeric constant, `mc0124 = 229`
  (`0xE5`), matching this page's "push literal 0" opcode. **Checked in
  detail and it's a false lead** — that constant is used in the
  disassembler's own *output-file-writing* code
  (`Chr$(mc0124)` appended to a string while generating a `.txt` report,
  guarded by `Asc(Right$(...)) <> mc0124` to avoid a duplicate
  terminator), not in any opcode-dispatch table. Coincidental overlap in a
  256-value byte space, not evidence the two tools share an opcode
  encoding. `VBDIS3E.exe` itself has still not been run (needs
  `MSVBVM60.DLL`. **Update: ran it.** Installed `MSVBVM60.DLL` + `ComDlg32.OCX`
  via `winetricks vb6run comdlg32ocx`, launched it under Wine, and drove it
  through File > Open on `qrace.exe` and a project-output path — the UI
  works (module/subroutine/control panes, scan button), but scanning
  `qrace.exe` produces zero modules, zero output files, and no error. Dead
  end: the bundled `vbdis3i.dat` opcode-table file is flagged by the tool's
  own startup check as "wrong version," and the scan silently no-ops
  without it. No usable disassembly obtained. Not pursuing this tool
  further — continuing opcode discovery via the empirical compile-and-diff
  method instead.

## Setup: getting a VB3 compiler running

See `README.md` for the full setup. Summary: no VB3 *installer* run is
needed — `VB.EXE` (the IDE/compiler) can be decompressed directly out of
the individual KWAJ-compressed files on the original setup disks
(`tools/vb3ide/kwaj_extract.py`) and run under Wine's built-in 16-bit
(`winevdm`) shim with only `VBRUN300.DLL` alongside it. The graphical
`SETUP.EXE` reliably fails with a bogus "Insufficient memory or disk
space" error under Wine regardless of actual free space — not root-caused
since it turned out to be unnecessary.

## Segment/procedure structure

An NE executable's segment table holds the compiled p-code. For a VB3
project:

- **Segment 1** is a fixed, universal 25-byte bootstrap/entry stub —
  **byte-identical across every VB3 p-code executable tested**, regardless
  of what the program does (confirmed across a dozen+ test programs plus a
  real full-sized game binary, [Quibble Race](https://github.com/vmartinv/qrace)).
- Each `Sub`/`Function`/event procedure with at least one statement
  contributes code to a CODE segment. An **empty** procedure (e.g. an
  unused `Form_Load`) contributes nothing. **Correction**: earlier testing
  (single-procedure programs only) suggested one segment per procedure;
  compiling a form with a `Form_Load` that `Call`s two extra `Sub`s showed
  all three procedures' code concatenated into the *same* CODE segment —
  so it's one segment per module (or some larger unit), not strictly one
  per procedure. Not yet clear what the real grouping boundary is.
- A form/module's own segment (which also carries a fixed-format header —
  see `RESOURCE_FORMAT.md` — plus embedded picture data if any) acts as a
  **procedure descriptor table**: one entry per procedure, tracking at
  least that procedure's total code length. Confirmed by sweeping many
  variants and finding this segment byte-identical except for one length
  field that exactly tracks the corresponding procedure segment's size.

### Universal procedure prologue/epilogue

Every procedure segment found so far:

- **Starts** with the same 2 bytes: `4B 49`.
- **Ends** with the same 8 bytes: `4B 49 D9 65 5E 0E 5B 0E`.

Identical regardless of the procedure's content — confirmed across
arithmetic, variable assignment, `Print`, and multi-statement procedures.

## Variables: LOAD / STORE, positional slots

- **LOAD**: opcode `21 2D` + a `<u16 LE slot>` operand (4 bytes total).
- **STORE**: opcode `D4 2F` + a `<u16 LE slot>` operand (4 bytes total).

Variable **identity is positional, not name-based** — `x = 1` and `y = 1`
compile to byte-for-byte identical procedure segments. The first local
variable referenced gets slot `0x001A`; each subsequently-referenced
distinct variable gets the next slot, **+4** from the last (`0x001E`,
`0x0022`, ...) — assigned in first-*use* order during compilation, not
textual declaration order. Confirmed with up to 3 variables in one
procedure. **Caveat**: this was only tested with a single procedure per
segment. With multiple `Sub`s sharing a segment (see above), slot
numbering was observed to continue *across* procedures rather than
resetting at `0x001A` for each one — e.g. a second `Sub`'s only variable
got slot `0x001E`, not `0x001A`. Whether slots are truly segment-wide
(shared storage) or this is a compile-time-only bookkeeping artifact
(with real per-procedure stack frames at runtime) is not determined.

Example (`y = x`, a pure variable-to-variable copy):

```
4B49                  prologue
212D 1A00             LOAD  slot 0x1A (x)
D42F 1E00             STORE slot 0x1E (y)
4B49 D965 5E0E 5B0E   epilogue
```

## Integer literals: two encodings

**N = 0 to 10**: dedicated single-byte "push small int" opcodes, a fixed
18-byte procedure (`4B49 <OP> 37 B00E D42F 1A00 4B49 D9655E0E5B0E`) where
`<OP>` is:

| N | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| opcode | `E5` | `ED` | `F8` | `FE` | `04` | `0A` | `10` | `16` | `1C` | `22` | `28` |

N=2 through N=10 are evenly spaced by exactly **6** (a linear opcode
table — consistent with "threaded code" p-code VMs where each opcode
indexes a table of fixed-size handler stubs). N=0 and N=1 don't fit that
same linear extrapolation — most likely separate, earlier table slots (0
and 1 being by far the most common integer literals in real code), but
not confirmed independent of the byte pattern itself.

**N = 11 to 32767** (up to the max positive 16-bit signed `Integer`,
VB3's default numeric type): a generic 2-byte opcode, `34 38`, followed by
the literal as a raw **little-endian u16** operand. A 20-byte procedure:
`4B49 3438 <NN NN> B00E D42F 1A00 4B49 D9655E0E5B0E`. Verified exactly for
`0x000B`=11, `0x0064`=100, `0x0100`=256, `0x03E8`=1000, `0x7FFF`=32767.

`B0 0E` here is a separate coercion opcode — see below.

## Arithmetic

Integer `ADD`/`SUB`/`MUL` share a `38` second byte (same second byte as
the u16-literal-push opcode, `34 38` — `38` likely marks a general
"integer stack operation" class):

| op | ADD | SUB | MUL |
|---|---|---|---|
| opcode | `D3 38` | `E1 38` | `EF 38` |

`D3`, `E1`, `EF` are evenly spaced by exactly **14** — a second linear
opcode table, same pattern as the small-int push table but a different
stride.

**ADD has two distinct forms depending on operand provenance**:

- `D3 38` when both operands came from literal pushes (`x = 1 + 2`).
- `DF 40` when both operands came from LOADs (`x = y + z` →
  `LOAD y, LOAD z, DF40 (ADD), STORE x` — no coercion opcode at all).
- Mixing the two (`x = x + 1` → `LOAD x, PUSH 1, B00E, DF40 (ADD), STORE
  x`) needs `B0 0E` first, to convert the freshly-pushed literal into
  whatever representation LOAD results are already in before `DF 40` can
  combine them.

This was resolved by comparing three variants: `x = y + z` (no literal, no
`B0 0E`), `x = x + 1` (one literal, one `B0 0E`), `x = 1 + 2` (two
literals, no `B0 0E`, uses `D3 38` instead). `B0 0E` is best understood as
a general "coerce a freshly-pushed literal for use in a Variant-typed
binary op" opcode — plausible given VB3 variables are Variant-backed
internally even when holding what looks like a plain Integer.

**Unary negate**: opcode `0B 39` (own family suffix, `39`, distinct from
both `37` used by literal pushes and `38` used by binary arithmetic).
Inserted directly after the pushed operand, before the store: `x = -1` →
`PUSH 1, NEGATE, STORE`.

## Division: promotes to floating point

`/` (true division) behaves completely differently from the integer
family, consistent with real VB semantics where `/` always produces a
floating-point result even for integer operands (unlike `\`, integer
division — not yet tested). `x = 1 / 2`:

- Pushes its operands with **different opcodes** than the integer table —
  `91 37`, `9A 37` for the literals 1 and 2 (same `37` family suffix as
  the integer push table, but different values than integer-1/2's
  `ED`/`F8`) — implying a **separate small-constant table for
  `Double`/floating literals**.
- Divides with `89 3B` — a third family suffix, `3B`, distinct from
  arithmetic's `38`.
- Uses a different pre-store coercion opcode, `F1 10`, in place of `B0
  0E` — consistent with a Double-specific coercion rather than the
  Integer one.

Only structurally understood so far, not decoded in detail.

## Strings

String literals are embedded **directly inline** in the code stream, no
separate string pool/table:

```
<u16 length, little-endian><raw ASCII bytes, no terminator>
```

Confirmed two independent ways: (1) empirically, by checking that the 2
bytes immediately preceding several different known UI strings in a real
VB3 binary always exactly equal that string's byte length; (2) by
compiling `Print "hi"` through the real IDE and finding the exact same
`<u16 length><ASCII>` pattern (`02 00 68 69`) in the output.

## `Print`

Tested `Print "hi"`, `Print "bye!"` (different string length), `Print 5`,
`Print 7`, `Print 0` (numeric literals), and `Print x` (a variable) to
isolate `Print`'s call shape from its argument:

- **`15 4A 37 21`** (4 bytes) is a fixed prefix before every `Print`
  argument, identical regardless of argument type — this is `Print`'s own
  opcode.
- **Variable argument**: prefix + the already-decoded `LOAD` opcode
  (`21 2D <slot>`) + a 2-byte finisher `A4 60`.
- **Small-integer-literal argument** (tested N=0, 5, 7 — within the
  existing 0-10 small-int table): prefix + a push whose first byte matches
  the standalone small-int table (`E5`/`0A`/`16` for 0/5/7) + a finisher
  `DE 60`. The push's *second* byte is inconsistent across samples — `37`
  (matching the standalone table exactly) for N=0, but `38` for N=5 and
  N=7 — not understood; possibly N=0 is compiler-special-cased, or there's
  a real distinction this 3-sample test doesn't capture. Flagged open.
- **String-literal argument**: prefix + `9A 38 <u16 A> <u16 0x000C>` +
  the inline length-prefixed string (see above) + trailer `00 00 32 61`.
  `<u16 A>` = `6 + <string char length>`, confirmed exactly with both a
  2-char and a 4-char string. The `0x000C` field's meaning is unresolved
  (constant in both samples). Structurally distinct from the
  numeric/variable cases — no `X 60`-style finisher, ends in `32 61`
  instead.

Not yet tested: multiple `Print` arguments (`;`/`,` separated), floating-
point literal arguments, `Print` with no trailing newline (`;`-terminated).

## Comparison operators

`z = (y OP x)` (both operands LOADed, isolates the operator cleanly): all six
share second byte `44` — a third linear opcode table, stride **18**, followed
by a uniform result-coercion opcode `CE 49` (same role as `B0 0E`/`F1 10` for
arithmetic — converts the raw comparison result to Variant Boolean) before
STORE:

| op | `=` | `<>` | `<=` | `<` | `>=` | `>` |
|---|---|---|---|---|---|---|
| opcode | `68 44` | `7A 44` | `8C 44` | `9E 44` | `B0 44` | `C2 44` |

Not yet tested: literal-operand provenance (whether comparisons have a
"both-literal" alternate form the way `ADD` did with `D3 38` vs `DF 40`).

## Statement markers and `If`/`Then`/`Else` control flow

**`4B 49` marks the start of every source statement**, not just the
procedure prologue as first thought — confirmed by compiling `If`/`Then`
and `If`/`Then`/`Else` blocks and finding a `4B 49` before each line's
bytecode (the condition, the `Then` body, the `Else` body, and even an
implicit marker for the `End If` line itself). The "epilogue" is just this
same per-statement marker preceding the real `Sub`-exit sequence
(`D9 65 5E 0E 5B 0E`).

Jump targets are **absolute byte offsets from the start of the procedure
segment** (not relative/PC-relative) — confirmed by checking that a
branch's operand always lands exactly on a `4B 49` statement-start marker.

Decoded via `If x = 1 Then \n y = 2 \n End If`, a variant with a longer
`Then` body, and `If x = 1 Then \n y = 2 \n Else \n z = 3 \n End If`:

- **`B7 34 <u16 LE target>`**: conditional branch. Pops the Boolean left by
  a comparison (see above); jumps to `target` if false, falls through if
  true. Used right after the condition to skip the `Then` body (or jump
  straight to `Else`, if present).
- **`FE 35 <u16 LE target>`**: unconditional jump. Emitted at the end of a
  `Then` body when an `Else` exists, to skip over the `Else` body.
- **`EC 35`** (no operand): a fixed marker that appears once, immediately
  after the last statement of an `If`/`Else` construct, right before the
  real procedure epilogue. Structurally it's the same family as `FE 35`
  (suffix `35` = unconditional/structural jump family, vs `34` =
  conditional) but takes no operand — best guess is a compiler-emitted
  placeholder for the `End If` line itself (VB3 tracks per-line info for
  the IDE's step debugger), not yet confirmed against a non-`If` construct.

Not yet tested: `ElseIf`, loops (`For`/`Do`/`While`), and whether `EC 35`
appears in other block-closing contexts (e.g. loop ends) or is `If`-specific.

### `For`/`Next`: partially decoded

`For`/`Next` does **not** reuse the comparison (`44`)/branch (`34`/`35`)
opcodes — it's a separate family. Tested `For i = 1 To 3` / `Next i`, a
`Step 2` variant, and a 2-statement-body variant to triangulate:

- `0F 32 <u16 LE slot>` appears identically on both the `For` line and the
  `Next` line, referencing the loop control variable's slot.
- Start/end are pushed as normal literals (small-int table + `B0 0E`
  coerce, same as everywhere else). An explicit `Step` value is pushed the
  same way, right after start/end; without an explicit `Step`, that push
  is skipped and the following "commit bounds" opcode's first byte changes
  (`37 1B` with implicit step-1 vs `3E 1B` with an explicit step on the
  stack) — so `X 1B` = "commit FOR bounds," first byte flags whether step
  came from the stack.
- **Back-edge jump (end of the `Next` line), CONFIRMED**: a 2-byte opcode
  + `<u16 LE target>` whose target reliably equals the exact byte offset
  of the loop body's first statement — verified in all 3 variants
  (targets 20/24/20 correctly tracking body position as body length and
  step changed).
- **Entry-test / early-skip branch (right after bounds are committed), NOT
  fully resolved**: present in all 3 variants, but its operand does not
  land on the post-loop statement marker the way `If`'s branch does —
  it's consistently 2 bytes before that marker (i.e. it points at the
  back-edge jump's own operand field, not a real instruction boundary).
  Exact semantics unclear; may reference a loop-control descriptor rather
  than being a plain code-segment jump.
- The 2-byte "opcode" preceding each of these operands was `B8 FF` in the
  two 1-statement-body tests but `A8 FF` in the 2-statement-body test —
  **not stable**, so it's probably not a fixed mnemonic; more likely these
  bytes encode something numeric (not yet determined) rather than a
  literal opcode. Don't treat `B8 FF`/`A8 FF` as confirmed opcode values.

Next step if resumed: vary loop-body length more granularly (byte-by-byte)
to see if the "opcode" bytes are actually a relative offset/count, and
check `Do`/`While` for a simpler (non-FOR-specific) loop encoding to
cross-reference against.

## `Call`ing another `Sub` — DECODED

`Call DoThing` (a no-argument `Sub` in the same module) compiles to
`E0 62 <u16 LE reserved=0000> <u16 LE segment-3 offset>` — a 6-byte
instruction. **The second u16 field is the exact byte offset, within
segment 3 (the module's procedure-descriptor table — see below and
`RESOURCE_FORMAT.md`), where the callee's descriptor entry begins.**

Confirmed by compiling 1, 2, then 3 extra `Sub`s and checking each new
`Call`'s operand against segment 3's size *before* that `Sub`'s descriptor
was appended:

| callee added | operand's high `u16` | segment-3 size before this `Sub` |
|---|---|---|
| `DoThing` (1st) | `0x0100` = 256 | 256 (no-extra-`Sub` baseline) |
| `DoOther` (2nd) | `0x0138` = 312 | 312 (with only `DoThing` added) |
| `DoThird` (3rd) | `0x0170` = 368 | 368 (with `DoThing`+`DoOther` added) |

Exact match in all 3 cases. Also confirmed: calling the same `Sub` twice
from one caller produces the identical operand both times (stable
reference, not a call-site value).

**Each extra `Sub` grows segment 3 by exactly 56 (`0x38`) bytes** — a
fixed-size descriptor record appended per procedure, laid out in
declaration order (not physical code-layout order — see the segment note
above).

### The 56-byte descriptor record: code location is DECODED

Found by compiling the same `Call DoThing` program three different ways
(alone, with a second callee physically laid out *before* it in segment 4,
and with a longer 2-statement body) and diffing the 56-byte record:

- **Bytes 24-25 (`u16` LE) = the callee's code START offset within
  segment 4.** Confirmed exactly: `0` when `DoThing` is laid out first in
  its file, `18` (`0x12`) when a differently-ordered callee pushes it to
  start at byte 18 instead — matches the real code position in both cases.
- **Bytes 36-37 (`u16` LE) = the callee's code END offset within segment
  4** (i.e. start + length, not length alone — this was the source of an
  early false start: it looked like "code length" only because the first
  test case happened to start at offset 0). Confirmed exactly against 3
  independent layouts, including a body-length change (18 → 28 bytes)
  that shifted this field by the same 10 bytes.
- So: **`Call`'s operand → segment-3 offset of a 56-byte record → bytes
  [24:26) and [36:38) of that record give the exact `[start, end)` byte
  range of the callee's p-code within segment 4.** This is enough to
  fully resolve a `Call` to real, disassemblable code.
- Bytes 28-29 = `start + 12` in every sample (a secondary pointer into the
  body, e.g. skipping some fixed prologue/argument-setup region — not
  investigated further).
- Bytes 0-3 (`26 00 00 00`), 8-21 mostly, and the `ff ff`/`fe 0f` runs
  near the end were constant across every test — likely fixed record-type
  tags / unused argument slots (all tests used no-argument `Sub`s), not
  decoded.
- Bytes 4-7, 18-19, 26-27, and 38-39 varied in ways that track *other*
  descriptors' positions (cross-referencing neighboring records, plausibly
  a linked-list-style chain) rather than this procedure's own code — not
  fully mapped out, but not needed for the code-location result above.

### Multi-segment projects: the start/end recipe alone is AMBIGUOUS (resolved below)

Tested by adding a standard code module (`Module1.bas`, which VB3 always
compiles into its own segment — confirmed: a 1-form-1-module project has 5
segments, not 4, with the module's code landing in the *lower*-numbered
segment and the form's code pushed one higher) and calling both a
module-level `Sub` and a form-level `Sub` from `Form_Load`:

- **Both callees' 56-byte descriptors show identical `start=0, end=18`**
  — genuinely indistinguishable by code-offset alone, despite living in
  two different physical segments (module=segment 4, form=segment 5).
  **The start/end recipe above is necessary but not sufficient**: applying
  it naively on a multi-segment project (like `qrace.exe`, which has 16+
  forms) will misattribute code.
- Found a **second record type**, tagged `16 00 00 00` (vs. the Sub
  descriptor's `26 00 00 00`), one per module/form — plausibly a
  "container" record. Each Sub descriptor's bytes 26-27 (previously
  guessed as a "prev descriptor" pointer) turned out inconsistent with
  that theory once a module was involved: sometimes it points to *another
  Sub descriptor* (a next-in-chain link), sometimes to a `16`-tagged
  container record directly — looks like a singly-linked chain that
  eventually terminates at the owning container, which is what would need
  to be resolved to get a real segment number.
- Checked whether the resource table (the "project directory" resource,
  `RT_RCDATA` id 1, documented in `RESOURCE_FORMAT.md`) lists the module
  for cross-referencing — **it doesn't**: only `Form1` appears anywhere in
  the resource table of a 1-form-1-module test project; standard modules
  get no resource entry at all (makes sense, they have no visual layout).
  So container→segment resolution must be self-contained within segment 3
  (or hardcoded by a hopefully-fixed compiler convention like "modules
  always occupy the lowest-numbered non-reserved CODE segments"), not
  resource-assisted.
- `RT_RCDATA` id 2 (unidentified both here and in `qrace.exe` — see
  `RESOURCE_FORMAT.md`) is worth a closer look as a candidate for holding
  this exact information, given both files independently have an
  unexplained id-2 resource of similar shape.

The exact container→segment-number mapping wasn't apparent from this data
alone — see "CRACKED" below, where a proper sweep tool nailed it down.

**Deeper dig, and an honest retraction**: pushed further with a
2-modules-1-form project (3 code segments) to try to crack the
container→segment mapping directly:

- Confirmed each `.bas` module gets its own segment too (not shared with
  other modules) — segment order was module-1, module-2, then the form,
  which does *not* match `.mak` declaration order (the `.mak` listed the
  form first). Untested whether this ordering is a fixed rule or
  incidental to this test.
- Attempted a cross-*form* call (`Form2.SomeSub`, the more realistic case
  for `qrace.exe`, which has 16 forms and no `.bas` modules per
  `RESOURCE_FORMAT.md`) — hit real VB3 syntax friction (`Call Form2.Sub`,
  `Call Form2.Sub()`, and `Form2.Sub()` all failed to compile with
  different parser/semantic errors). Not a research dead end, just VB3
  call-syntax trivia not yet worked out; set aside since the underlying
  segment-resolution mechanism should be identical either way.
- Followed each `Sub` descriptor's byte 26-27 link and found it forms a
  genuine chain across *all* procedures in the project (module 1's `Sub`
  → module 2's `Sub` → a `16`-tagged record whose own start/end fields
  matched `Form_Load`'s own code region exactly) — so the chain is
  real and walkable, terminating at something that looks like a
  per-segment "default procedure" anchor.
- **Retraction**: the `16`-tagged "container" fields I labeled "MODULE
  container?" / "FORM container?" earlier in this section were matched
  by *field position only*, without confirming the two records actually
  share a layout — they don't. The container reached by the module chain
  above has clean, plausible start/end values; an earlier one (originally
  written up as the "module container") has a `+36` value (2167) far too
  large to be a code-end offset, meaning that earlier read was pattern-
  matching noise, not a real field. Struck through in spirit here rather
  than left uncorrected in place.

**Assessment**: this stopped being simple opcode decoding and needed real
tooling — built `tools/vb3ide/sweep_project_structure.py`, which generates
whole multi-file VB3 projects (not just Form1 code snippets) and dumps
every segment-3 record automatically. Ran it across 1/2/3-module projects
and got a clean, decisive answer.

### CRACKED: segment resolution is positional (chain-walk), not a stored field

**There is no field anywhere that stores a literal segment number.**
Instead: a fixed project-root record (`16`-tagged container at segment-3
offset 72, present and structurally identical in every test) has, at byte
offset 30 within its own 56-byte record, a pointer to the *first* module's
`16`-tagged container. Each module's container has that *same* field
(offset 30) pointing to the *next* module's container, terminated by
`0xFFFF`. **Walking this chain and counting hops gives the code segment
number directly: the Nth container reached (0-indexed) is segment `4+N`.**
The form (always compiled after all modules) is segment `4 + module_count`
— i.e. one past the end of the module chain.

Verified programmatically (not by eye) across 1, 2, and 3-module test
projects — the algorithm's predicted segment number matched the real NE
segment table exactly in all 3 cases:

```
1 module: root -> container@256 (segment 4) -> end        => form = segment 5  ✓
2 modules: root -> @256 (seg 4) -> @376 (seg 5) -> end     => form = segment 6  ✓
3 modules: root -> @256 (seg 4) -> @376 (5) -> @496 (6) -> end => form = seg 7  ✓
```

This also explains why earlier per-record fields looked so inconsistent
under a "does this field encode segment N" search: **module 1's own
container record is byte-for-byte identical whether it's compiled alongside
1, 2, or 3 other modules** (confirmed directly) — its shape only depends on
whether it *has* a next-module pointer, never on its absolute position.
There was never a "segment number field" to find.

**Combined with the earlier `Call` decode**: resolving any `Call` target
now requires (1) segment-3 offset from the `Call` operand → the `26`-tagged
`Sub` descriptor, (2) that descriptor's start/end fields for the code
range, and (3) — for multi-segment projects — walking the module-container
chain from the fixed root at segment-3 offset 72 to count which physical
segment (4, 5, 6, ...) owns that code range. Step 3 is a fixed, mechanical
algorithm now, not a guess.

### Forms: NOT fully cracked — each new test revealed a new wrinkle, not a converging answer

Since `qrace.exe` has zero standard modules (all 16 units are forms, per
`RESOURCE_FORMAT.md`), the module-chain result above isn't directly usable
on it — pushed further with form-only projects. This did **not** converge
to a clean rule the way the module case did; documenting the honest state
rather than forcing a false conclusion:

- **1 module + 2 extra forms**: the module-chain (offset-30 "next")
  continued seamlessly from the module's container straight into the
  extra forms' containers, in file order — *skipping* the startup form
  (`Form1`) entirely, even though `Form1` had real code in its own segment.
  `Form1`'s segment position had to be inferred as "the implicit gap right
  after the module count," not found via any chain.
- **0 modules, empty startup form, 3 extra forms**: chain ran cleanly
  through all 3 extra forms' containers with no gap — consistent with
  "`Form1` is skipped," since here it had no code to skip *over* (it
  contributed no segment at all).
- **0 modules, non-empty startup form (real code), 2 extra forms**: this
  should have been the decisive test, but it exposed a **new, different**
  record pattern: three `26`-tagged (`Sub`-shaped) records appeared —
  including one for `Form_Load` itself, which no earlier test ever showed
  as `Sub`-shaped — chained to *each other* via the byte-26 field (in
  file/declaration order: `Form_Load` → `S2` → `S3`), not via the
  `16`-tagged container chain used in the module tests. Only 2
  `16`-tagged containers existed for 3 code-bearing forms, not 3.
  Whether this is the *same* underlying mechanism as the module chain
  (with `Form_Load` newly participating because it's the *only* procedure
  in `Form1` here) or a genuinely different code path wasn't resolved.
- First attempt at this last test also had a methodology bug (every test
  `Sub` used an identical `x = 1` body, making segments indistinguishable
  by content) — caught and fixed by giving each `Sub` a distinct literal,
  but the corrected run is what produced the new-pattern finding above,
  not a confirmation of the module-chain hypothesis.

**Honest conclusion**: the segment-3 procedure table has more internal
structure/variation than 5-6 hand-picked test cases converge on. Continuing
to add one-off project-shape variants was producing a new open question
each time rather than narrowing toward an answer — a sign to stop and
report accurately rather than keep guessing. The module-chain algorithm
above is solid and directly useful (verified 3-for-3, programmatically).
The general form-resolution case is **not solved**. A real fix would need
either substantially more systematic sampling via
`sweep_project_structure.py` (dozens of shapes, not 5), or a different
strategy entirely for `qrace.exe` specifically: since code *content*
reliably and uniquely identifies which segment it belongs to once you can
see it (confirmed across every test here), a practical fallback for
resolving `Call` targets in `qrace.exe` is to cross-reference a `Call`'s
segment-3 descriptor (start/end + surrounding string/opcode landmarks)
against each candidate code segment's actual content by hand or
semi-automated matching, rather than relying on a fully general
algorithmic resolution that isn't proven yet.

## Reproducing this / extending it further

- `tools/vb3ide/kwaj_extract.py <disk-files-dir> <out-dir>` — decompress a
  VB3 setup disk's files.
- `tools/vb3ide/compile_snippet.py --sweep <values...>` — compiles `x =
  N` for each integer literal and saves the EXE.
- `tools/vb3ide/compile_snippet.py --name <n> --code-file <path>` —
  compiles an arbitrary `Form_Load` body from a text file.
- `tools/segment_parser.py <exe>` — parses the NE segment table, dumps
  each segment, and scans for the inline string pattern above.

The general method: change exactly one thing about a minimal test
program, compile, diff the resulting segment against the previous
variant's — same technique used for every finding above. See `README.md`
for full environment setup (Xvfb, window manager, Wine prefix).

## Open questions / next steps

- Decode the floating-point arithmetic family in the same way
  arithmetic-on-integers was decoded (`Print`'s call sequence is now
  decoded, see above).
- Extend control flow to `ElseIf` and loops (`For`/`Do`/`While`) — only
  `If`/`Then`/`Else` is decoded so far; `For`/`Next` structure is sketched
  but not confirmed (see above).
- `Call`'s opcode shape and single-segment resolution are fully decoded.
  Multi-segment resolution is solved for **module** chains (verified
  3-for-3, programmatically) but **not solved for forms** — several form
  project-shapes were tested and each revealed a new, seemingly
  inconsistent pattern rather than confirming the module algorithm
  generalizes. This directly matters for `qrace.exe` (16 forms, 0
  modules) and is the top priority next step — likely needs a much larger
  systematic sweep (`tools/vb3ide/sweep_project_structure.py` exists and
  works; it needs dozens of samples, not the ~6 tried this session) or a
  different strategy — see "Forms: NOT fully cracked" above for the
  practical fallback in the meantime.
- `Function` calls (with a return value) and built-in function calls not
  attempted yet.
- Once enough of the opcode set is decoded, it should generalize
  directly to any VB3 p-code binary, not just small test programs —
  [Quibble Race](https://github.com/vmartinv/qrace) is being used as the
  real-world target this was built for.
