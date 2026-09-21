# Offline context replay

This is a promptfoo **Python provider**, not another prompt/agent implementation.
It calls the existing `analyze_requirement` → review → `seal_confirmation` →
`generate_confirmed_ladder` path with recorded providers. The Python Core,
Pydantic projections, Haystack routers, bundled SQLite index and final request
assembly are real. The small completions in `cases.json` are synthetic boundary
fixtures, not verified PLC solutions and not transcripts of a live model.

Install once (Python 3.10+, Node 22 for promptfoo):

```powershell
python -m pip install -r requirements/web.txt
npm install --prefix .tools/context-eval --no-audit --no-fund promptfoo@0.123.1
```

Run from the repository root. The Python-only entry is useful without Node:

```powershell
python scripts/context_replay.py --output replay-results.json
$env:PROMPTFOO_DISABLE_TELEMETRY = '1'
$env:PROMPTFOO_DISABLE_UPDATE = '1'
$env:HAYSTACK_TELEMETRY_ENABLED = 'false'
$env:PROMPTFOO_PYTHON = (Get-Command python).Source
& ./.tools/context-eval/node_modules/.bin/promptfoo.cmd eval -c evals/context-replay/promptfooconfig.yaml --no-cache -o promptfoo-results.json
```

On Linux/macOS use `export` and the executable without `.cmd`. Dependencies
require networking only at installation time. Replay has no live provider,
remote grader, model download or cloud upload. The Python provider blocks socket
connections and implicit production-provider resolution; any attempted call
fails the replay even if the application catches it. CLI telemetry and update
checks must be disabled as above (CI sets both). Runs are serial because the
existing retrieval compatibility hooks are process-global.

## What is checked

- Stable parameter identities, including generic transport questions that must
  not become a drive model; typed zero and false remain real selected values.
- Review choices/defaults/provenance do not enter generation parameter fields;
  a generated candidate overview stays in review, while a user-edited summary
  or note survives. Unknown historical free text is not guessed away.
- Design without an explicit technical question doesn't backfill debug cases;
  generation uses current instruction evidence. Debugging still has its own
  allowed source lane. Low-level tests check SQL/entity/dense filtering before
  candidate limits, rather than merely hiding hits after top-k.
- One fixture completion for A and one for B; no automatic annotation model,
  no changes to B's supplied reasoning effort and no old audit envelope in B.

The replay result is a boundary regression result. It is **not** a claim about
live reasoning latency, behavioral correctness, GX compilation, hardware timing,
or general semantic understanding. It doesn't solve hardware-negation scope or
calibrate a continuous conveyor's timer-to-distance model.

## Private diagnostic archives

```powershell
python scripts/context_replay.py --archive path/to/diagnostics.zip --output archive-replay.json
```

Only the frozen confirmed spec and last recorded response are read. Saved model
profiles/keys are never reused. A generation archive without A's completion is
marked `generation_only_no_analysis_reconstruction`, not fabricated into a full
A/B test. Original files are not modified; output must be a new path. Default
reports contain checks/counts/hashes, not full prompts. `--include-content` adds
sanitized source and runtime views for local investigation; review before sharing.
Do not check private archives or generated reports into the repository.

## Component boundaries

`plc/specification/parameters.py` uses Pydantic's discriminated value union and
separate review/generation serializers. `hardware.<registered question ID>` is
an exact Core namespace; a different explicit namespace cannot bind a hardware
field even when the old id or question text collides. Missing metadata uses only
registered IDs and a small frozen **exact label** compatibility set. Unknown
parameters remain generic. No substring-based identity inference remains.
Text form values of a declared number/boolean use JSON primitive parsing before
strict validation; `0` is not a boolean and `"false"` is not truthiness-cast.

`knowledge/scope.py` uses Haystack 2.31 `MetadataRouter` for request lanes and
source eligibility. Existing SQLite, FTS, structured instruction lookup and local
LSA remain; no DocumentStore reindexing, external vector service or reranker is
added. Haystack-selected metadata partitions constrain FTS/entity/dense recall
before their candidate caps, then the same policy checks final records. Source
IDs, original text, model/source applicability and budget receipts are retained.
Unclassified legacy records are not relabeled as official evidence.

References: Pydantic unions/serialization
https://docs.pydantic.dev/latest/concepts/unions/
https://docs.pydantic.dev/latest/concepts/serialization/
Haystack MetadataRouter/filters
https://docs.haystack.deepset.ai/docs/metadatarouter
https://docs.haystack.deepset.ai/docs/metadata-filtering
promptfoo Python provider/telemetry
https://www.promptfoo.dev/docs/integrations/python/
https://www.promptfoo.dev/docs/configuration/telemetry/
