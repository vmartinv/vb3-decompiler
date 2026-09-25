# Plan: complete the VB3 decompiler

## Context
The decompiler already round-trips every sample: 483/483 procedures and every form are byte-identical (`/MAKE` build vs `/MAKE` build). It passes 742 of 743 generated battery cases.

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

### types: 68/69
- object variables: epilogue free order depends on name-table offsets (Phase 3).

### deftype: done (56/56)
The DefType letter table itself is still undecoded (declarations record +44 is only a "has DefType" flag); p-code is identical without it, so it moves to Phase 3.

**Exit:** all seven batteries at 100%, all samples still identical, commit.

## Phase 2: batteries for uncovered features
Build one battery per item, each probed first:
1. **controls: done (36/36).** every standard control class, with every design-time property at a non-default value, and every event handler signature. This also exercises `formblob.py`.
2. **ctlarrays: done (13/13).** indexed controls, `Index` parameters in events, and `Load`/`Unload` of array elements.
3. **menus: done (12/12).** nested menus, shortcuts, checked/disabled/invisible items, separators, menu control arrays.
4. **forms:** MDI parent/child, startup form vs `Sub Main`, a .bas-only project, form properties (BorderStyle, icons, etc.).
5. **graphics:** Line with B/BF and Step, Circle with all its optional arguments, PSet, Point, Cls, Scale, PaintPicture-era methods, and property get/set on controls.
6. **objects:** Me, Screen, App, Clipboard, Printer and Debug, plus `Set`, `Nothing`, `Is`, `TypeOf … Is`, form and control variables, `New` form instances.
7. **errors:** On Error GoTo/Resume Next/GoTo 0, `Resume`/`Resume Next`/`Resume label`, `Err`/`Erl`/`Error$`.
8. **declares:** every parameter kind (ByVal, `As Any`, strings, arrays, Alias, Lib ordinal), Sub and Function forms.
9. **modlevel:** Global, Type, Const and every `Option` statement, across .bas and form modules.
10. **vbx:** the Pro VBX controls shipped with VB3 (grid, gauge, etc.) and their custom properties.
11. **ddeole:** Link* properties and methods, LinkExecute/Poke/Request, OLE2 control properties.
12. **misc:** DoEvents, Shell, Beep, Environ, Command, `Format$` masks, file I/O (Get/Put/Seek/EOF/LOF/Loc), string comparison (`Option Compare`).

**Exit:** each battery at 100%, samples still identical. Update the OPCODES.md counts and commit after each battery or pair of batteries.

## Phase 3: whole-exe identity
1. Add an exe comparison mode to `battery.py` (`--exe`) and to the samples roundtrip. It diffs the full NE image and reports the differing words by structure (table records, name tables, hash).
2. Decode the remaining name-dependent words:
   - record offsets 42/46/106/110, which are module name tables;
   - Type and field name storage;
   - hash-bucket order;
   - the record +4 offsets that still differ in `objects` and `recedit`.
   - unused Declares' parameter types (none recorded at +15) and Alias names (the VB-side name isn't at +46).
   - name-table offsets: epilogue free order of object/Type locals depends on them (bucket = (offset >> 1) & 7); the types case "object vars" needs it.
   - a module's last Long constant (`&HFFFF&`) is emitted as a Long variable (room cut short by `first_owned`); p-code identical, table not.
   - the DefType letter table (26 types per module), so `DefXxx` lines are recovered rather than inferred.
3. Pad generated names to their original lengths and keep the hash order, so a rebuild matches the original exe byte for byte. Target: every battery case and every sample identical in `--exe` mode.

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
