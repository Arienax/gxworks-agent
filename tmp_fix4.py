from pathlib import Path
path = Path("scripts/native_ladder_acceptance.py")
text = path.read_text(encoding="utf-8")
old = '''    table, records = _read_program_rows(path)
    rows = []
    # PI声明 sits in a column the record reader does not model, so take it from the
    # same parsed table instead of reading the file a second time.
    for row in table[3:]:
        if len(row) > 5 and row[5].strip():
            rows.append({"kind": "pi_declaration", "text": row[5].strip()})'''
new = '''    table, records = _read_program_rows(path)
    rows = []
    # PI声明 sits in a column the record reader does not model. Take it from the same
    # parsed table and keep the parsed CSV row number: records are merged and skipped,
    # so a declaration has to remember where it was, not which record it followed.
    for csv_row, cells in enumerate(table[3:], start=4):
        declaration = cells[5].strip() if len(cells) > 5 else ""
        if declaration:
            rows.append({"kind": "pi_declaration", "csv_row": csv_row,
                         "text": declaration})'''
assert text.count(old) == 1
path.write_text(text.replace(old, new, 1), encoding="utf-8")
import py_compile; py_compile.compile("scripts/native_ladder_acceptance.py", doraise=True)
print("csv_row kept")
