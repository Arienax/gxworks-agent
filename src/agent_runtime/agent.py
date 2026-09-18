"""Vendor-neutral PLC Agent orchestration."""

from __future__ import annotations

import copy
import re
from shared.i18n import language_scoped, tr
from model_runtime.responses import preserved_annotations
from application.response_contracts import tool_argument_contract
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from model_runtime.provider import (
    AssistantMessage,
    ModelProvider,
    ModelRequest,
    SystemMessage,
    ToolCall,
    UserMessage,
    collect_response,
    get_active_provider,
)
from agent_runtime.runtime import (
    InProcessToolRuntime,
    ToolRuntime,
    build_default_tool_runtime,
)


AGENT_SYSTEM_PROMPT = """你是 PLC AI 工作台内的工程助手。

规则：
1. 涉及当前项目、当前程序、手册、校验或 GX Works2 导入状态时，必须先调用相应工具，不得猜测。
2. 只能使用提供的高层工程工具，不得假设存在鼠标、键盘、文件删除、PLC 写入或强制软元件工具。
3. import_current_program_to_gxworks2 只提出确认请求，不代表已经导入。
4. patch_program 只生成经过本地校验的候选补丁；必须告诉用户仍需查看差异并确认，不能自行接受候选版本或同步 GX Works2。
5. 工具失败时如实说明。回答简洁，遵守应用设置的输出语言；引用手册事实时给出 source、page/section。
"""

_AGENT_PATTERNS = (
    re.compile(r"(?:一键)?导入.{0,18}(?:GX\s*Works\s*2|GX2)", re.I),
    re.compile(r"(?:GX\s*Works\s*2|GX2).{0,18}(?:导入|读入)", re.I),
    re.compile(r"(?:把|将).{0,18}(?:刚才|当前|这个).{0,12}(?:程序|版本).{0,12}(?:导进去|导入)", re.I),
    re.compile(r"(?:查|搜索|检索|查询).{0,12}(?:PLC|FX3U|FX5U|GX|指令|软元件|错误码)?.{0,8}(?:手册|说明书|知识库)", re.I),
    re.compile(r"(?:手册|说明书|知识库).{0,12}(?:查|搜索|检索|查询)", re.I),
    re.compile(r"(?:校验|验证|检查|评审).{0,10}(?:当前|刚才|这个|所选).{0,8}(?:程序|版本|梯形图)", re.I),
    re.compile(r"(?:当前|所选).{0,8}(?:项目|程序|版本).{0,8}(?:信息|状态|详情|摘要|是什么|是哪版)", re.I),
    re.compile(r"(?:现在|当前).{0,8}(?:选中|打开).{0,8}(?:哪个项目|哪个版本|什么项目|什么版本)", re.I),
    re.compile(r"(?:修改|改写|调整|增加|添加|删除).{0,12}(?:当前|刚才|所选|这个).{0,10}(?:程序|梯形图|网络|Network)", re.I),
    re.compile(r"(?:当前|刚才|所选|这个).{0,10}(?:程序|梯形图|网络|Network).{0,12}(?:修改|改写|调整|增加|添加|删除)", re.I),
)

_IMPLICIT_EDIT_PATTERNS = (
    re.compile(r"(?:把|将).{1,30}(?:加进去|加入|改成|换成|删除|移除)", re.I),
    re.compile(r"(?:程序|梯形图|网络|Network).{0,16}(?:增加|添加|修改|调整|删除|移除)", re.I),
)

_NEW_GENERATION_RE = re.compile(r"(?:生成|新建|创建|做一个|重新生成)", re.I)


def should_route_to_tool_agent(text: Any, *, has_current_program: bool = False) -> bool:
    """Route project operations and context-bound edits, never initial generation."""

    value = " ".join(str(text or "").split())
    if not value:
        return False
    if any(pattern.search(value) for pattern in _AGENT_PATTERNS):
        return True
    if not has_current_program or _NEW_GENERATION_RE.search(value):
        return False
    return any(pattern.search(value) for pattern in _IMPLICIT_EDIT_PATTERNS)


@dataclass
class AgentRunResult:
    content: str
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)
    audit: List[Dict[str, Any]] = field(default_factory=list)
    rounds: int = 0


def _sanitized_history(
    history: Optional[Sequence[Mapping[str, Any]]],
) -> List[Any]:
    cleaned = []
    for message in list(history or [])[-12:]:
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")[:6000]
        if not content:
            continue
        if role == "user":
            cleaned.append(UserMessage(content))
        elif role == "assistant":
            cleaned.append(AssistantMessage(content))
    return cleaned


def _emit_stream(callback, value: Any) -> None:
    text = str(value or "")
    if text and callback is not None:
        callback(text)


