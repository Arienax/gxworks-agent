# Contributing

Contributions here concern the Web interface and its documented HTTP contract. Do not attach credentials, personal engineering projects or generated artifacts to issues, pull requests or workflow artifacts.

Use Node.js 22.14 or newer, Python 3.10 or newer, and Git. Enable `.githooks` as described in [repository checks](docs/publishing.md). Run `npm ci`, `npm test` and `npm run build` at the repository root. Run repository checks before pushing. New public files require an explicit allowlist change and review.

UI work must preserve backend-owned permissions, approval flows and PLC rules. A frontend change does not establish native GX Works2 or PLC execution support. Include the validation actually performed in your pull request.

Public interface contributions are governed by [MPL-2.0](LICENSE); see the
[license scope](LICENSE-SCOPE.md). Public forks, modifications and compliant
distribution of MPL-covered files are permitted. The proprietary Runtime has
its own [free use agreement](LICENSE-RUNTIME.md).

Contributors retain their copyright. By intentionally submitting a contribution
to an MPL-covered file, you provide it under MPL-2.0, including its normal
copyright and patent grants. No copyright assignment or additional blanket
relicensing grant to the maintainer is required. Preserve existing copyright,
license and third-party notices, and ensure you have the right to submit your
work under the target file's license. State the applicable terms separately
for contributions to differently licensed files; this guide does not relicense
private or third-party material.

贡献者保留自己的版权。MPL 文件的贡献遵循 MPL-2.0 正常授权机制，不要求转让
版权或额外授予维护者任意再许可权。公开 Fork、修改和合规分发 MPL 部分均被
允许；其他许可文件按其明确条款处理。
