# 研究证据

本目录保存 GXW 逆向的输入、原生截图、失败语料、冻结基线和机器可读结果。样本事实以对应清单和原始文件为准。

## 阅读路径

[源声明重建与 SFC 源图快照（2026-09-27）](../docs/reports/gxw/2026-09-27-source-sfc-checkpoint.md)固定后续 Q 源码重建、真实工程覆盖、SFC 图读取与局部修改成果；[清单](results/gxw-source-sfc-20260927/manifest.json)保留失败记录及源码／本地证据包的逐文件身份。

当前编辑、导入及原生验证步骤见 [FBD 指南](../docs/guides/fbd.md)。已完成的实验从[报告索引](../docs/reports/README.md)进入；假设、预注册及下一步采样见[过程索引](../docs/process/README.md)。

[原生检查缓存反例（2026-09-28）](results/program-check-freshness-20260928.json)确认 `ProgramCheck` 可读取工作区中的旧资源：同一份新生成的双线圈代码在旧顺序下误判通过，检查前调用 `Workspace.UpdatePCode` 后被正确拒绝。[隔离复跑工具](replay_gxw_workspace.py)现在默认先更新代码，并在结果中记录 `program_check_target`。历史 `program_check_completed` 或检查通过记录本身不能证明被修改的代码接受过检查；应核对资源字节与本次编译输出，或按新顺序复跑。冻结输入、输出和旧报告不改写。

[原生 PCode 转梯形图工具](native_gxw_ladder.py)独立重放 FX3G 编译器的转换调用，保留块边界、返回码和原始布局缓冲区。[对照数据](results/pcode-ladder-native-20260928.json)包括真实工程的 454 次调用全字段一致结果、32 个生成对照及 24／25 条赋值的源码编译边界。成功调用可能只消耗输入前缀；局部转换、完整检查、保存和重开分别记录。

[2026-09-27 阶段快照](../docs/reports/gxw/2026-09-27-callsite-array-real-checkpoint.md)固定调用位置绑定、数组调用转移和 REAL 原生转换证据；[清单](results/gxw-checkpoint-20260927/manifest.json)区分可分发的 SCPI 证据与仅保留本地的 BoxID 衍生材料。

[梯形图节点复跑工具](probe_gxw_ladder_primitives.py)在隔离副本中检查节点编号与端口标志的组合；[原生观察](../tests/fixtures/gxw_ladder_primitives.json)保留 26 组源记录、SIC／PCode 对照、未支持样本和无插桩复核。SET、RST、取反和边沿读取按已观察组合开放，类型编号本身不等于最终指令。

[功能块端口复跑工具](probe_gxw_callable_ports.py)重建双向参数的隔离实验；[接口与反例](../tests/fixtures/gxw_callable_ports.json)保留原生参数描述、端口排序、取反及写回差异。[接口展开](probe_gxw_library_sources.py)读取原生库声明，处理隐式 EN/ENO、可变输入和双向参数，执行效果另行验证；输入连接不等于只读访问，编译通过也不保证修改前后的写回目标一致。

复跑工具的 `--trace-lowering` 在第二个隔离进程中观察[编译器的参数转移与指令记录](native/TraceCallableLowering.js)，并与新鲜的无插桩编译结果逐字节比较。五组数组、模板指令和字符串控制已通过该检查；原始操作数、数组取反退化路径及失败观察保存在同一接口 fixture 的 `compiler_lowering` 中。记录出口覆盖已观察的数字指令与名称模板两条路径，当前范围是测试工程的程序 `1`，最终指令仍由独立原生解码核对。

## 数据维护

保留原始样本和归档名称，不覆盖失败结果或以重新生成的文件替换冻结输入。新增证据应记录来源、工具版本、输入/输出哈希、命令与观察结果；需要修正旧结论时新增复审记录并链接旧记录。

[results](results/)保存结构化结果，[evidence](evidence/)保存归档证据，[baselines](baselines/)保存历史实现对照。产品代码不导入历史基线。研究工具位于 [tools](../tools/)，具体工具与参数从对应实验记录查询。

许可沿用 [LICENSE](../LICENSE)及资料各自的来源约定；手册知识资料的第三方归属见 [THIRD_PARTY_NOTICES.md](../resources/knowledge/THIRD_PARTY_NOTICES.md)。

[2026-09-19—20 token 与编译器实验记录](../docs/process/research/token-compiler-20260919-20.md)保存逐阶段观察、失败、截图勘误和对应证据位置。
