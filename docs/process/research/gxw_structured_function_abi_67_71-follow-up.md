# GX Works2 Structured Function ABI findings: samples 67-71：后续实验

来源版本：`f2f1781`。已完成的结果见[GX Works2 Structured Function ABI findings: samples 67-71](../../reports/gxw/gxw_structured_function_abi_67_71.md)。

## Development implications

The current semantic pipeline should distinguish at least:

```text
Function family identity
Function serialized symbol
extensible vs fixed arity
data-input count
has execution-enable interface
port_kind_code sequence
terminal node roles
ConnectivityGraph nets
```

A first semantic Function descriptor can now safely model:

```text
base_name
serialized_symbol
data_input_count
has_enable_interface
ports
```

but should keep extensibility as registry-backed evidence rather than infer it from `-N` alone.

Next high-information targets:

- Function/FB with multiple ordinary outputs;
- FB instance ABI vs built-in Function ABI;
- timer/counter blocks;
- labels / typed locals at Function terminals;
- compile-derived representation after a controlled compile.
