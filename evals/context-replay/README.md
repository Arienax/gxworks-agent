# Offline context replay

This is a promptfoo **Python provider**, not another prompt/agent implementation.
It calls the existing `analyze_requirement` → review → `seal_confirmation` →
`generate_confirmed_ladder` path with recorded providers. The Python Core,
Pydantic projections, Haystack routers, bundled SQLite index and final request
assembly are real. The small completions in `cases.json` are synthetic boundary
fixtures, not verified PLC solutions and not transcripts of a live model.

Install the pinned Python dependencies and the promptfoo version used by the context-replay CI job:

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
connections, DNS/datagram calls and implicit production-provider resolution; any attempted call
fails the replay even if the application catches it. CLI telemetry and update
checks must be disabled as above (CI sets both). Runs are serial because the
existing retrieval compatibility hooks are process-global.

## What is checked

- Stable parameter identities, including generic transport questions that must
  not become a drive model; typed zero and false remain real selected values.
- Historical review drafts recover choices without manufacturing note provenance,
  changing answers or crossing stable parameter identities.
- Review choices/defaults/provenance do not enter generation parameter fields;
  a generated candidate overview stays in review, while a user-edited summary
  or note survives. Unknown historical free text is not guessed away.
- Design without an explicit technical question doesn't backfill debug cases;
  generation uses current instruction evidence. Debugging still has its own
  allowed source lane. Low-level tests check SQL/entity/dense filtering before
  candidate limits, rather than merely hiding hits after top-k.
- One fixture completion for A and one for B; no automatic annotation model,
  no changes to B's supplied reasoning effort and no old audit envelope in B.

The result covers context projection, evidence routing and recorded-provider calls. Live-model timing uses the [Agent B measurement procedure](../../docs/guides/agent-b-measurement.md).

## Private diagnostic archives

```powershell
python scripts/context_replay.py --archive path/to/gxworks-diagnostics-job.zip
```

Only the frozen confirmed spec and last recorded response are read. Saved model
profiles/keys are never reused. A generation archive without A's completion is
marked `generation_only_no_analysis_reconstruction`, not fabricated into a full
A/B test. By default the report is written atomically back into the same
operator export as `offline_replay.json`; the existing job, transcript,
operator actions and decision receipt members are preserved. Re-running replaces
that one replay member instead of creating duplicate ZIP entries.

Use `--output archive-replay.json` only when a detached report is explicitly
wanted; detached output still must be a new path and leaves the source archive
unchanged. Default reports contain checks/counts/hashes, not full prompts.
`--include-content` adds sanitized source and runtime views for local
investigation; review before sharing. Do not check private archives or generated
reports into the repository.

## Component boundaries

Typed parameter identity and review/generation projection belong to [parameters.py](../../src/plc/specification/parameters.py). Retrieval scope belongs to [scope.py](../../src/knowledge/scope.py). Their integration rules are documented under [runtime ownership](../../docs/architecture/runtime-ownership.md); replay cases exercise those implementations directly.

## Servo identity and retrieval follow-up

The `servo-typed-handoff` fixture is a small synthetic boundary sentinel,
not a servo control program: independent signal owners/levels, a conditionally
inactive module question, actual ZRN evidence and one recorded completion. The
live operator ZIP is never checked into this corpus. Confirmation rejects two
unlinked X/Y owners at the same address and unfinished choices requesting actual
details; explicit row sharing remains supported. Coincident A/B position targets
produce an advisory warning, not a global ban on equal numeric parameters.

The source launcher uses `.venv` when present. Installing with a different global
`python` does not repair that environment. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements/web.txt
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe scripts/web_entry.py --self-test-knowledge
```

`build-web.bat` performs this dependency check in its actual Web interpreter.
Restart the Web service after updating. Frozen builds have the same
`--self-test-knowledge` diagnostic. A failure remains nonblocking at service
startup and is captured with a safe exception/dependency code in receipts
and the original interaction export. The original `retrieval_failed` trace
contained no exception detail; it cannot establish the historical cause.

Instruction facts follow completion-flag links present in the read-only index,
within the same manual/revision and existing budget. They do not inject a
selected opcode, add a prompt recipe, or declare behavior verified. The ZRN
arity and operand order use Mitsubishi JY997D16801K §6.3.1 (B-110); completion
ownership cautions are in §4.7.4 (B-81..83). Catalogue metadata is not described as
successfully retrieved evidence when the runtime is missing a dependency.
