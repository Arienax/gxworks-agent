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
from gxw.native_write import (native_program_task_plan, native_scope_creation_plan, native_source_creation_plan, native_source_plan,
                              verify_native_program_task_binding, verify_native_save, verify_native_scope_creation,
                              verify_native_source_creation)
from gxw.native_diagnostics import native_validation_source_plan, project_native_validation
from .finder import GXWorks2Finder
from shared.paths import bundled_executable
from .workspace_protocol import WORKSPACE_PROTOCOL, workspace_business_request


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
    wire_request = workspace_business_request(request)
    if request['operation'] not in ('validate_project','export_library','read_sfc_graph'):
        from gxw.project_metadata import gui_application_save_plan
        from base64 import b64encode
        gui = gui_application_save_plan((root/'input.gxw').read_bytes(), request['source'])
        if gui is not None:
            wire_request['change']['gui_application'] = b64encode(gui).decode('ascii')
    deadline = None if timeout is None else time.monotonic() + timeout
    for attempt in range(3):
        try:
            process = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding='utf-8', cwd=root,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as error:
            raise error_type('无法启动 GX Works2 离线工作区适配器。') from error
        try:
            try:
                stdout, _ = process.communicate(json.dumps(wire_request, ensure_ascii=False),
                                                timeout=None if deadline is None else max(0, deadline - time.monotonic()))
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
        if type(observation.get('protocol_version')) is not int or observation['protocol_version'] != WORKSPACE_PROTOCOL:
            raise error_type('原生工作区协议版本不匹配，请更新运行组件。', observation)
        observation['protocol_version'] = 1  # Internal evidence schema; not a wire fallback.
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
    """Execute validated engineering objects with the version-bound runtime."""

    def __init__(self, executable=None, installation=None):
        default = Path(os.environ.get('LOCALAPPDATA', '')) / 'PLC AI Studio/workspace-adapter/PlcAi.WorkspaceSourceSave.exe'
        self.executable = Path(executable or os.environ.get('GX_WORKSPACE_ADAPTER_EXE')
                               or bundled_executable('workspace-adapter', 'PlcAi.WorkspaceSourceSave.exe')
                               or default).expanduser().resolve()
        gx = GXWorks2Finder().find_executable() if installation is None else None
        self.installation = Path(installation).resolve() if installation else gx.parent.parent if gx else None


