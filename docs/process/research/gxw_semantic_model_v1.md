# GXW Structured Ladder semantic model v1

> 历史记录。来源文档冻结于 `f2f1781a846c9f7073724b4cca485082d57f414c`；实验日期、样本和被测版本按正文记录。代码路径、命令和未完成事项描述当时环境。当前操作从[文档索引](../../README.md)进入。

> Status: experimental, read-only.
>
> Evidence base: controlled GX Works2 / FX3U Structured Ladder/FBD samples through 77.

## Purpose

The binary parser and `ConnectivityGraph` recover editor records and conductive nets, but they deliberately do not decide what a Function port means. `src/gxw/semantic.py` is the first semantic layer above that geometry.

```text
*.Program.pou
  -> StructuredProgram
  -> ConnectivityGraph
  -> StructuredSemanticModel
  -> future Structured Ladder lowering
  -> project-level plc_ir.py
```

The model does **not** lower Structured Ladder directly into the canonical project PLC IR. It now describes contacts/coils and the observed TON/TON_E/CTU/CTU_E instance interfaces, while execution order, state evaluation and declaration binding remain separate milestones.

## Modeled objects

### Function ports

The model classifies the Function-node port layouts verified by controlled samples:

```text
port_kind_code 3 on the left  -> incoming Function port
port_kind_code 2 on the right -> ordinary data result/output
port_kind_code 0 on the right -> ENO/execution-continuation-like output
```

When a single code-0 right port exists, the aligned code-3 left port is classified as `enable_in`; the remaining incoming ports are `data_in`.

Current semantic roles:

```text
enable_in
data_in
enable_out
data_out
unknown
```

Unknown layouts are retained and reported as semantic warnings rather than guessed.

### Contacts and coils

`StructuredSemanticModel.contacts` and `.coils` retain the symbol separately from the element's kind. `SemanticContact.polarity` is `NORMALLY_OPEN` for kind `0x03` and `NORMALLY_CLOSED` for kind `0x04`; kind `0x05` becomes `SemanticCoil` with `CoilRole.NORMAL`.

| Element | Left port | Right port |
|---|---|---|
| NO contact | code 3, `EXECUTION_IN` | code 2, `EXECUTION_OUT` |
| NC contact | code **11**, `EXECUTION_IN` | code 2, `EXECUTION_OUT` |
| Ordinary coil | code 3, `EXECUTION_IN` | code 0, `UNKNOWN` |

The original sample-50 bytes establish the NC input code as 11. The earlier synthetic test that changed only a NO node's kind to NC was insufficient and has been replaced by a real regression. Codes 3 and 11 are not treated as interchangeable.

The observed layout has two aligned ports on opposite bbox edges, inside the bbox height. Every `SemanticLadderPort` preserves its serialized index, code, point, net and terminal bindings. Unsupported counts/layouts preserve all ports with unknown roles; unsupported codes produce a warning for the affected port. Elements with zero ports are still retained.

Contacts expose optional `execution_in` / `execution_out`; coils expose optional `execution_in`. The coil's right graphic port emits `unmodeled_coil_output`: its existence and net are known, but its execution meaning is not. Ordinary coils currently produce this expected warning. Contact polarity is not a runtime contact state, and symbols such as `T0`, `C0` or a label do not change an element's kind.

### FB instances and timer/counter interfaces

Samples 72-75 introduce a separate binary node kind, `0x02`, with instance name and type name stored as separate strings; sample 77 adds CTU_E using the same layout. `StructuredNode.symbol` retains the instance name, `.instance_name` exposes it explicitly, and `.type_name` retains `TON`, `TON_E`, `CTU` or `CTU_E`. This layout has no ordinary-node `object_flag` / `reserved` fields; those values are `None` on FB nodes.

`StructuredSemanticModel.function_blocks` contains `SemanticFunctionBlock` objects, separate from `.functions`. Each block preserves its instance name, type, category and `SemanticFunctionBlockPort` objects. `.timers` and `.counters` filter the registered categories; they do not simulate elapsed time or counting.

