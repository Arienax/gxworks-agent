# GX Works2 `.gxw` reverse-engineering notes

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimental / reverse-engineered from controlled FX3U samples created in GX Works2 on 2026-09-05.
>
> These observations are intended to support a future **read-only GXW parser**. Do not treat offsets or encodings as universal until they are validated against more PLC families, project layouts, POU types, GX Works2 versions, and structured-ladder projects.

## Goal

The long-term path under investigation is:

```text
GXW
  -> container reader
  -> project object resolver
  -> Program.pou tokenizer
  -> Mitsubishi instruction decoder
  -> PLC IR
```

Writing `.gxw` directly is a separate, higher-risk problem because a project contains duplicated/derived data, metadata, hashes and compiler state. The first milestone should therefore be a deterministic read-only parser.

## Controlled sample method

Each sample changes one variable where possible. The currently analyzed set includes:

- `00_empty.gxw`
- `02_X0_Y0.gxw`
- `03_X1_Y0.gxw`
- `04_X1_Y1.gxw`
- `05_add_comment.gxw` (`X1` device comment changed to `"1"`)
- `06_X2_Y1.gxw`
- `07_X10_Y1.gxw`
- `08_X100_Y1.gxw`
- `09_M1_Y1.gxw`
- `10_MOV_K10_D1.gxw` through `36_DSUB_D0_D2_D4.gxw`
- `37_SERIES_X1_M1_Y1.gxw` through `47_COUNTER_C1_K10.gxw`

The filenames beginning with `DM0V` contain a digit zero in the filename only; the actual GX Works2 instruction is `DMOV`.

## Container structure

Observed `.gxw` files are Microsoft Compound File Binary (CFB/OLE) containers rather than a single opaque program blob.

Observed high-level structure:

```text
GXW (outer CFB)
├─ checkout.xml
├─ dataprotection.xml
├─ history.xml
├─ label.xml
├─ projectdatalist.xml
├─ projectlist.xml
├─ ...
└─ _hdb
   └─ nested CFB
      ├─ numbered streams
      ├─ MAIN.res
      ├─ MAIN.Program.pou
      ├─ COMMENT.qcd
      ├─ ESCompiler.stg
      └─ Gppw2.gpj
```

In the current sample project, `history.xml` maps numbered streams to logical files. Observed mappings included:

```text
12 -> MAIN.res
16 -> MAIN.Program.pou
19 -> COMMENT.qcd
37 -> ESCompiler.stg
39 -> Gppw2.gpj
```

**Do not hard-code these numeric stream IDs.** A parser should resolve logical object names from the project metadata (`history.xml`, `projectdatalist.xml`, etc.). Different projects can assign different IDs.

## `MAIN.Program.pou`

### Observed layout

For the current controlled sample set, the instruction token stream begins at offset `0x4F`.

Other observed fields:

- `0x22`: a Windows `SYSTEMTIME`-shaped timestamp block (`WORD Year`, `Month`, `DayOfWeek`, `Day`, `Hour`, `Minute`, `Second`, `Milliseconds`). This is save-time noise for diffing purposes.
- `0x37`: little-endian `uint32` length-like field.
- `0x3B`: duplicate of the same length-like field.
- observed relation: `field_at_0x37 == token_stream_length + 20`.
- observed file-length relation: `file_size == 0x4F + token_stream_length + 24`.
- observed trailer: 24 zero bytes after the token stream.

These offsets are **sample-layout observations**, not yet proven format invariants.

### Token framing

Instruction and operand tokens observed so far use matching leading/trailing length bytes:

```text
[length] [payload ...] [length]
```

Examples:

```text
03 00 03
04 9C 01 04
05 E8 00 01 05
```

This enables sequential tokenization without searching for magic byte patterns.

## Basic instruction tokens

Observed basic instruction opcodes:

