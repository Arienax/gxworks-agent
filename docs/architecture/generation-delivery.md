# Generation delivery

## Version identity

The viewer, downloads and reports use the selected version's saved specification, IR and registered artifacts. A later project edit does not change an older version's report. Artifact availability is read from the manifest; formats vary by program representation.

[delivery_summary](../../src/application/delivery.py) gathers version-bound specification, changes, source receipts, tests and native records. `render_delivery` in the same module renders the Markdown report. [application.projects](../../src/application/projects.py) owns artifact lookup and integrity checks.

## Refresh and failed candidates

Refreshing a Ladder preview renders the saved canonical IR without another model call. A missing old SVG can be recreated from valid IR; a fingerprint mismatch is reported rather than displayed as the selected program.

[build_gxworks2_csv_bundle](../../src/application/fresh_exports.py) re-exports a saved
Ladder IR with the current deterministic exporter. The saved version and original
artifacts remain unchanged, and this operation issues no model request.

An output connected directly to the left bus starts with an independent TRUE
condition. [csv_export](../../src/gxworks2/csv_export.py) lowers it using the exact
CPU's verified always-on relay from Core; it cannot inherit the preceding
network's condition. Missing CPU facts prevent this export. CSV readability or
IR checks alone do not establish the delivered listing's behavior; the teaching
runner reuses Core on the imported CSV's actual instruction order.

An unaccepted or invalid candidate may have a diagnostic preview. Its validation state remains attached to the candidate, and it does not become the active version. Partial repairs are materialized before rendering according to [repair boundaries](generation-repair.md).

## Evidence labels

Reports keep structural validation, review, simulation and native compilation separate. Simulation records identify the backend and bind the program, test suite and result hashes. Native validation records retain the operator, tool version and any attached GXW hash. An operator report remains an operator report.

Missing evidence is displayed as missing. Failed or interrupted execution remains in the result history. [test_delivery_summary.py](../../tests/test_delivery_summary.py) and [test_generation_delivery.py](../../tests/test_generation_delivery.py) cover report and artifact binding. User export instructions are in [diagnostics](../guides/diagnostics.md).
