# GXW Structured FB ABI findings: samples 72-75, follow-ups 76-77

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

Status: experimental, read-only. Evidence: the original controlled GX Works2 / FX3U Structured Ladder/FBD projects supplied in the user's `Desktop/te` directory. This document describes saved records and interfaces, not simulated or compiled execution.

## Actual samples

| Sample | Instance | Serialized type | First node length | Port codes | Connected outputs |
|---|---|---|---:|---|---|
| 72 | timer_a | TON | 128 | 1, 1, 0, 0 | Q -> Y1; ET -> elapsed_time |
| 73 | timer_b | TON | 128 | 1, 1, 0, 0 | Q -> Y1; ET -> elapsed_time |
| 74 | timer_a | TON_E | 164 | 1, 1, 1, 0, 0, 0 | ENO -> M0; Q -> Y1; ET -> elapsed_time |
| 75 | counter_a | CTU | 148 | 1, 1, 1, 0, 0 | Q -> **T1**; CV -> D0 |

Sample 75's actual Q terminal is `T1`, although the requested construction used Y1. The fixture and semantics preserve T1. Its node kind is still `0x0E` (sink); it does not make the CTU instance a timer.

The fixtures in `tests/fixtures/gxw_structured_72_75.json` preserve exact Program.pou bytes, original project/program SHA-256 hashes, and the related `1.Labels.lh` / `Global1.gh` bytes and hashes. All four projects retain the existing body-size, record-count and 24-zero-byte trailer invariants.

## A different node layout

All four block invocations are record class 1 with node kind **0x02**:

```text
uint32 record_length
uint32 record_class          # 1
uint32 node_kind             # 2
uint32 instance_char_count   # includes terminating NUL
utf16le instance_name[]
uint32 type_char_count       # includes terminating NUL
utf16le type_name[]
uint32 left, top, right, bottom
uint32 port_count
PortDescriptor ports[]       # existing 16-byte descriptors
```

Ordinary Function/contact/terminal nodes have `object_flag` and `reserved` between their symbol and bbox. These FB records do not. Reusing the ordinary-node layout misreads the second string as flags/geometry and eventually reports an incorrect port size. The parser now branches on kind 0x02 while keeping string bounds, UTF-16, descriptor-size, record-end and trailer validation.

`StructuredNode.symbol` is the first serialized string (`timer_a`, `timer_b`, `counter_a`); `.instance_name` exposes that identity for FB nodes. `.type_name` is the second string. `.object_flag` and `.reserved` are `None` on this layout rather than invented values.

## Rename evidence

Samples 72 and 73 have equal Program.pou length (571 bytes). Their differences are exactly:

```text
0x2C: 0x1B -> 0x1C   # header save-time region
0x2E: 0x32 -> 0x1D   # header save-time region
0x7B: 0x61 -> 0x62   # timer_a -> timer_b
```

The first node begins at 0x5F, so the changed name byte is record offset 28. All other node and wire bytes are identical. This is direct evidence that instance identity is stored independently of the TON type and port geometry.

## FB ports require type-specific interpretation

| Type | Bbox | Left ports: local Y, formal | Right ports: local Y, formal |
|---|---|---|---|
| TON, 72/73 | (17,9)-(22,13) | 2 IN; 3 PT | 2 Q; 3 ET |
| TON_E, 74 | (19,9)-(25,14) | 2 EN; 3 IN; 4 PT | 2 ENO; 3 Q; 4 ET |
| CTU, 75 | (20,7)-(27,12) | 2 CU; 3 RESET; 4 PV | 2 Q; 3 CV |

Every left FB port has code 1; every right FB port has code 0. EN and IN therefore share a code, as do ENO, Q and ET. The Function rule `right code 0 -> ENABLE_OUT` cannot apply globally.

The controlled constructions and operand positions establish the listed interfaces. Formal names are not serialized in the 16-byte port descriptors. `DEFAULT_FUNCTION_BLOCK_REGISTRY` supplies names and semantic roles for TON, TON_E and CTU, plus CTU_E verified by follow-up sample 77 below, after checking the complete expected geometry/code/count layout. Unknown types, including unobserved `_E` variants, keep instance/type identity and all nets but receive unknown roles and warnings. A `-N` type suffix is not stripped.

All operand terminals in these four samples attach by coincident points. There is one standalone rail wire per sample. Explicit FB-to-FB wiring is still a separate evidence gap; the geometric layer remains shared with ordinary Functions and ladder elements.

## Related declarations and derived data

`1.Labels.lh` grows from 312 bytes in sample 72 to 406 in 73. The raw strings show that sample 73 retains `timer_a` and adds `timer_b`; the current Program.pou invokes only timer_b. This supports reading invocation identity from the saved node rather than selecting any matching declaration string.

