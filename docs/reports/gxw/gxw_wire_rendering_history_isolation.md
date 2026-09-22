# GX Works2 Structured Ladder/FBD wire-rendering isolation: `history.xml` / `iFileSize`

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimentally confirmed for the controlled sample-48 -> generated 535-byte Structured Ladder/FBD case.
>
> Date: 2026-09-08
>
> Scope: GX Works2 / FX3U controlled reverse-engineering samples. This note records observed behavior only and deliberately separates confirmed facts from current hypotheses.

## Purpose

A generated Structured Ladder/FBD `Program.pou` with valid parsed nodes and 44-byte wire records could be written into a GXW and opened by GX Works2, but some horizontal wires were not rendered when the project was produced through the dense MiniStream repack path.

Earlier work had already shown that the same generated `Program.pou` renders correctly when placed into a native sample-51 project layout. The immediate question was therefore:

```text
Which state outside Program.pou controls the editor's wire rendering?
```

The experiments below isolated that failure to the outer `history.xml` metadata, specifically the `D_History` entry corresponding to `Program.pou` and its `iFileSize` field.

## Controlled files

Native baseline:

```text
48_STRUCT_X1_Y1.gxw
```

Native donor/reference:

```text
51_STRUCT_SERIES_X1_M1_Y1.gxw
```

Generated/repacked test file:

```text
48_X1_X2_Y1_DENSE_REPACK.gxw
```

The generated `Program.pou` is 535 bytes and parses as:

```text
Program.pou size = 535
record_count     = 7

NODE  offset= 95 end=174 len=80 symbol=X1
NODE  offset=175 end=254 len=80 symbol=X2
NODE  offset=255 end=334 len=80 symbol=Y1

WIRE0 offset=335 end=378 len=44 1,0 -> 1,5
WIRE1 offset=379 end=422 len=44 1,3 -> 5,3
WIRE2 offset=423 end=466 len=44 7,3 -> 10,3
WIRE3 offset=467 end=510 len=44 12,3 -> 13,3
```

The parser's existing format model is unchanged:

```text
Structured records start at 0x5F
wire record size = 44 bytes
trailer = 24 zero bytes
```

## Prior reverse-isolation results

### Nested `_hdb` non-Program streams

Starting from native sample 51, keeping native `Program.pou` byte-for-byte unchanged, selected non-Program nested streams were replaced with sample-48 versions.

Observed:

```text
C editor/project group:     PASS
D derived/compiler group:   PASS
E all tested non-Program:   PASS
```

For this controlled experiment, the tested nested streams were therefore not required for correct wire rendering.

This does not prove that those streams are irrelevant to all GX Works2 workflows; it only rules them out as the cause of this specific rendering difference.

### Outer-stream reverse isolation

Keeping native sample-51 `_hdb` byte-for-byte unchanged:

```text
O1 project outer streams: OPEN FAIL
O2 user outer streams:    PASS
O3 all outer streams:     OPEN FAIL
```

The project-level group was then split:

```text
P1 projectdatalist.xml: OPEN FAIL
P2 projectlist.xml:     OPEN FAIL
P3 history.xml:         OPEN FAIL
```

These whole-file replacements are not sufficient to identify the wire-rendering cause, because the XML streams also contain project identity, timestamps, IDs, names and consistency metadata. Replacing an entire XML stream can therefore make the project invalid for reasons unrelated to wire rendering.

All GX Works2 test artifacts were manually renamed to short filenames before opening. Long generated filenames can themselves cause GX Works2 open failures in the test environment and were not used as evidence.

## Outer XML comparison

Between sample 48 and sample 51:

```text
projectdatalist.xml: 6 changed XML fields
projectlist.xml:     2 changed XML fields
history.xml:        48 changed XML fields
```

The high-value `history.xml` difference was the `Program.pou` entry:

```text
sample48:
    iFileSize = 411
    szMD5val  = 3cPrO/OC+LnalTGCnA6xTg==

sample51:
    iFileSize = 535
    szMD5val  = LJXlWDiQXA8mRZFMKVe7DA==
```

