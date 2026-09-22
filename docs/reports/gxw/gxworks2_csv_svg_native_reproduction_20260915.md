# GX Works2 CSV / SVG native reproduction (2026-09-15)

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

## Source and baseline

The defect was reproduced from the original user-supplied GX Works2-compatible
program CSV before creating and then correcting the fix branch.

- source CSV SHA-256: `dfb0c57c2655524000da3578c724baa7e5e67f59894bde2f7bba1c39d887b64d`
- source instruction rows: 854
- baseline branch: `feat/comprehensive-runtime-diagnostics-20260914`
- baseline commit: `9d73bcadaa2fd35292a2604b5cd98f4f34dbe253`

No source CSV data is committed; minimized fixtures encode the reproduced
instruction shapes.

## Reproduction 1: native program-step drift

The source CSV is internally consistent with the legacy `draw.py`
`get_step_size()` implementation, but that implementation counted `PLS` and
`INC` too narrowly for the FX3U/GX Works2 display. Replaying the source with
`PLS = 2` and `INC = 3` reproduces the GX Works2 labels:

| source CSV label | GX Works2/native label | instruction |
| ---: | ---: | --- |
| 77 | 78 | `LD M100` (16-contact serial rung) |
| 454 | 485 | `LD M115` (last increment branch) |
| 459 | 492 | `LD M100` (M30 range network) |
| 810 | 843 | `OUT M30` |
| 811 | 844 | `LD M100` (M31 range network) |
| 1162 | 1195 | `OUT M31` |
| 1163 | 1196 | `LD M100` (M32 range network) |
| 1514 | 1547 | `OUT M32` |
| 1515 | 1548 | `LD M0` (next network) |

The +33 drift before the first range network is one missing PLS step plus
sixteen missing two-step INC increments.

## Reproduction 2: M100-M115 SVG wrap

The source rung at labels 77-94 is:

`LD M100`, `AND M101` ... `AND M115`, `OUT M13`, `RST M12`.

GX Works2 displays M100-M110 on the first visual row, creates a K0 continuation
pair, then resumes with M111-M115 on the next visual row. The SVG fix remains a
layout-only change: canonical ladder/IR logic is not rewritten.

## Reproduction 3: yellow M30/M31/M32 ladder blocks

The source contains three 16-way ORB networks:

- source 459-810: M100-M115 -> M30, K1..K3 / D101..D102
- source 811-1162: M100-M115 -> M31, K4..K6 / D103..D104
- source 1163-1514: M100-M115 -> M32, K7..K9 / D105..D106

Each branch is five list instructions (one M contact plus four comparisons).
The original export therefore produces 96 list rows per network:

`16 * 5 + 15 * ORB + 1 * OUT = 96`.

GX Works2 simple-project list editing/import limits one ladder block to 24 rows;
blocks that exceed the limit are displayed yellow. The first attempt preserved
all 15 ORB merges and therefore preserved the defect.

The corrected export-only lowering splits each 16-way logical OR into four
helper blocks, each containing four original branches:

`4 * 5 + 3 * ORB + 1 * helper OUT = 24` rows.

The four collision-free helper M relays are then merged by a five-row block:

`LD helper0`, `OR helper1`, `OR helper2`, `OR helper3`, `OUT M30`.

M31 and M32 are lowered identically. Helper relays are allocated downward from
M7679 while scanning all existing M0-M7679 references first, so user devices are
not overwritten. The canonical ladder/IR is deep-copied and remains unchanged;
only the GX Works2 CSV artifact is lowered.

Replaying this policy against the original CSV shape gives:

- M30: 24, 24, 24, 24, 5 rows
- M31: 24, 24, 24, 24, 5 rows
- M32: 24, 24, 24, 24, 5 rows

With the uploaded source's currently used M devices, the helpers are allocated
as M7679..M7668 across the three networks.

## Regression coverage

`tests/test_gxworks2_csv_native_regressions.py` locks down:

1. native PLS/INC step-label spacing;
2. M100-M115 wrapping after M110 with a K0 source/destination pair;
3. every physical helper/final range block is <=24 rows;
4. the final M30/M31/M32 merge is `LD/OR/OR/OR/OUT`;
5. all original M100-M115 branches remain present;
6. helper M allocation is unique, inside M0-M7679, collision-aware, and the
   canonical ladder object is not mutated.
