"""Regressions for material separation without per-file source approval."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
import lzma
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from publication.source_policy import PRIVATE_FILES, exclusion_reason, source_files
from scripts.export_open_source import export_source
from tools import check_public_boundary as boundary


def write(root, name, data=b'# authored source\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def run_check(root, *arguments, required=()):
    # Each small tree tests its own delivery requirements; the real tree is also
    # checked by the CI command with the production REQUIRED set unchanged.
    output = StringIO()
    with patch.object(boundary, 'ROOT', root), patch.object(sys, 'argv', ['check_public_boundary', *arguments]):
        with patch.object(boundary, 'REQUIRED', set(required)), redirect_stdout(output), redirect_stderr(output):
            status = boundary.main()
    return status, output.getvalue()


def template_archive(*, entries=None, models=None):
    if entries is None:
        entries = {'projects/q.gxw': boundary.CFB_SIGNATURE + b'blank project'}
    if models is None:
        models = [{'file': name} for name in entries if name.endswith('.gxw')]
    data = BytesIO()
    with ZipFile(data, 'w') as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        archive.writestr('manifest.json', json.dumps({'models': models}))
    return lzma.compress(data.getvalue())


def test_new_source_and_regressions_need_no_manifest():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        names = ['src/new_feature.py', 'src/reports/renderer.py', 'tools/new_tool.py',
            'scripts/new_entry.py', 'tests/test_new_feature.py', 'checks/new_case.py', 'docs/guides/new.md']
        for name in names:
            write(root, name)
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        subprocess.run(['git', 'add', '--', *names], cwd=root, check=True, capture_output=True)
        assert not (root / 'publication/public-files.json').exists()
        assert run_check(root)[0] == 0
        assert run_check(root, '--disk')[0] == 0


def test_research_and_temporary_outputs_are_rejected():
    names = ['research/result.json', 'docs/reports/result.md', 'docs/process/run.md',
        'tmp/test_run.py', 'tmp_probe.py', 'acceptance/native/result.json',
        'resources/knowledge/page_text.json', 'tests/fixtures/user.gxw',
        'download/manual.pdf', 'download/project.gxw', 'run.log', 'output.exe', '.env']
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name in names:
            write(root, name)
            assert boundary.check_file(name, root=root), name


def test_material_folders_do_not_ban_product_modules():
    assert exclusion_reason('src/reports/writer.py') is None
    assert exclusion_reason('src/local/settings.py') is None
    assert exclusion_reason('docs/Reports/run.md')
    assert exclusion_reason('checks/fixtures/authored.csv') is None


def test_large_source_and_authored_media_are_allowed():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write(root, 'docs/guide.md', b'Author: Arienax; examples use relative public paths.\n')
        write(root, 'src/generated_table.py', b'# ' + b'x' * 2_100_000 + b'\n')
        write(root, 'resources/assets/author.gif',
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff'
            b'\x21\xfe\x07Arienax\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00'
            b'\x02\x02\x44\x01\x00\x3b')
        for name in ['docs/guide.md', 'src/generated_table.py', 'resources/assets/author.gif']:
            assert boundary.check_file(name, root=root) == []


def test_credentials_still_fail_without_printing_values():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        token = 'ghp_' + 'x' * 36
        write(root, 'settings.py', token.encode())
        status, output = run_check(root, '--disk')
        assert status == 1 and 'GitHub token' in output and token not in output
        assert exclusion_reason('.env.example') is None


def test_export_includes_new_code_and_excludes_materials():
    with TemporaryDirectory() as directory:
        base = Path(directory)
        root, destination = base / 'development', base / 'public'
        included = ['src/new.py', 'tools/new.py', 'checks/test_new.py', 'docs/guides/new.md',
            'publication/source_policy.py', 'src/gxw/templates/new-template.gxw']
        excluded = ['research/notes.json', 'tmp/check.py', 'docs/reports/run.md',
            'samples/original.gxw', 'manual.pdf', 'node_modules/package/index.js',
            'publication/public-files.json']
        for name in included + excluded:
            write(root, name)
        assert export_source(root, destination) == len(included)
        actual = {path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file()}
        assert actual == set(included)


def test_export_keeps_runtime_data_without_research_metadata():
    with TemporaryDirectory() as directory:
        base = Path(directory)
        root, destination = base / 'development', base / 'public'
        write(root, 'src/gxw/templates/new.json', b'{"op": "LD", "evidence": "private locator"}')
        write(root, 'resources/instructions/mitsubishi/new_catalog.json',
            b'{"instructions": [{"mnemonic": "LD", "arity": 1, "description": "manual prose"}]}')
        assert export_source(root, destination) == 2
        assert json.loads((destination/'src/gxw/templates/new.json').read_text()) == {'op': 'LD'}
        assert json.loads((destination/'resources/instructions/mitsubishi/new_catalog.json').read_text()) == {
            'instructions': [{'mnemonic': 'LD', 'arity': 1}]}


def test_export_is_independent_of_git_ignores():
    with TemporaryDirectory() as directory:
        base = Path(directory)
        root, destination = base / 'development', base / 'public'
        write(root, '.gitignore', b'publication/\nresources/\nsrc/new.py\n')
        write(root, 'src/new.py')
        write(root, 'tmp/local_run.py')
        write(root, 'publication/README.md')
        write(root, 'resources/config.default.json', b'{}')
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        (root/'.git/info/exclude').write_text('/publication/README.md\n/src/new.py\n', encoding='utf-8')
        global_ignore = write(base, 'global-ignore', b'/publication/README.md\n/src/new.py\n')
        subprocess.run(['git', 'config', 'core.excludesFile', str(global_ignore)], cwd=root, check=True)
        subprocess.run(['git', 'add', '-f', '--', 'resources/config.default.json'],
            cwd=root, check=True, capture_output=True)
        export_source(root, destination)
        assert (destination/'src/new.py').is_file()
        assert (destination/'resources/config.default.json').is_file()
        assert (destination/'publication/README.md').is_file()
        assert not (destination/'tmp/local_run.py').exists()


def test_private_inputs_are_rejected_without_local_ignore_rules():
    names = sorted(PRIVATE_FILES | {'resources/gxw/observed_encoding.json',
        'resources/gxw/new-observation.json', 'publication/public/src/local.py',
        'src/config.json', 'tools/private_materials.py', 'tools/download_mitsubishi_manuals.py',
        'resources/instructions/mitsubishi/instruction_behavior_facts.json',
        'web/.sfc-check-123/source.ts'})
    with TemporaryDirectory() as directory:
        base = Path(directory)
        root, plain = base/'checkout', base/'plain'
        for name in names + ['publication/README.md', 'src/config.default.json']:
            write(root, name, b'{}')
            write(plain, name, b'{}')
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        subprocess.run(['git', 'add', '--all'], cwd=root, check=True, capture_output=True)
        for name in names:
            assert exclusion_reason(name), name
            assert boundary.check_file(name, root=root), name
        assert exclusion_reason(r'SRC\CONFIG.JSON')
        assert exclusion_reason('src/config.default.json') is None
        expected = {'publication/README.md', 'src/config.default.json'}
        assert {p.relative_to(root).as_posix() for p in source_files(root)} == expected
        assert {p.relative_to(plain).as_posix() for p in source_files(plain)} == expected
        for arguments in [(), ('--disk',)]:
            status, output = run_check(root, *arguments)
            assert status == 1
            assert all(name in output for name in names)


def test_private_paths_are_checked_in_text_and_supported_binary():
    paths = [b'C:' + b'\\Users\\Example\\project',
        b'Z:' + b'/Users/Example/project', b'research/' + b'evidence/input',
        b'research\\' + b'evidence\\input']
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for content in paths:
            for name, prefix in [('docs/guide.md', b''),
                    ('resources/assets/demo.gif', b'GIF89a'),
                    ('src/gxw/templates/new.gxw', boundary.CFB_SIGNATURE)]:
                write(root, name, prefix + content)
                problems = boundary.check_file(name, root=root)
                assert problems and any('path' in problem for problem in problems), (name, problems)
                assert not any(content.decode() in problem for problem in problems)


def test_unexpected_binary_invalid_encoding_and_lfs_are_rejected():
    cases = {
        'src/binary.py': b'normal\x00content',
        'docs/non_utf8.md': b'\xff\xfeinvalid',
        'resources/assets/unknown.bin': b'\x89\x80binary',
        'resources/assets/fake.png': b'not an image',
        'src/gxw/templates/fake.gxw': b'not a compound document',
    }
    pointer = b'version https://git-lfs.github.com/spec/v1\noid sha256:' + b'0'*64 + b'\nsize 1\n'
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in cases.items():
            write(root, name, content)
            assert boundary.check_file(name, root=root), name
        for name in ['src/missing.py', 'resources/assets/missing.gif',
                'src/gxw/templates/fx3u.gxw', 'src/gxw/templates/blank-ladder-projects.zip.xz']:
            write(root, name, pointer)
            assert any('LFS pointer' in problem for problem in boundary.check_file(name, root=root)), name
        assert exclusion_reason('src/gxw/templates/manual.pdf')


def test_template_archives_keep_structure_and_content_checks():
    good = template_archive()
    assert boundary.check_archive(good, None) == []
    assert any('57 projects' in problem for problem in boundary.check_archive(good))
    cases = [b'invalid xz', lzma.compress(b'not a zip'),
        template_archive(entries={}), template_archive(models=[{'file': 'projects/missing.gxw'}]),
        template_archive(models=[{}]), template_archive(models=None, entries={
            'projects/q.gxw': boundary.CFB_SIGNATURE, 'reports/private.md': b'private report'}),
        template_archive(entries={'projects/q.gxw': boundary.CFB_SIGNATURE,
            '../escaped.txt': b'private data'}),
        template_archive(entries={'projects/../../escape.gxw': boundary.CFB_SIGNATURE}),
        template_archive(entries={'projects/q.gxw': b'version https://git-lfs.github.com/spec/v1'}),
        template_archive(entries={'projects/q.gxw': boundary.CFB_SIGNATURE +
            b'research/' + b'evidence/input'}),
    ]
    with TemporaryDirectory() as directory:
        root = Path(directory)
        name = 'src/gxw/templates/blank-ladder-projects.zip.xz'
        write(root, name, good)
        assert boundary.check_file(name, root=root) == []
        for data in cases:
            assert boundary.check_archive(data, None)
            write(root, name, data)
            assert boundary.check_file(name, root=root)


def test_required_delivery_files_are_checked_without_source_registration():
    assert {'LICENSE', 'README.md', 'publication/README.md',
        'src/gxw/templates/blank-fbd-projects.zip.xz', 'checks/source_smoke.py'} <= boundary.REQUIRED
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write(root, 'src/unregistered.py')
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        subprocess.run(['git', 'add', '--all'], cwd=root, check=True, capture_output=True)
        for arguments in [(), ('--disk',)]:
            status, output = run_check(root, *arguments, required={'README.md'})
            assert status == 1 and 'missing required source/template: README.md' in output
        write(root, 'README.md')
        assert run_check(root, '--disk', required={'README.md'})[0] == 0
        assert run_check(root, required={'README.md'})[0] == 1
        subprocess.run(['git', 'add', '--', 'README.md'], cwd=root, check=True, capture_output=True)
        assert run_check(root, required={'README.md'})[0] == 0


def test_manual_review_tools_remain_private_without_banning_authored_tools():
    for name in ('tools/fact_new_review.py', 'tools/fbd_fact_review.py', 'tools/build_fact_knowledge.py'):
        assert exclusion_reason(name)
    assert exclusion_reason('tools/runtime_definition_stream.py') is None
    assert exclusion_reason('resources/instructions/mitsubishi/fbd_language_rules.json') is None


CASES = (test_manual_review_tools_remain_private_without_banning_authored_tools,
         test_new_source_and_regressions_need_no_manifest,
    test_research_and_temporary_outputs_are_rejected,
    test_material_folders_do_not_ban_product_modules,
    test_large_source_and_authored_media_are_allowed,
    test_credentials_still_fail_without_printing_values,
    test_export_includes_new_code_and_excludes_materials,
    test_export_keeps_runtime_data_without_research_metadata,
    test_export_is_independent_of_git_ignores,
    test_private_inputs_are_rejected_without_local_ignore_rules,
    test_private_paths_are_checked_in_text_and_supported_binary,
    test_unexpected_binary_invalid_encoding_and_lfs_are_rejected,
    test_template_archives_keep_structure_and_content_checks,
    test_required_delivery_files_are_checked_without_source_registration)

if __name__ == '__main__':
    for case in CASES:
        case()
        print('ok', case.__name__)
