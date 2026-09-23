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
- Each `Sub`/`Function`/event procedure with at least one statement gets
  its own small CODE segment holding just that procedure's compiled
  instructions. An **empty** procedure (e.g. an unused `Form_Load`)
  contributes no segment at all.
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
variable referenced in a procedure (by any name) gets slot `0x001A`; each
subsequently-referenced distinct variable gets the next slot, **+4** from
the last (`0x001E`, `0x0022`, ...) — assigned in first-*use* order during
compilation, not textual declaration order. Confirmed with up to 3
variables in one procedure.

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
`<u16 length><ASCII>` pattern (`02 00 68 69`) in the output. `Print`'s own
call sequence (`15 4A 37 21 9A 38 08 00 0C 00` before the string, `00 00
32 61` after it) is not yet decoded.

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

- Decode `Print`'s call sequence and the floating-point arithmetic family
  in the same way arithmetic-on-integers was decoded.
- Extend control flow to `ElseIf` and loops (`For`/`Do`/`While`) — only
  `If`/`Then`/`Else` is decoded so far.
- Calling other procedures/built-in functions — not attempted yet.
- Once enough of the opcode set is decoded, it should generalize
  directly to any VB3 p-code binary, not just small test programs —
  [Quibble Race](https://github.com/vmartinv/qrace) is being used as the
  real-world target this was built for.
