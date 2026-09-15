# GX Works2 CSV / SVG native reproduction (2026-09-15)

## Source and baseline

The defect was reproduced from the original user-supplied GX Works2-compatible
program CSV before creating the fix branch.

- source CSV SHA-256: `dfb0c57c2655524000da3578c724baa7e5e67f59894bde2f7bba1c39d887b64d`
- source instruction rows: 854
- baseline branch: `feat/comprehensive-runtime-diagnostics-20260914`
- baseline commit: `9d73bcadaa2fd35292a2604b5cd98f4f34dbe253`

No source CSV data is committed by this change; only minimized regression
fixtures are encoded in tests.

## Reproduction 1: native program-step drift

The source CSV is internally consistent with the legacy `draw.py`
`get_step_size()` implementation: every exported step label can be regenerated
with zero mismatches. That implementation, however, counts `PLS` as one step
and `INC` as one step.

Replaying the same source stream with the FX3U native widths observed in GX
Works2 (`PLS` = 2 steps, `INC` = 3 steps) reproduces the addresses shown by
GX Works2 exactly:

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

The +33 step drift at the first M30 network is exactly one missing PLS step
plus sixteen missing two-step increments.

## Reproduction 2: M100-M115 SVG wrap

The source rung at labels 77-94 is:

`LD M100`, `AND M101` ... `AND M115`, `OUT M13`, `RST M12`.

GX Works2 displays the default eleven contacts on the first visual row
(M100-M110), creates a K0 continuation pair, then resumes with M111-M115 on the
next visual row. The legacy SVG renderer uses a fixed `step_w = 120`, never
checks the eleven-contact display limit, and pins outputs at `x = 940`; the
M107/M108 area therefore collides with the output instead of wrapping.

The fix keeps the ladder/IR untouched and applies wrapping only in the SVG
layout layer. Long simple-contact series chains are split after 11 contacts,
with paired `K<n>` continuation markers. Outputs remain attached to the final
visual row.

## Reproduction 3: M30/M31/M32 range networks

The source contains three 16-way ORB networks:

- source 459-810: M100-M115 -> M30, K1..K3 / D101..D102
- source 811-1162: M100-M115 -> M31, K4..K6 / D103..D104
- source 1163-1514: M100-M115 -> M32, K7..K9 / D105..D106

Each branch contains one M contact plus four comparison instructions. The
existing logical lowering already emits the correct interleaved ORB form (15
ORB merges for 16 branches), so the fix does not rewrite these networks or
introduce helper relays. The native step-label correction removes the address
drift that accompanies their GX Works2 import.

## Regression coverage

`tests/test_gxworks2_csv_native_regressions.py` locks down:

1. native PLS/INC step-label spacing;
2. M100-M115 wrapping after M110 with a K0 source/destination pair;
3. preservation of all 16 ORB branches for the M30/M31/M32 range networks.
