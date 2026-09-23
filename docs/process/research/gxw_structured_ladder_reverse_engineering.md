# GX Works2 Structured Ladder/FBD `.gxw` reverse-engineering notes

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimental, read-only research. Findings below come from controlled FX3U / GX Works2 samples 48-58 created on 2026-09-06. They are deliberately scope-limited until more GX Works2 versions, PLC families, POU types, labels, FBs and network shapes are tested.

## Goal

The first implementation target is a deterministic read-only path:

```text
GXW
  -> outer CFB reader
  -> _hdb nested CFB
  -> projectdatalist.xml logical-file resolver
  -> *.Program.pou
  -> Structured Ladder/FBD record parser
  -> nodes + wires
  -> geometric ConnectivityGraph
  -> future PLC IR / Structured Ladder AST
```

Direct `.gxw` writing is intentionally out of scope for this milestone.

## Controlled samples 48-58

| Sample | Change / program | Main question |
|---|---|---|
| `48_STRUCT_X1_Y1.gxw` | direct device: `X1 -> Y1` | base node/wire layout |
| `49_STRUCT_GLOBAL_LABEL_X1_Y1.gxw` | same graph through global labels | label separation |
| `50_STRUCT_NC_X1_Y1.gxw` | normally-closed `X1 -> Y1` | NC node kind |
| `51_STRUCT_SERIES_X1_M1_Y1.gxw` | `X1 -- M1 -> Y1` | series geometry |
| `52_STRUCT_PARALLEL_X1_X2_Y1.gxw` | parallel `X1 / X2 -> Y1` | explicit graph topology |
| `53_STRUCT_MOV_K10_D1(1).gxw` | `X1` enables `MOV`, `10 -> s`, `d -> D1` | function/value ports |
| `54_STRUCT_MOV_ENO_CONNECTED.gxw` | sample 53 plus `MOV.ENO -> Y1` by coincident ports | whether port field A is connection state |
| `55_STRUCT_MOV_SPACED_IO.gxw` | sample 53 with `10` and `D1` moved away from MOV | coincident ports vs explicit wires |
| `56_STRUCT_T_JUNCTION.gxw` | T/branch topology | endpoint-on-segment junction |
| `57_STRUCT_CROSS.gxw` | two wires cross with both intersections in segment interiors | non-connecting crossing |
| `58_STRUCT_JUNCTION.gxw` | same crossing area, but one wire starts at the crossing | connecting junction |

The filename of sample 53 contains `(1)` only because of the local saved-file name; it is not part of the GX format.

## Project-object resolution

Structured-project samples contain an outer Microsoft Compound File Binary (CFB/OLE) container and a nested CFB stream named `_hdb`.

`projectdatalist.xml` provides the current logical-object mapping. In the controlled project it includes entries such as:

```text
12 -> MAIN.res
14 -> Global1.gh
15 -> 1.Labels.lh
16 -> 1.Program.pou
```

The numeric `_hdb` stream IDs must not be hard-coded. The parser resolves them from `projectdatalist.xml`.

## Structured `1.Program.pou` layout

For samples 48-58, the record area starts at offset `0x5F`.

Observed fields:

```text
0x37 uint32  size_like      observed == body_size + 12
0x3B uint32  size_like      observed == body_size + 12
0x47 uint32  body_size      == file_size - 0x5F
0x57 uint32  canvas_height  working name; equals left rail end_y in tested graphs
0x5B uint32  record_count
0x5F ...     variable-length records
             24 zero trailer bytes
```

The `0x37/0x3B` relation is strongly repeated but is not yet enforced by the parser because its universality across project variants is unproven.

The first `uint32` of every record is its byte length, so records can be parsed sequentially.

Two record classes are currently verified:

```text
record_class = 1 -> node
record_class = 2 -> wire
```

No extra junction record class appeared in samples 56-58.

## Node record

The controlled samples support this structure:

```text
uint32 record_length
uint32 record_class       # 1
uint32 node_kind
uint32 symbol_char_count  # includes terminating NUL
utf16le symbol[symbol_char_count]
uint32 object_flag        # observed 1
uint16 reserved           # observed 0
uint32 left
uint32 top
uint32 right
uint32 bottom
uint32 port_count
PortDescriptor ports[port_count]
```

### PortDescriptor

Samples 53-55 provide strong cross-validation for two of the three descriptor fields:

```text
uint32 size               # observed 16
uint32 port_kind_code     # observed 0, 2, 3; NC sample 50 additionally uses 11
uint32 local_x            # verified local X coordinate relative to node.left
uint32 local_y            # verified local Y coordinate relative to node.top
```

The absolute port point is:

```text
absolute_x = node.left + local_x
absolute_y = node.top  + local_y
```

Sample 55 is direct evidence: moving the value node `10` to bbox `(9,2)-(11,4)` produces a port descriptor with `(local_x, local_y) = (2,1)`, therefore absolute point `(11,3)`, and GX Works2 creates the explicit wire `(11,3) -> (22,3)` to `MOV.s`.

