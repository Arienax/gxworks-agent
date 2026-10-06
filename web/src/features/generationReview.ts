// Presentation only: check coverage and evidence decisions are owned by Core.
const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const rows = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter(v => typeof v === "string") : [];

export function generationReview(metadata: unknown) {
  const data = object(metadata), handoff = object(data.generation_handoff);
  const discovery = object(handoff.capability_discovery), review = object(data.maintainability_review);
  const included = new Set(strings(discovery.included_candidates));
  return {
    available: !!Object.keys(discovery).length || !!Object.keys(review).length,
    candidates: rows(discovery.candidates).filter(row => included.has(String(row.id))),
    omitted: strings(discovery.omitted_candidates).length + strings(discovery.omitted_evidence).length + strings(discovery.replaced_evidence).length,
    gaps: rows(discovery.gaps),
    used: rows(review.used_capabilities), findings: rows(review.findings), coverage: rows(review.coverage),
    conditionBarriers: rows(object(review.condition_review).barriers),
    tokens: object(discovery.tokens),
    behaviorVerified: review.behavior_verified === true,
  };
}
