# Capability coverage

Cross-refactor capability coverage is defined in
[capability-coverage.json](capability-coverage.json). It records capabilities
whose behavior has previously been fixed or stabilized and that must survive
later architecture changes.

The manifest is not a progress log. Each entry declares an owner, production
checks, regression owners, and one of two states:

- `enforced`: every declared owner/consumer/test check must remain valid.
- `tracked_gap`: the existing capability still has a known migration gap.
  Both the surviving owner checks and the machine-observable gap must remain
  explicit. If code closes the gap while the manifest still says
  `tracked_gap`, the audit fails until the entry is updated to
  `enforced`.

Run the audit from the repository root:

```powershell
python tools/audit_capability_coverage.py --check
python tools/audit_capability_coverage.py --json
```

The architecture test suite invokes the same audit, so Source Layout Validation
enforces the manifest on pull requests.

## Current migration gaps

The current manifest intentionally tracks gaps that should not be hidden by a
passing broad regression suite:

- device canonical identity is owned by `plc.device_identity`, while direct
  structured device lookup does not yet canonicalize through that owner;
- structured device records support more families than the current target
  extractor recognizes;
- the older complete instruction-source precedence set has not yet been
  replaced by a data-backed authority contract in the direct resolver.

Closing one of these gaps requires changing its manifest state and adding the
new consumer/regression evidence in the same change. Deleting the gap check
without establishing an enforced owner is not a valid migration.

## Adding coverage

Add an entry when a fix introduces a reusable capability or when an architecture
change creates a new ownership boundary. Prefer one owner and explicit
consumers over duplicating facts across prompts, retrieval code, validators and
exporters.

Supported check types are deliberately small:

- `file`: repository file exists;
- `python_symbol`: a Python function/class/method remains defined;
- `contains`: a required production or regression reference remains present;
- `absent`: a tracked gap remains observable.

The audit validates repository-relative paths, unique capability IDs, regression
test ownership, state rules and all checks. It does not certify PLC behavior by
string matching; behavioral correctness remains the responsibility of the
referenced regression tests.
