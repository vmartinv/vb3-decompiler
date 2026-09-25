# Standard controls: every event handler signature, and design-time
# properties at non-default values. Event lists and parameter types come
# from the runtime (VBRUN300.DLL); parameter names are the IDE's.
from pathlib import Path

import pcode_disasm as P  # battery.py runs from tools/ (on sys.path)
from decompile import EVENT_PARAMS, EVENT_TYPE

rt = P.Runtime(Path(P.__file__).resolve().parent.parent / "work" / "ide" / "VBRUN300.DLL")
EVENTS = rt.event_lists()

GEOM = "      Height = 500\n      Left = 100\n      Top = 100\n      Width = 1200\n"
PROPS = {
    "PictureBox": "AutoRedraw = -1\nAutoSize = -1\nBackColor = &H00FF0000&\nBorderStyle = 0\nDrawMode = 7\n"
                  "DrawStyle = 2\nDrawWidth = 3\nEnabled = 0\nFillColor = &H0000FF00&\nFillStyle = 0\n"
                  "FontItalic = -1\nFontName = \"Arial\"\nFontSize = 12\nForeColor = &H000000FF&\n"
                  "MousePointer = 2\nScaleMode = 3\nTabStop = 0\nTag = \"t\"\nVisible = 0\nDragMode = 1\n"
                  "ClipControls = 0",
    "Label": "Alignment = 2\nAutoSize = -1\nBackStyle = 0\nBorderStyle = 1\nCaption = \"cap\"\nWordWrap = -1\n"
             "FontUnderline = -1\nForeColor = &H00008000&",
    "TextBox": "MultiLine = -1\nScrollBars = 3\nMaxLength = 10\nPasswordChar = \"*\"\nText = \"abc\"\nHideSelection = 0",
    "Frame": "Caption = \"fr\"\nForeColor = &H00FF00FF&",
    "CommandButton": "Caption = \"ok\"\nCancel = -1\nDefault = -1",
    "CheckBox": "Caption = \"ck\"\nValue = 1\nAlignment = 1",
    "OptionButton": "Caption = \"op\"\nValue = -1\nAlignment = 1",
    "ComboBox": "Style = 2\nSorted = -1",
    "ListBox": "MultiSelect = 2\nSorted = -1\nColumns = 2",
    "HScrollBar": "Max = 100\nMin = 5\nValue = 10\nLargeChange = 20\nSmallChange = 2",
    "VScrollBar": "Max = 100\nMin = 5\nValue = 10\nLargeChange = 20\nSmallChange = 2",
    "Timer": "Interval = 500\nEnabled = 0",
    "DriveListBox": "FontSize = 10",
    "DirListBox": "FontItalic = -1",
    "FileListBox": "Pattern = \"*.txt\"\nArchive = 0\nHidden = -1\nReadOnly = 0\nSystem = -1\nMultiSelect = 1",
    "Shape": "Shape = 3\nBorderColor = &H000000FF&\nBorderStyle = 2\nFillStyle = 4\nFillColor = &H00FF0000&\nBackStyle = 1",
    "Line": "BorderColor = &H000000FF&\nBorderStyle = 3\nDrawMode = 7",
    "Image": "Stretch = -1\nBorderStyle = 1",
    "Data": "Caption = \"db\"\nExclusive = -1\nReadOnly = -1",
}


def block(cls: str, props: str = "") -> str:
    geom = "      Left = 100\n      Top = 100\n" if cls == "Timer" else \
        "      X1 = 100\n      X2 = 900\n      Y1 = 100\n      Y2 = 500\n" if cls == "Line" else GEOM
    extra = "".join(f"      {ln}\n" for ln in props.split("\n") if ln)
    return f"   Begin {cls} C1\n{geom}{extra}   End\n"


def handler(ev: str, cls: str) -> str:
    types = P.EVENT_TYPES.get((cls, ev), P.MASTER_EVENT_TYPES.get(ev, ()))
    names = EVENT_PARAMS.get(ev, "").split() or [f"P{j + 1}" for j in range(len(types))]
    params = ", ".join(f"{n} As {EVENT_TYPE.get(t, 'Integer')}" for n, t in zip(names, types))
    return f"Sub C1_{ev} ({params})\n    Debug.Print 1\nEnd Sub\n\n"


LOAD = "Sub Form_Load ()\n    Debug.Print 1\nEnd Sub\n"
cases = []
for cls, props in PROPS.items():
    evs = [e for e in EVENTS.get(cls, []) if e]
    if evs:
        cases.append(dict(name=f"events {cls}", controls=block(cls), code="".join(handler(e, cls) for e in evs)))
    cases.append(dict(name=f"props {cls}", controls=block(cls, props), code=LOAD))
