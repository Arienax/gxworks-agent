# GX Works2 Structured Ladder/FBD wire-rendering isolation: `history.xml` / `iFileSize`：后续实验

来源版本：`f2f1781`。已完成的结果见[GX Works2 Structured Ladder/FBD wire-rendering isolation: `history.xml` / `iFileSize`](../../reports/gxw/gxw_wire_rendering_history_isolation.md)。

## Implementation implication

The write path should no longer treat outer `history.xml` as unrelated metadata when a Structured `Program.pou` changes size.

For a safe general writer, the current engineering direction should be:

```text
serialize Structured Program.pou
    -> obtain new exact byte length
    -> write/allocate Program.pou
    -> update corresponding history.xml iFileSize
    -> update corresponding history.xml szMD5val when the hash algorithm is confirmed
    -> validate outer/nested container consistency
```

For wire rendering specifically, `iFileSize` synchronization is now experimentally required by the controlled growth case.

Even though stale MD5 did not block rendering, production code should not intentionally preserve stale integrity metadata once its derivation is understood.

## Next experiment

Characterize `WIRE0` independently:

```text
WIRE0 offset = 335
```

Test short artifacts with:

```text
iFileSize = 369, 370, 371, 372, 373
```

Observe only the vertical left bus and record whether it is:

```text
missing / malformed / normal
```

This will determine whether the repeated horizontal `offset + 33` threshold is tied to horizontal geometry fields or is a more general wire-record loading rule.
