# 研究证据存储与提交

## 存储边界

普通 Git 保存可审查的机器可读摘要、freeze/replay 脚本、必要的小型 witness、截图、研究结论及其环境和失败边界。现有 `research/results/*.json` 不因行数多就整体迁走或压成一行；先判断它是摘要、消费接口还是可重建的原始输出，保留已有消费者和引用。

证据归档和大型原始输出仅保存在本地。`research/evidence/` 下的 ZIP、7z、rar、tar、gz、bz2、xz、zst、tgz、tbz、tbz2、txz，以及 `research/results/raw/` 下的所有文件，不进入普通 Git 或 Git LFS，也不作为 Actions artifact 或 release asset 上传。`.gitignore` 忽略这些产物，`.gitattributes` 禁用这些目录的 LFS filter；摘要和必要的小型未压缩 witness 继续纳入 Git。

freeze 脚本继续生成真实归档供本地复现。摘要记录本地归档路径、生成命令、具体 CPU、GX 版本、来源和未通过项；该路径不代表 GitHub 上存在可下载的包。保留原始证据和冻结快照的内容，不增加新的哈希验证体系。

## 获取与提交

知识库仍使用 Git LFS。新工作区安装 LFS 后启用仓库钩子：

```powershell
git lfs install --local
git config --local core.hooksPath .githooks
git lfs pull
```

仓库钩子保留知识库所需的 LFS checkout/commit/merge 行为；pre-push 先检查所有待推送 ref，再把原始 ref 输入交给 Git LFS。不要用 `git lfs install --force` 覆盖这些钩子。

冻结证据后，只暂存摘要、脚本及必要的小型 witness：

```powershell
git add -- research/results/<summary>.json research/<freeze-script>.py
python scripts/check_repository_storage.py --staged
git diff --cached --stat
git lfs status
```

以上占位符是待替换的文件名。证据包保持在本地，不使用 `git add -f`、`git lfs push --all` 或 `git lfs push --object-id` 上传证据，不跳过提交或推送钩子。正常知识库 LFS 上传失败必须先处理，不能提交只有指针但没有对象的运行时资源。

从旧工作区更新时，可用 `git rm --cached -- <明确归档路径>` 取消跟踪并保留本地文件。新克隆没有这些证据包；需要本地归档回放时，从自己的本地备份恢复到原路径。使用 [tests/local_evidence.py](../../tests/local_evidence.py) 的回归测试会明确跳过缺少本地归档的案例，已有包损坏、缺少成员或断言不通过仍是失败。跳过的案例属于未验证，不能报告为原生回归通过。

## 防止再次膨胀

[check_repository_storage.py](../../scripts/check_repository_storage.py) 默认对新增普通 Git blob 设置 **5 MiB** 上限；这是仓库贡献阈值，不是 GitHub 文件限制。未修改的旧大文件不会使每个 PR 都失败。证据归档和 `results/raw/` 无论多小、无论是原始文件还是 LFS pointer 都被拒绝。

pre-commit 检查暂存修改及索引中仍被跟踪的证据包。pre-push 检查每个待推送 ref 的当前树和新增历史，在任何 LFS 上传前拒绝违规内容；新分支排除本地缓存的远端 refs，缺少远端缓存时检查全部历史。已有远端提交不在本次普通推送检查中重写；远端提交尚未获取时检查报错，应先 fetch。

[Repository Storage Policy](../../.github/workflows/repository-storage.yml) 在 PR/push 中复查所有新增对象及中间提交，不能用“先加包、下一提交删除”绕过检查。它读取 Git blob，不依赖下载 LFS。CI 是推送后的检查；克隆时应启用本地钩子，才能在上传前拦截。

```powershell
python scripts/check_repository_storage.py --base origin/main --head HEAD
```

Git 钩子不是 GitHub 服务端的 LFS 权限控制，手工调用上传接口或绕过钩子仍可能写入存储。CI 本身也不等于分支保护规则；仓库管理员可将该 check 设为合并必需项。

## 迁移范围与历史

2026-10-03 曾将 `6ab646e` 工作树中的 **57 个 ZIP，共 123,248,061 字节**迁移到 LFS；最大的 `gxw-current-source-check-20261002.zip` 为 35,893,073 字节。当时上传后使用空 LFS 缓存从远端重新下载成功，记录见 [Actions run 37053321042](https://github.com/Arienax/gxworks-agent/actions/runs/37053321042)。这是历史实测记录，不再是当前上传策略。

当前清理从 `main` 的最新树取消上述证据归档的跟踪，并将后续证据归档改为本地保存。本地原始包保留。已有历史中的 ZIP blob、LFS pointer 和其他旧分支不因这次删除自动消失；本次不强推或重写已发布历史。

根据 [GitHub 的 LFS 删除说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/removing-files-from-git-large-file-storage)，删除文件或重写 Git 历史后，远端 LFS 对象仍计入存储配额。需要 GitHub Support 协助清除指定对象，或另行授权删除并重建仓库。删除整个仓库会丢失关联的 issues、stars、forks，不能作为仅删除证据包的默认步骤。普通 `git gc` 或本地 `git lfs prune` 不会释放远端配额。

## 提交层次

一个研究模块形成可验证成果后，按依赖顺序组织提交：

1. **research finding**：最小可复现源码、冻结脚本、摘要、失败边界和必要的小型 witness；证据归档留在本地。
2. **Core semantic change**：Python Core 的格式/语义/编辑不变量及对应 Core 回归，引用前一层证据。
3. **integration**：Application、MCP、Web 接入与对应契约/端到端测试、用户文档。

每层尽量独立构建和验证；涉及同一文件的不同职责时按 hunk 暂存，不为了形式制造不能运行的中间状态。合并时保留有意义的提交边界。已发布的混合提交不在普通维护 PR 中追溯拆分。
