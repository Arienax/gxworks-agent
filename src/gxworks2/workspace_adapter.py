"""Isolated native source saves and checks; no UI or PLC session is used."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

from gxw.models import GXWFormatError
from gxw.native_write import native_source_plan, verify_native_save
from gxw.native_diagnostics import native_validation_source_plan, project_native_validation
from .finder import GXWorks2Finder


class WorkspaceAdapterError(GXWFormatError):
    def __init__(self, message, observation=None):
        super().__init__(message)
        self.observation = observation


class WorkspaceSaveError(WorkspaceAdapterError):
    pass


class WorkspaceValidationError(WorkspaceAdapterError):
    pass


def _clean_native_temp(pid, token):
    """Only our exited child process's explicitly marked native directory."""
    root = (Path(os.environ['LOCALAPPDATA']) / 'MITSUBISHI/SWnDN-GPPW2/Project/DZTempData').resolve()
    target = (root / str(pid)).resolve()
    if target.parent != root or target.name != str(pid):
        raise WorkspaceSaveError('原生临时目录不在本次隔离范围内。')
    marker = target / 'gxworks-agent-owner.txt'
    if not marker.is_file() or marker.read_text(encoding='utf-8-sig') != token:
        return  # Includes a native PID-directory collision: it is not ours.
    shutil.rmtree(target)


