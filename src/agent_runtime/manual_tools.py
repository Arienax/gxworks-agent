"""Manual retrieval through the shared knowledge service."""
from __future__ import annotations
import copy
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence
from agent_runtime.tool_context import (
    ToolContext,
)


def _search_plc_manual(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage, authored_packet
    from knowledge.evidence import evidence_record

    query = str(arguments.get("query") or "").strip()
    if not query:
        from agent_runtime.tool_errors import ToolPreconditionError
        raise ToolPreconditionError("INVALID_ARGUMENTS", "query 不能为空；请给出要查询的指令、软元件或错误码。",
            next_actions=({"tool": "search_plc_manual", "required_arguments": ["query", "plc_model"]},))
    top_k = int(arguments.get("top_k", 5))
    from knowledge.instruction_presentation import instruction_delivery_languages
    debug = arguments.get('debug', False)
    languages = ([arguments.get('language')] if debug else
        instruction_delivery_languages(query, arguments.get('language'), context.project.get('target_mode')))
    rows = []
    for language in languages:
        project_mode = (context.project.get('project_mode') if language == 'ladder' else None)
        if not debug and language == 'ladder' and re.search(r'结构化梯形图|structured\s+ladder', query, re.I):
            project_mode = 'structured_ladder_block'
        selected = retrieve_fact_aware_knowledge(query, plc_model=context.plc_model,
            task_type="analysis", top_k=top_k, language=language, aspects=arguments.get('aspects'),
            **({'project_mode': project_mode} if not debug and project_mode else {}))
        for original in selected:
            row = copy.deepcopy(original)
            if not debug:
                row.setdefault('_knowledge_context', {}).update(language=language, delivery_languages=languages)
                if project_mode:
                    row['_knowledge_context']['project_mode'] = project_mode
            rows.append(row)
    if not arguments.get('debug', False):
        from shared.model_context import technical_knowledge_results, model_knowledge_coverage
        results = technical_knowledge_results(rows)
        return {'query': query, 'plc_model': context.plc_model, 'count': len(results), 'results': results,
                'coverage': model_knowledge_coverage(retrieval_coverage(query, rows, plc_model=context.plc_model,
                    language=languages[0] if len(languages) == 1 else arguments.get('language'), aspects=arguments.get('aspects')))}
    results = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        packet = authored_packet(row)
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
                # A typed debug packet must reach the existing object pager.
                # Encoding it as one JSON string makes all facts indivisible.
                **({"fact_packet": packet} if packet else {"text": str(row.get("text") or "")}),
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
    return {
        "query": query,
        "plc_model": context.plc_model,
        "count": len(results),
        "results": results,
        "coverage": retrieval_coverage(query, rows, plc_model=context.plc_model,
                    language=arguments.get('language'), aspects=arguments.get('aspects')),
        "evidence_delivery": {
            "policy": "relevance_complete_units",
            "top_k": top_k,
            "omitted_evidence_is_not_unsupported": True,
        },
    }
