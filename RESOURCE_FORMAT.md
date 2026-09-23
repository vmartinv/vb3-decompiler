# VB3 NE Resource Format

VB3 stores forms, control layout and pictures in standard NE resources,
using its own formats inside them. Worked example: `qrace.exe`.

## Resource table

- `RT_GROUP_ICON` / `RT_ICON`: the app icon (standard).
- `RT_RCDATA`: everything else. There are no `RT_STRING`, `RT_BITMAP` or
  `RT_DIALOG` resources. Code strings are inline in p-code (`OPCODES.md`).
- The smallest unpaired `RT_RCDATA` (id 1) is the project directory: the
  project name plus the list of `.FRM` files. Standard modules get no
  resource.
- Deleted forms can leave an id gap and an orphaned, unreferenced data
  blob.

## Per-form `RT_RCDATA` pair

- **Name table** (small, 256 B): Pascal strings (u8 length + ASCII). The
  first is the form name, followed by each named control in declaration
  order.
- **Data blob** (large): compiled form properties and layout, plus
  embedded FRX pictures. Only emitted when the form has one; forms without
  pictures may have no blob.

### Data blob header

```
+0   u16 0xFFCC            magic
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

- Caption ↔ name-table pairing: in `qrace.exe` most captions read like
  the *next* form's (e.g. `frmInfo` → "Options"). Settle it by matching a
  name table's controls against control records inside a blob.
- Blob layout beyond the header: control positions, sizes, properties.
- The FRX picture wrapper header (needed to detect non-BMP pictures).

## Tools

- `tools/ne_parser.py <exe>`: NE header + resource table, dumps resources.
- `tools/extract_bitmaps.py`: extracts embedded BMPs from resource dumps.
- `tools/parse_form_headers.py <exe>`: decodes blob headers and captions.
