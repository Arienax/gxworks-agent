# GX Works2 Structured Ladder/FBD write-back validation：后续实验

来源版本：`f2f1781`。已完成的结果见[GX Works2 Structured Ladder/FBD write-back validation](../../reports/gxw/gxw_structured_writeback_validation.md)。

## Next falsifiable write tests

### 1. Record insertion

Starting from sample 48:

```text
X1 -> Y1
```

produce a series contact graph such as:

```text
X1 -> X2 -> Y1
```

This should test one structural variable set at a time:

```text
new contact node record
record_count
record ordering
wire construction / modification
geometry
Program.pou body rebuild
```

### 2. Record deletion

Delete a known contact/wire set and verify that GX Works2 opens, saves, closes, and reopens the result.

### 3. Allocation-boundary growth

Construct a mutation whose new Program.pou exceeds the existing 448-byte allocation. This will force the first real mini-sector-chain growth test and should remain separate from the record-insertion experiment if possible.

### 4. General serializer round trip

For every supported controlled fixture:

```text
raw Program.pou
  -> parse
  -> serialize without semantic changes
  -> byte-for-byte identical Program.pou
```

This remains the regression target before broader graph generation is treated as supported.

## Implementation implication

Keep the two writing problems separate:

```text
StructuredProgram serializer
    -> node/wire/header serialization

CFB writer
    -> stream-byte replacement
    -> directory stream_size updates
    -> future allocation-chain growth
```

The two successful write-back experiments validate this boundary and provide regression targets for future structure-level writer work.
