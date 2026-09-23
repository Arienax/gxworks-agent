# GX Works2 Structured Ladder/FBD write-back validation

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimentally validated, deliberately narrow write path.
>
> Date: 2026-09-07
>
> Scope: FX3U / GX Works2 Structured Ladder/FBD controlled sample 48 only. This note records observed write-back results; it does not claim a general-purpose `.gxw` writer yet.

## Purpose

The existing reverse-engineering work established a deterministic read path:

```text
GXW
  -> outer CFB
  -> nested `_hdb` CFB
  -> `projectdatalist.xml` logical-object resolution
  -> `*.Program.pou`
  -> Structured Ladder/FBD node/wire parser
```

The next unknown was whether GX Works2 would accept externally modified Structured Ladder/FBD `Program.pou` data after it was written back through both CFB layers.

Two controlled write-back experiments have now been validated:

1. equal-length node-symbol mutation: `X1 -> X2`;
2. variable-length node-symbol mutation: `X1 -> X100` while remaining within the stream's existing MiniFAT allocation.

## Experiment 1: equal-length symbol mutation

Source project:

```text
48_STRUCT_X1_Y1.gxw
```

Original visible program:

```text
X1 -> Y1
```

Mutation:

```text
X1 -> X2
```

The target contact symbol is stored directly in the Structured Ladder node record as UTF-16LE text. Because `X1` and `X2` have the same encoded length, the test does not require any change to:

- node `record_length`;
- `symbol_char_count`;
- Program.pou body size;
- header fields at `0x37`, `0x3B`, or `0x47`;
- record count;
- nested `_hdb` stream length;
- outer `_hdb` stream length.

This isolates the question of whether the saved editor model can be externally modified and accepted by GX Works2 without another mandatory source-level integrity update.

### Write-back path

```text
48_STRUCT_X1_Y1.gxw
  -> read outer CFB `_hdb` stream
  -> open `_hdb` as nested CFB
  -> resolve `1.Program.pou` through existing logical-object mapping
  -> parse Structured Ladder/FBD program
  -> locate the node whose symbol is `X1`
  -> replace only that node's UTF-16LE symbol bytes with `X2`
  -> write the unchanged-size Program.pou stream back into nested `_hdb`
  -> write the unchanged-size `_hdb` stream back into the copied outer GXW
  -> 48_STRUCT_X2_Y1_PATCHED.gxw
```

The mutation was node-targeted rather than a whole-file/global byte replacement.

### Observed GX Works2 result

1. GX Works2 opened the patched `.gxw` successfully.
2. The Structured Ladder/FBD editor displayed `X2` in place of the original `X1`.
3. The project was saved successfully in GX Works2.
4. GX Works2 was closed.
5. The saved project was reopened successfully.
6. The editor still displayed `X2 -> Y1` after the save/close/reopen cycle.

The save/reopen result is important because GX Works2 itself accepted and reserialized the externally modified project rather than merely rendering it once.

## Experiment 2: variable-length symbol mutation within existing allocation

The second experiment used the same source project but changed:

```text
X1 -> X100
```

The generated output was:

```text
48_STRUCT_X100_Y1_PATCHED.gxw
```

The symbol encoding grows by four bytes:

```text
X1\0    = 6 bytes in UTF-16LE
X100\0  = 10 bytes in UTF-16LE
```

The Structured `Program.pou` therefore grew:

```text
411 bytes -> 415 bytes
```

The serializer rebuilt the affected node and the known Program.pou length fields, including:

```text
symbol_char_count
node record_length
body_size
header[0x37]
header[0x3B]
header[0x47]
```

The record count and canvas height did not change.

### MiniFAT allocation observation

The target Program.pou stream is stored as a CFB mini stream. The CFB mini-sector size is 64 bytes.

For this sample:

```text
original size: 411 bytes
new size:      415 bytes
mini sectors:  7
capacity:      7 * 64 = 448 bytes
```

Both the original and modified stream fit in the same seven-mini-sector chain. Therefore this experiment did **not** require allocating another mini sector or rebuilding MiniFAT/FAT chains.

The writer instead:

1. serialized the new 415-byte Program.pou;
2. verified that 415 bytes fit within the existing 448-byte allocation;
3. rewrote the bytes through the existing mini-sector chain;
4. updated the CFB directory entry's `stream_size` from 411 to 415;
5. wrote the modified nested `_hdb` back into the outer GXW while preserving the outer allocation constraints.

### Automated validation

The writer and allocation-aware CFB replacement tests were run with:

```text
python -m pytest -q tests/test_gxw_structured_writer.py tests/test_gxw_container_writer.py
```

Observed result:

```text
17 passed
```

The patch tool reported:

```text
logical object: 1.Program.pou
nested stream:  16
symbol:         X1 -> X100
size:           411 -> 415 bytes
allocation:     mini, 448 bytes capacity (7 sectors)
Parser check:   OK
```

### Observed GX Works2 result

1. GX Works2 opened `48_STRUCT_X100_Y1_PATCHED.gxw` successfully on the first attempt.
2. The Structured Ladder/FBD editor displayed `X100` at the target contact.
3. The project was saved successfully in GX Works2.
4. GX Works2 was closed.
5. The saved project was reopened successfully.
6. The edited project remained valid and still displayed the changed device after reopening.

A GX Works2 Convert/Compile result was not recorded for this experiment, so compile validation is intentionally **not** claimed here.

## Established conclusions

For controlled sample 48, the following paths are now experimentally validated:

```text
same-size node symbol edit
  -> Program.pou write-back
  -> nested CFB write-back
  -> outer GXW write-back
  -> GX Works2 open/save/reopen
```

and:

```text
variable-size node symbol edit
  -> StructuredProgram serialization
  -> Program.pou size change
  -> rewrite inside existing MiniFAT allocation
  -> CFB directory stream_size update
  -> nested CFB write-back
  -> outer GXW write-back
  -> GX Works2 open/save/reopen
```

These results establish that, for this controlled case:

- `*.Program.pou` is writable Structured Ladder/FBD editor-source state;
- variable-length node records can be externally rebuilt successfully;
- the observed Program.pou header/body size relations are sufficient for this mutation;
- no additional undiscovered source checksum/hash was required for GX Works2 to open, save, and reopen either tested mutation;
- a mini-stream can be resized within its already allocated mini-sector chain by rewriting data and updating the directory `stream_size`.

The conclusion remains deliberately scope-limited. It does **not** yet prove that:

- records can be inserted or deleted safely;
- arbitrary node/wire graphs can be generated from scratch;
- a Program.pou stream can grow beyond its current MiniFAT/FAT allocation capacity;
- MiniFAT or FAT chains can be extended safely;
- all GX Works2 versions or PLC families behave identically;
- compile-derived objects such as `MAIN.res` never require synchronization for later compile/runtime workflows;
- Convert/Compile succeeds for the `X1 -> X100` generated file.

## Milestone status

The Structured Ladder/FBD reverse-engineering status is now:

```text
Read path:
  validated for the currently covered controlled samples

Write path:
  same-size node-symbol mutation                         validated
  variable-size node-symbol mutation within allocation validated
  record insertion/deletion                             not yet validated
  allocation growth / MiniFAT-FAT extension             not yet validated
```

The project is therefore beyond a byte-patch-only prototype: it now has an experimentally validated StructuredProgram serializer plus allocation-aware CFB stream write-back for the covered mutation class.

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_structured_writeback_validation-follow-up.md)。
