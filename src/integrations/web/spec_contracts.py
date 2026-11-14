# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Specification wire records; Core owns confirmation and PLC semantics."""
from __future__ import annotations

from pydantic import Field, field_validator
from plc.specification.implementation import normalize_open_questions

from .response_types import EngineeringMap, EngineeringObject, EngineeringValue, ExtensibleResource


class TextProvenance(ExtensibleResource):
    source: str | None = None
    evidence: list[str] | None = None
    grounding_status: str | None = None
    request_id: str | None = None
    kind: str | None = None
    sha256: str | None = None


class IORow(ExtensibleResource):
    id: str | None = None
    address: str | None = None
    name: str | None = None
    label: str | None = None
    type: str | None = None
    kind: str | None = None
    comment: str | None = None
    metadata: EngineeringObject | None = None


class SpecificationParameter(ExtensibleResource):
    id: str | None = None
    name: str | None = None
    label: str | None = None
    question: str | None = None
    semantic_key: str | None = None
    source: str | None = None
    value: EngineeringValue = None
    value_kind: str | None = None
    required: bool | None = None
    required_when: EngineeringObject | None = None
    options: list[str] | None = None
    suggested_default: EngineeringValue = None
    note: str | None = None
    note_provenance: TextProvenance | None = None


class ExpressionValueType(ExtensibleResource):
    kind: str
    bits: int | None = None
    signed: bool | None = None
    unit: str | None = None
    encoding: str | None = None
    element: ExpressionValueType | None = None
    count: int | None = None


class SpecificationExpression(ExtensibleResource):
    op: str
    type: ExpressionValueType
    args: list[SpecificationExpression] = Field(default_factory=list)
    name: str | None = None
    value: EngineeringValue = None
    overflow: str | None = None


class EffectTarget(ExtensibleResource):
    device: str
    kind: str
    offset: int | None = None


class OperationEffect(ExtensibleResource):
    target: EffectTarget
    kind: str | None = None
    value: SpecificationExpression | None = None
    source: SpecificationExpression | None = None
    count: SpecificationExpression | None = None
    shift: SpecificationExpression | None = None
    direction: str | None = None


class OperationExecution(ExtensibleResource):
    trigger: str
    required_state: EngineeringMap[int | bool] | None = None


class SpecificationOperationIntent(ExtensibleResource):
    id: str
    opcode: str | None = None
    effects: list[OperationEffect]
    enable: SpecificationExpression
    parameters: EngineeringMap[SpecificationExpression] | None = None
    execution: OperationExecution | None = None
    status: str | None = None
    label: str | None = None
    provenance: TextProvenance | None = None


class InitializationRange(ExtensibleResource):
    start: str
    end: str
    value: int | bool


class SpecificationExecutionContext(ExtensibleResource):
    program_type: str | None = None
    initial_execution_program: bool | None = None
    cpu_version: str | None = None


class BehaviorTransition(ExtensibleResource):
    id: str
    when: SpecificationExpression
    effects: list[OperationEffect]


class SpecificationBehavior(ExtensibleResource):
    id: str
    kind: str
    status: str | None = None
    label: str | None = None
    provenance: TextProvenance | None = None
    values: EngineeringMap[int | bool] | None = None
    ranges: list[InitializationRange] | None = None
    execution_context: SpecificationExecutionContext | None = None
    source: SpecificationExpression | None = None
    accept: SpecificationExpression | None = None
    edge: str | None = None
    startup_policy: str | None = None
    output: str | None = None
    events: list[str] | None = None
    enable: SpecificationExpression | None = None
    transitions: list[BehaviorTransition] | None = None
    predicate: SpecificationExpression | None = None
    when: SpecificationExpression | None = None


class ConstructionInstance(ExtensibleResource):
    id: str | None = None
    requirement_id: str | None = None
    method: str | None = None
    depends_on: list[str] | None = None


class ConstructionRange(ExtensibleResource):
    start: str
    end: str


class SpecificationConstructionPlan(ExtensibleResource):
    instances: list[ConstructionInstance] = Field(default_factory=list)
    internal_ranges: list[ConstructionRange] = Field(default_factory=list)
    occupied_devices: list[str] | None = None
    execution_context: SpecificationExecutionContext | None = None


class SpecificationApproach(ExtensibleResource):
    approach_id: str | None = None
    id: str | None = None
    name: str | None = None
    title: str | None = None
    description: str | None = None
    generation_guide: str | None = None
    construction_plan: SpecificationConstructionPlan | None = None


class SpecificationRequirement(ExtensibleResource):
    id: str
    text: str
    source: str | None = None
    status: str | None = None


class SpecificationOpenQuestion(ExtensibleResource):
    id: str
    text: str
    source: str | None = None
    required: bool | None = None


class SpecificationImplementationItem(ExtensibleResource):
    id: str
    requirement_ids: list[str] = Field(default_factory=list)
    language: str
    location: str
    method: str
    state_owner: str
    template: EngineeringValue = None
    instances: EngineeringValue = None
    source: str | None = None
    rationale: str | None = None


class Specification(ExtensibleResource):
    requirements: list[SpecificationRequirement] | None = None
    implementation_items: list[SpecificationImplementationItem] | None = None
    open_questions: list[SpecificationOpenQuestion] | None = None
    schema_version: int | None = None
    plc_model: str | None = None
    summary: str | None = None
    io_table: list[IORow] | None = None
    parameters: list[SpecificationParameter] | None = None
    operation_intents: list[SpecificationOperationIntent] | None = None
    confirmed_operation_intent_ids: list[str] | None = None
    behavior_constraints: list[SpecificationBehavior] | None = None
    confirmed_behavior_ids: list[str] | None = None
    approaches: list[SpecificationApproach] | None = None
    selected_approach: SpecificationApproach | None = None
    user_notes: str | None = None
    metadata: EngineeringObject | None = None
    extensions: EngineeringObject | None = None
    variables: EngineeringObject | None = None
    structs: EngineeringObject | None = None

    @field_validator("open_questions", mode="before")
    @classmethod
    def normalize_question_text(cls, rows):
        return normalize_open_questions(rows)


class SpecificationDifference(ExtensibleResource):
    path: str
    before: EngineeringValue = None
    after: EngineeringValue = None


class SpecDocumentIssue(ExtensibleResource):
    path: str | None = None
    message: str
    code: str | None = None


class SpecDocumentIssues(ExtensibleResource):
    errors: list[SpecDocumentIssue] = Field(default_factory=list)
    warnings: list[SpecDocumentIssue] = Field(default_factory=list)


class SpecDocument(ExtensibleResource):
    project_id: str
    plc_model: str
    target_mode: str
    path: str
    draft: Specification | None = None
    confirmed_spec: Specification | None = None
    base_revision: str | None = None
    draft_revision: str
    differences: list[SpecificationDifference] = Field(default_factory=list)
    version_spec: Specification | None = None
    issues: SpecDocumentIssues | None = None
