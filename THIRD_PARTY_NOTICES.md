# Third-party notices

The project's own copyright, original repository and author attribution are
recorded in [NOTICE](NOTICE), retained in source copies and in released packages.

GXWorks Agent is independent of Mitsubishi Electric. GX Works, MELSEC and
Mitsubishi Electric are trademarks of their respective owners. This repository
does not include GX Works2, its manuals, vendor binaries, or a license to use
them. Native blank project templates are included for interoperability; see
[license scope](LICENSE-SCOPE.md).

Python and npm dependencies retain their own licenses and copyright notices.
Dependency versions are listed in `requirements/` and `web/package-lock.json`.
Distributors of binaries must collect and retain the notices required by the
dependencies they include. The project's AGPL-3.0 license does not replace
those terms.

Web Ladder generation embeds the DeepSeek Harness Python SDK and distributes
the independent DSH SDK runtime, copyright (c) 2026 DeepSeek, under MIT.
The SDK source attribution and retained license are in
`src/integrations/dsh_runtime/vendor/`; Full packages also include the standalone
runtime and its Node.js and dependency notices under `dsh-runtime/`.
DSH Desktop and its user settings are not required or copied into the product.

ST recognition uses [tree-sitter](https://github.com/tree-sitter/tree-sitter)
0.25.2 and [tree-sitter-iec61131-3-st](https://github.com/HeytalePazguato/tree-sitter-iec61131-3-st)
0.1.2, pinned at commit `00e24f50f8ded0d71c146659030fb66d7c4d1529`.
Both retain their MIT licenses. Binary distributions include their original
license files under dependency notices. GX source adaptation and PLC semantics
are maintained in GXWorks Agent Core.

Executable packages include project-authored fact representations and original
examples. Vendor manuals and original manual-text indexes are excluded. Source
references identify the basis of facts and do not grant a vendor license.
