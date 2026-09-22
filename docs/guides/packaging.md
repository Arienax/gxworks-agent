# 打包与更新

## 使用与核对发布包

从[仓库 Releases](https://github.com/Arienax/gxworks-agent/releases)选择需要的版本，核对标签、提交和附件名称。CI 构建产物与正式 Release 分别查询；仓库存在打包脚本只说明可以构建。

完整解压应用目录，保留可执行文件旁的资源、脚本和网关目录。启动方法见[安装指南](getting-started.md)。在不同机器使用时，按集成指南准备 GX 软件及原生依赖。

## 从源码构建

构建描述是 [web.spec](../../packaging/pyinstaller/web.spec)和 [mcp.spec](../../packaging/pyinstaller/mcp.spec)，编排入口是 [build_web_package.ps1](../../scripts/build_web_package.ps1)。在隔离的 Windows 构建环境安装根 [requirements.txt](../../requirements.txt)，按脚本参数提供 Python 及已构建的 Simulator Gateway：

```powershell
python -m pip install -r requirements.txt
.\tools\build_simulator_gateway.ps1 -OutputDirectory "$env:TEMP\gxworks-build-gateway"
.\scripts\build_web_package.ps1 -Python (Get-Command python).Source -GatewayDirectory "$env:TEMP\gxworks-build-gateway"
```

网关构建条件见[网关说明](../../simulator_gateway/README.md)。脚本会构建前端及 Web/MCP 启动器；产物目录和可选参数以脚本定义为准。准备真实 PLC 只读入口时，另按[读取器说明](../../hardware_reader/README.md)构建，不要用 Simulator Gateway 替代。

## 文档、模板与资源

包内说明由 [package_documentation.py](../../scripts/package_documentation.py) 的 `stage_documentation` 从同一源码目录生成。它保留文档间的相对链接，将被引用的源码复制为只读用途的 `.txt` 参考文件，并生成 `documentation-manifest.json`，分别记录源文件与分发文件 SHA-256。入口由 `build_web_package.ps1` 在构建后调用；PyInstaller 的运行资源清单保持原有定义。

直接检查文档分发，可指定独立输出目录：

```powershell
python scripts/package_documentation.py --destination "$env:TEMP/gxworks-documentation"
```

许可证及第三方归属原样复制。报告中的原始二进制证据引用保留对应字节。修改说明时更新来源文档或生成器，不编辑 `dist` 中的副本。

GXW 模板与知识资源按已有来源、许可和清单打包。冻结样本、原生失败记录及其校验和保持原样。许可证和第三方归属分别来自 [LICENSE](../../LICENSE)与 [THIRD_PARTY_NOTICES.md](../../resources/knowledge/THIRD_PARTY_NOTICES.md)，随对应资料分发。

发布前在产物上执行现有 [web_package_smoke.py](../../scripts/web_package_smoke.py)，核对版本来源、资源、启动器、文档链接以及 `--self-test-settings` 等已有自检。原生 GX/仿真验收使用[版本报告](../reports/README.md)记录环境和实际结果。

## 更新而不丢设置

正常停止旧服务，保留工作区和[实际配置目录](model-settings.md#storage)的备份，再把新包解压到独立目录。不要以新包的默认配置覆盖用户设置，也不要把旧可执行文件、旧 `web/dist` 或旧网关混入新包。

源码升级先检查 `git status --short`，保留本地改动，再更新预期分支并重新构建。启动后核对程序来源、工作区及模型配置。迁移冲突按模型设置指南处理，不删除恢复日志来强行重置。
