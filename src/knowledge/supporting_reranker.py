"""Phase-2c scoped reranking for gxw2-skill supporting evidence.

This module intentionally does not change the base retrieval engine. It only
boosts third-party supporting chunks when they were reached through an exact
skill concept entity and the requested task matches the chunk role.
"""

from __future__ import annotations


from knowledge.gxw2_skill import SKILL_CONCEPTS as _GXW2_SKILL_CONCEPTS

_BOOSTS = {
    "st": {
        "st_rule": 320.0,
        "data_type": 280.0,
        "compatibility": 240.0,
    },
    "generate": {
        "st_rule": 280.0,
        "data_type": 240.0,
        "compatibility": 160.0,
    },
    "edit": {
        "st_rule": 280.0,
        "data_type": 240.0,
    },
    "analysis": {
        "data_type": 220.0,
        "compatibility": 300.0,
    },
}


def supporting_boost(candidate: dict, task_type: str) -> float:
    if str(candidate.get("manual_type") or "").casefold() != "third_party_skill":
        return 0.0
    if "entity" not in set(candidate.get("retrieval_signals") or ()):
        return 0.0
    matched = str(candidate.get("matched_entity") or "").strip().upper()
    if matched not in _GXW2_SKILL_CONCEPTS:
        return 0.0
    task = str(task_type or "").casefold()
    chunk_type = str(candidate.get("chunk_type") or "").casefold()
    return float(_BOOSTS.get(task, {}).get(chunk_type, 0.0))


def rerank(results: list[dict], task_type: str) -> list[dict]:
    ranked = []
    for result in results:
        candidate = dict(result)
        boost = supporting_boost(candidate, task_type)
        if boost:
            candidate["gxw2_supporting_boost"] = boost
            candidate["score"] = round(float(candidate.get("score") or 0.0) + boost, 4)
        ranked.append(candidate)
    ranked.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -int(item.get("manual_priority") or 0),
            int(item.get("pdf_page") or 0),
            str(item.get("id") or ""),
        )
    )
    # Exact task-scoped supporting evidence gets at most one reserved slot.
    # Keep the leading authoritative result and all scores/weights unchanged;
    # a page merely sharing "GX Works2" must not crowd out a requested rule.
    supporting_index = next(
        (index for index, item in enumerate(ranked)
         if item.get("gxw2_supporting_boost")), None,
    )
    if supporting_index is not None and supporting_index >= 5:
        supporting = ranked.pop(supporting_index)
        supporting["gxw2_supporting_slot"] = True
        ranked.insert(1, supporting)
    return ranked


__all__ = ["supporting_boost", "rerank"]
