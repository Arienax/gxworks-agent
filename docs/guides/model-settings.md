# 模型设置与配置存储

## 连接模型

在“设置 → 模型”中填写服务地址和凭据，选择明确的模型 ID，再测试连接。模型列表连接测试与生成能力验证是不同操作。能力配置先从本地目录和已有证据解析；需要真实请求时，显式确认单项验证。旧版深度批量扫描已停用。不要用模型列表中的第一个名称代替自己的选择。

模型参数按端点与模型的能力合同展示。`reasoning_effort`、采样参数及其他调优值由保存的模型配置控制。Direct / Design 只选择[分析方式](../architecture/analysis-modes.md)，工作流不会替用户恢复 `high` 或其他推理强度。

能力来源、参数取值和请求映射由[能力合同](../integrations/capability-contract-v3.md)说明。配置字段的实现来源是 [storage.config](../../src/storage/config.py)，请求合并规则是 [model_runtime.request_policy.resolve_request](../../src/model_runtime/request_policy.py)。界面不提供的字段不要从其他服务的示例配置直接套用。

<a id="storage"></a>
## 查询实际存储位置

模型配置与模型观测数据库保存在用户数据目录，不随源码目录或前端构建移动。Windows 默认使用 `%APPDATA%\PLC-AI-Studio`。其他平台及环境覆盖规则由 [storage.user_data.user_data_dir](../../src/storage/user_data.py)维护。

从源码仓库根目录查询当前 Python 进程实际使用的路径：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe -c "from storage.config import get_config_path, get_observations_path; print(get_config_path()); print(get_observations_path())"
```

这条命令只输出路径，不输出密钥。`PLC_AI_DATA_DIR` 可指定用户数据目录；`PLC_AI_CONFIG_PATH` 可指定配置文件。两者都要求绝对路径。显式配置文件覆盖时，观测数据库位于该文件旁；准确优先级见 [config_path](../../src/storage/user_data.py) 和 [get_observations_path](../../src/storage/config.py)。修改启动进程的环境变量后需重新启动服务。

配置文件包含模型配置及凭据引用。系统凭据存储与 JSON 文件分开；拷贝 JSON 不会把 Windows 凭据一并带到另一台机器。

## 迁移、备份与恢复

旧源码目录或旧发布包旁的配置会在配置加载时按 [migrate_user_settings](../../src/storage/config.py)迁移。已有目标配置优先。迁移保留源文件，对配置和 SQLite 使用暂存文件、迁移日志及原子替换；SQLite 备份包含已提交的 WAL 数据。迁移失败时保留恢复材料并报告原因，不以默认配置覆盖原有数据。

备份前正常停止使用这组设置的服务，保存查询得到的配置文件、观测数据库以及同目录的迁移恢复材料。不要在 SQLite 仍有写入时只复制主数据库文件。恢复或切换目录前先保留现有目标副本；遇到迁移冲突时检查报错及两侧文件，不要删除日志后重试。

工程、版本与产物属于工作区，独立于上述模型设置。更新程序时保留工作区；切换到另一源码 clone 后，检查是否设置了不同的环境覆盖路径。

## 排查参数没有生效

检查当前选择的模型配置、服务地址和模型 ID，再检查请求记录中的实际参数。保存配置时的值、显式省略和继承状态应分别核对，不应仅凭界面上的滑块位置推断已发送值。旧项目里的工作流推理强度字段不再拥有参数决定权。

导出方式见[诊断与记录](diagnostics.md)。共享记录前检查其中的需求文本、端点、响应及工程信息；不要上传配置文件或凭据。
