"""Manual retrieval through the shared knowledge service."""
from __future__ import annotations
import copy
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_context import (
    ToolContext,
)


def _search_plc_manual(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage
    from knowledge.evidence import evidence_record

    query = str(arguments.get("query") or "").strip()
    if not query:
        from agent_runtime.tool_errors import ToolPreconditionError
        raise ToolPreconditionError("INVALID_ARGUMENTS", "query 不能为空；请给出要查询的指令、软元件或错误码。",
            next_actions=({"tool": "search_plc_manual", "required_arguments": ["query", "plc_model"]},))
    top_k = int(arguments.get("top_k", 5))
    rows = retrieve_fact_aware_knowledge(
        query,
        plc_model=context.plc_model,
        task_type="analysis",
        top_k=top_k,
    )
    results = []
    for row in rows[:top_k]:
        if not isinstance(row, Mapping):
            continue
        results.append(
            {
                "id": str(row.get("id") or ""),
                "manual_id": str(row.get("manual_id") or ""),
                "source": str(row.get("source") or ""),
                "page": row.get("page"),
                "pdf_page": row.get("pdf_page"),
                "section": str(row.get("section") or ""),
                "chunk_type": str(row.get("chunk_type") or ""),
                "role": evidence_record(row)['role'],
                "instruction_opcode": str(row.get("instruction_opcode") or ""),
                # The shared retriever delivers complete evidence. Cutting a block can detach a CPU row
                # from its footnote or cut structured operand JSON in half.
                "text": str(row.get("text") or ""),
                **{key: copy.deepcopy(row[key]) for key in (
                    "manual_number", "revision", "manual_type", "page_end", "pdf_page_end",
                    "plc_models", "source_applicability", "source_evidence", "source_chunk_id", "source_section_id",
                    "source_address_locations",
                    "fact_kind", "fact_target", "fact_dimensions", "device_lookup_basis",
                    "device_range",
                    "structured_fact_requested_target",
                    "device_definition_status", "device_definition", "evidence_scope",
                    "table_binding_status", "content_status",
                    "content_origin", "distribution_status", "technical_status",
                ) if key in row},
            }
        )
    if not arguments.get('debug', False):
        from shared.model_context import technical_knowledge_text
        results = [{**{key: copy.deepcopy(row[key]) for key in
                    ('fact_kind', 'fact_target', 'plc_models', 'instruction_opcode') if row.get(key)},
                    'text': technical_knowledge_text(row)} for row in results]
        return {'query': query, 'plc_model': context.plc_model, 'count': len(results), 'results': results,
                'coverage': retrieval_coverage(query, rows, plc_model=context.plc_model)}
    return {
        "query": query,
        "plc_model": context.plc_model,
        "count": len(results),
        "results": results,
        "coverage": retrieval_coverage(query, rows, plc_model=context.plc_model),
        "evidence_delivery": {
            "policy": "relevance_complete_units",
            "top_k": top_k,
            "omitted_evidence_is_not_unsupported": True,
        },
    }
