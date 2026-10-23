# Public source separation

Source publication separates research inputs and local development output from
the application. Ordinary source, maintained regression tests, guides and runtime
resources do not require registration or per-file approval.

The shared rules in [source_policy.py](source_policy.py) exclude development
reports, temporary experiments and tests, native acceptance records, vendor
manuals, collected project samples, private knowledge databases, credentials,
caches and build output. New normal source files are included automatically.
Temporary tests belong in temporary or research directories; maintained tests
remain source code. Native engineering templates under `src/gxw/templates/`
remain runtime resources and are included.

`python scripts/export_open_source.py --destination <new empty directory>` uses
these same exclusions. It retains existing runtime-metadata projections so raw
research locators and manual prose do not enter exported catalogs. Git history
is never copied into the export. Git checkouts and source archives use the same
directory traversal and exclusions. Neither `.gitignore`, `.git/info/exclude`
nor a user's global Git ignore file decides publication contents. Put local
experiments and output in the excluded material directories; add stable private
input locations to the shared policy. In particular, local application config,
raw GXW observations and private evidence catalogs are excluded by that policy.

`python tools/check_public_boundary.py` checks the tracked files for excluded
materials, credentials and local/private paths. `--disk` checks a prepared
directory before staging. Both modes require the core source and runtime
templates, reject unresolved LFS pointers and unexpected binary/non-UTF-8 files,
and inspect the contents and manifest of template archives. Native templates and
supported image formats receive binary content checks; they are not silently
skipped when UTF-8 decoding fails. There is no per-file allowlist or exact source
file-count requirement; the old
`public-files.json` inventory is no longer used. Source and build checks run
separately and do not determine whether a source file is approved for publication.

Research reports, original samples and acceptance output remain in the separate
private materials repository. Source pushes and executable-package releases are
separate operations.
