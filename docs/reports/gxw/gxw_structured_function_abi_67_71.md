# GX Works2 Structured Function ABI findings: samples 67-71

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimental, read-only reverse engineering.
>
> Scope: FX3U / GX Works2 Structured Ladder/FBD controlled samples created on 2026-09-06. This is a follow-up to the 59-66 Function ABI study.

## Controlled samples

| Sample | Purpose |
|---|---|
| 67 | `AND_E` with two data inputs |
| 68 | same function family with three data inputs |
| 69 | fixed two-input `DIV_E` |
| 70 | plain `ABS` without execution-enable interface |
| 71 | `ABS_E`, same operation with EN/ENO |

## Extensible-function `-N` suffix is cross-family

Sample 67 serializes the two-input AND variant as:

```text
AND_E-2
```

with five ports:

```text
EN   code 3
IN1  code 3
IN2  code 3
ENO  code 0
OUT  code 2
```

Therefore:

```text
AND_E-2 -> [3, 3, 3, 0, 2]
```

Sample 68 adds one data input and serializes:

```text
AND_E-3 -> [3, 3, 3, 3, 0, 2]
```

This exactly matches the previously observed `ADD_E-2` / `ADD_E-3` behavior.

The 67 -> 68 `Program.pou` growth is exactly 80 bytes:

```text
+16 bytes  one additional PortDescriptor on the Function record
+64 bytes  one additional D4 source/input terminal node
=80 bytes
```

This is strong structural evidence that the suffix is an instantiated data-input arity marker for extensible function families, not an ADD-specific naming quirk.

## Fixed two-input function does not use `-2`

Sample 69 has the same visible ABI shape as a two-input `_E` function:

```text
EN, IN1, IN2, ENO, OUT
```

but its serialized function symbol is:

```text
DIV_E
```

not:

```text
DIV_E-2
```

Its port-kind sequence is still:

```text
[3, 3, 3, 0, 2]
```

Therefore the rejected model is:

```text
-N == number of data inputs for every Function
```

The stronger working model is:

```text
extensible family instance:
    ADD_E-2
    ADD_E-3
    AND_E-2
    AND_E-3

fixed-arity family:
    DIV_E
    CMP
    MOV
    ...
```

A semantic layer must not split every Function symbol at the last hyphen and assume an arity. Extensibility needs registry/family knowledge or additional evidence.

## Plain Function vs `_E` Function

Samples 70 and 71 form a controlled pair for the same operation.

### Sample 70: ABS

The serialized node is:

```text
symbol = ABS
ports  = 2
```

Port sequence:

```text
IN   code 3
OUT  code 2
```

so:

```text
ABS -> [3, 2]
```

The data terminals attach directly:

```text
D0 -> ABS.IN
ABS.OUT -> D2
```

No EN/ENO ports exist on the Function node.

### Sample 71: ABS_E

The serialized node is:

```text
symbol = ABS_E
ports  = 4
```

Port sequence:

```text
EN   code 3
IN   code 3
ENO  code 0
OUT  code 2
```

so:

```text
ABS_E -> [3, 3, 0, 2]
```

The operation and data operands remain the same (`D0 -> D2`); the `_E` form adds the execution-enable interface:

```text
plain ABS:
    data IN -> data OUT

ABS_E:
    EN + data IN -> ENO + data OUT
```

This strongly supports `_E` as the execution-enable form of the same Function family in the tested pair.

## Port-kind working model

Across MOV, CMP, ADD_E, AND_E, DIV_E, ABS, and ABS_E, the following interpretation is now strongly supported for Function nodes:

```text
port_kind_code = 3
    incoming Function port
    observed on EN and data IN

port_kind_code = 2
    ordinary outgoing Function result
    observed on data OUT

port_kind_code = 0
    execution-continuation / ENO-like Function output
```

Keep the implementation field name `port_kind_code`: code `0` is still not globally solved because a coil's unused/right graphic port was also observed with code `0`.

The complementary terminal-node pattern remains:

```text
0x0D source/input terminal:
    one port, code 2

0x0E sink/output terminal:
    one port, code 3
```

This produces the repeated graph-role pairing:

```text
source terminal code 2 -> Function input code 3
Function output code 2 -> sink terminal code 3
```

## Header invariants continue to hold

All samples 67-71 continue to satisfy:

```text
field 0x37 = body_size + 12
field 0x3B = body_size + 12
```

and:

```text
field 0x57 = 17
left power rail = (1,0) -> (1,17)
```

for these controlled diagrams.

## 后续研究

未执行方案及下一步实验见[过程记录](../../process/research/gxw_structured_function_abi_67_71-follow-up.md)。
