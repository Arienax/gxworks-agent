# nested `_hdb` CFB 元数据比较（2026-09-08）：后续实验

来源版本：`f2f1781`。已完成的结果见[nested `_hdb` CFB 元数据比较（2026-09-08）](../../reports/gxw/gxw_cfb_metadata_comparison.md)。

## 推荐的下一步单一实验（未执行）

只验证 **root modified time**：在 dense-repack 的副本中，仅将 nested root 的 8-byte modified time 改为 native51 的原始值；保持所有分配链、stream size 和流内容不变。复核差异仅为这一个字段，关闭并重新打开工程，以原 dense-repack 为对照观察导线是否显示。

推荐理由是它是本组唯一剩余的非分配目录字段差异，能用一个字段隔离验证，且无需新 allocator。时间差也可能仅记录保存时刻，目前没有证据支持它是原因。无论结果如何，均不能外推成整个 CFB 元数据机制的证明；本里程碑不生成或执行这个实验。
