# SQLite device heuristic warning review — 2026-09-13

Branch: `fix/rejected-json-repair-20260913`

## Result

The 34 `possible_operand_placeholder_device_record` warnings are not a deletion list.
Manual provenance review separates them into:

- **29 real concrete device/pointer examples — KEEP**
- **3 operand-variable / constraint leaks — FIX EXTRACTION**: `N4`, `N9`, `N512`
- **2 PDF/layout fusion artifacts — FIX EXTRACTION**: `D17`, `N36`

No SQLite rows were bulk-deleted during this review.

## Why the old heuristic over-reports

The quality audit selects `S/D/N/M/P + digits` records when most provenance is in instruction chunks and at least one instruction chunk contains structured-instruction markers. That is useful for discovery but cannot distinguish a real address used in a program example from an operand placeholder appearing elsewhere in the same chunk.

The builder's `DEVICE_RE` is case-insensitive, so lowercase mathematical/operand variables such as `n 512` can normalize to a fake `N512` device entity. In addition, flattened PDF/table layout can fuse adjacent cells such as `D | 17 steps` into text resembling `D17`.

## Classification

| Token | Decision | Evidence / reason |
| --- | --- | --- |
| D14 | KEEP | Concrete WXOR/CML examples: `WXOR(... D14)` and `CML(... D14, D14)` |
| D15 | KEEP | RTC example maps D15 to second; also `(D 15, D 14)` concrete data pair |
| D17 | REMOVE AS DEVICE | ABSD instruction table flattened `D | 17 steps | DABSD` into `D 17 steps`; no register-address use |
| D24 | KEEP | FLT/DEDIV examples use `D24` as a concrete register |
| D26 | KEEP | DEDIV/DEMUL examples use concrete `D26` |
| D27 | KEEP | Floating-point example uses concrete pair `(D27,D26)` |
| D28 | KEEP | DEDIV/DEMUL examples use concrete `D28` |
| D29 | KEEP | Floating-point example uses concrete pair `(D29,D28)` |
| D307 | KEEP | Explicit table/range examples include `D306,D307` and `D300 to D307` |
| D51 | KEEP | FLT/DEDIV examples use concrete `D51` |
| D7 | KEEP | FINS explicitly says data-table device range is `D0 to D7`; concrete table contains D7 |
| D9 | KEEP | BMOV program/table uses concrete `D9` |
| D99 | KEEP | Explicit example: `K0 is written to D0 to D99 at one time` |
| M127 | KEEP | DECO output-size illustration explicitly uses M127 |
| M14 | KEEP | PRUN map and DECO prose explicitly use M14 |
| M16 | KEEP | PRUN/ENCO examples explicitly use M16 |
| M17 | KEEP | PRUN examples explicitly use range ending at M17 |
| M255 | KEEP | DECO output-size illustration explicitly uses M255 |
| M31 | KEEP | DECO output-size illustration explicitly uses M31 |
| M599 | KEEP | ZRST example explicitly uses `M500 to M599` and `ZRST(... M599)` |
| M63 | KEEP | DECO output-size illustration explicitly uses M63 |
| N36 | REMOVE AS DEVICE | Single provenance is PDF/layout contamination (`N\n36 | END | ...`), not a PLC device/address example |
| N4 | REMOVE AS DEVICE | Matches lowercase operand/math variable `n 4` and flattened layout constraints, not device N4 |
| N512 | REMOVE AS DEVICE | SFWR operand constraint is `2 <= n <= 512`; lowercase `n 512` was normalized by case-insensitive device regex |
| N9 | REMOVE AS DEVICE | Matches lowercase `n 9` / side-layout fragments, not a concrete device address |
| P10 | KEEP | CJ documentation explicitly says pointer P10 and shows P10 label |
| P11 | KEEP | CALL/CALLP program explicitly uses label/pointer P11 |
| P12 | KEEP | CALL program explicitly uses label/pointer P12 |
| P2 | KEEP | CJ/master-control example explicitly uses `CJ P2` and label P2 |
| P20 | KEEP | CJ example explicitly uses `CJ P20` and label P20 |
| P4 | KEEP | CJ/master-control example explicitly uses `CJ P4` and label P4 |
| P5 | KEEP | CJ example explicitly uses P5 as pointer/label |
| P7 | KEEP | CJ example explicitly uses P7 as pointer/label |
| P9 | KEEP | CJ documentation explicitly uses pointer/label P9 |

## Repair policy

Do **not** add a broad deletion regex for all `S/D/N/M/P + digits` rows.

The extraction fixes should be provenance-aware:

1. Preserve concrete addresses/pointers when the token participates in an instruction call, ladder example, explicit device range/table, pointer/label declaration, or prose that names the concrete device.
2. Treat lowercase operand variables (`n` followed by a numeric bound/value) as operand/constraint text, not device addresses.
3. Reject narrow PDF-layout fusions where the matched token is formed by adjacent instruction-table columns, such as `D 17 steps`.
4. Keep layout-only contamination such as the `N\n36 | END | ...` case out of `device_records`; do not generalize this into deleting legitimate whitespace-separated addresses like `P 11`.
5. Rebuild/repair `entity_index` first, then derive `device_records` from corrected entity provenance. Do not delete aggregate `device_records` rows without fixing their source entities, or a rebuild will recreate them.

## Diagnostic tooling

`tools/audit_device_placeholder_evidence.py` was added as a read-only diagnostic. It emits local source spelling, line/excerpt and evidence signals for every heuristic candidate. The structured knowledge quality workflow runs it against the Git LFS SQLite database and uploads the JSON/log artifact. The diagnostic intentionally does not mutate SQLite.
