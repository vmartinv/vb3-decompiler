# vb3-decompiler

Reusable VB3 reverse-engineering tools + findings: a decompiler for
Visual Basic 3.0 executables. Public at
https://github.com/vmartinv/vb3-decompiler. Companion to `~/qrace`
(Quibble Race port).

## Rules

- `.md` files hold **current findings and next steps only**, kept
  succinct. When something is superseded, rewrite it in place. Don't keep
  retractions, dead ends or history (git has that).
- Commit after each validated milestone.
- Never commit VB3, `VBRUN300.DLL`, analyzed executables or anything
  extracted from them (see `.gitignore`). Findings about them (offsets,
  opcode meanings) are fine.
- Tokens are expensive, wall-clock time is not. Prefer controlled
  experiments (batch many cases into one generated project, compile, read
  the result) over long manual analysis; runs may take as long as needed.
  Keep tool output small (filter/summarize before printing).
- Tests run under pytest: `pytest` runs the unit tests (seconds, no IDE),
  `DISPLAY=:99 pytest -m ide` every battery case and sample through the
  IDE (`-k <battery>` / `-k <sample>` to narrow). Add unit tests
  (`tests/test_*.py`) for pure logic such as naming or table models.
- Validate with **feature batteries** (`tests/batteries/<feature>.py`, run by
  `tests/battery.py`): one battery per language feature, sweeping its
  whole range, round-tripped case by case. The VB3 samples are only a
  sanity check (`tests/roundtrip.py`): they cover a narrow slice of the
  language. `battery.py --check` validates a new battery's source first.
- Name unknown opcodes with **probes** (`tests/probes/<name>.py`, run by
  `tests/opprobe.py`): one statement per line, unknown ops reported per
  statement; `--sem` proposes builtin entries.
- Builds use `VB.EXE /MAKE` (~2 s, no GUI clicks). Its output differs
  from a GUI "Make EXE" build in one word of RT_RCDATA 1, so compare
  /MAKE builds only with /MAKE builds. Build from short directory paths
  (under `work/`): from a long one (~90 characters) the IDE compiles some
  form properties differently.
- Target: the decompiled source recompiles to the **same p-code and form
  resources** (PLAN.md). Don't add fitting for exe-only fields (name-table
  sizes, init-list/hash orders, pool offsets, line counts): exe byte
  identity is not a goal. The one name-length effect that reaches p-code
  (object locals' free order) is handled in `src/vb3decompiler/naming.py`.
- Code layout: the decompiler is the `vb3decompiler` package in
  `src/vb3decompiler/` (only the decompiler; `pyproject.toml`, `pip install .`
  gives the `vb3decompile` command);
  the regression tests in `tests/` (batteries, probes and their runners);
  everything else (analysis, the IDE driver) in `tools/`. Scripts add
  `src/` (and `tools/`) to `sys.path` and import `vb3decompiler.*`, so
  they run without installing; tests/ and tools/ module names must not
  repeat (the script's own directory wins).
- Before changing what the decompiler emits for some construct, get
  ground truth: hand-write two minimal `.frm`s that differ only in that
  construct and compile them directly with
  `tools/vb3ide/compile_project.py`, then compare their p-code.
- A fix must be validated against the *whole* battery (`battery.py` with
  no `-k`) and the full sample roundtrip, not just the isolated case:
  several modules share one project's global image, and a change that's
  correct alone can still cascade into an unrelated module's p-code once
  bundled. Zero p-code regressions (CODE/FORM/CRASH/DECOFAIL) is
  non-negotiable. For a pure refactor, also check that the decompiled
  text of every battery/sample exe is unchanged.
- For a *sample* with known original source, localize a p-code mismatch
  by splicing hybrid `.frm`/`.bas` files (original text with one aspect
  swapped in from the reconstruction), recompiling each and diffing the
  p-code (`tools/pcode_diff.py`). Get the true slot/record <-> name
  correspondence from `tools/align_source.py`, not from file position.
- **VB.EXE's and VBRUN300's own logic is readable**, and often the faster
  path once black-box probing stalls on an ordering/emission rule:
  decompile the relevant segment with Ghidra instead of guessing further
  from input/output pairs. Ghidra is installed (`pacman -S ghidra`,
  official `extra` repo — `pacman` is aliased to `yay` here, so AUR
  works too if something's not in `extra`). It decompiles a raw NE
  segment cleanly once imported as `x86:LE:16:Real Mode` with the plain
  binary loader at base 0 (segment bytes are already one contiguous
  blob once pulled via `ne.parse_ne(...)[n].data`, src/vb3decompiler/ne.py) — far more
  legible than manually walking capstone output, which is fine for a
  short handler body (as `src/vb3decompiler/runtime.py` already does for
  VBRUN300.DLL) but not for tracing control flow through a real
  compiler. Headless recipe:
  `analyzeHeadless <proj-dir> <name> -import <segment.bin> -processor
  "x86:LE:16:Real Mode" -loader BinaryLoader -loader-baseAddr 0`
  to import + auto-analyze once, then re-run against the saved project
  with a script to decompile specific addresses:
  `analyzeHeadless <proj-dir> <name> -process <segment.bin>
  -noanalysis -scriptPath <dir> -postScript <Script>.java` — write the
  script as a Java `GhidraScript` (a `.py` one needs PyGhidra, which
  isn't enabled by default here), using `DecompInterface` +
  `getFunctionContaining(addr)` + `getDecompiledFunction().getC()` per
  address of interest. Segment 53 alone (VB.EXE's compile-time symbol
  table code, ~35KB) took Ghidra ~25s to auto-analyze; whole-binary
  import wasn't tried. A far call with segment `0xFFFF` in the raw
  bytes is an unresolved NE relocation, not a real target — the plain
  `BinaryLoader` import here doesn't see the NE relocation table at all
  (it's outside the raw segment blob), so don't assume Ghidra resolved
  it either; check `Segment.relocs` from `ne.parse_ne(...)`
  for the real fixup if one of those matters.

## Key files

- `OPCODES.md`: p-code format; `ne.py`, `runtime.py`, `symbols.py` in
  `src/vb3decompiler/`
  implement it (`tools/pcode_disasm.py`: the disassembler command line).
- `RESOURCE_FORMAT.md`: NE resources / forms.

## Local data (`work/`, gitignored)

- `root/vb/samples/<name>/`: VB3 sample projects (`.mak`/`.frm`/`.bas`
  source + compiled `.exe`, e.g. `calc/calc.exe`, `mdi/mdinote.exe`).
  Sanity check only.
- `battery/<battery>/<project>/{orig,deco}`: battery builds;
  `probe/<probe>/`: probe builds.
- `tests/<name>/`: generated test projects (source + exe).
- `ide/`: `VB.EXE`, `VBRUN300.DLL`, VBX/DLLs.
- `.wineprefix/` (C: = `vdrive_mnt/`, loop-mounted `vdrive.img`),
  `proj/`, `sweep/`, `sweep_struct/`: used by `tools/vb3ide/` (defaults
  need no env vars; X display `:99`).
- `corpus/`: aligned sample corpus.

```sh
python3 tools/validate.py work/root/vb/samples \
    --runtime work/ide/VBRUN300.DLL --vbx-dir work/ide -v
python3 tools/lift_score.py score work/corpus --runtime work/ide/VBRUN300.DLL
```
