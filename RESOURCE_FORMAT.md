# VB3 NE Resource Format

VB3 stores forms, control layout and pictures in standard NE resources,
using its own formats inside them. Worked example: `qrace.exe`.

## Resource table

- `RT_GROUP_ICON` / `RT_ICON`: the app icon (standard).
- `RT_RCDATA`: everything else. There are no `RT_STRING`, `RT_BITMAP` or
  `RT_DIALOG` resources. Code strings are inline in p-code (`OPCODES.md`).
- The smallest unpaired `RT_RCDATA` (id 1) is the project directory: the
  project name, the `.FRM` files, VBX files and their control classes, and
  the late-bound property table (see `OPCODES.md` → Properties). Standard
  modules get no resource.
- `RT_RCDATA` 2: initial data images (global segment, then each module's
  data as `u16 length, 00 00, 1E 00, …` chunks, plus slot fix-up lists
  `09 <u16 slot>`). Control and form slot records are documented in
  `OPCODES.md` → "Symbols".

## Per-form `RT_RCDATA` pair

Each form is a **data blob immediately followed by its name table**
(blob id N, name table id N+1), in project-directory order. Confirmed on
`qrace.exe`: under this pairing every caption matches the strings in that
form's code segment (see `OPCODES.md`: code segment = 4 + directory index).

- **Name table** (small, 256 B): Pascal strings (u8 length + ASCII). The
  first is the form name, then one entry per control name (control-array
  elements share one). Empty entries are deleted controls and still count
  as indices.
- **Data blob**: compiled form properties and layout, plus embedded FRX
  pictures.

### Data blob header

```
+0   bytes FF CC           magic
+2   u16 0x002C
+4   u32                   varies, meaning unknown
+8   u8  0x00
+9   u8                    varies, meaning unknown
+10  u16 0x0003
+12  u16 0x0000
+14  (u8 tag, u8 0)*       terminated by (0xFF, 0); tags seen 0,38,39,40,50
then Pascal string         form Caption
```

## Embedded pictures

Each FRX picture is a complete Windows 3.x BMP (`BM` + valid headers,
24-bit uncompressed) behind a small VB wrapper header. Scanning for `BM`
with a sane `bfSize`/`bfOffBits` extracts them.

## Open questions

- Blob layout beyond the header: control positions, sizes, properties.
- The FRX picture wrapper header (needed to detect non-BMP pictures).

## Tools

- `tools/ne_parser.py <exe>`: NE header + resource table, dumps resources.
- `tools/extract_bitmaps.py`: extracts embedded BMPs from resource dumps.
- `tools/parse_form_headers.py <exe>`: decodes blob headers and captions.