FB inputs use code **1**, and all observed FB outputs use code **0**, including ordinary data outputs. Consequently, FB EN/ENO roles cannot use the Function port inference rules. `DEFAULT_FUNCTION_BLOCK_REGISTRY` records only these verified interfaces:

| Type | Input formals, top to bottom | Output formals, top to bottom | Category |
|---|---|---|---|
| TON | IN, PT | Q, ET | timer |
| TON_E | EN, IN, PT | ENO, Q, ET | timer |
| CTU | CU, RESET, PV | Q, CV | counter |
| CTU_E | EN, CU, RESET, PV | ENO, Q, CV | counter |

Formal names are supplied by this evidence-backed registry, not decoded from strings on each port. Roles require the full registered count, code and geometry layout, with the first port on each side at local Y=2 and subsequent ports on consecutive rows. Serialized port indices remain intact even when ports are reordered. Unknown types or unsupported layouts retain the instance and every port with `UNKNOWN` roles, no formal names, and warnings.

```python
block = semantic.function_blocks[0]
print(block.instance_name, block.type_name)
port = block.port_named("PT")
if port is not None:
    print(port.net_index, port.terminal_symbols)  # observed TON: ("T#1s",)
```

See [FB ABI findings 72-75 and follow-ups 76-77](../../reports/gxw/gxw_structured_fb_abi_72_75.md) for the exact record layout, rename comparison and remaining evidence limits.

### Source and sink terminals

Observed terminal node kinds are interpreted as graph roles:

```text
0x0D -> source terminal
0x0E -> sink terminal
```

The terminal `symbol` remains separate from the graph role. A source terminal may contain `X1`, `D0`, `10`, a label, or the observed unresolved placeholder `?`.

`?` is accepted by the binary/semantic parser and emitted as an `unresolved_terminal` semantic issue. It is not treated as a malformed GXW record.

### Function family identity and arity

Controlled samples prove that `-N` is not a universal Function suffix:

```text
ADD_E-2 / ADD_E-3
AND_E-2 / AND_E-3
```

are extensible families, while the fixed two-input function:

```text
DIV_E
```

has no `-2` suffix.

Therefore v1 uses a small evidence-backed family registry. Only registered extensible families interpret a trailing `-N` as declared data-input arity. Unknown symbols such as an unrelated `FOO-3` are preserved verbatim instead of being split speculatively.

Registry entries currently cover:

```text
ADD_E    extensible
AND_E    extensible
MOV      fixed, observed 1 data input
CMP      fixed, observed 2 data inputs
DIV_E    fixed, observed 2 data inputs
ABS      fixed, observed 1 data input
ABS_E    fixed, observed 1 data input
```

## Connectivity binding

Every semantic Function port, FB port, ladder port and terminal keeps its `ConnectivityGraph` net index. A supplied graph is used without rebuilding or renumbering its nets.

This means function-to-function execution flow does not need a special edge type. Sample 65 is represented by a shared net:

```text
MOV.enable_out.net_index == ADD_E.enable_in.net_index
```

Directly attached terminals and explicit wire paths resolve to the same semantic binding because both have already been normalized by `ConnectivityGraph`.

Each Function port also exposes directly attached/connected terminal symbols when terminals occur on the same conductive net.

FB and ladder ports expose the same terminal bindings. Nets are never merged through a node. Real sample 54 connects `contact.execution_out` to `MOV.enable_in`, then `MOV.enable_out` to `coil.execution_in`. Sample 51 preserves the successive nets between series contacts, while sample 52's parallel contacts share an input net and share an output net with their two sides remaining distinct.

## Public API

```python
from gxw import build_semantic_model, read_structured_program

program = read_structured_program("project.gxw")
semantic = build_semantic_model(program)

for function in semantic.functions:
    print(function.serialized_symbol)
    print(function.base_name)
    print(function.data_input_count)
    print(function.has_enable_interface)
    for port in function.ports:
        print(port.role, port.net_index, port.terminal_symbols)
```

