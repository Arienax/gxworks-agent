# License scope / 许可范围

Copyright (c) 2026 Arienax and the respective contributors.

The unmodified Mozilla Public License 2.0 in [LICENSE](LICENSE) applies to the
project-authored public interface files described below. It is not a license
for every component in the private development tree or an official package.

| Material | Applicable terms |
| --- | --- |
| Project-authored files in `web/`, excluding installed dependencies and generated build output | MPL-2.0; see `web/LICENSE.md` |
| Project-authored public interface definitions, tests, configuration, documentation and demonstration assets listed in `publication/public-files.json` in the public repository, or `publication/public/publication/public-files.json` in the private tree | MPL-2.0, except license texts and material with a separate third-party notice; the runtime agreement applies only to the Runtime it defines |
| Root `README.md`, `README.zh-CN.md`, `CONTRIBUTING.md`, `LICENSE-SCOPE.md`, `SOURCE_CODE.md` and `THIRD_PARTY_NOTICES.md` | MPL-2.0 for project-authored text; quoted licenses and third-party notices retain their original terms |
| Individual tools | The notice in the file or `tools/LICENSE.md`; the directory name does not grant a license to other tools |
| Official compiled Core, backend and native adapters, plus project-authored private runtime configuration and resources | [LICENSE-RUNTIME.md](LICENSE-RUNTIME.md) |
| Private source in `src/`, `native_adapters/`, `hardware_reader/`, `simulator_gateway/`, private build scripts, research, evidence, original templates and engineering samples, and other files outside the public scope above | All rights reserved unless a file expressly states another license; access to the private development tree does not grant public redistribution rights |
| Third-party software, vendor material and other third-party resources | Their original licenses and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |

Generated executable forms of MPL-covered files remain subject to the MPL's
distribution requirements. The runtime agreement does not restrict public
forks, modification, commercial use or compliant distribution of MPL-covered
files. Combining separate MPL and proprietary files does not change their
respective licenses. Copying MPL-covered code into a new file remains subject
to the MPL's definition of Modifications.

Previously distributed versions, including versions released under Apache-2.0,
retain the licenses under which those copies were distributed. This change does
not revoke those grants or relicense third-party material.

The MPL notice attached to the public files is:

> This Source Code Form is subject to the terms of the Mozilla Public License,
> v. 2.0. If a copy of the MPL was not distributed with this file, You can obtain
> one at https://mozilla.org/MPL/2.0/.

公开界面及其配套文件按上表适用 MPL-2.0，贡献者保留各自版权。闭源核心仅按
`LICENSE-RUNTIME.md` 授予使用权；私有源码、研究及第三方材料不因根目录中的
MPL 文本而获得开源授权。已有版本的许可继续有效。
