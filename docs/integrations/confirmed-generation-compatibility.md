# Confirmed-generation compatibility

Compatibility adapters translate supported legacy representations before deterministic candidate checks. They retain user intent, active binding identity and version ownership.

| Boundary | Detailed source |
| --- | --- |
| Current specification versus historical decisions | [Intent and evidence handoff](../architecture/intent-evidence-handoff.md) |
| Address aliases, labels and reconfirmation | [I/O purpose labels](../architecture/io-binding-labels.md) |
| Compact and full-wire generation | [Generation call contracts](../architecture/generation-call-contracts.md) |
| Scoped and structural repairs | [Repair boundaries](../architecture/generation-repair.md) |
| Model values and retired workflow hints | [Runtime ownership](../architecture/runtime-ownership.md) |

[CandidateService](../../src/plc/candidate_service.py) owns the shared generation boundary. [plc.device_identity](../../src/plc/device_identity.py) normalizes device spelling without renumbering: `X010` remains `X10`, not decimal `X8`. Existing saved snapshots retain original bytes and hashes; read-only views may project aliases without rewriting them.

Hardware requirements are derived from current user evidence and confirmed facts, not speculative model questions. The owner is [plc.hardware_profiles](../../src/plc/hardware_profiles.py). Historical VFD, polarity and source-handoff regression investigations are preserved in the [process archive](../process/README.md).

[test_confirmed_compatibility.py](../../tests/test_confirmed_compatibility.py) and [test_confirmed_reconfirmation.py](../../tests/test_confirmed_reconfirmation.py) cover the corresponding acceptance path. Numerical results belong to the tested-version reports rather than this compatibility reference.
