"""Deterministic construction-example routing from confirmed structured needs.

No user prose, embedding model or RAG ranking participates here. The router is
conservative: without a composite motif, multiple independent primary needs are
treated as ambiguous and receive no example rather than several weak matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class ConstructionNeedProfile:
    structures: frozenset[str] = frozenset()
    execution_semantics: frozenset[str] = frozenset()
    motifs: frozenset[str] = frozenset()

    def manifest(self):
        return {
            "structures": sorted(self.structures),
            "execution_semantics": sorted(self.execution_semantics),
            "motifs": sorted(self.motifs),
        }


def build_construction_need_profile(confirmed_spec):
    spec = confirmed_spec if isinstance(confirmed_spec, Mapping) else {}
    selected = spec.get("selected_approach")
    selected = selected if isinstance(selected, Mapping) else {}
    contract = selected.get("generation_contract")
    contract = contract if isinstance(contract, Mapping) else {}

    structures = {
        str(item).strip().casefold()
        for item in (contract.get("required_structures") or [])
        if str(item or "").strip()
    }
    semantics = {
        str(item.get("semantic") or "").strip().upper()
        for item in (spec.get("execution_semantics") or [])
        if isinstance(item, Mapping) and str(item.get("semantic") or "").strip()
    }
    # Future Agent-A/Core grounding may populate this routing-only field. It is
    # deliberately not inferred from generation_guide or free user prose here.
    motifs = {
        str(item).strip().casefold()
        for item in (spec.get("construction_motifs") or [])
        if str(item or "").strip()
    }
    return ConstructionNeedProfile(
        structures=frozenset(structures),
        execution_semantics=frozenset(semantics),
        motifs=frozenset(motifs),
    )


def route_construction_examples(profile, candidates, *, max_examples=2):
    profile = profile if isinstance(profile, ConstructionNeedProfile) else ConstructionNeedProfile()
    rows = [dict(item) for item in candidates if isinstance(item, Mapping)]
    primary_hits = []
    scored = []
    for row in rows:
        structures = {
            str(item).strip().casefold()
            for item in row.get("primary_structures") or []
            if str(item or "").strip()
        }
        semantics = {
            str(item).strip().upper()
            for item in row.get("primary_execution_semantics") or []
            if str(item or "").strip()
        }
        motifs = {
            str(item).strip().casefold()
            for item in row.get("primary_motifs") or []
            if str(item or "").strip()
        }
        hit_structures = structures & set(profile.structures)
        hit_semantics = semantics & set(profile.execution_semantics)
        hit_motifs = motifs & set(profile.motifs)
        score = 5 * len(hit_motifs) + 3 * len(hit_structures) + 2 * len(hit_semantics)
        if score:
            primary_hits.extend(
                [f"motif:{item}" for item in hit_motifs]
                + [f"structure:{item}" for item in hit_structures]
                + [f"execution:{item}" for item in hit_semantics]
            )
        scored.append({
            "id": str(row.get("id") or ""),
            "score": score,
            "hit_structures": sorted(hit_structures),
            "hit_execution_semantics": sorted(hit_semantics),
            "hit_motifs": sorted(hit_motifs),
        })

    distinct_needs = set(primary_hits)
    if not profile.motifs and len(distinct_needs) > 1:
        return {
            "selected_ids": [],
            "reason": "multiple_primary_needs_without_composite_motif",
            "profile": profile.manifest(),
            "candidates": scored,
        }

    eligible = [row for row in scored if row["score"] > 0 and row["id"]]
    eligible.sort(key=lambda row: (-row["score"], row["id"]))
    selected = [row["id"] for row in eligible[:max(0, int(max_examples))]]
    return {
        "selected_ids": selected,
        "reason": "matched_primary_need" if selected else "no_primary_match",
        "profile": profile.manifest(),
        "candidates": scored,
    }


__all__ = [
    "ConstructionNeedProfile",
    "build_construction_need_profile",
    "route_construction_examples",
]
