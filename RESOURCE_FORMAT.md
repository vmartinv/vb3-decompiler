# VB3 NE Resource Format

VB3's compiler embeds everything (forms, control layout, strings,
pictures) through the standard Windows 3.x NE resource mechanism, but the
actual data inside those resources is VB's own proprietary format, not any
standard Win32 resource type. No existing Linux tool parses this out of
the box, and no public documentation of the internal layout was found —
this is original empirical work, verified against a real compiled VB3
program ([Quibble Race](https://github.com/vmartinv/qrace), used
throughout as the worked example; findings should generalize to any VB3
executable).

## Resource table shape

A VB3 exe's NE resource table typically has:

- `RT_GROUP_ICON` / `RT_ICON` — the app icon. Standard format, nothing
  VB-specific.
- `RT_RCDATA` — **everything else**: forms, control layout, strings,
  pictures. No `RT_STRING`, `RT_BITMAP`, or `RT_DIALOG` resources exist at
  all — VB3 doesn't use those standard resource types. All UI text lives
  in the p-code / form data instead (see `OPCODES.md` for the p-code
  string encoding; form `Caption`/`Text` properties are decoded below).
- A resource id gap in the sequence, and/or an orphaned oversized `RCDATA`
  entry with no name-table pair, are both possible — in the qrace.exe
  example, a deleted form left exactly this pattern: a missing id where
  its name-table used to be, and its old data blob (identical header/tags/
  caption to the form that replaced it) still sitting in the file, dead
  and unreferenced. Not proven to be universal VB3 linker behavior, just
  observed once.

## `RT_RCDATA` pairing: (name table, data blob) per form

The RCDATA entries follow a repeating pattern — a small resource (exactly
the 256-byte alignment minimum) immediately followed by a much larger one:

- **Name table** (small): a sequence of Pascal-style (1-byte length
  prefix, no null terminator) ASCII strings. First string is the form's
  internal name (`frmTitle`, `frmMain`, ...), followed by every named
  control on that form in declaration order. This is almost certainly how
  the p-code resolves symbolic control names to indices at runtime.
- **Data blob** (large): the compiled form — control properties/layout
  plus every FRX-embedded picture used by that form's controls,
  concatenated.

One project-level `RCDATA` entry (the smallest, non-paired one) is a
directory of the project's compiled `.FRM` modules by filename — useful
for confirming the full form list up front.

**Not every form has a data-blob pair.** Best working hypothesis: VB3
only emits a data-blob `RCDATA` resource when a form has FRX-referenced
binary content (an embedded picture); a form with none apparently has no
data blob at all, and its compiled control-layout/property data (if it
needs to exist outside the p-code) lives somewhere else — not confirmed.

## Data blob header: fixed prefix + tag list + caption

Every data-blob resource starts with the same fixed-format header before
any control layout or FRX picture data (decoded by
`tools/parse_form_headers.py`):

```
offset 0:  u16 0xFFCC          -- constant magic
offset 2:  u16 0x002C          -- constant (purpose unknown)
offset 4:  u32                 -- varies per form; no correlation found
                                   with blob length or control count
offset 8:  u8  0x00            -- constant
offset 9:  u8                  -- varies, purpose unknown
offset 10: u16 0x0003          -- constant
offset 12: u16 0x0000          -- constant
offset 14: (u8 tag, u8 0x00)*  -- variable-length list, terminated by
                                   (0xFF, 0x00). Tag values seen: 0, 38,
                                   39, 40, 50 -- meaning unknown, possibly
                                   form-level boolean properties (a set of
                                   "flag present" markers).
then:      Pascal string (u8 length + ASCII) -- the form's Caption
           property.
```

This alone recovers every form's real window caption without touching any
p-code — a cheap, high-value first pass on any VB3 binary.

**Open question, not resolved**: on the one real project checked, the
recovered caption in most rows read as a near-exact description of the
*next* form in the project directory's order, rather than of the form
whose name-table it's nominally paired with by id-adjacency (e.g. a form
internally named `frmInfo` had a data blob captioned "Options" — which
reads like it belongs to a form named `frmOptions`). Consistent across
most of one project's forms, but not conclusively distinguishable from
forms simply having internal names that lag their in-game captions (a
common, mundane occurrence in real codebases, and some rows fit their
*own* paired name just as well as the "shifted" reading). Needs an
independent anchor — e.g. matching a name-table's control list against
actual control-position records inside a specific data blob — to settle.
Don't treat a caption recovered this way as definitively belonging to its
adjacent name-table's form without checking for this ambiguity.

## Embedded pictures: standard BMP, trivially extractable

Every FRX picture found so far is a **complete, standalone Windows 3.x
BMP** (`BM` magic + valid `BITMAPFILEHEADER`/`BITMAPINFOHEADER`, 24-bit,
no compression) embedded verbatim inside the data blob, preceded by a
small VB-specific wrapper header (not decoded — irrelevant for
extraction, just scan for `BM` + a sane `bfSize`/`bfOffBits`).
`tools/extract_bitmaps.py` pulls these out; every one found so far opens
cleanly in standard image tools.

## Still unmapped

- The exact byte layout of the data blob outside of the header decoded
  above and the embedded BMPs — control positions, sizes, most property
  values, event procedure entry points/offsets into the module's p-code.
  Would also help resolve the pairing-shift open question above.
- The small header preceding each embedded `BM` signature (VB's FRX
  picture wrapper) — not needed for extraction, but would confirm whether
  non-BMP picture types (icons/cursors/metafiles, also storable via FRX)
  are being missed.

## Tooling

- `tools/ne_parser.py <exe>` — parses the NE header + resource table,
  extracts the string table (if any) and dumps every raw resource.
- `tools/extract_bitmaps.py` — scans raw resource dumps for embedded BMPs
  and writes them out as standalone `.bmp` files.
- `tools/parse_form_headers.py <exe>` — decodes the fixed-format data-blob
  header (magic/tags/caption) for every form.