| Instruction | Token | Evidence |
|---|---|---|
| `LD` | `03 00 03` | `X1` input samples |
| `LDI` | `03 01 03` | `39_NC_X1_Y1.gxw` |
| `OR` | `03 06 03` | `38_PARALLEL_X1_X2_Y1.gxw` |
| `ORI` | `03 07 03` | `43_ORI_X1_NC_X2_Y1.gxw` |
| `AND` | `03 0C 03` | `37_SERIES_X1_M1_Y1.gxw` |
| `ANDI` | `03 0D 03` | `40_SERIES_X1_NC_M1_Y1.gxw` |
| `ORB` | `03 18 03` | `44_BRANCH_ORB.gxw` |
| `ANB` | `03 19 03` | `45_BRANCH_ANB.gxw` |
| `OUT` | `03 20 03` | `Y1` output samples |
| T/C output variant | `04 21 03 04` | timer/counter samples 41, 42, 46, 47 |
| `SET` | `03 23 03` | `23_SET_M1.gxw` |
| `RST` | `03 24 03` | `24_RST_M1.gxw` |
| `END` | `03 34 03` | all completed program samples |

The following inversion relation is verified for the tested contact families:

```text
LD   0x00 -> LDI  0x01
OR   0x06 -> ORI  0x07
AND  0x0C -> ANDI 0x0D
```

That is, the normally-closed variant is `normal opcode + 1` for these three tested families. Do not generalize this rule to unrelated instruction classes without evidence.

## Ladder topology findings (samples 37-45)

The topology samples show that ordinary GX Works2 Ladder logic in `MAIN.Program.pou` is represented primarily as a logical instruction sequence rather than as explicit graph nodes/edges.

### Series contact

`37_SERIES_X1_M1_Y1.gxw`:

```text
LD X1
AND M1
OUT Y1
END
```

Token stream:

```text
03 00 03        # LD
04 9C 01 04     # X1
03 0C 03        # AND
04 90 01 04     # M1
03 20 03        # OUT
04 9D 01 04     # Y1
03 34 03        # END
```

### Simple parallel contact

`38_PARALLEL_X1_X2_Y1.gxw`:

```text
LD X1
OR X2
OUT Y1
END
```

Token stream:

```text
03 00 03
04 9C 01 04
03 06 03
04 9C 02 04
03 20 03
04 9D 01 04
03 34 03
```

No separate branch-start, branch-end, merge-node, edge, or coordinate token was observed for this simple parallel network.

### Normally-closed contact

`39_NC_X1_Y1.gxw` verifies `LDI`:

```text
03 01 03        # LDI
04 9C 01 04     # X1
03 20 03        # OUT
04 9D 01 04     # Y1
03 34 03        # END
```

`40_SERIES_X1_NC_M1_Y1.gxw` verifies `ANDI`:

```text
03 00 03        # LD X1
04 9C 01 04
03 0D 03        # ANDI M1
04 90 01 04
03 20 03        # OUT Y1
04 9D 01 04
03 34 03
```

### ORI

`43_ORI_X1_NC_X2_Y1.gxw` verifies `ORI = 0x07`:

```text
LD X1
ORI X2
OUT Y1
END
```

```text
03 00 03
04 9C 01 04
03 07 03
04 9C 02 04
03 20 03
04 9D 01 04
03 34 03
```

### ORB branch merge

`44_BRANCH_ORB.gxw` represents two series branches in parallel as a stack-like instruction sequence:

```text
LD X1
AND M1
LD X2
AND M2
ORB
OUT Y1
END
```

```text
03 00 03
04 9C 01 04
03 0C 03
04 90 01 04
03 00 03
04 9C 02 04
03 0C 03
04 90 02 04
03 18 03        # ORB
03 20 03
04 9D 01 04
03 34 03
```

This verifies `ORB = 0x18` for the tested FX3U sample.

### ANB branch merge

`45_BRANCH_ANB.gxw` is represented as:

```text
LD X1
LD M1
OR M2
ANB
OUT Y1
END
```

with:

```text
03 19 03        # ANB
```

This verifies `ANB = 0x19` for the tested FX3U sample.

### Topology conclusion