class NativeWorkspaceSourceSave(_NativeWorkspaceAdapter):
    """Save a Core-prepared edit and verify the native candidate bytes."""

    def compile_project(self, raw, *, timeout=None):
        from gxw.native_write import verify_native_compilation_save
        checker = NativeWorkspaceValidation(executable=self.executable, installation=self.installation)
        saved, validation, observation, snapshots = checker.validate(raw, timeout=timeout, _save_output=True)
        cold = checker.validate(saved, timeout=timeout, inspection={'assignments': [], 'locations': [], 'saved_code': True})
        result = verify_native_compilation_save(raw, saved, validation, cold, observation)
        import base64
        result.report['native_evidence'] = {'observation': observation,
            'snapshots': {name: base64.b64encode(value).decode('ascii') for name,value in snapshots.items()}}
        return result

    def author(self, raw, command, *, timeout=None):
        from gxw.fb_authoring import authoring_plan, verify_authoring
        from gxw.st_program import (ST_PROGRAM_OPERATIONS, background_st_program_plan,
                                    verify_background_st_program_save)
        ordinary_st = command.get('operation') in ST_PROGRAM_OPERATIONS
        planner = background_st_program_plan if ordinary_st else authoring_plan
        verifier = verify_background_st_program_save if ordinary_st else verify_authoring
        plan, _, _, _ = planner(raw, command)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('新建类型或编写 ST/FB 需要本地 GX Works2 离线工作区适配器。')
        with tempfile.TemporaryDirectory(prefix='gxw-definition-') as directory:
            root = Path(directory); (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'author_definition', 'protocol_version': 1, 'installation': str(self.installation),
                       'directory': str(root), 'owner_token': uuid.uuid4().hex, 'source': plan}
            code, observed = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observed.get('calls')
            required = {'OpenProjectEX2', 'SaveProject', 'ExportOneFileProject'}
            required.add('Definition.RemoveEmptyProgram' if command['operation']=='remove_empty_program' else 'Definition.ConfigureProject' if command['operation']=='configure_project'
                else 'Definition.GetGlobalTable' if command['operation'] in ('edit_global_labels','create_global_labels')
                else 'Definition.EditPOULabels' if command['operation']=='edit_pou_labels'
                else 'Definition.GetOwner' if command['operation'] in ('edit_st_fb', 'edit_st_program') else 'Definition.Create')
            if command['operation'] not in ('create_structure','edit_global_labels','create_global_labels','edit_pou_labels','configure_project','remove_empty_program'):
                required.add('Definition.SetSTBody')
            if (code or observed.get('status') != 'saved' or observed.get('protocol_version') != 1
                    or observed.get('native_version') != '1.635.0.1' or observed.get('cpu') != plan['cpu']
                    or observed.get('compile') != 'not_requested' or observed.get('check') != 'not_requested'
                    or not isinstance(calls, list) or any(not isinstance(row, dict) or row.get('hresult', -1) < 0
                        or row.get('code') != 0 for row in calls)
                    or not required.issubset(row.get('operation') for row in calls)):
                raise WorkspaceSaveError('原生定义写入未完成：' + str(observed.get('message') or 'definition_write_failed'), observed)
            output = root / 'candidate.gxw'
            if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                raise WorkspaceSaveError('原生定义写入缺少有效候选文件。', observed)
            try:
                return verifier(raw, command, output.read_bytes(), observed)
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生定义回读不一致：' + str(error), observed) from error

    def import_library(self, raw, donor, library_name, *, timeout=None):
        """Export a named donor library, import its definitions into a copy."""
        from gxw.library_transfer import library_transfer_plan, verify_library_import
        plan = library_transfer_plan(raw, donor, library_name)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('导入库需要本地 GX Works2 离线工作区适配器。')
        stages = []
        with tempfile.TemporaryDirectory(prefix='gxw-library-') as directory:
            base = Path(directory)
            for operation, data, cpu, codepage in (
                    ('export_library', donor, plan['source_cpu'], plan['source_codepage']),
                    ('import_library', raw, plan['cpu'], plan['codepage'])):
                root = base / operation
                root.mkdir(); (root / 'input.gxw').write_bytes(data)
                if operation == 'import_library':
                    (root / 'library.sul').write_bytes((base / 'export_library/library.sul').read_bytes())
                request = {'operation': operation, 'protocol_version': 1, 'installation': str(self.installation),
                           'directory': str(root), 'owner_token': uuid.uuid4().hex,
                           'source': {'cpu': cpu, 'codepage': codepage, 'library_name': plan['library_name']}}
                code, observed = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
                calls = observed.get('calls')
                required = ({'Library.SetOwnedLibPath', 'Library.SaveUserLibrary'} if operation == 'export_library'
                            else {'Library.CreateUserLibrary', 'SaveProject', 'ExportOneFileProject'})
                if (code or observed.get('status') != ('exported' if operation == 'export_library' else 'saved')
                        or observed.get('native_version') != '1.635.0.1' or observed.get('protocol_version') != 1
                        or observed.get('cpu') != cpu or observed.get('compile') != 'not_requested'
                        or observed.get('check') != 'not_requested' or not isinstance(calls, list)
                        or any(not isinstance(row, dict) or type(row.get('hresult')) is not int or row['hresult'] < 0
                               or type(row.get('code')) is not int or row['code'] != 0 for row in calls)
                        or not {'OpenProjectEX2', *required}.issubset(row.get('operation') for row in calls)):
                    raise WorkspaceSaveError('原生库操作未完成：' + str(observed.get('message') or operation), observed)
                if operation == 'export_library' and any(row.get('operation') in ('SaveProject', 'ExportOneFileProject') for row in calls):
                    raise WorkspaceSaveError('库导出意外保存了来源工程。', observed)
                output = root / ('library.sul' if operation == 'export_library' else 'candidate.gxw')
                if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                    raise WorkspaceSaveError('原生库操作缺少有效输出。', observed)
                stages.append(observed)
            try:
                return verify_library_import(raw, donor, library_name, output.read_bytes(), {'stages': stages})
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生库导入未通过回读校验：' + str(error), {'stages': stages}) from error

    def read_sfc_graph(self, raw, program_name=None, *, timeout=None):
        """Read original cells without changing/saving project source."""
        from gxw.native_write import _native_sfc_graph_output
        from gxw.sfc_pou import read_sfc_project_context, native_sfc_graph_profile
        from gxw.project_writer import prepare_sfc_project_write

        context = read_sfc_project_context(raw, program_name)
        graph_profile = native_sfc_graph_profile(context.project['cpu'])
        if graph_profile is None or context.source.graph_tokens is None:
            raise WorkspaceSaveError('此 SFC 格点读取配置尚未对当前型号或图形存储方式验证。')
        prepared = prepare_sfc_project_write(raw, context.program_name, context.source, {})
        source = native_source_plan(prepared)
        source['graph_profile'] = graph_profile
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('原生格点读取需要本地 GX Works2 离线工作区适配器。')
        with tempfile.TemporaryDirectory(prefix='gxw-sfc-read-') as directory:
            root = Path(directory); (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'read_sfc_graph', 'protocol_version': 1, 'installation': str(self.installation),
                       'directory': str(root), 'owner_token': uuid.uuid4().hex, 'source': source}
            code, observed = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observed.get('calls')
            forbidden = {'SaveProject', 'ExportOneFileProject', 'Workspace.SetPOUBodyData', 'Workspace.SetSfcGraphBody',
                'Sfc.WriteStepAttribute', 'Sfc.WriteStepSymbol', 'Workspace.CreateRetainedSfcZoom',
                'Workspace.NameRetainedSfcAction', 'Workspace.NameRetainedSfcZoom', 'Workspace.BindRetainedSfcZoomName',
                'Workspace.WriteRetainedSfcBody', 'Workspace.MarkRetainedSfcEdited', 'Workspace.MarkRetainedSfcCompileChanged'}
            required = {'OpenProjectEX2', 'Sfc.ConvertPcodeToSFC', 'Sfc.GetCmXY', 'Workspace.GetPOUBodyData'}
            required.add('Sfc.SAPOpenFX' if graph_profile['family'] == 'fx' else 'Sfc.SAPOpen')
            if (code or observed.get('status') != 'observed' or observed.get('native_version') != '1.635.0.1'
                    or observed.get('protocol_version') != 1 or observed.get('cpu') != source['cpu']
                    or observed.get('compile') != 'not_requested' or observed.get('check') != 'not_requested'
                    or not isinstance(calls, list) or any(not isinstance(row, dict) or row.get('hresult', -1) < 0 or row.get('code') != 0 for row in calls)
                    or not required.issubset(row.get('operation') for row in calls)
                    or forbidden.intersection(row.get('operation') for row in calls)):
                raise WorkspaceSaveError('原生 SFC 格点读取未完成：' + str(observed.get('message') or 'native_graph_read_failed'), observed)
            body = root / 'sfc-graph-after.bin'
            if not body.is_file() or not 0 < body.stat().st_size <= 8 * 1024 * 1024:
                raise WorkspaceSaveError('原生格点读取缺少当前正文。', observed)
            data = body.read_bytes()
            region = context.source.layout['graph']
            if data != context.source.raw[region['offset']:region['offset'] + region['size']]:
                raise WorkspaceSaveError('原生格点读取改变了当前图形正文。', observed)
            _source, graph = _native_sfc_graph_output(prepared, {**source, 'graph_edits': []}, data, observed)
            graph.update(logical_name=context.program_name, source_body_hex=data.hex(), native_workspace=observed)
            return graph

    def create(self, raw, name, *, pou_kind='function_block', timeout=None):
        """Create a native empty FBD Program or FB and verify its selected type."""
        source = native_source_creation_plan(raw, name, pou_kind=pou_kind)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('新建原生 POU 需要本地 GX Works2 离线工作区适配器；请先构建 workspace adapter。')
        with tempfile.TemporaryDirectory(prefix='gxw-source-create-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'create_source', 'protocol_version': 1,
                       'installation': str(self.installation), 'directory': str(root),
                       'owner_token': uuid.uuid4().hex, 'source': source}
            returncode, observation = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observation.get('calls')
            if (returncode or observation.get('status') != 'saved'
                    or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
                    or observation.get('native_version') != '1.635.0.1' or observation.get('cpu') != source['cpu']
                    or observation.get('compile') != 'not_requested' or observation.get('check') != 'not_requested'
                    or not isinstance(calls, list)
                    or any(not isinstance(c, dict) or type(c.get('hresult')) is not int or c['hresult'] < 0
                           or type(c.get('code')) is not int or c['code'] != 0 for c in calls)
                    or not {'OpenProjectEX2', 'Workspace.GetPOUCollection', 'Workspace.CreateData', 'SaveProject',
                            'ExportOneFileProject'}.issubset(c.get('operation') for c in calls if isinstance(c, dict))):
                raise WorkspaceSaveError('原生 POU 创建未完成：' + str(observation.get('message') or 'native_creation_failed'), observation)
            output = root / 'candidate.gxw'
            if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                raise WorkspaceSaveError('原生工作区未生成有效的候选文件。', observation)
            try:
                return verify_native_source_creation(raw, name, output.read_bytes(), observation, pou_kind=pou_kind)
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生新建输出未通过回读校验：' + str(error), observation) from error

    def create_resource(self, raw, name, *, timeout=None):
        """Create one original native resource in the current project."""
        return self._create_scope(raw, name, scope_kind='resource', timeout=timeout)

    def create_task(self, raw, name, *, resource, timeout=None):
        """Create an empty task, retaining its actual original configuration."""
        return self._create_scope(raw, name, scope_kind='task', resource=resource, timeout=timeout)

    def _create_scope(self, raw, name, *, scope_kind, resource=None, timeout=None):
        source = native_scope_creation_plan(raw, name, scope_kind=scope_kind, resource=resource)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('创建原生资源或任务需要本地 GX Works2 离线工作区适配器。')
        with tempfile.TemporaryDirectory(prefix='gxw-scope-create-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'create_' + scope_kind + '_scope', 'protocol_version': 1,
                       'installation': str(self.installation), 'directory': str(root),
                       'owner_token': uuid.uuid4().hex, 'source': source}
            returncode, observation = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observation.get('calls')
            required = {'OpenProjectEX2', 'Workspace.GetPOUCollection', 'Workspace.GetResourceCollection',
                        'Workspace.CreateTaskScopeData' if scope_kind == 'task' else 'Workspace.CreateResourceScopeData',
                        'SaveProject', 'ExportOneFileProject'}
            if scope_kind == 'task':
                required.update(('Workspace.GetTaskCollection', 'Workspace.SetTaskScopeName'))
            if scope_kind == 'task' or source['tasks']:
                required.update(('Workspace.GetTaskEventName', 'Workspace.GetTaskInterval', 'Workspace.GetTaskPriority'))
            if (returncode or observation.get('status') != 'saved'
                    or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
                    or observation.get('native_version') != '1.635.0.1' or observation.get('cpu') != source['cpu']
                    or observation.get('compile') != 'not_requested' or observation.get('check') != 'not_requested'
                    or not isinstance(calls, list)
                    or any(not isinstance(c, dict) or type(c.get('hresult')) is not int or c['hresult'] < 0
                           or type(c.get('code')) is not int or c['code'] != 0 for c in calls)
                    or not required.issubset(c.get('operation') for c in calls if isinstance(c, dict))):
                raise WorkspaceSaveError('原生资源或任务创建未完成：' + str(
                    observation.get('message') or 'native_scope_creation_failed'), observation)
            output = root / 'candidate.gxw'
            if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                raise WorkspaceSaveError('原生工作区未生成有效的候选文件。', observation)
            try:
                result = verify_native_scope_creation(raw, name, output.read_bytes(), observation,
                                                      scope_kind=scope_kind, resource=resource)
                if result.report['native_task_configuration'] not in ('current', 'not_applicable'):
                    raise GXWFormatError('original task configuration getters are missing')
                return result
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生资源或任务创建输出未通过回读校验：' + str(error), observation) from error

    def bind_program(self, raw, program, *, resource, task, timeout=None):
        """Append one current ordinary Program, preserving the selected task."""
        source = native_program_task_plan(raw, program, resource=resource, task=task)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceSaveError('注册原生 Program 需要本地 GX Works2 离线工作区适配器。')
        with tempfile.TemporaryDirectory(prefix='gxw-task-bind-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'bind_program_task', 'protocol_version': 1,
                       'installation': str(self.installation), 'directory': str(root),
                       'owner_token': uuid.uuid4().hex, 'source': source}
            returncode, observation = _run_native_adapter(self.executable, request, root, timeout, WorkspaceSaveError)
            calls = observation.get('calls')
            if (returncode or observation.get('status') != 'saved'
                    or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
                    or observation.get('native_version') != '1.635.0.1' or observation.get('cpu') != source['cpu']
                    or observation.get('compile') != 'not_requested' or observation.get('check') != 'not_requested'
                    or not isinstance(calls, list)
                    or any(not isinstance(c, dict) or type(c.get('hresult')) is not int or c['hresult'] < 0
                           or type(c.get('code')) is not int or c['code'] != 0 for c in calls)
                    or not {'OpenProjectEX2', 'Workspace.GetTaskEventName', 'Workspace.GetTaskInterval',
                            'Workspace.GetTaskPriority', 'Workspace.CreateTaskElement', 'Workspace.SetTaskElementName',
                            'Workspace.SetTaskElementLine', 'SaveProject', 'ExportOneFileProject'}.issubset(
                                c.get('operation') for c in calls if isinstance(c, dict))):
                raise WorkspaceSaveError('原生 Program 注册未完成：' + str(
                    observation.get('message') or 'native_task_binding_failed'), observation)
            output = root / 'candidate.gxw'
            if not output.is_file() or not 0 < output.stat().st_size <= 30 * 1024 * 1024:
                raise WorkspaceSaveError('原生工作区未生成有效的候选文件。', observation)
            try:
                result = verify_native_program_task_binding(raw, program, output.read_bytes(), observation,
                                                           resource=resource, task=task)
                if result.report['native_task_configuration'] != 'current':
                    raise GXWFormatError('original task configuration getters are missing')
                return result
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生 Program 注册输出未通过回读校验：' + str(error), observation) from error

    def save(self, prepared, *, timeout=None, graph_edits=None):
        source = native_source_plan(prepared, graph_edits=graph_edits)
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
            required_calls = {'OpenProjectEX2', 'SaveProject', 'ExportOneFileProject'}
            if graph_edits is not None:
                required_calls.update(('Sfc.ConvertPcodeToSFC', 'Sfc.ConvertSFCToPcode',
                                       'Sfc.CheckSFCError', 'Workspace.SetSfcGraphBody'))
                if any(row.get('kind') not in ('jump', 'rebuild') for row in source['graph_edits']):
                    required_calls.add('Sfc.WriteStepAttribute')
                if any(row.get('kind') == 'jump' for row in source['graph_edits']):
                    required_calls.add('Sfc.WriteStepSymbol')
                if any(row.get('kind') == 'rebuild' for row in source['graph_edits']):
                    required_calls.update(('Sfc.ClearSFC', 'Sfc.WriteStepSymbol', 'Sfc.WriteTrSymbol', 'Sfc.SAPOpen'))
            if source.get('program_kind') != 240 or source['before_children'] != source['after_children']:
                required_calls.add('Workspace.SetPOUBodyData')
            if (returncode or observation.get('status') != 'saved'
                    or type(observation.get('protocol_version')) is not int or observation['protocol_version'] != 1
                    or observation.get('native_version') != '1.635.0.1'
                    or observation.get('cpu') != source['cpu'] or observation.get('compile') != 'not_requested'
                    or observation.get('check') != 'not_requested' or not isinstance(calls, list)
                    or any(not isinstance(c, dict) or type(c.get('hresult')) is not int or c['hresult'] < 0
                           or type(c.get('code')) is not int or c['code'] != 0 for c in calls)
                    or not required_calls.issubset(
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
                if graph_edits is not None:
                    graph_output = root / 'sfc-graph-after.bin'
                    if not graph_output.is_file() or not 0 < graph_output.stat().st_size <= 8 * 1024 * 1024:
                        raise GXWFormatError('native graph converter did not return its current source readback')
                    return verify_native_save(prepared, output.read_bytes(), observation,
                                              graph_edits=graph_edits, graph_body=graph_output.read_bytes())
                return verify_native_save(prepared, output.read_bytes(), observation)
            except GXWFormatError as error:
                raise WorkspaceSaveError('原生保存输出未通过回读校验：' + str(error), observation) from error


class NativeWorkspaceValidation(_NativeWorkspaceAdapter):
    """Collect original compile/check observations, then interpret them in Core."""

    def validate(self, raw, *, timeout=90, inspection=None, _save_output=False):
        source = native_validation_source_plan(raw)
        if inspection is not None:
            from gxw.native_diagnostics import native_inspection_queries
            source['inspection'] = native_inspection_queries(raw, inspection)
        if os.name != 'nt' or not self.executable.is_file() or self.installation is None:
            raise WorkspaceValidationError('原生检查需要本地 GX Works2 离线工作区适配器；请先构建 workspace adapter。')
        with tempfile.TemporaryDirectory(prefix='gxw-check-') as directory:
            root = Path(directory)
            (root / 'input.gxw').write_bytes(raw)
            request = {'operation': 'compile_save_project' if _save_output else 'validate_project', 'protocol_version': 1,
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
                from gxw.project_metadata import project_gui_configuration
                result['gui_configuration'] = project_gui_configuration(raw)
                if inspection is not None:
                    from gxw.native_diagnostics import project_native_inspection
                    result['inspection'] = project_native_inspection(raw, observation, snapshots, result, source['inspection'])
                result['adapter']['exit_code'] = returncode
                if returncode and observation.get('status') != 'failed':
                    raise GXWFormatError('native collector exited unsuccessfully without a failed observation')
                if _save_output:
                    output = root/'candidate.gxw'
                    if returncode or observation.get('status') != 'observed' or not output.is_file():
                        raise WorkspaceSaveError('原生编译保存未产生工程：' + str(observation.get('message') or result), observation)
                    return output.read_bytes(), result, observation, snapshots
                return result
            except GXWFormatError as error:
                raise WorkspaceValidationError('原生工作区检查结果无法绑定本次工程：' + str(error), observation) from error


def _validation_snapshots(root, events):
    """Read only bounded, named collector outputs inside this isolated root."""
    names = {'imported-hdb.bin'}
    for row in events:
        if row.get('operation') in ('NativeBodyRead', 'NativeSfcChildRead'):
            names.add(row.get('file'))
        elif row.get('operation') == 'Resource':
            names.update(channel.get('file') for channel in row.get('channels', []))
        elif row.get('operation') == 'SavedResourceCode':
            names.update(channel.get('file') for channel in row.get('channels', []))
        elif row.get('operation') == 'OwnedCheckInput':
            names.update(item.get('file') for item in row.get('rows', []))
        elif row.get('operation') == 'PublishedResourceCodeCorrespondence':
            names.update(channel.get('published_snapshot') for channel in row.get('channels', []))
        elif row.get('operation') == 'NativeCodeLexicalRead':
            names.update(row[key] for key in ('input_file', 'output_file') if key in row)
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