Primary model types:

```text
StructuredSemanticModel
SemanticFunction
SemanticFunctionPort
SemanticContact
SemanticCoil
SemanticLadderPort
SemanticFunctionBlock
SemanticFunctionBlockPort
SemanticTerminal
SemanticIssue
UnmodeledNodeRef
```

## Fail-soft semantic policy

The binary parser remains fail-closed for unsupported record structure. The semantic layer is intentionally fail-soft for unknown meaning:

- unknown Function port layouts become `SemanticPortRole.UNKNOWN` plus a warning;
- arity mismatches become warnings;
- unresolved `?` terminals become warnings;
- unknown contact/coil port layouts remain on modeled elements with warnings;
- the observed coil right port remains unknown with an expected warning;
- unknown FB types remain modeled instances with unknown port roles and warnings;
- other not-yet-modeled node kinds are retained in `unmodeled_nodes`.

This separation avoids turning incomplete reverse-engineering knowledge into false format errors.

## Regression coverage

The v1 semantic tests use extracted real `Program.pou` fixtures from controlled samples 64-71 and lock the following findings:

- sample 64: unresolved `?` source terminal is preserved and reported;
- sample 65: `MOV.ENO -> ADD_E.EN` shares one semantic net;
- samples 66/68: `ADD_E-3` and `AND_E-3` resolve as registered extensible families with arity 3;
- sample 69: fixed two-input `DIV_E` is not interpreted as a `-2` family instance;
- samples 70/71: `ABS` is `[data_in, data_out]`, while `ABS_E` is `[enable_in, data_in, enable_out, data_out]`;
- samples 65-71 produce no unexpected semantic warnings.

Additional real regressions cover samples 49-52 and 54-58 for labels, NC polarity/code 11, series/parallel nets and Contact/Function/Coil connections. Sample 52 reuses the exact bytes already held by the binary reader test. Samples 72-75 preserve exact Program.pou bytes, related label streams and SHA-256 provenance. They lock instance identity, the rename-only record difference, named timer/counter interfaces, net bindings, and sample 75's actual `Q -> T1` sink. All four FB samples produce no semantic warnings under the registered interfaces.

Sample 76 adds two TON instances in the same POU with separate input/preset/output/elapsed-time terminal bindings and disjoint net sets. The existing model preserves both invocations without grouping them by type; this is source-connectivity evidence, not runtime state-allocation evidence. Its exact bytes and related label streams are in `gxw_structured_76.json`.

Sample 77 adds CTU_E with seven ports. EN binds X0 and ENO binds M0, while CU/RESET/PV/Q/CV preserve sample 75's X1/X2/5/T1/D0 bindings. All three right-side ports have code 0, but only ENO is `ENABLE_OUT`; Q and CV are `DATA_OUT` on distinct nets. Its exact bytes and related label streams are in `gxw_structured_77.json`. Registration removes the previous unknown-type warning without changing the binary parser. The `_E` suffix alone still does not identify an unknown FB interface, and a known CTU_E type with an incompatible port layout remains fail-soft.

Synthetic cases are explicitly named as such and test unknown interfaces, changed port order, unsupported geometry/codes/counts and malformed FB record boundaries. They are not counted as additional controlled GX Works2 samples.

## Next semantic milestones

The next layer should not be implemented by guessing from ordinary Function behavior. Controlled samples are still needed for:

1. user-defined FBs, IN_OUT formals and explicit FB-to-FB wire paths;
2. additional timer/counter forms, declaration binding and stateful execution;
3. remaining contact/coil execution questions, power rails, SET/RESET and edge forms;
4. labels and typed local variables at terminals;
5. multiple networks/POUs;
6. deterministic lowering from `StructuredSemanticModel` into `plc_ir.py`.
