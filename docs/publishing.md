# Publication boundary

This repository has independent Git history. Do not merge the private development repository, add it as a submodule, copy its `.git` directory, or push its branches or tags here.

Only reviewed frontend source, interface schemas, synthetic frontend tests, these documents and publication checks belong here. Core implementation, native adapters, templates, knowledge databases, engineering samples, research scripts, experimental records, reports, snapshots and archives remain in private development storage. Private development can retain its own private remote and history.

`publication/public-files.json` lists each approved public file. New frontend files must be reviewed and added explicitly. The checker also enforces an independent path boundary, text-only content, a size limit and common credential/native-code checks. A filename allowlist cannot prove that arbitrary new text is safe: inspect the diff before every push.

Enable local checks after cloning:

```powershell
git config core.hooksPath .githooks
python tools/check_public_boundary.py --index
python tools/check_public_boundary.py --history
```

The pre-commit hook examines the complete staged tree. The pre-push hook examines every commit reachable from local refs, including files added and later deleted. The workflow repeats the history check and tests. CI runs after upload, so local review and the pre-push check are the first boundary. Do not bypass hooks. No LFS content or binary files belong in Git history.

## Binary releases

Reviewed Windows assets are built in the private development environment and attached to GitHub Releases separately. Publish MCP and Web as independent packages. Each must pass isolated startup and engineering checks, contain its runtime dependencies and third-party licenses, and identify the public project URL.

First-party Python implementation is compiled into native extensions; native adapters are obfuscated. Exclude raw source, private bytecode, debug symbols, source maps, obfuscation mappings, experimental records, reports, build logs and research archives. Legacy developer bundles are not public release assets. Audit the final archives; the Git hooks do not audit Release uploads.

Local protection increases analysis effort but cannot guarantee a minimum reverse-engineering cost or prevent extraction of algorithms and runtime data. Public interfaces describe user-visible behavior.

Keeping the old development repository private does not recall copies previously downloaded or publicly forked. Existing licenses and third-party notices continue to apply to their respective materials.
