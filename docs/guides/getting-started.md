# 安装与第一个工程

## 准备环境

源码运行需要 Git、Git LFS、Python 和 Node.js/npm。依赖文件分别是 [Web 后端依赖](../../requirements/web.txt)和[前端 package.json](../../web/package.json)；[依赖说明](../../requirements/README.md)列出环境的用途。GX Works2 集成使用 Windows；单独浏览工程、生成和检查本地产物无需启动 GX 软件。

在 Windows PowerShell 中执行：

```powershell
git clone https://github.com/Arienax/gxworks-agent.git
cd gxworks-agent
git lfs install --local
git lfs pull
.\build-web.bat --no-pause
.\start-web.cmd
```

`build-web.bat` 调用 [build_web_source.ps1](../../scripts/build_web_source.ps1)：准备本目录 `.venv`、安装后端依赖，再安装锁定的前端依赖、生成类型并构建 `web/dist`。构建失败时查看仓库根目录的 `build-web.log`。已有独立 Python 环境时可用 `--frontend-only` 只构建前端；该选项不安装后端依赖。

启动后选择工作区。已有工作区应选择包含 `index.json` 和 `projects` 的外层目录；新工作区可选择空目录。浏览器没有自动打开时，使用启动窗口给出的 `Workbench link`。保留启动窗口，退出服务时按 `Ctrl+C`；关闭浏览器只关闭页面。

使用发布包时，完整解压包含 `start-web.cmd` 和 `GXWorks-Agent-Web.exe` 的目录后运行启动脚本。不要单独移动可执行文件。包的来源、构建与更新见[打包与更新](packaging.md)。

<a id="first-project"></a>
## 第一个工程

1. 在[模型设置](model-settings.md)中连接模型，并明确选择模型 ID。
2. 新建工程，选择 PLC 型号和程序表示。填写控制需求，包括已知的输入、输出、动作条件和停止条件。
3. 选择 Direct 或 Design。Direct 给出一个实现；Design 提供方案比较。检查规格中的地址、触点动作电平、参数及用途名称，回答确实缺失的必要参数后确认。
4. 生成后检查活动版本的梯形图、软元件用途和导出文件。生成失败时查看[诊断与记录](diagnostics.md)，不要把保留的失败候选当作已保存程序。

工作区版本、修改范围和任务恢复见 [Web 工作台](../integrations/web.md)。向 GX Works2 发送前，按 [GX 操作指南](gxworks2.md)备份目标工程并完成原生检查。

## 指定工作区或只读检查

以下命令从源码仓库根目录运行。将示例工作区改为本机实际目录：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -m integrations.web --workspace "D:\PLCWorkspaces\example" --read-only --port 8765
```

去掉 `--read-only` 可启动可写工作台，加 `--open-browser` 可打开登录页。参数及默认值由 [integrations.web.__main__.main](../../src/integrations/web/__main__.py)定义；启动器的选端口和窗口行为在 [start_web.ps1](../../scripts/start_web.ps1)。以控制台显示的地址为准。

只读检查不迁移旧版本，不启动任务或 GX 执行。工作区被占用时，正常关闭占用它的写入服务；不要删除运行中服务的锁。登录链接包含本机会话凭据，不要分享或写入工程。

## 更新后界面仍旧

先确认当前启动的是预期源码目录或发布包。源码更新后重新运行 `build-web.bat --no-pause`，正常停止旧服务，再运行同目录 `start-web.cmd`。工作区与模型配置的存放位置不同；配置路径和备份方式见[模型设置](model-settings.md#storage)。
