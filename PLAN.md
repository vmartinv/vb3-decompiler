# Plan: complete the VB3 decompiler

## Status
Phases 1 (feature batteries) and 2 (uncovered features) are done: every
VB3 language feature has a battery, and every case round-trips to
identical p-code and form resources. What's left is whole-exe byte
identity (Phase 3) and a final usage pass (Phase 4).

Current numbers: batteries 856/941 exe-identical (all p-code identical);
samples 14/22 exe-identical (all 483/483 procedures p-code identical).

"Complete" (Phase 3's target): with names padded to their original
lengths, the rebuilt exe is byte-identical to the original.

## Remaining items

1. **Global variable/constant name lengths.** Only the *sums* are
   observable from the exe (module decl+30, project record decl+30/+34,
   Type-pointer prefix sums over the global name table); the individual
   name lengths are a guess. Root cause of most remaining single-case
   failures across `deftype`, `types`, `names`, `objects`, `statements`,
   and of `modlevel` (0/16: short synthetic Global names shrink the
   shared name pool, shifting every later module's data by a few bytes,
   project-wide — one root cause, many symptoms).
2. **types: Static locals (rec+18, decl+50).** Confirmed unrecoverable:
   a scalar `Static x As T` local and an equivalent single-procedure
   module `Dim` compile to byte-identical p-code, with no data-level
   marker distinguishing them (unlike Static arrays, which carry an
   explicit 0xC1/0xC2 flag — see CLAUDE.md). Two fix attempts (a
   single-owning-procedure heuristic, and an exact decl+50-arithmetic
   version) both reverted after net regressions, including a p-code
   regression from the second. Not worth revisiting without a new
   signal.
3. **declares: Alias/ordinal names (decl+0/+64), parameter types.**
   Mostly the same name-length issue (item 1). A Declare parameter of a
   user Type resolving to `As Any` is not itself a bug — it already
   compiles and matches p-code (`calldlls` sample: 18/18 procs). A fix
   resolving it to the real Type name was reverted: it regressed
   `calldlls` by shifting the global image / record allocation order
   relative to a Global in the same module.
4. **timecard**: two words of a module list swapped (declaration
   order) — 2 bytes, `rc2.Card` region. Not yet root-caused.
5. **biblio**: data image diff, 3121 bytes — the largest remaining.
   Not yet broken down by place.
6. **Phase 4 final pass**: once the above settle, run every battery
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