Sample 74's Program.pou type is TON_E while the retained label text includes timer_a/TON. These bytes are preserved without claiming whether that relationship is an intentional enabled-form convention or stale declaration state. Declaration parsing and compatibility checks are not implemented here.

`Global1.gh` is byte-identical across 72-75. `MAIN.res` is also byte-identical at 142 bytes despite changes to the saved block invocations. Compiled/derived streams do not establish the current source diagram or runtime behavior.

## Follow-up: sample 76, two TON instances in one POU

`76_STRUCT_TWO_TON.gxw` contains two invocations of the same TON type in `1.Program.pou`. The POU has 967 bytes, 11 records, ten nodes and one standalone rail wire. Both FB records are 128 bytes and use the existing kind-0x02 layout and `[1, 1, 0, 0]` port codes.

| Instance | Node offset | Bbox | IN | PT | Q | ET |
|---|---|---|---|---|---|---|
| timer_a | 0x5F | (19,2)-(24,6) | X1 | T#1s | Y1 | elapsed_a |
| timer_b | 0x1F1 | (19,8)-(24,12) | X2 | T#2s | Y2 | elapsed_b |

The model retains two distinct instance objects with the same `type_name`. Their four-port net sets are disjoint (0-3 and 4-7 under the default graph numbering), as are their terminal-node references. Both remain ordinary TON interfaces without EN/ENO. The current parser and semantic registry handle the sample without changes or warnings.

`tests/fixtures/gxw_structured_76.json` preserves the exact POU, source/program hashes and related label streams. The local-label stream still contains older names such as `counter_a` and `elapsed_time`; they do not create invocations in the current POU. Tests verify both instances' complete terminal bindings and caller-supplied net numbering, guarding against grouping invocations by type name.

This establishes separate saved invocation identities and connectivity. It does not establish independently allocated runtime memory, elapsed-time behavior or declaration validity. Every operand is directly attached; this sample adds no explicit FB-to-FB wire evidence.

## Follow-up: sample 77, CTU_E enable interface

`77_STRUCT_CTU_E.gxw` contains `counter_a: CTU_E` at node offset `0x5F`, with bbox `(22,6)-(29,12)`. Its POU has 793 bytes, nine records, eight nodes and one standalone rail wire. The 184-byte FB record uses the existing kind-0x02, two-string layout without parser changes.

| Port index | Local point | Code | Formal | Semantic role | Terminal |
|---|---|---|---|---|---|
| 0 | (0,2) | 1 | EN | ENABLE_IN | X0 |
| 1 | (0,3) | 1 | CU | DATA_IN | X1 |
| 2 | (0,4) | 1 | RESET | DATA_IN | X2 |
| 3 | (0,5) | 1 | PV | DATA_IN | 5 |
| 4 | (7,2) | 0 | ENO | ENABLE_OUT | M0 |
| 5 | (7,3) | 0 | Q | DATA_OUT | T1 |
| 6 | (7,4) | 0 | CV | DATA_OUT | D0 |

Compared with sample 75's CTU, the original five data bindings are unchanged. The FB record grows from 148 to 184 bytes: four bytes for the `_E` UTF-16 type suffix and 32 bytes for two additional port descriptors. The POU grows from 629 to 793 bytes, including two additional 64-byte terminals for EN and ENO. All seven ports attach directly to terminals on distinct nets. ENO, Q and CV still share raw code 0, so code alone cannot determine their semantic roles.

`tests/fixtures/gxw_structured_77.json` preserves the exact POU, source/program SHA-256 hashes and related label streams. The local-label text still includes `counter_a` / `CTU` while the invocation stores `CTU_E`; as with TON_E in sample 74, declaration compatibility or stale state is not inferred. `Global1.gh` and the 142-byte `MAIN.res` are unchanged from sample 75.

CTU_E is now an explicitly registered counter interface, not a general inference from `_E`. Regression tests compare its data bindings with CTU, separate enable roles from data roles, preserve caller-supplied net indices, and require no semantic warnings for the real sample. A synthetic six-port TON_E layout labeled CTU_E remains unnamed/unknown with `function_block_port_count`; an unregistered `FOO_E` also remains unknown. This sample does not establish counting behavior, ENO runtime behavior or FB-to-FB wiring.

## Contact/Coil evidence recovered alongside this batch

Original samples 49-51 were present in the same directory and are now preserved in `gxw_structured_49_51.json`. Sample 49 verifies label symbols; sample 51 verifies series nets. Sample 50 corrects the earlier synthetic NC assumption: its left code is **11**, right code is 2. NO contacts continue to use `[3, 2]`.

The semantic classifier now requires the appropriate code for each contact polarity. The coil's observed right code 0 remains unknown with `unmodeled_coil_output`; FB findings do not establish the coil port's execution meaning.

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_structured_fb_abi_72_75-follow-up.md)。
