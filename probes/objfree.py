# Object locals: epilogue OBJ_FREE order vs Dim layout.
header = ""
lines = [
 ("one Dim", "Dim f As Form, c As Control\nSet f = Me\nSet c = Nothing"),
 ("two Dims", "Dim f As Form\nDim c As Control\nSet f = Me\nSet c = Nothing"),
 ("two Dims rev", "Dim c As Control\nDim f As Form\nSet f = Me\nSet c = Nothing"),
 ("three one Dim", "Dim f As Form, c As Control, g As Form\nSet f = Me\nSet c = Nothing\nSet g = Me"),
 ("three Dims", "Dim f As Form\nDim c As Control\nDim g As Form\nSet f = Me\nSet c = Nothing\nSet g = Me"),
]
