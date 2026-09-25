# Plan: complete the VB3 decompiler

## Status
Phases 1 (feature batteries) and 2 (uncovered features) are done: every
VB3 language feature has a battery, and every case round-trips to
identical p-code and form resources. What's left is whole-exe byte
identity (Phase 3) and a final usage pass (Phase 4).

Current numbers: batteries 949/969 exe-identical (all p-code identical);
samples 16/22 exe-identical (all 483/483 procedures p-code identical).

"Complete" (Phase 3's target): with names padded to their original
lengths, the rebuilt exe is byte-identical to the original.

## Remaining items
VB.EXE is a deterministic compiler: every original exe was produced by
*some* source text, so a byte-exact reconstruction is possible in
principle for all of these — none is a dead end, they're just unsolved.
Where a field's value can't be read off directly, the sums/counts we
can read (name-table sizes, line counts, etc.) are constraints on the
source, not the answer by themselves; whatever finds the right source
detail (derivation, search, cross-checking against another field,
recompiling and comparing, or something else) is fair game, and worth
picking per item based on how many unknowns and constraints it has.

1. **Global variable/constant name lengths.** Only sums are observable
   (each module's +30 counts the Globals it declares or references, the
   project +30 the global table). `fit_globals` fits only the declaring
   .bas; `fit_global_inits` pads for the global list's bucket order but
   reverts when that breaks a module's +30 (it keeps only the total).
   Needed: a solver over all modules' +30 plus the project +30 and the
   bucket order (`initlists` Globals cases, `modlevel` 003/005,
   `objects` 009, `vbx` 007).
2. **Static locals**: scalar Statics are recovered from procedure record
   +18 (module variable table, OPCODES.md; `fit_entries`) when trailing
   module Dims make the module part too big. Left: `Static Sub`/`Static
   Function` and Static locals mixed with other failures (names 023,
   statements 063/064), and modules where the +18 model is still off by
   2 (item boundary rounding) or too small.
3. **declares**: Aliases are recovered (from pool lengths); left:
   `+30` sums with Declare parameters (`ByRef String`, `param types`,
   `user type param`) and `many declares` (decl +140..).
4. **Init-list order** (see OPCODES.md "Init lists"): fitted for module
   lists, Static arrays and the global list (`fit_inits`,
   `fit_global_inits`, `order_pads`); fixed `timecard` and `biblio`.
   `initlists` Globals cases still differ in module/project +30 (global
   name lengths vs. the modules that reference them: item 1).
5. **Phase 4 final pass**: once the above settle, run every battery
   plus every sample in `--exe` mode and commit. (`tools/vb3decompile.py`
   itself is done; icons/.frx already come for free from
   `decompile.py`'s generic binary-property handling.)

## Critical files
- `tools/decompile.py` (declarations, naming, records)
- `tools/lift.py` (statements)
- `tools/opcodes.py` (NAMES/SEM)
- `tools/formblob.py` (controls/menus)
- `tools/exediff.py`, `tools/battery.py --exe` (exe-identity diffing)
- README.md, OPCODES.md

## Verification
- Per change, run the affected battery in isolation first
  (`DISPLAY=:99 python3 tools/battery.py <name> --exe -k "<case>"`), then
  the *whole* battery (`--chunk 48`, no `-k`): several remaining bugs
  only show up once a module shares a project/global-image with others,
  and a fix validated only in isolation can still regress the bundle.
- Before committing any fix: full `tools/battery.py --exe --chunk 48`
  (all batteries) and full `tools/roundtrip.py` (all samples), looking
  for CODE/CRASH/DECOFAIL, not just EXE byte counts. Zero p-code
  regressions is non-negotiable — revert rather than ship a net exe-byte
  win that costs even one p-code mismatch.