def _run_native_adapter(executable, request, root, timeout, error_type):
    token = request['owner_token']
    deadline = time.monotonic() + timeout
    for attempt in range(3):
        try:
            process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8', cwd=root,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            raise error_type('无法启动 GX Works2 离线工作区适配器。') from error
        try:
            try:
                stdout, _ = process.communicate(json.dumps(request, ensure_ascii=False),
                                                timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise error_type('本次原生工作区操作超时，隔离进程已停止。') from None
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            _clean_native_temp(process.pid, token)
        try:
            observation = json.loads(stdout.lstrip('\ufeff'))
        except (ValueError, TypeError):
            raise error_type('原生工作区没有返回完整结果。') from None
        if not isinstance(observation, dict):
            raise error_type('原生工作区返回格式无效。')
        # The native filename is fixed to the child's OS PID. A stale
        # foreign directory is never removed. Retry only the collision
        # reported before any COM/native operation or workspace write.
        if (attempt < 2 and process.returncode and observation.get('status') == 'failed'
                and observation.get('message') == 'native_temporary_directory_collision'
                and observation.get('calls') == []):
            continue
        break
    return process.returncode, observation


class _NativeWorkspaceAdapter:
    """Execute a Core-prepared plan with the installed, version-bound adapter."""

    def __init__(self, executable=None, installation=None):
        default = Path(os.environ.get('LOCALAPPDATA', '')) / 'PLC AI Studio/workspace-adapter/PlcAi.WorkspaceSourceSave.exe'
        self.executable = Path(executable or os.environ.get('GX_WORKSPACE_ADAPTER_EXE') or default).expanduser().resolve()
        gx = GXWorks2Finder().find_executable() if installation is None else None
        self.installation = Path(installation).resolve() if installation else gx.parent.parent if gx else None


class NativeWorkspaceSourceSave(_NativeWorkspaceAdapter):
    """Save a Core-prepared edit and verify the native candidate bytes."""

    def save(self, prepared, *, timeout=60):
        source = native_source_plan(prepared)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('修改此原生工程需要本地 GX Works2 离线工作区适配器；请先构建 workspace adapter。')
        with tempfile.TemporaryDirectory(prefix='gxw-source-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(prepared.baseline)
            token = uuid.uuid4().hex
            request = {'operation': 'save_source', 'protocol_version': 1,
                       'installation': str(self.installation), 'directory': str(root),
                       'owner_token': token, 'source': source}
            returncode, observation = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observation.get('calls')
            if (returncode or observation.get('status') != 'saved'
                    or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
                    or observation.get('native_version') != '1.635.0.1'
                    or observation.get('cpu') != source['cpu'] or observation.get('compile') != 'not_requested'
                    or observation.get('check') != 'not_requested' or not isinstance(calls, list)
                    or any(not isinstance(c, dict) or type(c.get('hresult')) is not int or c['hresult'] < 0
                           or type(c.get('code')) is not int or c['code'] != 0 for c in calls)
                    or not {'OpenProjectEX2', 'Workspace.SetPOUBodyData', 'SaveProject', 'ExportOneFileProject'}.issubset(
                        c.get('operation') for c in calls if isinstance(c, dict))):
                failures = [c for c in calls if isinstance(c, dict) and type(c.get('hresult')) is int
                            and type(c.get('code')) is int and (c['hresult'] < 0 or c['code'])] if isinstance(calls, list) else []
                detail = str(observation.get('message') or 'native_save_failed')
                if failures:
                    last = failures[-1]
                    detail += f" ({last.get('operation')}, 0x{last.get('code', 0) & 0xffffffff:08x})"
                raise WorkspaceSaveError('原生工程保存未完成：' + str(detail), observation)
            output = root / 'candidate.gxw'
            if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                raise WorkspaceSaveError('原生工作区未生成有效的候选文件。', observation)
            try:
                return verify_native_save(prepared, output.read_bytes(), observation)
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生保存输出未通过回读校验：' + str(error), observation) from error


class NativeWorkspaceValidation(_NativeWorkspaceAdapter):
    """Collect original compile/check observations, then interpret them in Core."""

    def validate(self, raw, *, timeout=90):
        source = native_validation_source_plan(raw)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceValidationError('原生检查需要本地 GX Works2 离线工作区适配器；请先构建 workspace adapter。')
        with tempfile.TemporaryDirectory(prefix='gxw-check-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'validate_project', 'protocol_version': 1,
                       'installation': str(self.installation), 'directory': str(root),
                       'owner_token': uuid.uuid4().hex, 'source': source}
            returncode, observation = _run_native_adapter(self.executable, request, root, timeout, WorkspaceValidationError)
            validation = observation.get('validation')
            if not isinstance(validation, dict) or not isinstance(validation.get('events'), list):
                raise WorkspaceValidationError('原生工作区没有返回检查过程记录。', observation)
            if any(not isinstance(row, dict) for row in validation['events']):
                raise WorkspaceValidationError('原生工作区检查过程记录格式无效。', observation)
            try:
                snapshots = _validation_snapshots(root, validation['events'])
                result = project_native_validation(raw, observation, snapshots)
                result['adapter']['exit_code'] = returncode
                if returncode and observation.get('status') != 'failed':
                    raise GXWFormatError('native collector exited unsuccessfully without a failed observation')
                return result
            except GXWFormatError as error:
                raise WorkspaceValidationError('原生工作区检查结果无法绑定本次工程：' + str(error), observation) from error


def _validation_snapshots(root, events):
    """Read only bounded, named collector outputs inside this isolated root."""
    names = {'imported-hdb.bin'}
    for row in events:
        if row.get('operation') == 'NativeBodyRead':
            names.add(row.get('file'))
        elif row.get('operation') == 'Resource':
            names.update(channel.get('file') for channel in row.get('channels', []))
        elif row.get('operation') == 'OwnedCheckInput':
            names.update(item.get('file') for item in row.get('rows', []))
        elif row.get('operation') == 'PublishedResourceCodeCorrespondence':
            names.update(channel.get('published_snapshot') for channel in row.get('channels', []))
    snapshots = {}
    root = root.resolve()
    for name in names:
        if (not isinstance(name, str) or Path(name).name != name or not name.endswith('.bin')
                or (root / name).resolve().parent != root):
            raise GXWFormatError('native snapshot path is outside the isolated workspace')
        path = root / name
        if path.is_file():
            if path.stat().st_size > 30 * 1024 * 1024:
                raise GXWFormatError('native snapshot exceeds the collector bound')
            snapshots[name] = path.read_bytes()
    return snapshots