For the ordinary Ladder cases tested through sample 45, `MAIN.Program.pou` behaves like a Mitsubishi mnemonic/stack bytecode. Simple and compound branch topology is represented using logical instructions (`OR`, `ORB`, `ANB`, etc.) rather than explicit graph topology tokens in the observed instruction stream.

This does **not** prove that GXW never stores graphical/layout information elsewhere. It only establishes that the logical program body in the tested `Program.pou` samples is reconstructible from the instruction sequence.

## Operand tokens

### Device / constant type codes

Observed type bytes:

| Operand class | Type byte | Notes |
|---|---:|---|
| `M` | `0x90` | bit device |
| `X` | `0x9C` | input |
| `Y` | `0x9D` | output |
| `D` | `0xA8` | data register |
| `T` | `0xC2` | timer device |
| `C` | `0xC5` | counter device |
| `K` 16-bit semantic constant | `0xE8` | decimal constant |
| `K` 32-bit semantic constant | `0xE9` | decimal constant in double-word context |
| `H` | `0xEA` | hexadecimal constant |

### Small operand shape

Observed general form:

```text
[length] [type] [little-endian value bytes ...] [length]
```

Examples:

```text
X1   -> 04 9C 01 04
M1   -> 04 90 01 04
Y1   -> 04 9D 01 04
D1   -> 04 A8 01 04
D10  -> 04 A8 0A 04
T0   -> 04 C2 00 04
T1   -> 04 C2 01 04
C0   -> 04 C5 00 04
C1   -> 04 C5 01 04
K10  -> 04 E8 0A 04
K11  -> 04 E8 0B 04
H10  -> 04 EA 10 04
```

### Numeric address encoding

Addresses are stored as parsed numeric values, not display strings.

For FX-series octal X addresses:

```text
X1   -> 0x01
X2   -> 0x02
X10  -> 0x08
X100 -> 0x40
```

This strongly indicates GX Works2 converts the displayed octal X/Y address into its numeric value before serialization.

Values greater than `0xFF` expand naturally in little-endian form:

```text
K255 -> 04 E8 FF 04
K256 -> 05 E8 00 01 05

D255 -> 04 A8 FF 04
D256 -> 05 A8 00 01 05
```

### Signed constants and semantic width

The constant type byte carries width semantics; raw payload length alone is insufficient to determine signedness/width.

Observed:

```text
MOV  K-1 D1  -> E8 FF FF
DMOV K-1 D0  -> E9 FF FF FF FF

MOV  K32767 D0  -> E8 FF 7F
DMOV K32767 D0  -> E9 FF 7F
DMOV K1 D0      -> E9 01
DMOV K32768 D0  -> E9 00 80
```

Current interpretation:

- `0xE8`: decimal `K` constant in 16-bit semantic context.
- `0xE9`: decimal `K` constant in 32-bit semantic context.
- negative values are serialized using two's-complement at the semantic width.
- positive values may use a shorter magnitude payload, so the parser must preserve the operand type byte and raw bytes before semantic interpretation.

## Timer and counter output form (samples 41, 42, 46, 47)

The tested timer/counter coils use a distinct instruction token before the timer/counter device and preset operand:

```text
04 21 03 04
```

Examples:

`41_TIMER_T0_K10.gxw`:

```text
03 00 03        # LD
04 9C 01 04     # X1
04 21 03 04     # T/C output form
04 C2 00 04     # T0
04 E8 0A 04     # K10
03 34 03        # END
```

`42_COUNTER_C0_K10.gxw` uses the same output form with `C0` (`0xC5`) instead of `T0` (`0xC2`).

Samples 46 and 47 verify device address incrementing:

```text
T0 -> 04 C2 00 04
T1 -> 04 C2 01 04

C0 -> 04 C5 00 04
C1 -> 04 C5 01 04
```

The exact meaning of the second payload byte `0x03` in `04 21 03 04` is not yet decoded. Keep this token as a distinct verified T/C output form rather than forcing it into the ordinary `03 20 03` OUT encoding.

