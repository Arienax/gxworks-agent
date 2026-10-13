# Publication boundary

This repository has independent Git history. Do not merge the private development repository, add it as a submodule, copy its `.git` directory, push its branches or tags here, or upload its binaries as Releases or Actions artifacts.

Only reviewed frontend source, interface schemas, synthetic frontend tests, these documents and publication checks belong here. Core implementation, native adapters, templates, knowledge databases, engineering samples, research scripts, experimental records, reports, snapshots and archives remain in private development storage. Private development can retain its own private remote and history.

`publication/public-files.json` lists each approved public file. New frontend files must be reviewed and added explicitly. The checker also enforces an independent path boundary, text-only content, a size limit and common credential/native-code checks. A filename allowlist cannot prove that arbitrary new text is safe: inspect the diff before every push.

Enable local checks after cloning:

```powershell
git config core.hooksPath .githooks
python tools/check_public_boundary.py --index
python tools/check_public_boundary.py --history
```

The pre-commit hook examines the complete staged tree. The pre-push hook examines every commit reachable from local refs, including files added and later deleted. The workflow repeats the history check and tests. CI runs after upload, so local review and the pre-push check are the first boundary. Do not bypass hooks. No LFS content or binary releases are published from this repository.

Keeping the old development repository private does not recall copies previously downloaded or publicly forked. Existing licenses and third-party notices continue to apply to their respective materials.
