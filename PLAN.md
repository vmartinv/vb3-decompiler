# Plan: complete the VB3 decompiler

## Context
The decompiler already round-trips every sample: 483/483 procedures and every form are byte-identical (`/MAKE` build vs `/MAKE` build). It passes 940 of 941 generated battery cases (19 batteries).

"Complete" means three things:
1. Every VB3 language feature has a battery.
2. Every case round-trips to identical p-code and form resources.
3. With names padded to their original lengths, the rebuilt exe is identical to the original.

The work splits into four phases. Each phase ends with a sample regression run, doc updates and a commit. Standing rules apply throughout: no VB3 or extracted binaries in git, no qrace, small output, and background runs with wait loops.

The loop for every feature is the same:
1. Write a probe in `probes/`.
2. Run `tools/opprobe.py -a/--sem` to name unknown ops in `tools/opcodes.py`.
3. Add a lift handler in `tools/lift.py` and any declaration or naming changes in `tools/decompile.py`.
4. Write a battery in `batteries/`, validate its source with `battery.py --check`, then run the full round-trip.

## Phase 1: close the existing batteries

### names: done (35/35)

### statements: done (68/68)

### types: done (69/69)

### deftype: done (56/56)
The DefType letter table itself is still undecoded (declarations record +44 is only a "has DefType" flag); p-code is identical without it, so it moves to Phase 3.

**Exit:** all seven batteries at 100%, all samples still identical, commit.

## Phase 2: batteries for uncovered features
Done: one battery per item, each at 100%:
controls 36, ctlarrays 13, menus 12, forms 36, graphics 46, objects 22,
errors 14, declares 17, modlevel 16, vbx 16, ddeole 8, misc 23.

Not covered: GRAPH.VBX (two MODELs, array properties saved by the VBX),
ANIBUTON.VBX (MODEL not found by `parse_models`), CRYSTAL.VBX (crashes
the IDE under Wine), an OLE control holding an object (only the empty
object's one-byte `OleObjectBlob` is known).

**Exit:** each battery at 100%, samples still identical. Update the OPCODES.md counts and commit after each battery or pair of batteries.

## Phase 3: whole-exe identity
Tools: `tools/exediff.py` (differing bytes by place: record field, code
segment, resource region), `battery.py --exe`, `roundtrip.py` (reports
`exe N bytes` / `EXE IDENTICAL`; decompiles our own /MAKE build, since
the shipped exes carry the build machine's paths and project name).

Done: epilogue free order (name-length solver), name-table size (+30)
fitting, line counts (+50), Static arrays, unused locals typed from the
records, unused event-parameter slots, shared pool names, DefType line
order, Type/field name lengths, Static Sub, volatile rc1 words.
Samples: 14/22 identical (mcitest 6, textedit 2, objects 2, timecard 2,
recedit 9, mdinote 32, calldlls 59, biblio 3121 bytes). Batteries: all
p-code identical; exe-identical 846/941.

Remaining, by place (`exediff`, `battery.py <name> --exe`):
- Global variable/constant name lengths: only sums are observable
  (module +30, project record table offset 12 +30/+34, Type pointers as
  prefix sums of the global name table); distribution is a guess.
- decl+30 name sizes where no generated name is free to resize
  (deftype, types, names, objects, statements cases).
- modlevel (0/16): global-name sizes shift the global image of every
  later module (one root cause per project).
- types: Static locals (rec+18, decl+50), Type all fields / nested.
- declares: Alias/ordinal names (decl+0/+64), parameter types.
- timecard: two words of a module list swapped (declaration order).
- biblio: data image (3121 bytes).

## Phase 4: usage
- Add a single entry point: `vb3decompile <exe> <outdir>` in `tools/`. It writes the .mak, .frm and .bas files, extracts resources (icons, .frx), and has a `--verify` flag that rebuilds with `/MAKE` and compares.
- Rewrite the README usage section and the tools list; CLAUDE.md stays short.
- Final pass: run all batteries plus the samples in `--exe` mode, then commit.

## Critical files
- `tools/decompile.py` (declarations, naming, records)
- `tools/lift.py` (statements)
- `tools/opcodes.py` (NAMES/SEM)
- `tools/formblob.py` (controls/menus)
- `tools/battery.py` (`--exe` mode)
- `tools/opprobe.py`
- `batteries/*.py`
- `probes/*.py`
- README.md, OPCODES.md

## Verification
- Per change, run the affected battery: `DISPLAY=:99 python3 tools/battery.py <name> --chunk 48`, and `-k` for single cases.
- Per phase:
  - run all batteries: `tools/battery.py`;
  - run the samples roundtrip `/MAKE` vs `/MAKE`, which must stay at 483/483 procedures with forms identical;
  - from Phase 3 on, also the `--exe` identity check.
