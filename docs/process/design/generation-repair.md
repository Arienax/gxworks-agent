# Generation repair boundary

> 历史设计记录，冻结来源版本 `f2f1781`。有效规则见[generation-repair](../../architecture/generation-repair.md)；下文保留原实验、选择和失败。

The saved project, the unaccepted candidate, and a repair response are distinct
objects. A `mode: partial` response during validation repair is applied to the
current **unaccepted full candidate**, not to the last saved version. This also
works on the first generation, when no saved version exists.

The API prompt uses the existing `plc_generation_contract` schema. Public inputs
such as `shared_inputs` cannot contain `parallel_block`; local parallel blocks
belong in branch inputs. Structural repair does not guess contact polarity,
rewrite priority logic, or replace failed programs with canned examples.

Pipeline:

1. Parse and check container shape before running compatibility normalizers.
2. Preserve the materialized candidate before PLC hard validation.
3. Try the existing deterministic repairs, then at most three model repairs.
4. Apply a valid partial repair by unique rung ID, preserving untouched rungs,
   order, and comments. Model repairs cannot add/delete/reorder rungs; a truncated
   full response cannot masquerade as the entire repaired program.
5. Revalidate the complete candidate. A later repair sees all prior accepted
   repair changes and the newest diagnostic. There is a 240-second between-call
   repair budget; each repair request is capped at a 120-second provider timeout.
6. Build/validate the canonical PLC IR, then derive JSON, SVG, ST and CSV from it.
   Only an independently reviewed proposal may become a local version. No GX
   import, simulator operation, PLC write, or automatic approval is added.

Evidence-scoped and explicitly requested contract repairs retain their original
scope restrictions. Contract-repair jobs do not gain hidden retries. Model
response-acceptance rejection and transport errors are not structural retry
signals. Cancellation is checked between work stages and repair calls.

Validation exhaustion is exposed as `generation_validation_failed`, with bounded
schema locations, reason codes and repair counts. Provider failures use safe
classifications such as `model_timeout`. Raw exception bodies, credentials and
private paths are not copied into the diagnostics. HTTP, persisted job records,
and SSE use the same public projection.

Regression coverage includes a 40-rung fixture with the reported invalid
`rungs[36].shared_inputs[3].type = parallel_block` and a repaired rung 37 containing
the three M20/M21/M22 priority branches. Tests exercise the real validation,
IR, SVG, ST and CSV pipeline, using injected deterministic model responses.
Browser tests use a mocked HTTP API. Neither proves an arbitrary model-generated
program's functional correctness or hardware safety; native GX compilation and
simulator/PLC acceptance remain separate checks.
