"""Project specification tools: reads are snapshots, writes are proposals."""
from __future__ import annotations

import copy
from collections.abc import Mapping

from agent_runtime.tool_errors import ToolPreconditionError


def get_project_spec(context, _arguments):
    document = context.project.get('_spec_document')
    if not isinstance(document, Mapping):
        raise ToolPreconditionError('SPEC_CONTEXT_UNAVAILABLE',
            '当前上下文未提供工程规格，请重新读取工程；本工具不会创建规格文件。')
    return copy.deepcopy(dict(document))


def update_project_spec(context, arguments):
    return {'requires_confirmation': True,
        'message': '局部规格变更待共享工程事务处理；更新草稿不等于用户确认方案。',
        'pending_action': {'type': 'update_project_spec', 'project_id': context.project_id,
            'changes': copy.deepcopy(arguments['changes']),
            'expected_revision': arguments['expected_revision']}}


def confirm_project_spec(context, arguments):
    if arguments.get('user_confirmed') is not True:
        raise ToolPreconditionError('USER_CONFIRMATION_REQUIRED', '需用户明确确认当前规格与实现方案。')
    return {'requires_confirmation': True,
        'message': '用户明确确认的规格待共享工程事务保存。',
        'pending_action': {'type': 'confirm_project_spec', 'project_id': context.project_id,
            'version_id': context.version_id or None,
            'expected_revision': arguments['expected_revision']}}


def specification_definitions():
    from agent_runtime.tool_registry import ToolDefinition
    revision = {'type': 'string', 'minLength': 1,
        'description': '最近 get_project_spec 返回的 draft_revision，原样传回；过期时重读差异。'}
    def schema(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required),
            'additionalProperties': False}
    yield ToolDefinition('get_project_spec',
        '只读返回当前工程规格路径、草稿、已确认规格、修订、差异、待补问题及所选版本的规格快照。读取不会创建文件；版本快照仅供参考。',
        schema({}), get_project_spec)
    yield ToolDefinition('update_project_spec',
        '按分节和条目 ID 局部更新当前工程规格草稿。未指定条目保持；保存不代表用户确认。用户明确参数变更可登记，实质实现方案变更须展示差异后重新确认。',
        schema({'expected_revision': revision, 'changes': {'type': 'array', 'minItems': 1,
            'maxItems': 256, 'items': schema({
                'section': {'type': 'string', 'minLength': 1},
                'id': {'type': 'string', 'minLength': 1},
                'value': {}, 'remove': {'type': 'boolean'}}, ('section',))}},
            ('expected_revision', 'changes')), update_project_spec, confirmation_required=True)
    yield ToolDefinition('confirm_project_spec',
        '仅在用户明确确认展示的当前规格与实现方案后调用；以 draft_revision 防止确认旧稿。user_confirmed 必须 true，工具调用本身不能代替用户确认。',
        schema({'expected_revision': revision, 'user_confirmed': {'type': 'boolean', 'enum': [True]}},
            ('expected_revision', 'user_confirmed')), confirm_project_spec, confirmation_required=True)
