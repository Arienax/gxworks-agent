# Contributing

Contributions here concern the Web interface and its documented HTTP contract. The private engineering backend is maintained separately. Do not attach private source, research records, engineering projects, credentials or backend executables to issues, pull requests or workflow artifacts.

Use Node.js 22.14 or newer, Python 3.10 or newer, and Git. Enable `.githooks` as described in [publication rules](docs/publishing.md). Install dependencies with `npm ci` in `web`, run `node --experimental-strip-types --test tests/*.test.mjs`, and run `npm run build`. Run publication checks before pushing. New public files require an explicit allowlist change and review.

UI work must preserve backend-owned permissions, approval flows and PLC rules. A frontend change does not establish native GX Works2 or PLC execution support. Include the validation actually performed in your pull request.

Contributions are governed by [LICENSE](LICENSE).
