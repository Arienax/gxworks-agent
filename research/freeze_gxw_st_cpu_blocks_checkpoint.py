"""Snapshot completed CPU/block/loop research without native reruns or hashes."""
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'research/experiments/sfc-graph-20260926/public-corpus-discovery'
GROUPS = [
    'st-keypad-whole-source-discovery-20260930',
    'fx-keypad-intrinsic-boundary-20260930',
    'fx-keypad-lowering-trace-20260930',
    'fx-keypad-lowering-trace-v2-20260930',
    'fx-st-conditional-profile-20260930',
    'q-st-conditional-profile-20260930',
    'q-st-conditional-profile-v2-20260930',
    'fx-st-cpu-profile-origin-20260930',
    'fx-st-cpu-profile-origin-v2-20260930',
    'q-st-cpu-profile-origin-20260930',
    'fx-keypad-source-forward-20260930',
    'fx-st-block-temporary-discovery-20260930',
    'q-st-block-temporary-discovery-20260930',
    'fx-st-block-temporary-pools-20260930',
    'fx-st-loop-shapes-discovery-20260930',
    'q-st-loop-shapes-discovery-20260930',
]


def main():
    output = ROOT / 'research/results/gxw-st-cpu-blocks-20260930'
    public = ROOT / 'research/evidence/gxw-st-cpu-blocks-20260930.zip'
    local = P / 'gxw-st-cpu-blocks-local-20260930.zip'
    for path in (output, public, local):
        if path.exists():
            raise FileExistsError(path)
    rows = []
    files = []
    for group in GROUPS:
        directory = P / group
        comparison = directory / 'comparison.json'
        if comparison.exists():
            for row in json.loads(comparison.read_text(encoding='utf-8-sig')):
                rows.append(dict(group=group, observation=row))
        for path in directory.rglob('*'):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if any(part in ('home', 'compiler', 'compiler_DZComp', '__pycache__') for part in relative.parts):
                continue
            if path.suffix.lower() in ('.exe', '.dll', '.pdb', '.pyc', '.zip'):
                continue
            files.append(path)
    output.mkdir()
    manifest = dict(
        created_utc=datetime.now(timezone.utc).isoformat(),
        base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        scope='Offline GX Works2 15.50 compiler observations, Q03UDV and FX3U/FX3UC; source model remains bounded research code.',
        findings=[
            'CPU predicate selection explains distinct conditional delegates; measured selectors 1 (FX) and 2 (Q). Other dispatch entries are static observations only.',
            'Two indexed CONCAT expressions in one flat IF reserve two word slots before condition generation and hold them to block end. Controlled FX one-word pool rejects; two-word pool accepts.',
            'Six native FOR/WHILE outputs match the current source model on Q and FX. These are post-observation fits, not prospective predictions.',
            'Original FX WORD_TO_STR is rejected; literal and decimal substitutions are separate experiments without semantic equivalence claims.',
            'Project allocation record count 10 is recognized; observed extended range bytes agree with the native getter.',
        ],
        observations=rows,
        retained_failures='Preparation errors, instrumentation failures and frontend rejections remain separate in the selected directories.',
        preservation='Original before-native files and prior checkpoints are unchanged. No native rerun or hash verification.',
        archives=dict(public=str(public.relative_to(ROOT)), local=str(local.relative_to(ROOT))),
        limitations=['No PLC or simulator execution; no production-safe promotion.', 'Local archive contains third-party projects/source; redistribution rights are not established.', 'Saved native event files may contain preexisting fingerprint metadata; no new hashes are computed.'],
    )
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output / 'working-diff.patch').write_bytes(subprocess.check_output(['git', 'diff', '--', 'research/probe_gxw_parameters.py'], cwd=ROOT))
    sources = list(P.glob('*.py')) + list(P.glob('*.js'))
    sources += [ROOT / 'research/probe_gxw_parameters.py', Path(__file__), ROOT / 'LICENSE', ROOT / 'AGENTS.md']
    sources += list((ROOT / 'src/gxw').rglob('*.py'))
    sources += list(output.iterdir())
    for target, selected in ((public, sources), (local, files)):
        entries = []
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(set(selected)):
                name = path.relative_to(ROOT).as_posix()
                archive.write(path, name)
                entries.append(dict(path=name, bytes=path.stat().st_size))
            archive.writestr('checkpoint-files.json', json.dumps(entries, ensure_ascii=False, indent=2))
        print(json.dumps(dict(path=str(target), files=len(entries), bytes=target.stat().st_size)))
    print(json.dumps(dict(completed_observations=len(rows), selected_groups=len(GROUPS))))


if __name__ == '__main__':
    main()
