# Fixed-length strings: LSet/RSet targets and by-reference uses, local vs module.
header = "Dim m As String * 8\nDim s$\n\nSub Q (x$)\nEnd Sub\n"
lines = [
 ("LSet loc", 'Dim f As String * 8\nLSet f = "ab"'),
 ("RSet loc", 'Dim f As String * 8\nRSet f = "ab"'),
 ("LSet mod", 'LSet m = "ab"'),
 ("RSet mod", 'RSet m = "ab"'),
 ("loc get", 'Dim f As String * 8\ns$ = f'),
 ("loc set", 'Dim f As String * 8\nf = "ab"'),
 ("loc byref", 'Dim f As String * 8\nQ f'),
 ("mod byref", 'Q m'),
 ("static loc LSet", 'Static g As String * 8\nLSet g = "ab"'),
]
