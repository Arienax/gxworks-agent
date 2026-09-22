# GX Works2 Structured Function ABI findings: samples 59-66

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimental, read-only reverse engineering.
>
> Scope: FX3U / GX Works2 Structured Ladder/FBD controlled samples created on 2026-09-06. These findings extend the general Structured Ladder notes for samples 48-58.

## Controlled samples

| Sample | Purpose |
|---|---|
| 59 | `ADD_E` with `X1`, constants `10/20`, result `D1`; all terminals directly attached |
| 60 | Same geometry as 59, but data terminals `D0/D2/D4` |
| 61 | Same logic as 59, but operands moved away so GX Works2 emits explicit wires |
| 62 | Same as 59 plus `ADD_E.ENO -> Y1` |
| 63 | `CMP` with `X1`, `D0`, `D1`, result `M0` |
| 64 | Accidental `MOV.ENO -> ADD_E.ENO`; unresolved `ADD_E.EN` is stored as `?` |
| 65 | Corrected `MOV.ENO -> ADD_E.EN` |
| 66 | Three-input `ADD_E` variant |

## Function symbols and arity

GX Works2 does not serialize the two-input editor item as plain `ADD_E`. The function node symbol is:

```text
ADD_E-2
```

For the three-input variant, sample 66 stores:

```text
ADD_E-3
```

The suffix matches the number of visible data `IN` ports in the controlled pair:

```text
ADD_E-2 -> EN + 2*IN + ENO + OUT -> 5 ports
ADD_E-3 -> EN + 3*IN + ENO + OUT -> 6 ports
```

This is strong evidence that the suffix is an overload/arity discriminator for this variadic function family. Do not generalize the `-N` convention to unrelated functions until more examples are tested.

## Repeated function-port ABI pattern

Samples 59-62 (`ADD_E-2`) all serialize the function node with the same five port kind codes:

```text
[3, 3, 3, 0, 2]
```

In visual order:

```text
left:  EN   -> 3
left:  IN1  -> 3
left:  IN2  -> 3
right: ENO  -> 0
right: OUT  -> 2
```

Sample 63 (`CMP`) independently uses the same five-code pattern:

```text
[3, 3, 3, 0, 2]
```

Sample 66 extends it consistently:

```text
ADD_E-3 -> [3, 3, 3, 3, 0, 2]
```

Current working interpretation:

```text
3 -> left-side function input/sink-like role
2 -> ordinary right-side result/source-like role
0 -> ENO/execution-continuation-like role in tested functions
```

The exact Mitsubishi meaning of code `0` remains unresolved because code `0` was also observed on the right port of coil nodes. Keep `port_kind_code` as the implementation name.

## Terminal node kinds describe graph role, not PLC device class

Samples 59 and 60 are a clean comparison.

Sample 59 input-side terminal symbols:

```text
X1
10
20
```

Sample 60 input-side terminal symbols:

```text
X1
D0
D2
```

All are serialized as:

```text
node_kind = 0x0D
single port with port_kind_code = 2
```

The result-side terminals `D1` / `D4` are:

```text
node_kind = 0x0E
single port with port_kind_code = 3
```

Sample 62 shows `Y1` directly attached to `ADD_E.ENO` as `0x0E`.
Sample 63 shows `M0` attached to `CMP.OUT` as `0x0E`.

Therefore `0x0D` and `0x0E` are editor terminal roles, not PLC device-type encodings:

```text
0x0D -> source/input terminal attached to a function port
0x0E -> sink/output terminal attached to a function port
```

The symbol text must be interpreted separately to decide whether the terminal denotes an X/Y/M/D device, numeric literal, label, or another expression.

This also explains why `X1` can be either:

```text
0x03 Contact   # when drawn as a ladder contact
0x0D terminal  # when used directly as a function input
```

The parser must never infer node kind from the symbol prefix alone.

## Direct attachment vs explicit wires confirmed on ADD_E

Sample 59 attaches data terminals directly to `ADD_E-2`; the terminal port point equals the function port point and no wire record is required.

Sample 61 moves the data terminals away and GX Works2 adds exactly these conductors:

```text
(6,6)  -> (12,6)   # 10 -> IN1
(6,7)  -> (12,7)   # 20 -> IN2
(18,6) -> (24,6)   # OUT -> D1
```

This independently confirms the connectivity model previously derived from MOV:

```text
coincident ports OR explicit wire path
```

## ENO code is not connection state

Sample 62 directly attaches `Y1` to `ADD_E.ENO`.

The function's ENO port remains:

```text
port_kind_code = 0
```

and the `ADD_E-2` function node record is unchanged relative to the unconnected-ENO baseline.

This independently rejects the hypothesis that code `0` means "unconnected".

## Unresolved function input placeholder

Sample 64 accidentally connected `MOV.ENO` to `ADD_E.ENO` rather than `ADD_E.EN`.

GX Works2 preserved the unbound `ADD_E.EN` as a source terminal:

```text
node_kind = 0x0D
symbol = "?"
```

The `?` terminal port coincides with the ADD EN port.

Therefore binary parsing must allow `?` as a valid serialized editor placeholder. It should be reported by a later semantic-validation layer as an unresolved input, not treated as malformed GXW.

## Correct function-to-function control connection

Sample 65 corrects sample 64.

The MOV ENO point is:

```text
(35,5)
```

The ADD EN point is:

```text
(45,11)
```

GX Works2 emits:

```text
(35,5) -> (45,5)
(45,5) -> (45,11)
```

The `?` placeholder disappears.

The existing geometric `ConnectivityGraph` therefore reconstructs:

```text
MOV.ENO -> ADD_E.EN
```

without requiring a special function-to-function connection record.

## ADD_E-3 geometry

Sample 66 stores:

```text
symbol = ADD_E-3
bbox   = (45,9)-(51,15)
ports  = 6
```

Ports:

```text
EN   (45,11) code 3
IN1  (45,12) code 3
IN2  (45,13) code 3
IN3  (45,14) code 3
ENO  (51,11) code 0
OUT  (51,12) code 2
```

The diagram-height field is 17 and the left power rail also ends at y=17, adding another observation supporting the existing height interpretation.

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_structured_function_abi_59_66-follow-up.md)。
