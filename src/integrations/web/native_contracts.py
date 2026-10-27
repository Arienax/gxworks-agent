# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Public native-check receipts; source and completion decisions remain in Core.

Known fields are typed for HTTP clients. Safe public extensions are preserved,
and absent evidence never supplies a successful status or completion flag.
"""
from __future__ import annotations

from typing import Literal

from pydantic import StrictBool

from .response_types import ExtensibleResource, PublicResource


class NativeStatus(ExtensibleResource):
    status: str
    reason: str | None = None
    public_check_promoted: StrictBool | None = None


class NativeReportText(ExtensibleResource):
    text: str
    is_null: StrictBool | None = None
    raw_hex_utf16le: str | None = None


class NativeReport(ExtensibleResource):
    kind: int
    code: int | None = None
    step: int | None = None
    arguments: list[NativeReportText] | None = None
    name: NativeReportText | None = None
    instance: NativeReportText | None = None
    library: NativeReportText | None = None
    instance_kind: int | None = None
    program_kind: int | None = None
    network: int | None = None
    left: int | None = None
    top: int | None = None
    right: int | None = None
    bottom: int | None = None
    poll: int | None = None
    report_index: int | None = None
    report_interface: str | None = None
    record_size: int | None = None
    source_object_id: list[int] | None = None


class NativeDiagnosticReport(NativeReport):
    code: int
    step: int
    arguments: list[NativeReportText]


class NativeDiagnosticOrigin(ExtensibleResource):
    phase: str | None = None
    poll: int | None = None
    report_index: int | None = None
    report_interface: str | None = None
    check_pass: str | None = None


class NativeDeclaration(ExtensibleResource):
    name: str
    data_type: str
    record_id: int | None = None
    class_code: int | None = None
    variable_id: list[int] | None = None


class NativeActionRegistration(ExtensibleResource):
    action_number: int
    action_name: str | None = None
    registration_index: int | None = None
    qualifier: int | None = None


class NativeSourceChild(ExtensibleResource):
    kind: str
    name: str
    number: int | None
    registrations: list[NativeActionRegistration]
    native_type: int | None = None


class NativeDiagnosticSource(ExtensibleResource):
    library: str | None = None
    pou: str | None = None
    logical_name: str | None = None
    declaration_owner: str | None = None
    program_kind: int | None = None
    network: int | None = None
    zero_based_line: int | None = None
    text: str | None = None
    declaration: NativeDeclaration | None = None
    child: NativeSourceChild | None = None


class NativeInstruction(ExtensibleResource):
    native_step: int
    op: str
    args: list[str]


class NativeInstructionPosition(NativeStatus):
    position: NativeInstruction | None = None


class NativeInstanceProjection(NativeStatus):
    instance: str | None = None
    task: str | None = None
    resource: str | None = None


class NativeSourceProjection(NativeStatus):
    source: NativeDiagnosticSource | None = None
    instance_resolution: str | None = None
    instruction_position: NativeInstructionPosition | None = None
    instance_projection: NativeInstanceProjection | None = None
    limits: list[str] | None = None


class NativeGraphSource(ExtensibleResource):
    instance: str | None = None
    formal: str | None = None
    port_name: str | None = None
    side: str | None = None
    pou: str | None = None
    library: str | None = None
    network: int | None = None
    symbol: str | None = None
    zero_based_line: int | None = None
    text: str | None = None
    object_id: str | None = None
    source_offset: int | None = None


class NativeGraphCaller(ExtensibleResource):
    pou: str
    network: int
    symbol: str | None = None
    instance: str | None = None
    object_id: str | None = None


class NativeGraphObject(ExtensibleResource):
    symbol: str
    object_id: str | None = None
    template: str | None = None
    source_offset: int | None = None


class NativeGraphCell(ExtensibleResource):
    id: str
    x: int
    y: int


class NativeGraphProjection(NativeStatus):
    source: NativeGraphSource | None = None
    caller: NativeGraphCaller | None = None
    object: NativeGraphObject | None = None
    cell: NativeGraphCell | None = None
    candidates: list[NativeGraphCell] | None = None
    instance: str | None = None
    task: str | None = None
    resource: str | None = None
    limits: list[str] | None = None


class NativeDiagnostic(ExtensibleResource):
    original: NativeDiagnosticReport
    source_projection: NativeSourceProjection
    graph_projection: NativeGraphProjection
    report_origin: NativeDiagnosticOrigin | None = None


class NativeFailure(ExtensibleResource):
    code: int | None = None
    hresult: int | None = None
    operation: str | None = None
    reason: str | None = None


class NativeTask(PublicResource):
    resource: str
    task: str
    programs: list[str]


class NativeImportedStream(ExtensibleResource):
    logical_name: str
    stream: str
    status: str | None = None
    location: str | None = None


class NativeImportedWorkspace(NativeStatus):
    streams: list[NativeImportedStream] | None = None
    outer_only_streams: list[NativeImportedStream] | None = None


class NativeTaskSource(NativeStatus):
    resource: str | None = None
    task: str | None = None
    pou: str | None = None
    library: str | None = None
    body_id: list[int] | None = None


class NativeSelection(NativeStatus):
    selected_sources: list[str]
    empty_task: StrictBool
    tasks: list[NativeTask]
    source_bytes_current: StrictBool | None = None
    configuration_bytes_current: StrictBool | None = None
    imported_workspace: NativeImportedWorkspace | None = None
    task_program_sources: list[NativeTaskSource] | None = None


class NativeChildBinding(ExtensibleResource):
    name: str
    type: int | None = None
    role: str | None = None
    status: str | None = None
    id: list[int] | None = None
    native_snapshot: str | None = None
    native_body_bytes: int | None = None


class NativeSFCScope(NativeStatus):
    children: list[NativeChildBinding] | None = None
    native_check_promoted: StrictBool | None = None


class NativeSFCChildren(NativeStatus):
    stage: str | None = None
    children: list[NativeChildBinding] | None = None
    missing: list[NativeChildBinding] | None = None
    source_scope: NativeSFCScope | None = None


class NativeSourceBinding(NativeStatus):
    pou: str | None = None
    library: str | None = None
    logical_name: str | None = None
    body_id: list[int] | None = None
    pou_id: list[int] | None = None
    native_snapshot: str | None = None
    source_body_bytes: int | None = None
    native_body_bytes: int | None = None
    sfc_children: NativeSFCChildren | None = None


class NativeSourcePhase(NativeStatus):
    stage: str
    sources: list[NativeSourceBinding]


class NativeSourceContext(NativeStatus):
    stages: list[NativeSourcePhase] | None = None


class NativeSourceBodies(NativeStatus):
    sources: list[NativeSourceBinding]
    phase_reads_complete: StrictBool | None = None


class NativeDeclarationBinding(NativeStatus):
    pou: str | None = None
    library: str | None = None
    name: str | None = None
    role: str | None = None
    logical_name: str | None = None


class NativeDeclarationContext(NativeStatus):
    tables: list[NativeDeclarationBinding] | None = None
    global_and_structure: NativeDeclarationContext | None = None


class NativeCompilationConversion(NativeStatus):
    failures: list[NativeFailure] | None = None


class NativeCompilation(ExtensibleResource):
    completed: StrictBool
    acceptance: str
    diagnostics_complete: StrictBool | None = None
    diagnostics: list[NativeReport] | None = None
    failures: list[NativeFailure] | None = None
    source_context: NativeSourceContext | None = None
    declaration_context: NativeDeclarationContext | None = None
    source_diagnostics: list[NativeDiagnostic] | None = None
    diagnostic_conversion: NativeCompilationConversion | None = None
    returned_resources: int | None = None
    bound_resources: int | None = None


class NativeResourceRead(ExtensibleResource):
    resource: str | list[int] | None = None
    name: str | None = None
    status: str | None = None
    body_bytes: int | None = None
    channel_sizes: list[int] | None = None


class NativeOwnedInput(NativeStatus):
    target: list[int] | None = None
    mask: int | None = None
    task_copy_stable: StrictBool | None = None
    resources: list[NativeResourceRead] | None = None


class NativeCorrespondence(ExtensibleResource):
    correspondence: str
    targets: list[NativeOwnedInput] | None = None
    reads: list[NativeResourceRead] | None = None
    size_queries: list[NativeResourceRead] | None = None
    size_query_reads: list[NativeResourceRead] | None = None
    scope_verified: StrictBool | None = None
    observation_errors: list[str | NativeFailure] | None = None


class NativeTargetOutcome(NativeStatus):
    target: list[int] | None = None
    reported_status: str | None = None
    terminal_progress: StrictBool | None = None
    resource_end_marker: StrictBool | None = None
    diagnostics_complete: StrictBool | None = None
    reported_diagnostic_counts: list[int] | None = None
    observed_diagnostic_counts: list[int] | None = None
    completed_resource: str | None = None


class NativeCheckState(NativeStatus):
    completed: StrictBool


class NativeBackendCheck(NativeCheckState):
    targets: list[NativeTargetOutcome] | None = None
    skipped_targets: list[NativeFailure] | None = None
    check_pass: str | None = None


class NativeDiagnosticProjection(NativeStatus):
    failures: list[NativeFailure]
    source_diagnostics: list[NativeDiagnostic]
    empty_public_list_means_no_errors: StrictBool | None = None
    public_diagnostics: list[NativeReport] | None = None
    backend_diagnostics: list[NativeReport] | None = None
    target_checks: list[NativeTargetOutcome] | None = None
    original_public_source_diagnostics: list[NativeDiagnostic] | None = None


class NativeIdentityMismatch(ExtensibleResource):
    target: list[int]
    original: NativeReport
    event_index: int
    expected_object_id: list[int]
    reported_object_id: list[int] | None
    reason: str


class NativePublicDiagnosticIdentity(NativeStatus):
    checked_report_count: int
    mismatches: list[NativeIdentityMismatch]


class NativeConvertedReport(ExtensibleResource):
    target: list[int]
    original: NativeReport
    report: NativeReport
    resource_scope_adapted: StrictBool


class NativePublicDiagnosticConversion(NativeStatus):
    completed: StrictBool
    current_source: str
    origin: str | None = None
    converted_report_count: int | None = None
    original_report_count: int | None = None
    reports: list[NativeReport | NativeConvertedReport] | None = None
    identity_mismatches: list[NativeIdentityMismatch] | None = None


class NativeDiagnosticRecheck(NativeStatus):
    trigger_code: int | None = None
    trigger_reasons: list[str] | None = None
    previous_tasks_terminal: StrictBool | None = None
    same_compilation: StrictBool | None = None
    same_checker_input_bytes: StrictBool | None = None
    public_check: NativeCheckState | None = None
    raw_check: NativeBackendCheck | None = None
    owned_check_inputs: NativeCorrespondence | None = None
    published_resources: NativeCorrespondence | None = None


class NativeCompletion(ExtensibleResource):
    completed: StrictBool
    exported: StrictBool | None = None


class NativeAdapter(ExtensibleResource):
    status: str
    message: str
    exit_code: int | None = None


class NativeSnapshot(ExtensibleResource):
    project_id: str
    version_id: str
    requested_program: str
    cpu: str


class NativeCheck(ExtensibleResource):
    mode: Literal['native']
    snapshot: NativeSnapshot
    native_version: str
    cpu: str
    adapter: NativeAdapter
    selection: NativeSelection
    compilation: NativeCompilation
    publication: NativeCompletion
    owned_check_inputs: NativeCorrespondence
    published_resources: NativeCorrespondence
    public_check: NativeCheckState
    raw_backend_check: NativeBackendCheck
    diagnostic_projection: NativeDiagnosticProjection
    current_source_check: str
    current_raw_source_check: str
    native_source_locations: NativeStatus
    native_instance_locations: NativeStatus
    save: NativeCompletion
    reopen: NativeStatus
    public_diagnostic_conversion: NativePublicDiagnosticConversion | None = None
    public_diagnostic_identity: NativePublicDiagnosticIdentity | None = None
    diagnostic_recheck: NativeDiagnosticRecheck | None = None
    codepage: int | None = None
    source_bodies: NativeSourceBodies | None = None
    checker_resources: NativeCorrespondence | None = None
    raw_diagnostics: list[NativeDiagnostic] | None = None
