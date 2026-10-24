# Authored fact task projection

The projection is a detached retrieval view, not an admission check and not a rewrite of the authored corpus or Core rules.

## Public retrieval options

`build_knowledge_context`, `retrieve_fact_aware_knowledge`, `retrieve_instruction_facts`, and `resolve_instruction_records` accept the following optional selectors:

- `language`: the schema language identity, such as `st`, `ladder`, or `fbd`.
- `aspects`: a list of semantic slots (a string is normalized to one slot), including `parameters`, `constraints`, `execution`, `effects`, operand facets, call signatures, or an exact dimension/prefix.
- `project_mode`: binds the existing typed condition context key `project_mode`; it is not a new fact scope field.
- `instance_context`: explicitly known typed condition inputs. Numeric operand values belong in `parameter_values`; other condition keys use their existing context names. Retrieval does not execute caller-supplied `memory_reader` callbacks.
- `version`: an exact fact-scope version identity when known.

`query` still determines semantic slots when `aspects` is omitted. Generation/edit retain their default operand, behavior and constraint aspects. Exact CPU/form lookup remains ahead of supplemental ranking; `top_k=0` does not remove explicit instruction records.

## Selection and uncertainty

Select task roots using CPU/form/language/version and only provably false instance conditions, then retain full transitive dependencies. Unknown conditions, missing version/language, conflicts, missing dependencies and cycles stay visible as gaps. A dependency that conflicts with the requested language is retained as `selection_applicability=not_applicable`, not silently promoted or removed from its dependent's prerequisites.

Source status is never upgraded. Every projected packet carries `fact_selection` with requested selectors, selected and available counts, root IDs, gaps, and `whole_instruction_complete=false`. An empty requested slot is a gap even if mandatory identity/capability roots remain. Related instructions are projected with their own opcode and propagate unresolved selection gaps. Prerequisites available in the current retrieval record pool (including related instruction payloads) are joined only on exact opcode and concrete owner model identity. Different CPUs/forms never supply a same-named prerequisite. Conflicting same-owner candidates remain ambiguous; unavailable prerequisites remain explicit missing-dependency gaps. No unrelated corpus lookup is guessed.

Operand-specific narrowing uses only source-verified, source-checked, certainly applicable order signatures. Competing signatures disable positional narrowing and produce an ambiguity gap. Facet requests retain operand arity/order anchors. Unsupported scope fields produce an unresolved schema gap.

Model delivery strips pure source/review positions and raw manual fields, retains technical conditions, dependencies, knowledge status, selection applicability and Core execution capability boundaries, and exposes selection uncertainty without backend source IDs. Existing PLC validation still receives authoritative unprojected definitions.

## Regression command

`PYTHONPATH=src python -m unittest discover -s checks -p test_fact_task_projection.py -v`

The public synthetic suite exercises selectors, language-specific operand order, conditional/project-mode applicability, CPU/form mismatches, unknown/missing/cyclic prerequisites, metadata projection, generation packing and full context construction with zero supplemental slots. For the context test only, corpus/authority fixtures, unrelated non-instruction lookups and external Haystack routing are substituted; the instruction resolver, selector, generation packer and context formatter execute normally. These are offline retrieval checks, not GX Works native compilation or hardware evidence.
