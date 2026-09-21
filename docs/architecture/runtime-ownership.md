# Runtime ownership and persistent model settings

## Profile-owned tuning

Project/workflow `effort` is a retired compatibility field. Existing project
bytes remain readable and are not mass-rewritten. New projects store null,
project updates ignore effort, and public project views return null.
GenerationRequest, analysis, generation, FBD, review and repair use no workflow
preference. Direct/Design cannot change model tuning.

The application model gateway removes top-level/extra-body reasoning_effort and
its configured wire alias before provider resolution. The model profile's
userModelSettings, requestOverrides and generationDefaults remain intact.
Value/omit/inherit behavior is unchanged, including contractless legacy profiles.
No profile preference means omission, not an automatic high/low fallback. Old
flat-config migration also removes preset effort from the selected imported
profile; only an explicit saved template value is carried over. The retired
{effort} placeholder is not a setting, including in extra_body. Existing saved
model profiles and explicit preset selection are not rewritten.
Other parameters, streaming, cancellation and retry policy are unchanged.
Low-level operator-authorized capability probes still use their trial profile;
this is not a prohibition on those explicit test requests.

Compatibility API effort arguments and benchmark --effort are accepted but
ignored. Benchmark output labels that argument as retired; offline replay checks
omission of workflow hints. Actual model-profile behavior is tested at the
provider wire-parameter boundary, not inferred from a fixture's ModelRequest.

## Single scoped retrieval entry

Application, agent_runtime and integrations use knowledge.retriever, not
knowledge.core or private facade attributes. The existing architecture suite
checks imports, aliases and literal dynamic imports. It is not a hostile-code
sandbox or an attempt to prevent arbitrary Python reflection.

Every public row lookup, including MCP and legacy core forwarding, resolves the
Haystack task/source policy before backend candidate limits. Caller source_lanes
can narrow this policy, never widen it. Returned injected rows are filtered too.
Generation excludes debug/design; explicit debug retains debug evidence; format
and contract repair do not retrieve. Explicit design retrieval remains analysis-only.

Old core retrieval names are compatibility forwarders, not a second builder.
Private backends remain internal to the knowledge implementation and its tests.
No corpus deletion, new model request, instruction ban or reranking recipe is
introduced. Existing tests that intentionally looked up curated debugging cases
now select the debug task instead of relying on an unscoped row API.

## Stable user settings

config.json and model-observations.sqlite share one directory:

- Windows: %APPDATA%/PLC-AI-Studio/
- macOS: ~/Library/Application Support/PLC-AI-Studio/
- Linux: $XDG_CONFIG_HOME/PLC-AI-Studio/, otherwise ~/.config/PLC-AI-Studio/

PLC_AI_DATA_DIR selects an absolute directory. PLC_AI_CONFIG_PATH selects an
absolute config filename; observations stay beside it. Explicit file overrides
are isolated and never import another checkout's configuration. Resolving paths
alone performs no I/O. Knowledge resources, project workspaces and Credential
Manager targets do not move.

When the active config is absent, source builds inspect only their own
src/config.json and then repository-root config.json. Frozen builds inspect the
executable directory, never _MEIPASS or arbitrary working directories. The first
existing source wins. An existing target always wins; other clones are not
heuristically merged. Legacy files remain available as rollback data.

## Migration and failure recovery

Location migration preserves raw JSON bytes, unknown fields, profile IDs and
credentialTarget values. It does not read, copy or rewrite Credential Manager
keys. The existing explicitly writable profile-schema migration remains a
separate operation. Invalid legacy JSON/database data raises an error instead of
being replaced by defaults. Missing read-only settings still use the bundled
safe template without initializing persistent files.

filelock serializes migration publication and atomic config-file replacements
across processes. sqlite3.Connection.backup captures committed WAL contents into
a checked standalone snapshot. Existing target observations are never replaced.
Close old app processes first: future writes by an old version after the
snapshot cannot be included. This is a copy-once migration, not bidirectional sync.

Files are prepared, checked and flushed in the destination directory. A sealed
.settings-pending journal is published before any destination file; observations
are published first and config last. Each replace is atomic on the local
filesystem; the pair is crash-recoverable, not a multi-file atomic rename.
Current config/observation access resumes a pending journal before use. Conflicts
with a subsequently modified target fail without overwriting it or discarding
source/staged data. The destination contains old data or fully prepared files,
not a silently accepted partial JSON/database copy.

.settings-migration.json records completion, hashes and private legacy paths.
It also prevents deleting the active config from resurrecting an old profile/key
on another clone's launch. An explicit later save can initialize a safe default.
Protect retained legacy files as before; do not publish them or the private
receipt. Do not manually remove pending journals to work around a reported
conflict. Settings writes are atomic per file; this is not a new whole-service
cross-process read-modify-write transaction system.

## Verification and diagnostics

Update requirements/web.txt and restart the backend. filelock is in the shared
context requirements so standalone MCP also receives the dependency. Frozen
executables must be rebuilt. The existing Windows package CI runs the synthetic
--self-test-settings along with SDK/knowledge checks and migration regressions.

From the repository root, with PYTHONPATH=src:

```powershell
.\.venv\Scripts\python.exe scripts/web_entry.py --settings-path-info
.\.venv\Scripts\python.exe scripts/web_entry.py --self-test-settings
```

Path info does not migrate data, read keys, start the server or call a model.
The self-test uses disposable synthetic state only. Existing test owners cover
profile priority, import boundaries, path stability, raw byte preservation, WAL,
concurrent migrations, publication/cleanup interruption and conflict retention.
No additional test-owner file or permanent CI workflow is introduced.

Implementation references: Python sqlite3 Connection.backup documentation,
SQLite Online Backup API, and filelock FileLock documentation.