The size change exactly matches the native Structured `Program.pou` growth:

```text
411 -> 535 bytes
```

This made `history.xml` the next falsifiable target.

## Single-field history experiments

The dense-repack test file was used as the host. `Program.pou` and nested `_hdb` were kept byte-for-byte unchanged while only the target `history.xml` record was modified.

### H1 / H2 / H3

```text
H1: update Program.pou iFileSize only
    OPEN PASS
    WIRE PASS

H2: update Program.pou szMD5val only
    OPEN PASS
    WIRE FAIL

H3: update iFileSize + szMD5val
    OPEN PASS
    WIRE PASS
```

This is the strongest causal result of the current investigation.

For this controlled file:

- changing only `history.xml -> Program.pou -> iFileSize` is sufficient to restore wire rendering;
- changing only the MD5 field is not sufficient;
- updating both also works.

Therefore the original dense-repack wire-rendering failure cannot be attributed solely to MiniFAT layout, root modified time, or another unmodified nested stream. A stale `history.xml` Program.pou size entry is sufficient to reproduce the failure phenotype.

## `iFileSize` is not an exact-size equality check

Additional values were tested while keeping the actual `Program.pou` at 535 bytes and keeping the old MD5 metadata unless otherwise stated.

Observed:

```text
411 -> WIRE FAIL
448 -> WIRE FAIL
449 -> WIRE FAIL

510 -> WIRE PASS
511 -> WIRE PASS
512 -> WIRE PASS
513 -> WIRE PASS

534 -> WIRE PASS
535 -> WIRE PASS
536 -> WIRE PASS
576 -> WIRE PASS
577 -> WIRE PASS
```

A previous `512` open failure was later repeated using a byte-identical file. The two SHA-256 hashes were identical and a full byte comparison reported zero differences, while the later test opened and rendered correctly. The earlier `OPEN FAIL` is therefore treated as a GX Works2/runtime anomaly and not as format evidence.

These results rule out both of the following simple models:

```text
iFileSize must equal the exact Program.pou byte length
```

and:

```text
iFileSize must imply the exact MiniStream mini-sector count
```

## Partial rendering at intermediate `iFileSize`

A decisive observation occurred with:

```text
iFileSize = 480
```

GX Works2 displayed only part of the expected wire graph: earlier wires rendered while the final `X2 -> Y1` horizontal connection was absent.

This shows that stale size metadata is not merely a binary project-validity switch. It can affect individual wire records progressively.

## Last horizontal wire boundary sweep

For `WIRE3`:

```text
offset = 467
end    = 510
```

The following `iFileSize` values were tested:

```text
491: X2-Y1 missing
492: X2-Y1 missing
493: X2-Y1 missing
494: X2-Y1 missing
495: X2-Y1 missing

496: malformed geometry; GX Works2 reported 5276 lines
497: malformed geometry; GX Works2 reported 6095 lines
498: malformed geometry; GX Works2 reported 6592 lines
499: malformed geometry; GX Works2 reported 7556 lines

500: X2-Y1 normal
501: X2-Y1 normal
502: X2-Y1 normal
503: X2-Y1 normal
504: X2-Y1 normal
507: X2-Y1 normal
```

The transition from malformed to normal occurs at:

```text
500 - WIRE3.offset(467) = 33 bytes
```

## Middle horizontal wire boundary sweep

For `WIRE2`:

```text
offset = 423
end    = 466
```

Observed:

```text
450: middle wire missing + malformed geometry; 6866 lines
451: middle wire missing + malformed geometry; 7465 lines
452: middle wire missing + malformed geometry; 7527 lines
453: middle wire missing + malformed geometry; 6760 lines
454: middle wire missing + malformed geometry; 7812 lines
455: middle wire missing + malformed geometry; 7190 lines
456: middle wire normal
```

Again:

