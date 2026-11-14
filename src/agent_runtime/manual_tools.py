# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

"""Manual retrieval through the shared knowledge service."""
from __future__ import annotations
import copy
from typing import Any, Dict, Mapping
from agent_runtime.tool_context import (
    ToolContext,
)


def _language_coverage(rows, languages, *, debug=False, explicit_query_langs=frozenset()):
    from knowledge.structured_facts import authored_packet
    matches = {language: set() for language in languages}
    explicit_rows = [row for row in rows if row.get('_explicit_target')]
    universal_only = (
        bool(explicit_rows)
        and not explicit_query_langs
        and all(row.get('fact_kind') in {'device', 'device_family', 'error', 'positioning'} for row in explicit_rows)
    )
    for row in explicit_rows:
        packet = authored_packet(row)
        packet_lang = packet.get('language')
        if packet_lang in matches:
            sources = packet.get('sources') or []
            matches[packet_lang].update(
                (source.get('manual_id') or 'authored') for source in (sources or [{'manual_id': 'authored'}])
            )
        for fact in (packet.get('value') or {}).get('facts', []):
            if fact.get('selection_applicability') not in (None, 'applicable'):
                continue
            scoped_langs = fact.get('scope', {}).get('languages', ())
            for language in languages.intersection(scoped_langs):
                sources = fact.get('sources') or packet.get('sources') or []
                matches[language].update(
                    (source.get('manual_id') or 'authored') for source in (sources or [{'manual_id': 'authored'}])
                )
    missing = [] if universal_only else sorted(language for language, manuals in matches.items() if not manuals)
    result = {'missing_languages': missing}
    if debug:
        result['manuals_by_language'] = {
            lang: sorted(mid for mid in ids if mid != 'authored') for lang, ids in sorted(matches.items())
        }
    else:
        result['covered_languages'] = sorted(lang for lang, ids in matches.items() if ids)
    if missing:
        result['message'] = '本次查询缺少所列语言的专属手册事实。已有通用 CPU 事实按原适用范围使用。'
    return result


def _search_plc_manual(context: ToolContext, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    import re
    from knowledge.retriever import retrieve_fact_aware_knowledge
    from knowledge.structured_facts import retrieval_coverage, authored_packet
    from knowledge.evidence import evidence_record

    query = str(arguments.get("query") or "").strip()
    if not query:
        from agent_runtime.tool_errors import ToolPreconditionError
        raise ToolPreconditionError("INVALID_ARGUMENTS", "query 不能为空；请给出要查询的指令、软元件或错误码。",
            next_actions=({"tool": "search_plc_manual", "required_arguments": ["query", "plc_model"]},))
    from knowledge.structured_facts import query_languages
    project_language = (context.version or {}).get('target_mode') or context.project.get('target_mode')
    explicit_query_langs = query_languages(query)
    inherited = project_language or ('ladder' if context.project_id else None)
    if arguments.get('language'):
        language = arguments['language']
        source = 'explicit'
    elif (inherited and len(explicit_query_langs) == 1 and inherited not in explicit_query_langs
            and not re.search(r'equivalent|对比|对照|对应|区别', query, re.I)):
        from knowledge.instruction_facts import instruction_fact_targets
        from plc.instructions import DEFAULT_INSTRUCTION_REGISTRY
        q_lang = next(iter(explicit_query_langs))
        ops = [row.get('opcode') for row in instruction_fact_targets(query, {}) if row.get('opcode')]
        project_only = any(
            (spec := DEFAULT_INSTRUCTION_REGISTRY.resolve_form(op, cpu=context.plc_model)) is not None
            and getattr(spec.spec, 'category', '') == 'contact'
            for op in ops
        ) if inherited == 'ladder' and ops else False
        if project_only:
            language, source = inherited, 'project'
        else:
            language, source = q_lang, 'query'
    elif inherited:
        language = inherited
        source = 'project'
    else:
        source = 'query' if explicit_query_langs else 'unspecified'
        language = next(iter(explicit_query_langs)) if len(explicit_query_langs) == 1 else None
    languages = query_languages(query, language)
    top_k = int(arguments.get("top_k", 5))
    from knowledge.instruction_presentation import instruction_delivery_languages
    debug = arguments.get('debug', False)
    delivery_languages = ([language] if debug else
        instruction_delivery_languages(query, language, project_language))
    rows = []
    for selected_language in delivery_languages:
        project_mode = (context.project.get('project_mode') if selected_language == 'ladder' else None)
        if not debug and selected_language == 'ladder' and re.search(r'结构化梯形图|structured\s+ladder', query, re.I):
            project_mode = 'structured_ladder_block'
        selected = retrieve_fact_aware_knowledge(query, plc_model=context.plc_model,
            task_type="analysis", top_k=top_k, language=selected_language, aspects=arguments.get('aspects'),
            **({'project_mode': project_mode} if not debug and project_mode else {}))
        for original in selected:
            row = copy.deepcopy(original)
            if not debug:
                row.setdefault('_knowledge_context', {}).update(language=selected_language, delivery_languages=delivery_languages)
                if project_mode:
                    row['_knowledge_context']['project_mode'] = project_mode
            rows.append(row)
    coverage = retrieval_coverage(query, rows, plc_model=context.plc_model, language=language,
                                  aspects=arguments.get('aspects'))
    if languages:
        coverage['language'] = _language_coverage(
            rows, languages, debug=bool(debug), explicit_query_langs=explicit_query_langs)
    scope = {'query': query, 'plc_model': context.plc_model, 'language': language,
             'language_source': source, 'coverage': coverage}
    if not debug:
        from shared.model_context import technical_knowledge_results, model_knowledge_coverage
        origins = {}
        results = technical_knowledge_results(rows, origins=origins)
        coverage = model_knowledge_coverage(coverage)
        if languages:
            coverage['language'] = _language_coverage(rows, languages, explicit_query_langs=explicit_query_langs)
        forms = []
        if language == 'ladder':
            from application.compact_bindings import CompactBindings, instruction_targets
            forms = CompactBindings(context.plc_model).instruction_forms(
                instruction_targets(unit for row in results for unit in row['knowledge']))
        return {**scope, 'count': len(results), 'results': results, 'coverage': coverage,
                **({'instruction_forms': forms} if forms else {}),
                **({'_fact_origins': origins} if origins else {})}
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
        **scope,
        "count": len(results),
        "results": results,
        "evidence_delivery": {
            "policy": "relevance_complete_units",
            "top_k": top_k,
            "omitted_evidence_is_not_unsupported": True,
        },
    }
