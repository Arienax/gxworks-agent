"""Isolated native offline GXW import experiment; never opens a device session."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path('D:/GXWORKS2/DNaviZero')
HASHES = {
    'DataAbsorber/DZDataABS_Workspace.dll': '861c1e25aebb05f6f6ef972f721bcff047c01679b89fe9922abd4743e137676f',
    'DataAbsorber/DZDataABS_ProjectOperation.dll': '74e4cd0fd98215eb36a7c5df7103fdb3cc917d636e036f7f8b774a89b01dcfdb',
    'DataAbsorber/DZDataABS_CompilerAdapter.dll': '4a048e05c189d903d1f8384fd9730c3094d4ef98c795259ba07496fb381b3959',
    'DataAbsorber/DZDataABS_Converter.dll': 'd0ad723422b1ca63f35bfe3a4eea57a13f7c7142395cfc419ffb43ca3a859e6f',
    'DataAbsorber/DZDataABS_DataManager_IEC.dll': '4e6785f6797f4b621b0df3876172e45b43bb8f60a85abe715b1edbba05fc2b6d',
    'DataAbsorber/DZDataABS_SICConverter_IEC.dll': '670fd037852287e4c99de6aa58d19ea0ba9505f10bb46969d2e17d1e54b863f6',
    'DataAbsorber/DZDataABS_Inside.dll': '914a9cd0fc1b82578dfd5ec62d58124bd7851ea6fa8c8fa3ed488241715e776f',
    'DZDataNavigatorServer/DZDataNavigatorServer.dll': 'c8b073657846d8841ffc99f32f1598fc3c1046c451f88d2deef2503f3e5e6a68',
    'DZDataNavigatorServer/_DNAVI.dll': 'e1bf88580a3f22a196fced795cc0a46d218188285a89a09bd5d9fd5a48d3e106',
    'CommunicationAbsorber/DZCommABS.dll': '57619ebc3ffc296f4d032ee904d9ef05a5987fb06833c8c28fcf68818a89e92a',
    'CommunicationAbsorber/DZCommABS_Inside.dll': 'f76188f214f26f963206d17d7dd4431ff4bb08059268fbbf8ca7a612cacbbaea',
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(input_path, output, *, compile=False, snapshot_frontend=False, export_project=False, commit_kind=None, change_sfc=False, program_check=False):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    if os.name != 'nt' or input_path.suffix.lower() != '.gxw':
        raise ValueError('requires Windows and an explicit GXW input')
    if export_project and not compile:
        raise ValueError('native export experiment requires a completed compile')
    if change_sfc and not compile:
        raise ValueError('SFC conversion requires --compile')
    if program_check and not compile:
        raise ValueError('program check requires --compile')
    for name, expected in HASHES.items():
        if digest(NATIVE / name) != expected:
            raise ValueError('uninspected native DLL: ' + name)
    output.mkdir(parents=True, exist_ok=False)
    for name in ['home', 'compiler']:
        (output / name).mkdir()
    shutil.copyfile(input_path, output / 'input.gxw')
    source = output / 'WorkspaceReplayOracle.cs'
    shutil.copyfile(ROOT / 'research/native/WorkspaceReplayOracle.cs', source)
    snapshot_source = output / 'NativeFrontendSnapshot.cs'
    shutil.copyfile(ROOT / 'research/native/NativeFrontendSnapshot.cs', snapshot_source)
    plan = dict(workspace_home=str(output / 'home') + '\\', workspace_name='WS',
                project_name='trace-x3', input_copy=str(output / 'input.gxw'),
                project_type=1, label=-1, open_mode=16, operation_kind=0,
                compile=compile, compiler_work=str(output / 'compiler'),
                build_identifier=0, report_kind=-1, gui_bootstrap=True,
                reserve_project=True, comm_metadata=True, cpu_group=True,
                create_native_temp=True, support_flags=4294967295,
                native_attributes=True, snapshot_frontend=snapshot_frontend, export_project=export_project,
                change_sfc=change_sfc, project_owned_compiler=change_sfc)
    if program_check:
        # GUI all-block/all-five-checkbox observation: resource ID, 0x7fffffff.
        # Resolve native IDs from the copied project; never replay GUI IDs.
        plan['program_check_kind'] = 0x7fffffff
        plan['program_check_collection'] = 7
        plan['project_owned_compiler'] = True
    if export_project:
        plan['compile_all_after'] = 1
        plan['project_compile_status'] = 0x200000
        # Observed GUI full-build postprocessing: resources and their tasks,
        # user POUs, then global labels. Native names confirm these roles.
        # IDs are resolved in the live copied project.
        # This is an experimental native call plan, not a general status rule.
        plan['compile_status_collections'] = [
            dict(kind=7, child_depth=1, value=0),
            dict(kind=25, child_depth=0, value=0),
            dict(kind=13, child_depth=0, value=0),
        ]
        plan['preserve_project_name'] = True
        plan['native_library_directory'] = str(NATIVE / 'DataAbsorber')
        # Recreate the owned backend without LoadData: otherwise an unchanged
        # compile-status field can retain stale internal state. The R.gxw
        # counterexample compiled X2 as AND instead of ANI until this reset.
        plan['reset_compiler'] = True
        plan['fresh_compiler_state'] = True
        # GUI save clears only these masks, not the entire status words.
        plan['save_status_masks'] = [dict(index=2, value=0, mask=1), dict(index=0, value=0, mask=2)]
        if change_sfc:
            # Observed simple FX SFC conversion uses the owned adapter's
            # ChangeSFCProgram / GetProgressOfSFCChange, not IEC Build.
            # The converter updates object status itself. Replacing its backend
            # with an IEC compiler or applying structured status masks is not
            # part of that observed sequence.
            plan.pop('compile_all_after')
            plan.pop('project_compile_status')
            plan['compile_status_collections'] = []
            plan['reset_compiler'] = False
            plan['fresh_compiler_state'] = False
    if commit_kind is not None:
        if not export_project:
            raise ValueError('commit probe requires isolated native export')
        plan['commit_kind'] = commit_kind
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    request = dict(input_sha256=digest(input_path), source_sha256=digest(source), snapshot_source_sha256=digest(snapshot_source), native=HASHES,
                   scope='offline import of a copied GXW into a new experiment directory')
    (output / 'request.json').write_text(json.dumps(request, indent=2) + '\n')
    exe = output / 'WorkspaceReplayOracle.exe'
    csc = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework/v4.0.30319/csc.exe'
    result = subprocess.run([str(csc), '/nologo', '/platform:x86', '/r:System.Web.Extensions.dll',
                             '/r:System.Windows.Forms.dll', '/out:' + str(exe), str(source), str(snapshot_source)],
                            capture_output=True, timeout=20)
    (output / 'build.stdout.txt').write_bytes(result.stdout)
    (output / 'build.stderr.txt').write_bytes(result.stderr)
    result.check_returncode()
    return exe


def run(input_path, output, *, compile=False, snapshot_frontend=False, export_project=False, commit_kind=None, change_sfc=False, program_check=False):
    output = Path(output).resolve()
    exe = prepare(input_path, output, compile=compile, snapshot_frontend=snapshot_frontend, export_project=export_project, commit_kind=commit_kind, change_sfc=change_sfc, program_check=program_check)
    with (output / 'process.stdout.txt').open('wb') as stdout, (output / 'process.stderr.txt').open('wb') as stderr:
        try:
            for attempt in range(5):
                result = subprocess.run([str(exe), str(output / 'plan.json')], cwd=output,
                                        stdout=stdout, stderr=stderr, timeout=50)
                if result.returncode != 5:
                    break
                (output / 'native-events.jsonl').rename(output / f'startup-collision-{attempt}.jsonl')
            outcome = dict(returncode=result.returncode, timed_out=False, startup_attempts=attempt + 1)
        except subprocess.TimeoutExpired:
            outcome = dict(returncode=None, timed_out=True)
    outcome['input_copy_unchanged'] = digest(output / 'input.gxw') == digest(input_path)
    outcome['compile_operation'] = 'ChangeSFCProgram' if change_sfc else 'Build' if compile else None
    outcome['outputs'] = {p.name: dict(size=p.stat().st_size, sha256=digest(p))
                           for p in output.glob('pcode-*.bin')}
    exported = output / 'native-saved.gxw'
    if exported.is_file():
        outcome['native_export'] = dict(file=exported.name, size=exported.stat().st_size, sha256=digest(exported))
    event_file = output / 'native-events.jsonl'
    events = [json.loads(line) for line in event_file.read_text(encoding='utf-8').splitlines()] if event_file.exists() else []
    diagnostics = {}
    for event in events:
        for row in event.get('reports', []):
            if row['kind'] not in (2, 3):
                continue
            portable = {k: v for k, v in row.items() if k != 'raw_base64'}
            portable['phase'] = event['operation']
            diagnostics[json.dumps(portable, sort_keys=True)] = portable
    outcome['diagnostics'] = list(diagnostics.values())
    outcome['project_attributes'] = next((e for e in events if e.get('operation') == 'NativeProjectAttributes'), None)
    outcome['open_succeeded'] = any(e.get('operation') == 'OpenProjectEX2' and e.get('hresult') == e.get('code') == 0 for e in events)
    outcome['compile_completed'] = any(e.get('operation') == 'Progress' and e.get('percent') == 100 for e in events)
    outcome['program_check_requested'] = program_check
    outcome['program_check_completed'] = any(e.get('operation') == 'ProgramCheckCompleted' for e in events)
    outcome['compiler_rejected'] = any(d['kind'] == 2 for d in diagnostics.values())
    outcome['last_event'] = events[-1] if events else None
    # Relocate only the process directory this helper created and marked. Never
    # reuse pre-existing vendor temp data or touch another process's directory.
    temporary = next((Path(e['path']) for e in events if e.get('operation') == 'NativeTemporaryDirectory'), None)
    if temporary is not None:
        vendor_root = (Path(os.environ['LOCALAPPDATA']) / 'MITSUBISHI/SWnDN-GPPW2/Project/DZTempData').resolve()
        temporary = temporary.resolve()
        marker = temporary / 'gxw-research-owner.txt'
        if temporary.parent != vendor_root or not temporary.name.isdigit() or not marker.is_file() or marker.read_text() != str(output):
            raise ValueError('native temporary directory ownership mismatch')
        shutil.move(str(temporary), str(output / 'native-temp'))
        outcome['native_temp_preserved'] = 'native-temp'
    (output / 'outcome.json').write_text(json.dumps(outcome, indent=2) + '\n')
    return outcome


def trace_prepared(output, script, *, timeout=45):
    """Run only this newly built isolated helper under read-only instrumentation."""
    import sys
    sys.path.insert(0, str(ROOT / 'research/experiments/re-tools'))
    import frida
    output = Path(output).resolve()
    script = Path(script).resolve()
    snapshot = output / 'Trace.js'
    if script != snapshot:
        shutil.copyfile(script, snapshot)
    device = frida.get_local_device()
    pid = device.spawn([str(output / 'WorkspaceReplayOracle.exe'), str(output / 'plan.json')],
                       cwd=str(output), stdio='pipe')
    session = device.attach(pid)
    ended = []
    session.on('detached', lambda *args: ended.append(str(args)))
    with (output / 'events.jsonl').open('w', encoding='utf-8') as events:
        agent = session.create_script(snapshot.read_text(encoding='utf-8'))
        def record(message, data):
            events.write(json.dumps(message, ensure_ascii=False) + '\n')
            events.flush()
        agent.on('message', record)
        agent.load()
        device.resume(pid)
        deadline = time.monotonic() + timeout
        while not ended and time.monotonic() < deadline:
            time.sleep(.1)
        timed_out = not ended
        if timed_out:
            device.kill(pid)
        result = dict(pid=pid, timed_out=timed_out, detached=ended)
        (output / 'trace-outcome.json').write_text(json.dumps(result, indent=2) + '\n')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--compile', action='store_true')
    parser.add_argument('--snapshot-frontend', action='store_true')
    parser.add_argument('--export-project', action='store_true')
    parser.add_argument('--change-sfc', action='store_true', help='experimental native SFC graph conversion instead of generic Build')
    parser.add_argument('--program-check', action='store_true', help='run native resource program checks after conversion and before export')
    parser.add_argument('--commit-kind', type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output, compile=args.compile, snapshot_frontend=args.snapshot_frontend, export_project=args.export_project, commit_kind=args.commit_kind, change_sfc=args.change_sfc, program_check=args.program_check), indent=2))
