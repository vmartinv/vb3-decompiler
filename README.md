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
tools/
  ne_parser.py           NE header + resource table parser
  extract_bitmaps.py     pulls embedded BMPs out of raw RCDATA dumps
  parse_form_headers.py  decodes form captions/control names
  segment_parser.py      NE segment table parser, p-code string scanner
  pcode_disasm.py        full p-code disassembler (needs your VBRUN300.DLL
                         + `pip install capstone`)
  opcodes.py             handler names
  align_source.py        aligns a compiled project with its source, per statement
  corpus.py              builds/queries the aligned corpus (names handlers)
  validate.py            scores recovered names against sample source
  lift.py                lifts statements to BASIC; scores against the corpus
  decompile.py           rebuilds a project (.mak/.frm/.bas) from an exe
  vbdecl.py              declarations from the data images (Types, globals)
  roundtrip.py           recompiles decompiled samples in the IDE, compares p-code
  battery.py             feature batteries (batteries/*.py): generated cases, round-tripped
  opprobe.py             opcode discovery probes (probes/*.py)
  pcode_diff.py          instruction-level diff of two builds
  vb3ide/
    kwaj_extract.py      decompresses VB3 setup-disk files (libmspack via ctypes)
    restore_install.py   rebuilds the install tree (incl. sample projects)
    compile_snippet.py   drives the real VB3 IDE to compile test programs
    compile_project.py   compiles .mak projects with `VB.EXE /MAKE`
```

All Python, standard library only except `vb3ide/kwaj_extract.py` (needs
`libmspack`, see Setup) and `pcode_disasm.py` (needs `capstone`). No build
step.

```sh
python3 tools/pcode_disasm.py some.exe --runtime VBRUN300.DLL --check
python3 tools/pcode_disasm.py some.exe --runtime VBRUN300.DLL --out listing.lst
python3 tools/decompile.py some.exe --runtime VBRUN300.DLL --out src/
```

## Setup

You need your own legally-obtained copy of Visual Basic 3.0 to use
`tools/vb3ide/` (empirical opcode research) — not included here, and not
covered by this repo's license (see `LICENSE`). The resource-extraction
tools (`ne_parser.py`, `extract_bitmaps.py`, `parse_form_headers.py`,
`segment_parser.py`) only need a VB3-compiled `.exe` to analyze, not VB3
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

- Resources: form layouts decoded to `.frm` text (`formblob.py`);
  recompiled form resources are byte-identical on all samples.
- P-code: decompiles to source that recompiles to identical p-code on
  all samples and on all 941 generated feature cases (`battery.py`, 19
  batteries).
- Whole exe: 12 of 22 samples and 846 of 941 feature cases rebuild byte-identical (`roundtrip.py`,
  `exediff.py`, `battery.py --exe`).

"Complete" means: every language feature has a battery, every case
round-trips to identical p-code and form resources, and, with names
padded to their original lengths, to an identical executable.

Plan: see [PLAN.md](PLAN.md).

## License

MIT for this project's own code and documentation — see `LICENSE`. Does
not cover VB3 itself or anything you analyze with these tools.
