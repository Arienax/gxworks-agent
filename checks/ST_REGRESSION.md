# Offline ST compatibility regression

Base: `4c456be917e67fe4091811c575690a7c17fdd28b` (main at checkout).
Command: `python checks/st_regression.py` from the repository root.
Dependencies: the exact tree-sitter versions in `requirements/mcp.txt`.
On Linux without clang, install the pinned extension with `CC=gcc`.

## Confirmed corrections

| Counterexample | Before | After |
| --- | --- | --- |
| `1 := 2;`, `TRUE := FALSE;` (also typed, based, string and parenthesized literals) | Syntax and semantics passed; lowering admitted a non-reference destination | Syntax recognized; `ST_WRITE_TARGET`; lowering blocked |
| Structure member `value : INT := TRUE` | Static passed; scan stopped with conversion gap | Static type failure; lowering blocked |
| Structure member `value : INT := 40000` | Static passed; scan stopped with overflow gap | Static range failure; lowering blocked |
| `NOT WORD#0` | Constant evaluation returned Boolean false; runtime attempted to decode Python's unbounded negative complement | Constant and runtime evaluation use a masked, width-specific complement, yielding WORD 65535 |

No grammar replacement or new CPU permission rules are introduced. Boolean NOT remains Boolean. Existing declaration/type/range checking supplies the structure-default checks.

## Coverage and observed evidence

The suite has 10 test methods with parameterized subcases. On the base revision it reproduces 10 assertion failures in the three affected test methods. With the corrections: 9 methods pass, 1 skips.

| Area | Offline evidence |
| --- | --- |
| Assignment targets, data types, invalid input | Literals cannot be destinations; duplicate declarations, malformed syntax/literals, integer/string ranges, invalid array bounds/member/index types are rejected |
| Device addressing and CPU restrictions | FX3U rejects X8, D0.0 and writes to M8000; Q03UDV accepts hexadecimal bit selectors D0.0/A/F with expected memory effects and rejects X writes; Q00 without profile remains unverified |
| Arrays and structures | Repetition, negative bounds, multidimensional indexing, whole-array copy, structure defaults; runtime variable-index violation yields a gap |
| Functions and function blocks | User function call and FB state over two scans |
| Control flow | IF/ELSIF, CASE lists/ranges/overlap, descending FOR, WHILE/EXIT, REPEAT, RETURN; nonterminating loop hits execution budget |
| Expressions | Isolated binding checks for parentheses, multiplication/addition, ordering/equality, left associativity of exponentiation/subtraction, NOT/AND; arithmetic and finite-width NOT values |
| Native compilation | Not run; no GX Works2 installation available in this Linux environment |

The manual-derived fixtures follow Mitsubishi **MELSEC-Q/L/F Structured Programming Manual (Fundamentals), SH080782ENG-O**, section 5.1: operator priorities/types p.74, assignment p.75, control flow pp.76-80, function/FB invocation p.81. Standalone declaration wrappers were added to bind labels in the offline harness. They are not exported GXW projects or compiler receipts.
Source: https://www.mitsubishielectric.com/dl/fa/document/manual/plc/sh080782eng/sh080782engo.pdf

## Important limits

The public checkout lacks `instruction_definitions.json.gz` and the `ST.OPERATORS` language facts. Running the original `check_st_runtime()` unchanged therefore fails its assertion after the inspector returns `unverified` (GX operator facts missing or ambiguous). The regression runner skips that integration check explicitly when the real operator facts are unavailable; it does not label it passed.

Only the expression unit test injects a precedence table derived from manual p.74. It does not write production resources or fabricate `source_verified` records. Its results establish adapter/evaluator behavior in isolation, not production fact-bundle integration, instruction availability, power numeric-domain compatibility, or native compilation. The missing-facts test independently verifies production inspection remains unverified.

`syntax=passed`, `semantic=passed` and an observed bounded scan remain distinct evidence levels. The static inspector's `behavior` and `native_compile` remain unverified without their respective checks. A runtime bounds/budget gap is not an observed successful scan.

Additional observed input-form limitations, not changed here: a complete IEC source beginning with a top-level VAR_GLOBAL block before PROGRAM is routed as a body and rejected; an empty POU is deliberately rejected by inspection/lowering. These are not claimed as GX Works2 native-compiler incompatibilities without matching project evidence.

## Native follow-up samples

In an actual GX Works2 project bind the CPU, software version, declarations and compile options, then compare: literal assignment rejection; invalid structure defaults; WORD NOT at 0000/FFFF; Q03UDV D0.A read/write; and one FB called across two scans. Record native diagnostics against the exact source/project revision. No native result is inferred from the offline outcomes above.
