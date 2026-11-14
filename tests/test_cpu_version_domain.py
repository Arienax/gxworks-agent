import pytest

from plc import runtime_semantics


@pytest.mark.parametrize(('model', 'version', 'section', 'pdf_page'), [
    ('FX3S', '1.00', 'A-3-3', 918),
    ('FX3G', '1.00', 'A-3-4', 918),
    ('FX3GC', '1.40', 'A-3-5', 919),
    ('FX3U', '2.20', 'A-3-6', 919),
    ('FX3UC', '1.00', 'A-3-7', 920),
])
def test_cpu_first_release_keeps_its_manual_binding(model, version, section, pdf_page):
    fact = runtime_semantics._control_facts()['cpu_version_domain'][model]
    assert fact['first_release'] == version
    assert fact['sources'] == [{
        'manual_id': 'fx3_programming_r', 'manual_number': 'JY997D16601', 'revision': 'R',
        'section': section, 'pdf_pages': [pdf_page], 'printed_pages': [pdf_page - 2],
    }]