## Application-instruction header

Observed application instructions use a five-byte header token:

```text
05 FAMILY WIDTH_OR_SLOT_DESCRIPTOR SUBTYPE 05
```

The third byte currently follows this verified rule for the tested instructions:

```text
width_descriptor = 1 + 2 * total_operand_word_slots
```

Examples:

```text
MOV:   2 x 16-bit operands -> 2 word slots -> 1 + 2*2 = 0x05
DMOV:  2 x 32-bit operands -> 4 word slots -> 1 + 2*4 = 0x09
ADD:   3 x 16-bit operands -> 3 word slots -> 1 + 2*3 = 0x07
DADD:  3 x 32-bit operands -> 6 word slots -> 1 + 2*6 = 0x0D
BMOV:  D,D,K              -> 3 word slots -> 1 + 2*3 = 0x07
INC:   1 x 16-bit operand -> 1 word slot  -> 1 + 2*1 = 0x03
```

### Arithmetic family (`0x49`)

Observed:

```text
ADD   D1 D2 D3 -> 05 49 07 28 05
SUB   D1 D2 D3 -> 05 49 07 2A 05
MUL   D1 D2 D3 -> 05 49 07 2C 05
DIV   D1 D2 D3 -> 05 49 07 2E 05
DADD  D0 D2 D4 -> 05 49 0D 29 05
DSUB  D0 D2 D4 -> 05 49 0D 2B 05
```

For the tested arithmetic instructions:

```text
subtype/opcode = FNC_number * 2 + D_modifier
```

where `D_modifier` is `0` for the normal word form and `1` for the tested double-word form.

Do not assume this arithmetic-family relation applies to every GX Works2 instruction family until separately verified.

### INC/DEC family (`0x4A`)

Observed:

```text
INC D1 -> 05 4A 03 00 05
DEC D1 -> 05 4A 03 02 05
```

Current hypothesis: family-relative subtype based on a family base instruction. More samples are required before generalizing.

### MOV family (`0x4C`)

Observed:

```text
MOV  K10 D1    -> 05 4C 05 00 05
DMOV D1 D10    -> 05 4C 09 01 05
DMOV K32768 D0 -> 05 4C 09 01 05
BMOV D0 D10 K5 -> 05 4C 07 06 05
```

Current interpretation:

- `0x4C` identifies a MOV-like instruction family.
- subtype `0x00` is observed for MOV.
- subtype `0x01` is observed for DMOV.
- subtype `0x06` is observed for BMOV.
- DMOV appears to be represented as MOV-family + double-word variant rather than an unrelated top-level opcode.

A plausible family-relative rule is:

```text
subtype = 2 * (FNC - family_base) + D_modifier
```

For MOV/BMOV this matches the current samples with `family_base = FNC12`, but this remains a hypothesis until more members of the family are tested.

## `COMMENT.qcd`

Adding a device comment `"1"` to `X1` did **not** change `MAIN.Program.pou`, but increased `COMMENT.qcd` from 60 bytes to 82 bytes in the controlled sample.

The changed region contains evidence consistent with:

```text
X type code: 0x9C
X1 address:   0x01
text bytes:   31 00   # UTF-16LE "1"
```

Conclusion: device comments are stored separately from the ladder instruction body. `COMMENT.qcd` should be parsed as a separate logical object rather than inferred from `Program.pou`.

The exact `COMMENT.qcd` record schema is not yet fully decoded.

## `MAIN.res` duplication

For the controlled samples, `MAIN.res` contains a byte-identical copy of the instruction token stream found in `MAIN.Program.pou` (at a different offset, observed at `0x3A` in the current corpus).

Implication:

- a read-only parser can prioritize `MAIN.Program.pou`.
- a future writer cannot safely update only `MAIN.Program.pou`; duplicated/derived representations and project metadata would also need consistent updates.

## `ESCompiler.stg` and `Gppw2.gpj`

