# GXW Structured FB ABI findings: samples 72-75, follow-ups 76-77：后续实验

来源版本：`f2f1781`。已完成的结果见[GXW Structured FB ABI findings: samples 72-75, follow-ups 76-77](../../reports/gxw/gxw_structured_fb_abi_72_75.md)。

## Remaining focused evidence

The TON/TON_E and CTU/CTU_E interface comparisons are now covered. User-defined FBs with separately declared formals, explicit FB-to-FB wires and IN_OUT formals remain unverified. Request concrete diagrams for those comparisons when needed.

This implementation provides instance identity, named interfaces, category and net bindings. It does not resolve label declarations, infer device validity, evaluate timer/counter state, assign memory, reconstruct scan order, or lower to `plc_ir.py`.
