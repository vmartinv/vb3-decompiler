# vb3-decompiler

Tools and findings for reverse-engineering Visual Basic 3.0 (1993)
executables — both the NE resource format (forms, controls, pictures) and
the p-code bytecode format (`VBRUN300.DLL`-interpreted, not native x86).

No other decompiler targets VB3 (existing tools cover VB5/6, a different
p-code). Findings: [`OPCODES.md`](OPCODES.md) (p-code) and
[`RESOURCE_FORMAT.md`](RESOURCE_FORMAT.md) (forms/resources). Grew out of
[Quibble Race](https://github.com/vmartinv/qrace).

## What's here

```
src/                     the decompiler (exe -> .mak/.frm/.bas/.frx)
  vb3decompile.py        command line
  decompiler.py          Decompiler: runs the passes below; write_project
  layout.py              module list and data-image layout
  analyze.py             per-module p-code analysis: variables, calls
  declarations.py        module declarations: Globals, Dims, Consts, Types, Declares
  naming.py              names (procedure sort order, object-local free order)
  localvars.py           local declarations, unused locals/parameters
  emit.py                source text: headers, procedures, statements
  lift.py                p-code statements -> BASIC expressions/statements
  model.py               shared types and helpers
  forms.py               form/control resources -> .frm text (+ .frx)
  dataimage.py           global/module data images (Types, globals)
  nametable.py           model of the IDE's per-module name table
  ne.py                  NE segments, relocations, resources, procedure table
  runtime.py             VBRUN300.DLL interpreter model, p-code decoder
  symbols.py             control/form/procedure/object names
  opcodes.py             handler names
tools/                   everything else (analysis, testing, the IDE)
  verify.py              decompile + rebuild with VB.EXE + compare p-code/forms
  battery.py             feature batteries (batteries/*.py): generated cases, round-tripped
  roundtrip.py           recompiles the decompiled samples in the IDE, compares
  opprobe.py             opcode discovery probes (probes/*.py)
  pcode_disasm.py        p-code disassembler (needs your VBRUN300.DLL + capstone)
  pcode_diff.py          instruction-level diff of two builds
  exediff.py             whole-exe diff by structure (record/segment/resource)
  formdump.py            prints decoded form layouts
  lift_score.py          scores the lifter against the aligned corpus
  align_source.py        aligns a compiled project with its source, per statement
  corpus.py              builds/queries the aligned corpus (names handlers)
  validate.py            scores recovered names against sample source
  ne_parser.py           NE header + resource table dump
  extract_bitmaps.py     pulls embedded BMPs out of raw RCDATA dumps
  segment_parser.py      NE segment table parser, p-code string scanner
  vb3ide/
    kwaj_extract.py      decompresses VB3 setup-disk files (libmspack via ctypes)
    restore_install.py   rebuilds the install tree (incl. sample projects)
    compile_snippet.py   drives the real VB3 IDE to compile test programs
    compile_project.py   compiles .mak projects with `VB.EXE /MAKE`
```

All Python, standard library only except `capstone` (p-code decoding) and
`vb3ide/kwaj_extract.py` (needs `libmspack`, see Setup). No build step.

```sh
# reconstruct a project from a compiled exe
python3 src/vb3decompile.py some.exe out/

# ... and check it by rebuilding with a real VB3 IDE (needs the Setup below):
# same p-code and form resources as some.exe
python3 tools/verify.py some.exe out/

python3 tools/pcode_disasm.py some.exe --runtime VBRUN300.DLL --check
python3 tools/pcode_disasm.py some.exe --runtime VBRUN300.DLL --out listing.lst
```

## Setup

You need your own legally-obtained copy of Visual Basic 3.0 to use
`tools/vb3ide/` (empirical opcode research) — not included here, and not
covered by this repo's license (see `LICENSE`). The resource-extraction
tools (`ne_parser.py`, `extract_bitmaps.py`, `segment_parser.py`) only need a VB3-compiled `.exe` to analyze, not VB3
itself.

### Getting the VB3 IDE running (for `vb3ide/`)

No installer run needed. From your VB3 setup disks:

```sh
# 1. Merge all disk files into one directory (adjust to however your
#    disks are provided -- floppy images, ISO, etc.)
mkdir merged && cp /path/to/disk*/* merged/

# 2. Decompress the KWAJ-compressed files (most of them)
python3 tools/vb3ide/kwaj_extract.py merged/ expanded/

# 3. VB.EX_ decompresses to a valid win16 NE executable -- the IDE itself.
mkdir -p work/ide
cp expanded/VB.EX work/ide/VB.EXE
# VBRUN300.DLL is needed alongside it -- get it from any VB3-compiled
# program's install, or from the VB3 disks (VBRUN300.DL_).
cp <somewhere>/VBRUN300.DLL work/ide/
```

Also copy the custom controls (`*.VBX`, from `windows/system/` after
`restore_install.py`) next to `VB.EXE`: the IDE doesn't find them in the
Wine prefix's system directory.

Run it under Wine (its built-in win16 shim handles this transparently, no
`WINEARCH=win32` or extra packages needed on a modern Wine build):

```sh
WINEPREFIX=$PWD/work/.wineprefix wine work/ide/VB.EXE
```

Don't use the disks' `SETUP.EXE`; it fails under Wine and isn't needed.

### Automating it (`compile_snippet.py`)

`tools/vb3ide/compile_snippet.py` drives the IDE via `xdotool` to compile
test programs unattended — needed for the empirical opcode sweeps in
`OPCODES.md`. It needs:

- A headless X display with a window manager (bare Xvfb has no window
  manager, and Wine windows never receive input focus without one):
  ```sh
  Xvfb :99 -screen 0 1024x768x24 &
  DISPLAY=:99 openbox --sm-disable &
  ```
- A project saved once as **text** from the IDE (File > Save Project As,
  check "Save as Text") at `work/proj/Project1.mak` /
  `work/proj/Form1.frm`, with an empty `Sub Form_Load () / End Sub` block
  — the script rewrites `Form1.frm`'s body on each run and relaunches
  `VB.EXE` straight into the saved project.

Then:

```sh
export DISPLAY=:99
export WINEPREFIX=$PWD/work/.wineprefix
python3 tools/vb3ide/compile_snippet.py --sweep 0 1 2 3 10 100 32767
python3 tools/vb3ide/compile_snippet.py --name my_test --code-file snippets/my_test.bas
```

Output lands in `work/sweep/`. Disassemble results with
`tools/pcode_disasm.py`.

### Sample-project corpus

```sh
python3 tools/vb3ide/restore_install.py merged/PACKING.LST expanded/ work/root
python3 tools/vb3ide/compile_project.py --all work/root/vb/samples
python3 tools/corpus.py build work/root/vb/samples work/corpus --runtime VBRUN300.DLL
python3 tools/corpus.py examples work/corpus --runtime VBRUN300.DLL --exe some.exe
```

## Status

The decompiled source recompiles to identical p-code and form resources on
all 22 sample projects (483 procedures) and on all 969 generated feature
cases (`battery.py`, 20 batteries). Byte-identical executables are not a
goal: they depend on the original identifier lengths, which aren't stored
(`exediff.py` still shows where two builds differ).

Plan: see [PLAN.md](PLAN.md).

## License

MIT for this project's own code and documentation — see `LICENSE`. Does
not cover VB3 itself or anything you analyze with these tools.