Both can change when ladder content changes. Current evidence suggests they contain compiler/project-management state rather than being the preferred source of truth for decoding the ladder program.

For the initial reader, treat them as opaque derived state until evidence requires otherwise.

## Provisional grammar

A useful first approximation is:

```text
Program := Header Instruction* END Trailer

Instruction :=
    BasicInstruction
  | ApplicationInstruction
  | TimerCounterOutputInstruction

BasicInstruction :=
    [03 opcode 03]
    Operand*

ApplicationInstruction :=
    [05 family width_descriptor subtype 05]
    Operand*

TimerCounterOutputInstruction :=
    [04 21 03 04]
    TimerOrCounterOperand
    PresetOperand

Operand :=
    [length type value_le... length]
```

The decoder still needs an instruction registry to determine the number, role and semantic width of operands after an instruction header.

## Parser design constraints

A first implementation should:

1. Parse the outer CFB container.
2. Locate and parse project metadata rather than hard-coding numbered stream IDs.
3. Resolve `MAIN.Program.pou` by logical name.
4. Keep raw token bytes alongside decoded semantic values.
5. Validate matching token length sentinels.
6. Treat observed fixed offsets as version/layout-specific until proven otherwise.
7. Decode instructions through a registry, not a large handwritten `if/elif` chain.
8. Preserve unknown tokens instead of guessing semantics.
9. Remain read-only until duplicated project state, hashes/checksums and writer invariants are understood.
10. Reconstruct ordinary Ladder topology from mnemonic/stack semantics (`OR`, `ORB`, `ANB`, etc.) rather than inventing graph tokens not present in the observed `Program.pou` stream.

Suggested internal form:

```python
@dataclass
class RawGXWToken:
    offset: int
    raw: bytes
    kind: str

@dataclass
class GXWOperand:
    type_code: int
    raw_value: bytes
    semantic_width_bits: int | None
    decoded: object | None

@dataclass
class GXWInstruction:
    family: int | None
    opcode: int
    width_descriptor: int | None
    operands: list[GXWOperand]
    raw_tokens: list[RawGXWToken]
```

## Next controlled samples: Structured Ladder/FBD

The ordinary Ladder baseline is now sufficient to start a read-only tokenizer/decoder. The next reverse-engineering line should compare equivalent logic in a GX Works2 **Structured Project / Structured Ladder-FBD** program:

```text
48_STRUCT_X1_Y1.gxw
X1 -> Y1

49_STRUCT_NC_X1_Y1.gxw
/X1 -> Y1

50_STRUCT_SERIES.gxw
X1 -- M1 -> Y1

51_STRUCT_PARALLEL.gxw
X1 || X2 -> Y1

52_STRUCT_MOV.gxw
X1 -- MOV K10 D1
```

The key question is whether Structured Ladder/FBD ultimately serializes to the same mnemonic token stream or introduces a separate node/graph representation and additional POU/label objects.

## Confidence levels

Treat findings in three classes:

- **Verified across controlled pairs:** X/Y/M/D/T/C type codes listed above; K/H distinctions; octal X numeric conversion; little-endian value expansion; LD/LDI, OR/ORI, AND/ANDI, ORB/ANB, OUT, SET/RST and END tokens; T/C address codes and the observed T/C output form; arithmetic ADD/SUB/MUL/DIV/DADD/DSUB encodings; MOV/DMOV/BMOV encodings; width-descriptor relation for the tested application instructions.
- **Strong but still scope-limited:** token framing; current `Program.pou` header/trailer relationships; `E8` vs `E9` semantic-width interpretation; ordinary Ladder topology being represented as mnemonic/stack semantics in `Program.pou`; `MAIN.res` token-stream duplication.
- **Hypotheses requiring more samples:** universal family-relative opcode formula; universal fixed offsets; full `COMMENT.qcd` schema; exact semantics of `04 21 03 04`; writer requirements; Structured Ladder/FBD storage format.

Keep this document synchronized with machine-readable observations under `resources/gxw/` as the reverse-engineering corpus grows.
