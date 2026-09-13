#!/usr/bin/env python3
"""One-shot generic patch for no-error code ranges in strict error lists."""
from pathlib import Path

path = Path('tools/build_fx3u_knowledge_v3.py')
text = path.read_text(encoding='utf-8')

old = '''def plausible_error_matches(text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for match in ERROR_CODE_RE.finditer(text):
        digits = match.group(1).upper()
        if len(digits) != 4:
            continue
        is_hex = bool(re.search(r"[A-F]", digits))
        if digits == "0000" or is_hex or digits[:1] in set("3456789"):
            matches.append(match)
    return matches
'''
new = '''NO_ERROR_CODE_RANGE_RE = re.compile(
    r"(?<![0-9A-F])(?:0x)?[3-9][0-9A-F]{3}(?:H)?\\s+"
    r"(?:to|through|[-–—~])\\s+"
    r"(?:0x)?[3-9][0-9A-F]{3}(?:H)?\\s+"
    r"(?:[-–—]\\s*)?no\\s+error\\b",
    flags=re.I,
)


def no_error_code_range_spans(text: str) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in NO_ERROR_CODE_RANGE_RE.finditer(text)]


def plausible_error_matches(text: str) -> list[re.Match[str]]:
    # Some Mitsubishi error tables contain reserved/no-error ranges such as
    # ``6307 to 6311 No error``. PDF text extraction makes both endpoints look
    # like independent error codes. Exclude the whole range before slicing
    # records so neither endpoint becomes a fake diagnostic row.
    no_error_ranges = no_error_code_range_spans(text)
    matches: list[re.Match[str]] = []
    for match in ERROR_CODE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in no_error_ranges):
            continue
        digits = match.group(1).upper()
        if len(digits) != 4:
            continue
        is_hex = bool(re.search(r"[A-F]", digits))
        if digits == "0000" or is_hex or digits[:1] in set("3456789"):
            matches.append(match)
    return matches
'''
assert text.count(old) == 1, 'plausible_error_matches block not found exactly once'
text = text.replace(old, new, 1)

old = '''            block_end = (
                matches[match_index + 1].start()
                if match_index + 1 < len(matches)
                else min(len(text), match.end() + 1800)
            )
'''
new = '''            candidate_ends = [
                matches[match_index + 1].start()
                if match_index + 1 < len(matches)
                else min(len(text), match.end() + 1800)
            ]
            candidate_ends.extend(
                start for start, _end in no_error_code_range_spans(text)
                if start > match.end()
            )
            block_end = min(candidate_ends)
'''
assert text.count(old) == 1, 'strict error block boundary not found exactly once'
text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')
print('patched no-error code ranges and strict record boundaries')