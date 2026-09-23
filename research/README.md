# 研究证据

本目录保存 GXW 逆向的输入、原生截图、失败语料、冻结基线和机器可读结果。样本事实以对应清单和原始文件为准。

## 阅读路径

当前编辑、导入及原生验证步骤见 [FBD 指南](../docs/guides/fbd.md)。已完成的实验从[报告索引](../docs/reports/README.md)进入；假设、预注册及下一步采样见[过程索引](../docs/process/README.md)。

## 数据维护

保留原始样本和归档名称，不覆盖失败结果或以重新生成的文件替换冻结输入。新增证据应记录来源、工具版本、输入/输出哈希、命令与观察结果；需要修正旧结论时新增复审记录并链接旧记录。

[results](results/)保存结构化结果，[evidence](evidence/)保存归档证据，[baselines](baselines/)保存历史实现对照。产品代码不导入历史基线。研究工具位于 [tools](../tools/)，具体工具与参数从对应实验记录查询。

许可沿用 [LICENSE](../LICENSE)及资料各自的来源约定；手册知识资料的第三方归属见 [THIRD_PARTY_NOTICES.md](../resources/knowledge/THIRD_PARTY_NOTICES.md)。

[2026-09-19—20 token 与编译器实验记录](../docs/process/research/token-compiler-20260919-20.md)保存逐阶段观察、失败、截图勘误和对应证据位置。
