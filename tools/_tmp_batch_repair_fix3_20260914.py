from pathlib import Path

path = Path("src/application/field_repair.py")
text = path.read_text(encoding="utf-8")
path.write_text(text.rstrip() + "\n", encoding="utf-8")