The first semantic field must **not** be interpreted as connected/unconnected state. Sample 54 connects `MOV.ENO` to `Y1` by placing the two ports at `(29,2)`, while the MOV ENO descriptor remains `port_kind_code=0`, byte-identical to sample 53 where ENO was not connected.

Observed values so far:

| Port role in controlled samples | `port_kind_code` |
|---|---:|
| contact left, coil left, MOV EN, MOV s, output-value D1 left | 3 |
| contact right, MOV d, input-value `10` right | 2 |
| coil right, MOV ENO | 0 |
| normally-closed contact left (original sample 50) | 11 |

The first row's contact-left code 3 applies to normally-open contacts. Inspecting the original sample-50 bytes establishes `[11, 2]` for NC, not `[3, 2]`.

These values look role-like rather than state-like, but the exact Mitsubishi semantics remain unproven. Keep the neutral name `port_kind_code`.

For compatibility with the earliest experimental API, `PortDescriptor.field_a/field_b/field_c` remain read-only aliases for `port_kind_code/local_x/local_y`.

Verified node-kind codes:

| Code | Meaning | Evidence |
|---:|---|---|
| `0x01` | built-in Function node | `MOV` in samples 53-55 |
| `0x03` | normally-open Contact | X1/M1/X2 samples |
| `0x04` | normally-closed Contact | sample 50 |
| `0x05` | Coil | Y1/X2 output-node samples |
| `0x0D` | Input/value node | `10` in samples 53-55 |
| `0x0E` | Output/value node | `D1` in samples 53-55 |

The symbol is stored directly as UTF-16LE text. The direct-device and global-label samples show that the program node stores the symbol string; global-label binding is held separately in `Global1.gh`.

Later samples 72-75 establish `0x02` as an FB instance with a different layout: instance string, type string, bbox and ports, without the ordinary-node flag/reserved fields above. FB port codes also differ. See [FB ABI findings 72-75](../../reports/gxw/gxw_structured_fb_abi_72_75.md).

## Wire record

All wires in samples 48-58 are 44 bytes. The currently useful decoded portion is:

```text
uint32 record_length      # 44
uint32 record_class       # 2
uint32 unknown0
uint32 unknown1
uint16 unknown2
uint16 unknown3
uint32 unknown4
uint32 start_x            # offset 24
uint32 start_y            # offset 28
uint32 end_x              # offset 32
uint32 end_y              # offset 36
uint32 suffix             # offset 40
```

Across the tested ordinary wires, T junctions, non-connecting crossings and connecting junctions, the prefix remains:

```text
(unknown0, unknown1, unknown2, unknown3, unknown4) = (0, 1, 0, 1, 0)
suffix = 0
```

Therefore these currently unnamed fields are **not** required to distinguish the tested crossing/junction semantics. They remain preserved raw until another controlled sample proves their meaning.

## Sample 52: explicit graph structure

`52_STRUCT_PARALLEL_X1_X2_Y1.gxw` parses to:

```text
Contact X1 @ (6,1)-(8,3)
Coil Y1    @ (22,1)-(24,3)
Contact X2 @ (6,4)-(8,6)

Wire (1,0) -> (1,6)
Wire (1,2) -> (6,2)
Wire (6,2) -> (6,5)
Wire (8,2) -> (8,5)
Wire (8,2) -> (22,2)
```

This is decisive evidence that Structured Ladder/FBD `1.Program.pou` preserves an editor graph (nodes, coordinates and wires), unlike the ordinary Ladder `MAIN.Program.pou` samples that primarily exposed Mitsubishi mnemonic-like instruction tokens.

## Samples 53-55: port geometry and two connection encodings

Sample 53 parses to four nodes:

```text
Function MOV @ (22,0)-(29,4)   port_count=4
Contact X1  @ (6,1)-(8,3)      port_count=2
Input 10    @ (20,2)-(22,4)    port_count=1
Output D1   @ (29,2)-(31,4)    port_count=1
```

The MOV ports resolve to:

```text
EN  -> (22,2)
s   -> (22,3)
ENO -> (29,2)
d   -> (29,3)
```

In sample 53 the value/output nodes touch the function directly:

```text
10.right == MOV.s == (22,3)
MOV.d == D1.left  == (29,3)
```

No wire records are needed for those connections.

Sample 54 adds `Y1` with:

```text
MOV.ENO == Y1.left == (29,2)
```

Again, no wire is created. This proves coincident port points are themselves a valid connection representation.

Sample 55 moves `10` and `D1` away from MOV while preserving the same logic. GX Works2 creates exactly the missing explicit conductors:

```text
Wire (11,3) -> (22,3)   # 10.right -> MOV.s
Wire (29,3) -> (50,3)   # MOV.d -> D1.left
```

Therefore the read-only semantic layer must support both:

```text
coincident port points
OR
explicit wire paths
```

## Samples 56-58: junction and crossing semantics

