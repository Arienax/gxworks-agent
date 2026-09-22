# GX Works2 Structured Function ABI findings: samples 59-66：后续实验

来源版本：`f2f1781`。已完成的结果见[GX Works2 Structured Function ABI findings: samples 59-66](../../reports/gxw/gxw_structured_function_abi_59_66.md)。

## Development implications

Keep the binary and semantic layers separate:

```text
Program.pou
  -> raw node/port/wire parser
  -> geometric ConnectivityGraph
  -> function/terminal semantic layer
  -> PLC IR
```

The next semantic layer should use at least:

```text
node_kind
symbol
port_kind_code
port position/order
connectivity net
```

Do not classify devices or expressions from `symbol` alone.

Potential next experiments:

- another variadic function family to test whether `-N` is a general arity convention;
- functions with multiple ordinary outputs;
- an FB instance to compare built-in Function ABI vs FB instance ABI;
- timer/counter blocks;
- labels and typed local variables attached to function terminals.
