# Capability discovery, selection and review

## Shared context

Existing generation and edit contexts include the raw requirement, user facts,
current program, selected language and available engineering interfaces.
[generation_context](../../src/application/generation_context.py),
[confirmed_generation_context](../../src/application/confirmed_generation_context.py)
and [FBD's context builder](../../src/application/fbd.py) use
[augment_generation_knowledge](../../src/application/capability_context.py).
MCP `get_generation_context` exposes the same context; the server performs no
nested model generation. An optional context receipt can accompany candidate
creation and Ladder patches. Missing or expired receipts retain the existing
trace-gap behavior and do not weaken engineering version checks.

[discover_capabilities](../../src/knowledge/retriever.py) is the application
retrieval entry. [functional_queries](../../src/knowledge/capability_discovery.py)
expands Chinese and English functional vocabulary. Explicit calls use the
existing exact fact resolver. Manual results retain retriever ordering and
Registry CPU applicability. Engineering callables retain prototype identities
and ports, including colliding names. FBD candidates come from its current
engineering catalog. A native instruction fact does not establish an ST calling
signature; that gap remains explicit.

Program iteration, stage sequences and the PLC scan cycle are distinct concepts.
A scan-cycle requirement does not request iteration capabilities. Source-purpose
matching distinguishes iteration from control loops and cyclic redundancy;
these terms alone do not establish functional relevance.

Functional discovery creates no parallel index or per-instruction workflow.
The CPU scopes technical evidence and callable applicability; it does not route
Direct, detailed, Design or repair. Sequential behavior does not prescribe SFC,
STL or an internal state representation.

## Selection and budget

[SELECTION_POLICY](../../src/plc/maintainability.py) requires behavior, types,
interfaces, scan order, edges, stop and reset to be correct before comparing
repetition and ease of modification. Candidates are technical options, not a
whitelist or confirmed process facts. No opcode, instruction count or rung count
constitutes acceptance. Discovery and local review issue zero model requests.
Sufficient Ladder Direct still uses its existing single generation call.

The existing model knowledge budget includes option briefs, source facts and
selection guidance. User requirements and required engineering interfaces retain
their existing priority. Duplicate evidence is removed; residual references may
be replaced to make room. Function coverage precedes alternatives, with no fixed
candidate-count limit. Source facts are retained as complete evidence units.
Compact source views omit uninterpreted chapter copies and use existing compiled
evidence, without manufacturing semantic verification.

Optional `generation_handoff.capability_discovery` records discovered functions,
candidate identities, delivered and omitted units, replaced references, gaps and
token estimates. `reconcile_discovery` reflects the final compiler delivery.
The estimates use the existing heuristic, not provider billing. Discovery never
writes implementation choices to `confirmed_spec` or changes program protocols.

## Local review and public projection

[review_maintainability](../../src/plc/maintainability.py) records:

| Language | Local coverage | Remaining gap |
| --- | --- | --- |
| Ladder | IR output calls; repeated writes, shared conditions and contiguous groups; condition stability and output write footprints with locations | Unknown/implicit effects, unsupported expression forms, replacement equivalence and process behavior |
| FBD | Engineering object calls, locations and wire records; explicit named endpoints attached to call locations | Coordinate-only endpoint bindings, graph replacement equivalence and process behavior |
| ST | Lexically identifiable calls and line-initial literal assignment repetition, with source locations excluding comments/strings | Types, shared control conditions, control flow, equivalence and process behavior |

Repeated calls/writes produce advisory findings. They do not prove redundancy or
safe replacement, do not edit programs and cannot reject a correct candidate.
Incomplete ST lexical input remains unverified. Existing Core and language checks
continue to own candidate acceptance.

### Ladder condition normalization

[ConditionAnalysis](../../src/plc/condition_analysis.py) classifies every current
input/output tag, including legacy forms. It consumes the existing address policy,
exact CPU runtime facts, Registry operand roles and source-checked behavior
definitions. No per-opcode knowledge pack or model call is added. Stable timer and
counter reads require the applicable runtime fact; first-scan/special, indexed,
asynchronous and unknown reads are retained when their stability is unproven.
Edge conditions keep their evaluation sites and multiplicity. Known output calls
are retained even when stateful; their writes block moving dependent conditions.
Definitions with unmet state premises or unresolved extents use conservative
device-family write footprints where Registry roles permit; otherwise they form
an explicit barrier. No missing extent is inferred as a single-device write.

[normalize_shared_conditions](../../src/plc/condition_normalizer.py) removes
duplicate stable conjunctions and factors pure parallel arms. Pure conjunction
terms may exchange positions before the first edge or unknown condition. Fresh
programs use dynamic programming over consecutive branches to minimize condition
occurrences, then network count, then representation changes. The original
networks remain available choices, preventing a shorter later guard from expanding
an earlier shared subgroup. Output order and call multiplicity remain unchanged;
ordinary repeated coil writers are not combined into an OR condition.

The search retains the current flat Ladder protocol: whole parallel blocks cannot
become `shared_inputs`, and nested shared subgroups are expressed as consecutive
networks when this reduces repetition. This is not unrestricted Boolean
minimization or a guarantee of globally minimal PLC instructions. Local edits keep
network IDs and order for existing scope enforcement; they only normalize owned
network interiors. Cached edge/dependent guards cannot be expanded into repeated
reads. Annotations are preserved within current field limits; annotated absorption
and annotation overflow retain their original structure.

Normalization receipts retain the existing public `changes`/`skipped` fields.
The optional `maintainability_review.condition_review` contains condition counts,
effect precision and barrier locations. Review runs the same analysis on a copy,
reports remaining opportunities, and never edits the program or claims native
or process verification. Both API and MCP use this Core path.

Candidate and version `maintainability_review` is optional for old records.
[public_discovery](../../src/application/capability_context.py) supplies the same
compact Web/MCP projection: delivered options, sources, omissions, gaps and known
estimates; full queries and interface copies stay out of result metadata.
The existing result/check areas display actual use and check coverage. A source
match, readable CSV or structurally accepted graph does not establish native
execution or device behavior. Deep model review remains an explicit operation.

## Research context

[AutoPLC](https://arxiv.org/html/2412.02410v2) motivates functional/interface
retrieval; [Spec2Control](https://arxiv.org/html/2510.04519v1) motivates engineering
library context; [SemaPLC](https://arxiv.org/html/2608.18565v1) motivates evidence
bound to checks. These references do not establish verification of this
implementation. The integration adds shared discovery and review, without an
independent planning agent, task orchestrator or default repair loop.
