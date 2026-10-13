# Repository checks

This repository accepts Web interface source, HTTP interface definitions, frontend tests and user documentation. `publication/public-files.json` lists the accepted files, including the reviewed README demonstration at `resources/assets/demo.gif`. Add new files explicitly and include the allowlist change in the review.

Enable local checks after cloning:

```sh
git config core.hooksPath .githooks
python tools/check_public_boundary.py --index
python tools/check_public_boundary.py --history
```

The pre-commit hook checks the complete staged tree. The pre-push hook checks every commit reachable from local refs. CI repeats the checks, runs tests and builds the interface. Review the diff before pushing and do not bypass the hooks.

Keep credentials, personal engineering files, generated output and LFS content out of commits. Binary content is limited to the reviewed README demonstration; executable packages are distributed separately on the project's Releases page. Review every frame before replacing demonstration media.
