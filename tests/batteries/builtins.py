# Builtin functions: one case each ($ and non-$ forms where both exist).
def S(body):
    return f"Sub Form_Load ()\n{body}\nEnd Sub\n"

E = [
 'Asc("a")', 'Chr$(65)', 'Chr(65)', 'Len("abc")', 'Len(a%)', 'Left$("abc", 2)', 'Left("abc", 2)',
 'Right$("abc", 2)', 'Right("abc", 2)', 'Mid$("abc", 2)', 'Mid$("abc", 2, 1)', 'Mid("abc", 2, 1)',
 'InStr("abc", "b")', 'InStr(2, "abcb", "b")', 'InStr(1, "aB", "b", 1)', 'LCase$("A")', 'UCase$("a")', 'LCase("A")',
 'LTrim$(" a")', 'RTrim$("a ")', 'Trim$(" a ")', 'Trim(" a ")', 'Space$(3)', 'String$(3, "x")', 'String$(3, 65)',
 'Str$(12)', 'Str(12)', 'Val("12")', 'Hex$(255)', 'Hex(255)', 'Oct$(8)', 'Format$(1.5, "0.00")', 'Format$(Now)',
 'Format(1)', 'StrComp("a", "b")',
 'Abs(-1)', 'Sgn(-2)', 'Int(1.5)', 'Fix(-1.5)', 'Sqr(4)', 'Exp(1)', 'Log(2)', 'Sin(1)', 'Cos(1)', 'Tan(1)', 'Atn(1)',
 'Rnd', 'Rnd(1)', 'CInt(1.5)', 'CLng(1.5)', 'CSng(1)', 'CDbl(1)', 'CCur(1)', 'CStr(1)', 'CVar(1)', 'CVDate(1)',
 'Now', 'Date$', 'Date', 'Time$', 'Time', 'Timer', 'Day(Now)', 'Month(Now)', 'Year(Now)', 'Weekday(Now)',
 'Hour(Now)', 'Minute(Now)', 'Second(Now)', 'DateSerial(1994, 1, 2)', 'TimeSerial(1, 2, 3)', 'DateValue("1/2/94")',
 'TimeValue("10:00")', 'IsDate("x")', 'IsNumeric("1")', 'IsNull(v)', 'IsEmpty(v)', 'VarType(v)',
 'Dir$("*.*")', 'Dir$', 'Dir', 'CurDir$', 'CurDir$("c")', 'Environ$("PATH")', 'Environ$(1)', 'Command$',
 'FreeFile', 'Err', 'Erl', 'Error$', 'Error$(5)', 'RGB(1, 2, 3)', 'QBColor(4)', 'LoadPicture()', 'LoadPicture("c:\\a.bmp")',
 'InputBox$("p")', 'InputBox$("p", "t", "d")', 'InputBox("p")', 'Shell("x")', 'DoEvents()',
 'Screen.Width', 'Screen.ActiveForm.Caption', 'App.Path', 'App.EXEName', 'Clipboard.GetText()', 'Printer.Width',
 'TextWidth("abc")', 'TextHeight("A")', 'Point(1, 1)', 'LBound(arr)', 'UBound(arr)', 'UBound(arr, 1)',
 'Input$(1, #1)', 'Loc(1)', 'Seek(1)', 'LOF(1)', 'EOF(1)', 'FileAttr(1, 1)', 'FileLen("x")', 'FileDateTime("x")',
 'GetAttr("x")', 'Partition(5, 0, 10, 5)', 'Choose(1, "a", "b")', 'IIf(a, 1, 2)', 'Switch(a, 1)',
]
cases = [dict(name=e, code=S(f"    Static arr(3)\n    x = {e}")) for e in E]
