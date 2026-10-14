# MPL source code / MPL 源码获取

Public source repository: https://github.com/Arienax/gxworks-agent

Official downloads: https://github.com/Arienax/gxworks-agent/releases

The Web package includes the matching, editable first-party interface source
in `web/`, including `web/src/`, `web/openapi.json`, frontend tests and build
configuration. Its npm launcher source is `tools/web-runtime.mjs`; root
`package.json` and both npm lockfiles record the corresponding build inputs.
These files, rather than minified `web/dist/` assets, are the source form of
the bundled interface. Package documentation and demonstrations are already
provided in their original source form.

The independent MCP package does not distribute the Web interface. Its
first-party MPL-covered notices and documentation are supplied in source
form. The proprietary Core and native adapters are covered by
[LICENSE-RUNTIME.md](LICENSE-RUNTIME.md); their source is not MPL-covered.

For a repository checkout, use its revision. For an official Web package,
use the source supplied with that package to reproduce that version. The
repository's current default branch may differ from an older release. Keep
this notice, [LICENSE](LICENSE), [LICENSE-SCOPE.md](LICENSE-SCOPE.md) and the
applicable third-party notices with distributed executable forms, and make
the corresponding MPL-covered source, including your modifications, available
as required by MPL 2.0 section 3.2.

公开源码与官方下载地址见上。Web 包内 `web/` 和 npm 启动器是该包对应版本的
可修改源码；压缩后的 `web/dist/` 不能替代源码。独立 MCP 包不分发 Web 界面。
再分发时须保留许可及源码获取声明，并按 MPL 提供相应源码及修改。闭源核心
及第三方组件分别适用其原有条款。