These samples establish the first deterministic geometric net-reconstruction rule.

### Sample 56: T junction

Relevant wires include:

```text
(10,4) -> (10,7)
(10,4) -> (44,4)
(10,7) -> (10,9)
(10,9) -> (44,9)
```

The X1 right port is `(10,7)`. The branch is represented entirely by shared endpoints / endpoint-on-segment geometry. There is no junction object or special wire flag.

The left power rail supplies a second endpoint-on-segment example:

```text
rail       (1,0) -> (1,16)
branch     (1,7) -> (8,7)
```

`(1,7)` is an endpoint of the branch and an interior point of the rail, and the two conductors connect.

### Sample 57: interior/interior cross is not connected

```text
vertical   (73,4) -> (73,7)
horizontal (71,5) -> (75,5)
intersection = (73,5)
```

The intersection lies in the interior of both segments. GX Works2 treats the crossing as non-connecting. No special non-connect flag is present.

### Sample 58: endpoint on another segment is connected

```text
horizontal (71,5) -> (75,5)
vertical   (73,5) -> (73,8)
intersection = (73,5)
```

The same geometric area becomes a junction because `(73,5)` is the vertical segment endpoint and lies on the horizontal segment interior.

Samples 57 and 58 have the same `Program.pou` size (711 bytes), record count (11), body size (616), height field (16), wire prefix values and suffix values. Apart from save-time header noise, their meaningful differences are wire coordinates. This is strong evidence that connectivity is encoded geometrically rather than through a junction flag.

### Implemented ConnectivityGraph rules

The first read-only `ConnectivityGraph` implements the verified geometry conservatively:

```text
Port point == Port point
    -> connected

Port point lies on explicit Wire segment
    -> connected

Wire endpoint == Wire endpoint
    -> connected

Wire endpoint lies on another Wire segment interior
    -> connected

Wire interior crosses Wire interior
    -> NOT connected
```

Structured Ladder wires observed so far are orthogonal. The graph builder rejects diagonal wire records rather than guessing at unsupported geometry.

This layer intentionally reconstructs **conductive nets only**. It does not connect the two ports through a contact, coil or function node; element semantics belong in the separate `StructuredSemanticModel`, above geometry and before PLC IR lowering.

## Global-label separation (48 vs 49)

The program graph geometry remains the same while direct device symbols change to labels:

```text
48: X1       -> Y1
49: input_x1 -> output_y1
```

Observed sizes:

```text
1.Program.pou: 411 -> 437 bytes
Global1.gh:      58 -> 296 bytes
1.Labels.lh:    110 -> 110 bytes (save-time changes aside)
MAIN.res:       142 -> 142 bytes
```

The `1.Program.pou` growth is accounted for by the longer UTF-16LE symbol strings. This supports a symbol-based node reference rather than a separately observed global-label ID in these node records.

`Global1.gh` contains the global-label definitions/bindings. `1.Labels.lh` appears to be POU-local label state and did not gain the global-label records in this pair.

## Correction: `MAIN.res` is derived/stale unless compiled

A prior working hypothesis treated Structured Project `MAIN.res` as the current compiled equivalent of `1.Program.pou` after every save. Samples 48-53 disprove that assumption.

Across those samples, `MAIN.res` remained byte-identical while `1.Program.pou` changed and its update timestamp advanced. Therefore:

- `1.Program.pou` is the current saved Structured Ladder/FBD editor model.
- `MAIN.res` is derived/compiled state and may be stale when the user edits/saves without recompiling.
- a read-only source parser must prefer the current `*.Program.pou`, not infer current Structured Ladder logic from `MAIN.res`.

## Read-only implementation

The implementation is under `src/gxw/`:

```text
src/gxw/
├─ __init__.py
├─ container.py
├─ project_resolver.py
├─ structured_pou.py
├─ connectivity.py
├─ models.py
└─ decoder.py
```

`structured_pou.py` remains the binary record parser. `connectivity.py` is a separate semantic/geometric layer so new binary findings do not get mixed with ladder graph algorithms.

The parser remains fail-closed: unsupported structural invariants raise `GXWFormatError` instead of guessing.

Regression fixtures for samples 54-58 store the extracted `1.Program.pou` bytes as base64 text under `tests/fixtures/`, avoiding binary GXW test assets while preserving the exact controlled record layouts.

## Next samples

Add samples only to answer specific remaining unknowns, especially:

- exact semantics of `port_kind_code` (compare MOV/ADD/FB ports);
- additional node kinds (comparison, timer/counter, FB instance, connector, label/local variable);
- multiple networks/pages and multiple POU objects;
- local-label encoding in `*.Labels.lh`;
- global-label schema in `*.gh`;
- compile-state relation between `*.Program.pou`, `MAIN.res`, and other derived streams;
- any non-orthogonal or unusual wire forms before relaxing the ConnectivityGraph geometry checks.

Keep unknown fields raw until controlled samples prove their meanings.