def _fallback_content(audit: Sequence[Mapping[str, Any]]) -> str:
    if not audit:
        return str(tr("没有获得可用的工具结果。"))
    successful = [item for item in audit if item.get("ok")]
    failed = [item for item in audit if not item.get("ok")]
    parts = []
    if successful:
        parts.append(str(tr("已完成：")) + ", ".join(str(item.get("tool")) for item in successful))
    if failed:
        parts.append(str(tr("未完成：")) + ", ".join(str(item.get("tool")) for item in failed))
    return "\n".join(parts) or str(tr("工具调用已结束。"))


@language_scoped
def run_tool_agent(
    user_text: str,
    *,
    context: Any,
    conversation_history: Optional[Sequence[Mapping[str, Any]]] = None,
    runtime: Optional[ToolRuntime] = None,
    provider: Optional[ModelProvider] = None,
    registry: Any = None,
    max_rounds: int = 5,
    on_reasoning_chunk=None,
    on_content_chunk=None,
    on_progress=None,
) -> AgentRunResult:
    """Execute a bounded model/tool loop through the two public boundaries."""

    if not str(user_text or "").strip():
        raise ValueError("user_text cannot be empty")
    if max_rounds < 1 or max_rounds > 8:
        raise ValueError("max_rounds must be between 1 and 8")
    if runtime is None:
        runtime = (
            InProcessToolRuntime(registry)
            if registry is not None
            else build_default_tool_runtime()
        )
    provider = provider or get_active_provider()
    messages = [
        SystemMessage(AGENT_SYSTEM_PROMPT),
        *_sanitized_history(conversation_history),
        UserMessage(str(user_text).strip()),
    ]

    audit: List[Dict[str, Any]] = []
    pending_actions: List[Dict[str, Any]] = []
    for round_number in range(1, max_rounds + 1):
        _emit_stream(
            on_progress,
            tr("AI 正在判断需要使用的工具（第 {round} 轮）", round=round_number),
        )
        request = ModelRequest(
            tuple(messages),
            tools=tuple(runtime.list_tools(context)),
            tool_response_contracts=tuple(
                (name, tool_argument_contract(name))
                for name in ("create_program_candidate", "patch_program")
            ),
            preserved_annotations=preserved_annotations(
                getattr(context, "program_ir", None),
                getattr(context, "version", None),
                getattr(context, "ladder", None),
            ),
            options={
                "response_format": None,
                "tool_choice": "auto",
            },
            stream=True,
        )
        response = collect_response(
            provider,
            request,
            on_reasoning_chunk=on_reasoning_chunk,
            on_content_chunk=on_content_chunk,
            fallback_to_non_stream=True,
            on_fallback=lambda _error: _emit_stream(
                on_progress,
                tr("流式工具判断不可用，正在切换普通模式"),
            ),
        )
        assistant = response.message
        calls = []
        for call_index, call in enumerate(assistant.tool_calls, start=1):
            calls.append(
                call
                if call.id
                else replace(call, id=f"tool_call_{round_number}_{call_index}")
            )
        assistant = replace(assistant, tool_calls=tuple(calls))
        messages.append(assistant)
        if not calls:
            content = assistant.content.strip()
            return AgentRunResult(
                content=content or _fallback_content(audit),
                pending_actions=pending_actions,
                audit=audit,
                rounds=round_number,
            )

        for call in calls:
            _emit_stream(on_progress, tr("已确认工具：{name}", name=call.name or tr("未知工具")))
            _emit_stream(on_progress, tr("正在执行工具：{name}", name=call.name or tr("未知工具")))
            result = runtime.invoke(call, context)
            envelope = dict(result.data or {})
            audit.append(
                {
                    "round": round_number,
                    "tool": call.name,
                    "ok": not result.is_error,
                    "status": envelope.get("status"),
                    "error_code": (envelope.get("error") or {}).get("code"),
                }
            )
            pending = (envelope.get("data") or {}).get("pending_action")
            if (
                not result.is_error
                and envelope.get("status") == "confirmation_required"
                and isinstance(pending, Mapping)
            ):
                action = copy.deepcopy(dict(pending))
                if action not in pending_actions:
                    pending_actions.append(action)
            messages.append(result)
            _emit_stream(
                on_progress,
                tr("工具执行失败：{name}" if result.is_error else "工具执行完成：{name}", name=call.name or tr("未知工具")),
            )

    raise RuntimeError(tr("AI 工具调用超过上限（{rounds} 轮）。", rounds=max_rounds))


__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "AgentRunResult",
    "run_tool_agent",
    "should_route_to_tool_agent",
]
