# Read-only SQLite storage attribution

This diagnostic measures an existing sealed candidate database. It does not
rebuild the database, hydrate instruction owners, import the runtime, or change
which candidate the application uses. It writes only one new summary JSON file.
No fact text, source text, owner IDs, or private input paths are included in it.

From the repository root on Windows, replace the input placeholder with the
completed candidate's actual SQLite path:

```powershell
python tools\audit_runtime_sqlite_storage.py --sqlite "<candidate-directory>\plc_facts.sqlite" --output "tmp\owner-store-storage-audit.json"
```

The output parent directory must already exist and the output filename must be
new. Keep the candidate inactive and unchanged while the diagnostic runs. Do not
delete any WAL, journal or shared-memory sidecar to make the audit pass: a live or
unsealed database is rejected, and no repair or checkpoint is attempted.

The report separates physical SQLite pages (tables, indexes, FTS shadow tables,
unused page space and freelist) from logical stored JSON/string lengths. Logical
lengths cannot be summed as though they were physical disk usage, and neither
measurement is expanded runtime memory. If SQLite lacks `dbstat`, the JSON
explicitly reports that per-object physical attribution is unavailable; logical
sizes remain separately labelled and are not substituted for physical sizes.

On an invalid, changed or unreadable input, the command returns nonzero and prints
a bounded failure message instead of claiming success. Inspect a JSON only after
a successful exit. Existing output files are never overwritten. Share only the
small JSON report; there is no need to upload the database or original full bundle.

This standalone diagnostic has synthetic correctness/read-only regression tests.
It changes no runtime behavior and does not itself prove full-corpus semantic
correctness, native PLC compilation, or publication acceptance.
