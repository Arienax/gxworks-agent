# 研究证据存储与提交

## 存储边界

普通 Git 保存可审查的机器可读摘要、freeze/replay 脚本、必要的小型 witness、研究结论及其环境和失败边界。现有 `research/results/*.json` 不因行数多就整体迁走或压成一行；先判断它是摘要、消费接口还是可重建的原始输出，保留已有消费者和引用。

`research/evidence/**/*.zip` 保存为 Git LFS 对象，路径与归档内容不变。小型未压缩 witness、截图和源码仍可留在普通 Git。新增的大型原始 JSON/日志可放在 `research/results/raw/`，该目录由 LFS 跟踪；摘要留在原来的 results 目录，并记录原始数据路径、生成命令、具体 CPU、GX 版本、来源和未通过项。freeze 脚本继续生成真实归档，由 Git LFS clean filter 负责暂存指针，不让生成器自己伪造 pointer。

其他压缩格式必须显式增加对应的 LFS 跟踪规则。不要用全仓库 `*.json` 或整个 `research/**` 的 LFS 规则，把可读摘要、脚本和回归 witness 一起隐藏。历史冻结摘要不在本次存储迁移中修改，也不增加新的哈希验证体系。

## 获取与提交

仓库已有 LFS 知识库，沿用同一 Git LFS 安装：

```powershell
git lfs install --local
git lfs pull
```

仅需要证据归档时可选择性下载：

```powershell
git lfs pull --include="research/evidence/**/*.zip" --exclude=""
```

冻结证据后，先把摘要和相应归档暂存，再检查 Git 索引：

```powershell
git add -- research/results/<summary>.json research/evidence/<package>.zip
python scripts/check_repository_storage.py --staged
git diff --cached --stat
git lfs status
```

以上 `<summary>`、`<package>` 是待替换的文件名。完成相应 commit 后正常 `git push`；不要跳过 LFS pre-push hook，也不要设置 `lfs.allowincompletepush`。LFS 上传失败必须先处理，不能提交只有指针但没有对象的交付。新加入跟踪规则的已有文件须 `git add --renormalize -- <明确文件路径>`，只执行 `git lfs track` 不会自动转换已提交内容。

使用 LFS 的 CI 先通过 `actions/checkout` 的 `lfs: false` 获取指针，再调用 [缓存 checkout action](../../.github/actions/checkout-lfs/action.yml)。缓存键来自当前版本的 LFS 对象 ID；Linux 和 Windows 共用 `.git/lfs/objects` 缓存，对象变化时恢复已有缓存并仅下载缺失对象。下载后立即保存缓存，后续测试失败也不会使已下载对象丢失。主验证流程先准备共享缓存，再启动各验证任务，避免首次执行时各任务同时重复下载。

只有专门读取 Git 对象的 storage check 不下载 LFS；它检验指针格式和对象大小，不证明远端对象可用。缓存首次创建或被淘汰后仍需下载，已有下载流量不会因缓存或删除文件而退回。不要把 GitHub Download ZIP 当作一定包含 LFS 原始数据的交付方式，复现实验优先使用 clone + LFS pull。

## 防止再次膨胀

[check_repository_storage.py](../../scripts/check_repository_storage.py) 默认对新增普通 Git blob 设置 **5 MiB** 上限；这是仓库贡献阈值，不是 GitHub 文件限制。未修改的旧大文件不会使每个 PR 都失败。证据目录的 ZIP/7z/tar/gz/bz2/xz/zst 和 `results/raw/` 文件必须使用 LFS。

[Repository Storage Policy](../../.github/workflows/repository-storage.yml) 检查 PR/push 范围内所有新增对象及中间提交，不能用“先加大包、下一提交删除”绕过检查；并核对目标版本中证据归档的表示。读取的是 Git blob，不是已经 smudge 成真实归档的工作区文件。检查没有应用运行时副作用，不改模型、Core、Application、MCP 或 Web。

本地检查整个分支：

```powershell
python scripts/check_repository_storage.py --base origin/main --head HEAD
```

CI 检查本身不等于分支保护规则；仓库管理员可将该 check 设为合并必需项。本次修改不更改仓库保护设置。

## 迁移范围与历史

本次将 `6ab646e` 工作树中的 **57 个 ZIP，共 123,248,061 字节**迁移到 LFS；其中最大的 `gxw-current-source-check-20261002.zip` 为 35,893,073 字节。传输使用一次性隔离分支，上传后使用空 LFS 缓存从远端重新下载成功；记录见 [Actions run 37053321042](https://github.com/Arienax/gxworks-agent/actions/runs/37053321042)。一次性写权限 workflow 不进入正式修复分支。

这是不改历史的迁移：普通 Git 在新版本中只保存指针，后续归档版本进入 LFS；**已有历史里的 ZIP blob 仍然存在**。普通删除、`git gc` 或合并这个修复不会让所有历史 clone 自动缩小，完整 clone 仍会获取可达的旧归档；LFS 下载还会使用独立的存储和流量配额。需要轻量新工作区时可使用浅克隆与按需 LFS 下载，但它不适合完整 bisect。

真正清理历史需另行安排：先离线备份 refs/仓库，确认相关分支、tag、PR、其他 clone 与 LFS 对象可用性，再做定向历史迁移，核对 commit 映射并协调强推。不要直接在日常工作目录运行 `git lfs migrate import --everything` 或 `git push --force --mirror`。本次不重写 `main`，不拆改已发布的 `6ab646e`。

## 提交层次

一个研究模块形成可验证成果后，按依赖顺序组织提交：

1. **research finding**：最小可复现源码、冻结脚本、摘要、失败边界和 LFS evidence；不混入产品行为变化。
2. **Core semantic change**：Python Core 的格式/语义/编辑不变量及对应 Core 回归，引用前一层证据。
3. **integration**：Application、MCP、Web 接入与对应契约/端到端测试、用户文档。

每层尽量独立构建和验证；涉及同一文件的不同职责时按 hunk 暂存，而不是单靠目录分组。不要为了形式制造明知不能运行的中间状态。合并时保留有意义的提交边界；把三层 squash 回一个大提交会再次失去 bisect 粒度。已发布的混合提交不在普通维护 PR 中追溯拆分。
