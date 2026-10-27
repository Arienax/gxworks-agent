# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Bind verified native output to the selected project version."""
from __future__ import annotations
import copy
import tempfile

class NativeCompletion:
    """Bind completed native transactions to durable project versions."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 proposals,
                 execution,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.proposals = proposals
        self.execution = execution
        self.store = projects.store

    def complete(self, payload, approved_id, result):
        if (payload.get('plan') or {}).get('mode') == 'live':
            return self.complete_live(payload, approved_id, result)
        from application.native_engineering import normalize_native_plan, verified_native_data, native_engineering_summary
        receipt = result.get('_native_result') if isinstance(result, dict) else None
        if not isinstance(receipt, dict):
            summary = native_engineering_summary(result)
            summary.update(status='interrupted', passed=False,
                message='原生工程会话未完成，未创建活动版本。')
            return summary
        project_id, base_id = payload['project_id'], payload['version_id']
        version = self.projects.raw_version(project_id, base_id)
        from application.execution import native_saved_cpu
        cpu = native_saved_cpu(version, self.store.get_project(project_id))
        plan = normalize_native_plan(payload.get('plan'), plc_model=cpu)
        try:
            raw = verified_native_data(receipt, plan)
        except ValueError:
            summary = native_engineering_summary(receipt)
            summary.update(status='interrupted', passed=False,
                message='请求的原生验证层未完成，未创建活动版本。')
            return summary
        summary = native_engineering_summary(receipt)
        # Artifact projection is pure Core readback. Do not open an offline
        # Workspace or compile a second copy to replace this GUI evidence.
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix='native-result-') as directory:
            if version['target_mode'] == 'st':
                from application.native_st import prepare_candidate
            else:
                from application.fbd import prepare_candidate
            candidate = prepare_candidate(directory, imported=raw, plc_model=cpu,
                program_name=payload.get('st_program_name') or version.get('program_name'), summary='可见原生工程会话',
                **(dict(confirmed_spec=self.store.get_project(project_id).get('confirmed_spec'),
                        compile_status=summary['gx_compile_status'], native_receipt=receipt) if version['target_mode'] == 'st' else {}))
            validation = candidate['metadata']['validation']
            validation.update(native_save='passed', native_source_save_required=True,
                gx_compile=summary['gx_compile_status'],
                cold_reopen=next((r['status'] for r in summary['findings'] if r['kind'] == 'cold_reopen'), 'unverified'),
                messages=['已核对同一原生会话保存的声明、实现、图形和配置；运行对应关系仍未验证。'])
            if (candidate['metadata'].get('semantic_validation') or {}).get('behavior') == 'failed':
                return {**summary, 'status': 'interrupted', 'passed': False,
                        'validation': candidate['metadata']['semantic_validation'], 'message': '当前工程的行为检查发现反例，未激活候选版本。'}
            candidate['metadata']['generation_metadata'] = native_generation_metadata(cpu, receipt, summary)
            saved = self.proposals.complete_native_candidate(approved_id, candidate)
        return {**summary, **saved, 'status': 'completed', 'base_version_id': base_id,
                'project_id': project_id}

    def complete_live(self, payload, approved_id, result):
        from application.live_engineering import live_engineering_summary, verified_live_data
        receipt = result.get('_live_result') if isinstance(result, dict) else None
        if not isinstance(receipt, dict):
            failure = result if isinstance(result, dict) else {}
            return {'status': failure.get('status') if failure.get('status') in ('error', 'unavailable') else 'interrupted',
                    'passed': False, 'mode': 'live',
                    'message': failure.get('message') or '持续原生工程执行没有返回可确认的会话回执。'}
        summary = live_engineering_summary(receipt)
        successful = receipt.get('passed') is True and receipt.get('status') in ('completed', 'paused', 'ready', 'ended')
        if not successful:
            return {**summary, 'status': 'interrupted', 'passed': False}
        summary.update(session_status=receipt['status'], status='completed', passed=True)
        if not isinstance(receipt.get('data'), bytes):
            return summary
        project_id, base_id = payload['project_id'], payload['version_id']
        version = self.projects.raw_version(project_id, base_id)
        from application.execution import native_saved_cpu
        cpu = native_saved_cpu(version, self.store.get_project(project_id))
        baseline = self.projects.artifact(project_id, base_id, 'gxw').read_bytes()
        raw = verified_live_data(receipt, baseline)
        with tempfile.TemporaryDirectory(dir=self.state_dir, prefix='live-native-result-') as directory:
            if version['target_mode'] == 'st':
                from application.native_st import prepare_candidate
            else:
                from application.fbd import prepare_candidate
            current_compile = receipt.get('compile') or {}
            compile_status = (current_compile.get('status') if current_compile.get('revision') == receipt['data_revision']
                              else 'unverified')
            candidate = prepare_candidate(directory, imported=raw, plc_model=cpu,
                program_name=payload.get('st_program_name') or version.get('program_name'), summary='持续原生工程阶段保存',
                **(dict(confirmed_spec=self.store.get_project(project_id).get('confirmed_spec'),
                        compile_status=compile_status, native_receipt=receipt) if version['target_mode'] == 'st' else {}))
            candidate['metadata']['validation'].update(native_save='passed',
                native_source_save_required=True, gx_compile=compile_status,
                messages=['已核对本阶段保存与实际原生页面；编译和地址结果分别绑定其修订。'])
            candidate['metadata']['generation_metadata'] = native_generation_metadata(cpu, receipt, summary)
            if (candidate['metadata'].get('semantic_validation') or {}).get('behavior') == 'failed':
                return {**summary, 'status': 'interrupted', 'passed': False,
                        'validation': candidate['metadata']['semantic_validation'], 'message': '当前工程的行为检查发现反例，未激活候选版本。'}
            saved = self.proposals.complete_native_candidate(approved_id, candidate)
        self.execution.rebase_live_session(project_id, receipt['session_id'], base_id, saved['version_id'])
        summary.update(version_revision=receipt['data_revision'],
                       version_matches_active_source=receipt['data_revision'] == receipt['revision'])
        return {**summary, **saved, 'status': 'completed', 'project_id': project_id,
                'base_version_id': base_id, 'data_revision': receipt['data_revision']}


def native_generation_metadata(cpu, receipt, summary):
    """Retain the selected menu and observed native identity on saved versions."""
    from plc.target_support import require_native_program_target
    selected = require_native_program_target(cpu)
    identity = receipt.get('identity') or {}
    if identity.get('cpu_menu') is not None:
        actual = require_native_program_target(identity['cpu_menu'])
        if actual.cpu != selected.cpu:
            raise ValueError('Native engineering receipt CPU differs from the saved target')
    result = {'native_engineering': summary, 'native_cpu_menu': selected.cpu}
    result.update({key: copy.deepcopy(identity[key]) for key in
                   ('software_version', 'native_ui_language', 'project_codepage') if key in identity})
    return result
