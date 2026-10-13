# Contributing

Contributions here concern the Web interface and its documented HTTP contract. Do not attach credentials, personal engineering projects or generated artifacts to issues, pull requests or workflow artifacts.

Use Node.js 22.14 or newer, Python 3.10 or newer, and Git. Enable `.githooks` as described in [repository checks](docs/publishing.md). Run `npm ci`, `npm test` and `npm run build` at the repository root. Run repository checks before pushing. New public files require an explicit allowlist change and review.

UI work must preserve backend-owned permissions, approval flows and PLC rules. A frontend change does not establish native GX Works2 or PLC execution support. Include the validation actually performed in your pull request.

Contributions are governed by [LICENSE](LICENSE).
