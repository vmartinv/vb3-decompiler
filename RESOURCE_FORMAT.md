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
- `RT_RCDATA` 3 (only with OLE Automation): member-name pool for
  late-bound `As Object` calls (`OPCODES.md` → Properties).

## Per-form `RT_RCDATA` pair

Each form is a **data blob immediately followed by its name table**
(blob id N, name table id N+1), in project-directory order. Confirmed on
`qrace.exe`: under this pairing every caption matches the strings in that
form's code segment (see `OPCODES.md`: code segment = 4 + directory index).

- **Name table** (small, 256 B): Pascal strings (u8 length + ASCII). The
  first is the form name, then one entry per control name (control-array
  elements share one). Empty entries are deleted controls and still count
  as indices.
- **Data blob**: the form's control tree with its design-time
  properties. Decoded by `tools/formblob.py` (all sample forms decode;
  the round-trip rebuilds forms from it).

### Data blob

```
+0   FF CC 2C 00, u8 control count, u24 ?   header
+8   form record
then records, each preceded by a flag byte (0 = padding):
  controls: 1 first child of the previous record, 3 next sibling,
            2 end of children
  menus:    5 first menu (after the controls), 2 next menu,
            3 end of a menu level
  4 end of form
```

A menu whose unnamed property 7 is set (−1) has submenus: the records
after it are its children until a 3. Unnamed property 6 is set on
separators.

Record: `u8 flag, u16 length, u16 flags (8000 = control-array element),
u8 name index, [u16 array index], u8 0, u8 class`, then (class FF) a
Pascal class name for custom controls, early properties, `FF`,
properties, `FF`. If the form has code, an event table follows: `u8 count,
u16 per event` (the handler links). The length field leaves out that
count byte.

Classes follow toolbox order: 0 PictureBox, 1 Label, 2 TextBox, 3 Frame,
4 CommandButton, 5 CheckBox, 6 OptionButton, 7 ComboBox, 8 ListBox,
9 HScrollBar, A VScrollBar, B Timer, D Form, 10 DriveListBox,
11 DirListBox, 12 FileListBox, 13 Menu, 14 MDIForm, 16 Shape, 17 Line,
18 Image, 25 Data, FF custom (VBX).

A property is `u8 id` + value, where the id is the index in the class's
MODEL property list (VBRUN300 data segment or the VBX; `parse_models`).
The value is encoded by the PROPINFO data type (low byte of `fl`; bit 7
is a flag and is masked off):

- string (1, 0x0D): Pascal string. ComboBox.Text with Style 2 has no value.
- bool (4) and enum (6): u8. short (2) and Index (0x3D): i16. long (3)
  and color (5): i32. real (7): f32.
- standalone positions and sizes (8–11): i32 twips.
- picture (0x0C): i32 size (−1 = none) plus the file, the same as a `.FRX` entry.
- standard `Left` (any class but Timer): Left, Top, Width, Height as one
  record (i16 each, i32 for forms). Form records hold the **client**
  rectangle, also stored as ClientLeft..ClientHeight. The text form's
  Left/Top/Width/Height aren't stored.
- `FontName`: name, f32 FontSize, u8 style bits (1 bold, 2 italic,
  4 underline, 8 strikethru). This is the font as realized when compiling:
  missing fonts are replaced.
- `ScaleMode`: u16 mode, then for mode 0 (user) f32 ScaleLeft, ScaleTop
  and twips-per-unit X, Y, then u16 graphics bits (2 FontTransparent,
  20 AutoRedraw, 1 user scale).
- Menu `Shortcut`: u8 1..79 = ^A..^Z, {F1}..{F12}, ^{F1}.., +{F1}..,
  ^+{F1}.., ^{INSERT}, +{INSERT}, {DEL}, +{DEL}, %{BKSP}.
- A VBX's custom property can use its own save format (PictureClip
  `Location`: 4 × i32).
- A class with no Width/Height (MSComm and other invisible-at-run VBX
  controls) still stores the full rect; the text form has only Left/Top.

Early properties come before the first FF: window-style ones
(BorderStyle, MaxButton, MinButton, ControlBox, ClipControls, MDIChild,
ComboBox.Style, …).

## Open questions

- VBX properties with custom save formats beyond PictureClip.Location
  (only the samples' VBXes are covered).
- Header bytes 5–7; the control count's rule for control arrays.

## Tools

- `tools/ne_parser.py <exe>`: NE header + resource table, dumps resources.
- `tools/formblob.py <exe>`: decodes form blobs to `.frm` descriptions.
- `tools/extract_bitmaps.py`: extracts embedded BMPs from resource dumps.