```text
456 - WIRE2.offset(423) = 33 bytes
```

## First horizontal wire check

For `WIRE1`:

```text
offset = 379
```

Observed:

```text
411:
    left bus appeared,
    but the horizontal line was drawn on the wrong side of the bus,
    aligned with X1

412:
    left bus normal,
    left-bus -> X1 wire normal
```

Again:

```text
412 - WIRE1.offset(379) = 33 bytes
```

Three independent horizontal wire records therefore show the same normal-rendering threshold:

```text
WIRE1: 379 + 33 = 412
WIRE2: 423 + 33 = 456
WIRE3: 467 + 33 = 500
```

The records are exactly 44 bytes apart, and the observed normal-rendering thresholds are also exactly 44 bytes apart.

## Vertical wire observations

For `WIRE0`:

```text
offset = 335
geometry = (1,0) -> (1,5)
```

Observed so far:

```text
367: stray horizontal line at upper-left
368: stray horizontal line disappears
```

The exact threshold for the vertical bus has not yet been characterized. A focused `369..373` sweep is the next pending experiment.

Do not extrapolate the horizontal `offset + 33` rule to vertical wires until this is tested.

## Current confirmed conclusions

The following statements are supported by direct GX Works2 A/B observations in this controlled experiment.

### 1. `history.xml` Program.pou `iFileSize` is behaviorally significant

It is not merely passive history/display metadata. Changing this one field while keeping the actual generated `Program.pou` unchanged can change GX Works2 Structured Ladder/FBD wire rendering from FAIL to PASS.

### 2. The stale `iFileSize` was sufficient to explain the dense-repack wire failure

Updating only the size metadata restored the missing wires. This substantially deprioritizes the earlier CFB-layout-only hypotheses for this particular failure.

### 3. `szMD5val` is not required to restore wire rendering in this case

Updating MD5 alone did not restore wires. Updating size alone did.

This does **not** prove that MD5 is globally optional. It may still participate in history, integrity, save, compile, or other project workflows.

### 4. Incorrect size metadata can produce partial and malformed wire geometry

Intermediate values produced:

- missing individual wires;
- partially correct diagrams;
- geometrically invalid lines;
- GX Works2's `1024`-line limit error with calculated line counts in the thousands.

This is strong evidence that the size metadata participates in the editor's Structured Program loading/rendering path rather than only in a final project-level integrity check.

### 5. Three horizontal wire records share a reproducible `offset + 33` normal-rendering threshold

```text
WIRE1 -> 412
WIRE2 -> 456
WIRE3 -> 500
```

This is a robust observed pattern, but the exact internal meaning of `+33` is not yet proven.

## Open questions

Do **not** currently claim that:

- GX Works2 simply truncates `Program.pou` to exactly `iFileSize` bytes;
- missing bytes are definitely zero-filled;
- `offset + 33` is a universal wire validity rule;
- the same threshold applies to vertical wires, FB wires, function-block diagrams, branches, or other GX Works2 versions;
- CFB allocation metadata is globally irrelevant;
- `szMD5val` can always be left stale safely;
- updating only `history.xml` is sufficient for every structural edit.

The byte-level malformed-geometry behavior shows that the loading path is more complex than a simple strict parser, and further controlled tests are needed before assigning semantics to the threshold.

## Relationship to earlier CFB metadata work

The earlier CFB comparison correctly identified physical-layout differences between native51 and dense-repack, but those differences were only correlations. The current single-field experiment provides stronger causal evidence:

```text
same dense-repack Program.pou
same nested _hdb
same CFB allocation/layout
change only history.xml Program.pou iFileSize
    -> WIRE FAIL becomes WIRE PASS
```

Therefore root modified time and exact native51 physical allocation are no longer the highest-priority explanation for this wire-rendering failure.

They remain valid subjects for broader CFB research, but should not be investigated ahead of finishing the `history.xml`/wire-record loading model.

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_wire_rendering_history_isolation-follow-up.md)。
