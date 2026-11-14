import json
import pytest
from tools.install_instruction_candidate import install


def setup(tmp_path, passed=True):
    live, staged = tmp_path/'live', tmp_path/'staged'
    live.mkdir(); staged.mkdir()
    (live/'old.sqlite').write_bytes(b'old')
    (staged/'candidate.sqlite').write_bytes(b'candidate')
    formal, candidate = live/'manifest.json', staged/'manifest.json'
    formal.write_text(json.dumps({'fact_bundle_version': 'old', 'database': {'filename': 'old.sqlite'}}))
    candidate.write_text(json.dumps({'fact_bundle_version': 'new', 'database': {'filename': 'candidate.sqlite', 'bytes': 9}}))
    report = staged/'checked.json'
    report.write_text(json.dumps({'version': 'new', 'passed': passed}))
    return formal, candidate, report


def test_install_moves_only_checked_candidate_and_retains_old_until_default_check(tmp_path):
    formal, candidate, report = setup(tmp_path)
    result = install(candidate, formal, expected_version='old', database_name='new.sqlite',
                     reports=[report], receipt=tmp_path/'installation.json')
    assert (formal.parent/'new.sqlite').read_bytes() == b'candidate'
    assert (formal.parent/'old.sqlite').read_bytes() == b'old'
    assert not (candidate.parent/'candidate.sqlite').exists()
    assert json.loads(formal.read_text())['fact_bundle_version'] == 'new'
    assert result['fresh_process_default_load_verified'] is False


@pytest.mark.parametrize('passed,expected', [(False, 'old'), (True, 'wrong')])
def test_failed_or_wrong_version_does_not_change_formal(tmp_path, passed, expected):
    formal, candidate, report = setup(tmp_path, passed)
    original = formal.read_bytes()
    with pytest.raises(ValueError):
        install(candidate, formal, expected_version=expected, database_name='new.sqlite',
                reports=[report], receipt=tmp_path/'installation.json')
    assert formal.read_bytes() == original
    assert (candidate.parent/'candidate.sqlite').exists()
