# vb3-decompiler

Tools and findings for reverse-engineering Visual Basic 3.0 (1993)
executables — both the NE resource format (forms, controls, pictures) and
the p-code bytecode format (`VBRUN300.DLL`-interpreted, not native x86).

No modern decompiler targets VB3 specifically — tools like VB Decompiler
target VB5/6, which use a different (32-bit, Unicode) p-code encoding.
Both the resource format and the p-code opcode encoding here are original,
empirically-derived work: compile small known-source test programs
through a real VB3 compiler, diff the output against the previous
variant, repeat. See [`RESOURCE_FORMAT.md`](RESOURCE_FORMAT.md) and
[`OPCODES.md`](OPCODES.md) for the findings.

This project grew out of [Quibble Race](https://github.com/vmartinv/qrace),
a decompilation/port project for a specific VB3-compiled freeware game —
split out because the VB3-format knowledge here is useful independent of
that game.

## What's here

```
tools/
  ne_parser.py           NE header + resource table parser
  extract_bitmaps.py     pulls embedded BMPs out of raw RCDATA dumps
  parse_form_headers.py  decodes form captions/control names
  segment_parser.py      NE segment table parser, p-code string scanner
  vb3ide/
    kwaj_extract.py      decompresses VB3 setup-disk files (libmspack via ctypes)
    compile_snippet.py   drives the real VB3 IDE to compile test programs
```

All Python, standard library only except `vb3ide/kwaj_extract.py` (needs
`libmspack`, see Setup). No build step.

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

Run it under Wine (its built-in win16 shim handles this transparently, no
`WINEARCH=win32` or extra packages needed on a modern Wine build):

```sh
WINEPREFIX=$PWD/work/.wineprefix wine work/ide/VB.EXE
```

The graphical `SETUP.EXE` on the disks reliably fails with a bogus
"Insufficient memory or disk space" error under Wine, regardless of
actual free space — not worth chasing, since it's not needed.

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

Output lands in `work/sweep/`. See `OPCODES.md` for how to read the
results (parse with `tools/segment_parser.py`).

## Status

Actively developed. `RESOURCE_FORMAT.md` covers form/resource extraction
fairly completely. `OPCODES.md` covers a solid but partial slice of the
p-code instruction set — variable load/store, integer arithmetic, integer
and (partially) floating-point literals, string literals. Comparisons,
control flow, and procedure calls are not decoded yet. Contributions
(more opcode findings, applying this to other VB3 binaries) welcome.

## License

MIT for this project's own code and documentation — see `LICENSE`. Does
not cover VB3 itself or anything you analyze with these tools.
