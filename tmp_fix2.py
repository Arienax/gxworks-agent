from pathlib import Path
path = Path("scripts/native_ladder_acceptance.py")
text = path.read_text(encoding="utf-8")
old = """    failed_calls = [call for call in workspace.get("calls", [])
                    if call.get("code") or call.get("hresult")]"""
new = """    # S_FALSE (1) and other positive codes mean "already done", not failure.
    failed_calls = [call for call in workspace.get("calls", [])
                    if call.get("code") or (type(call.get("hresult")) is int
                                            and call["hresult"] < 0)]"""
assert text.count(old) == 1
text = text.replace(old, new, 1)
old_record = """        failed_calls = [call for call in workspace.get("calls", [])
                        if call.get("code") or call.get("hresult")]"""
new_record = """        failed_calls = [call for call in workspace.get("calls", [])
                        if call.get("code") or (type(call.get("hresult")) is int
                                                and call["hresult"] < 0)]"""
assert text.count(old_record) == 1
text = text.replace(old_record, new_record, 1)
path.write_text(text, encoding="utf-8")
import py_compile
py_compile.compile("scripts/native_ladder_acceptance.py", doraise=True)
print("failure filter corrected")
