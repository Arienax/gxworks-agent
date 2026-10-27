# Representative read-only runtime comparison

Compare two inactive owner-store candidates through their manifests. This is not
another migration and does not enable either candidate.
Run from the updated public repository root, retaining its normal catalog seed:

```powershell
python scripts\verify_owner_store_runtime.py --source-manifest "<source-directory>\instruction_definitions.manifest.json" --candidate-manifest "<candidate-directory>\instruction_definitions.manifest.json" --output "tmp\owner-store-runtime-comparison.json"
```

Use an existing output parent and a new JSON filename. Keep both database
snapshots sealed and unchanged while the comparison runs. Do not remove journal,
WAL or SHM sidecars to force a check to pass. No database is rebuilt or modified,
no deployed resource is replaced, and no permanent environment setting changes.
The JSON report contains the comparison results; database contents remain local.

Two fresh child Python processes each load the existing seed and attach their
explicit manifest. This isolates interpreter state; it does not flush filesystem
or operating-system caches. Runtime attachment checks identity and structure;
the explicit offline database digest check follows the selected requests. The comparison records attachment-time
owner hydration and then exercises only six explicit owner keys, nine selected
constraint cases with expected passed/failed/unverified outcomes, representative
language identity/alias queries and model applicability boundaries. It never
requests all owner facts or invokes native GX Works, PLC hardware or an LLM.

A report passes only when the required samples are exercised and their semantic
results agree. Missing samples or incomplete evidence remain unverified, not an
assumed pass; disagreement or invalid input fails. Exit status 0 means the bounded
comparison passed, 3 means failed or unverified, and 2 means the CLI could not
complete. Inspect the report's coverage gaps and limitations rather than treating
identical missing results as success. Comparing the same database with itself is
explicitly unverified (`same_input`), not migration evidence. The complete
database digest check at the end verifies the declared input; attachment must hydrate
zero owner facts. No result automatically accepts a release.

Memory counters belong to each measured child process. On Windows the report
uses GetProcessMemoryInfo current/peak working-set bytes; on supported Unix
platforms it labels RSS separately. Unsupported counters are null with a reason,
not zero. These metrics are not interchangeable across platforms and are not a
measurement of the whole desktop application. Store counters are cumulative read
events, not necessarily distinct owners or total operating-system I/O.

The comparison covers the named runtime cases. It does not establish exhaustive
instruction semantics or accept a release. Retain the candidate directories and
their source and audit records when investigating a reported difference.
