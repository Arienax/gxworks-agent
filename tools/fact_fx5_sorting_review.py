"""AB sorting diagram review. No execution support is implied by these facts.

The printed X/O marks on physical pages 876 and 879 qualify the preceding
no-overlap sentence: identical ranges are allowed, partial overlaps are not.
Keep illustrative endpoints as evidence, not as exclusive interval bounds.
"""
import re

_PARTIAL = 'D10 | D30 | D10 | D30 Source Data | Source Data D2 | D22 | D20 | D40 Sorted Data | Sorted Data'
_SAME_RIGHT = 'D10 | D30 | D10 | D30 Source Data | Source Data D10 | D30 | D35 | D55 Sorted Data | Sorted Data'
_LEFT = 'D50 | D70 Source Data D20 | D40 Sorted Data'
_TEXT = 'Ensure that the sorted data does not overlap with the source data.'


def review_sorting_precaution(clause, *, section, block, declared):
    page = block.get('page')
    if page not in (876, 879) or clause not in {_PARTIAL, _SAME_RIGHT, _LEFT, _TEXT}:
        return None
    form, first, bits = ('SORTTBL2', 874, 16) if page == 876 else ('DSORTTBL2', 877, 32)
    if section.get('first_page') != first or not {'s', 'n1', 'n2', 'n3', 'd'} <= set(declared):
        return None
    text = re.sub(r'\s+', ' ', section.get('text', ''))
    if not re.search(r'ENO\s*:=\s*' + form + r'\(EN,\s*s,\s*n1,\s*n2,\s*n3,\s*d\)', text):
        return None
    header_text = text + ' ' + ' '.join(t for p, t in section.get('page_layouts', []) if p == first)
    if not all(cpu in header_text for cpu in ('FX5S', 'FX5UJ', 'FX5U', 'FX5UC')):
        return None
    examples = {
        _PARTIAL: [{'source': [10, 30], 'destination': [2, 22], 'permitted': False},
                   {'source': [10, 30], 'destination': [20, 40], 'permitted': False}],
        _SAME_RIGHT: [{'source': [10, 30], 'destination': [10, 30], 'permitted': True},
                      {'source': [10, 30], 'destination': [35, 55], 'permitted': True}],
        _LEFT: [{'source': [50, 70], 'destination': [20, 40], 'permitted': True}],
    }
    points = {'operator': '*', 'left': {'operand': 'n1'}, 'right': {'operand': 'n2'}}
    return {
        'rules': [{'kind': 'device_ranges_overlap',
                   'left': {'head': {'operand': 's'}, 'points': points, 'element_bits': bits},
                   'right': {'head': {'operand': 'd'}, 'points': points, 'element_bits': bits},
                   'overlap_kind': 'partial', 'permitted': False,
                   'exception': {'same_device': ['s', 'd']}}],
        'forms': [form, form + '_U'], 'full_clause_review': True,
        'additional_pages': [first, page],
        'dimension': 'constraints.documented_precautions',
        'value_updates': {
            'diagram_review': {'basis': 'original_PDF_X_and_O_marks_plus_adjacent_in_place_clause',
                               'device_class': 'D', 'examples': examples.get(clause, []),
                               'endpoint_inclusivity': 'not_inferred_from_diagram'},
            'source_relation_group': form + '_partial_overlap_with_same_device_exception',
            'core_execution_capability': 'not_established',
        },
    }
