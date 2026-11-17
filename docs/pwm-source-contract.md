# PWM reviewed source and bounded software contract

This profile is not PLC admission, native GX Works compilation, or a physical
I/O timing guarantee. Unresolved stop-edge timing, startup transitions, finite
pulse counts, and source conflicts remain outside the admitted software subset.

## Source chain

1. The ordinary reviewed FX5 pipeline emits typed facts. A curated selection of
   192 facts across PWM/DPWM and four CPUs, including actual dependencies and
   signatures, is published in `pwm_reviewed_facts.json`. It contains technical
   relations and provenance only, not private manual text or page caches.
2. The independent AB table parser reads numerical cells from the reviewed
   table layout. It does not import a Core timing constant. The AG parameter
   table (PDF page 332, printed page 330) was independently extracted and checked;
   its document digest and distinct source identity are retained. FX5S Y0–Y3
   conflicts are not resolved by prioritizing either manual.
3. `tools/build_pwm_contract.py` compiles the reviewed source into the generated
   profile. Numeric limits, output ranges, hardware tables and OFF-time predicate
   values come from these facts. The consumer's relation vocabulary is code,
   not a second copy of the manual's values.
4. Static checks and software execution use the same complete generated contract
   and predicates. Runtime checks its schema, version and selected facts without
   reopening the offline input or recompiling it. Explicit build checks compare
   the reviewed input and generated contract. The software source binding retains
   the reviewed source identity.

Reproduction:

    python tools/export_pwm_reviewed_facts.py --facts <pipeline-facts.json> \
      --review-manifest resources/instructions/mitsubishi/pwm_reviewed_facts.json --check
    python tools/build_pwm_contract.py --check
    python -m unittest discover -s checks -p 'test_pwm_source_pipeline.py'

Updating an input requires review, source export, and regeneration. The exporter
reads AB values from the pipeline input, not from the manifest's existing rules.
Its manifest fixes the reviewed identities and separately reviewed AG fact.

## Distribution

The FX5/GX Works3 PWM/DPWM software extension is frozen. Its research code,
reviewed facts, generated contract and source tests remain available for offline
research, but MCP and Full distributions exclude `pwm_contract.generated.json`
and `pwm_reviewed_facts.json`. Product entrypoints and release checks do not run
the FX5 PWM diagnostic. These research checks are not required for the current
GX Works2 release. This restriction does not remove ordinary PWM instruction
knowledge for GX Works2 CPUs.

## Live graph and safety boundaries

A live registry owner always takes precedence, even if its facts are empty or
withdrawn. No missing witness is filled from the snapshot. When no owner has been
installed, the caller may explicitly opt into the version-bound published subset.
The report distinguishes these modes and never claims complete instruction
admission. `software_version` supplies the generic fact version scope; CPU version
is not substituted for it.

Execution internally selects exactly eleven required roots. Each must actually
pass, with source identity, effective scope, conditions and dependencies intact.
Callers cannot select roots through execution context. Generic static checks keep
their default of evaluating all constraint roots. Explicit subset reports state
`selected_roots_only`; empty or malformed selections are unknown, not passed.

Source-reference normalization ignores only `source_kind`, an audit category
removed by the existing public runtime projection. It does not ignore source
IDs, manual identities, revisions, page references, or applicability conditions.

The regression fixtures use public reviewed identities with synthetic mutations.
Old tests that invented source IDs were updated so successful controls retain
real provenance, while changed source, scope, dependencies and conditions remain
unknown. A standalone generated predicate is not authority without its witnesses.
