# FX3U contract coverage audit · 2026-09-21

Source revision: `f2f1781a846c9f7073724b4cca485082d57f414c`. The scope is the admitted literal FX3U generation catalog. Method and reproduction commands are defined in the [contract coverage reference](../architecture/instruction-contract-coverage.md).

| Dimension | Result |
| --- | ---: |
| Generation forms audited | 282 |
| Corroborated native signatures/form identities | 227 |
| Existing opcode-only forms gaining a signature | 84 |
| Exact arity coverage before → after | 11 → 230 |
| Corroborated continuous/pulse forms | 219 |
| Corroborated 16/32-bit operation widths | 216 |
| Not promoted by this method | 55 |
| Claimed fully verified behavioral contracts | 0 |

The 230 exact arities include three pre-existing contracts preserved without a
new cross-manual promotion. Unpromoted forms retain their existing declarations.

Nine forms lack independent complete-call corroboration: BK+, FOR, IRET, IST,
LIMIT, NEXT, RBFM, REFF, SRET. Another 46 lack a complete unambiguous native
signature/form under this method: ABS, ACOS, ADPRW, ASIN, ATAN, CALL, CJ, COS,
DEG, DRAMP, DSORT, EADD, EBCD, EBIN, ECMP, EDIV, EMOV, EMUL, ENEG, ESQR,
ESTR, ESUB, EVAL, EXP, EZCP, HCMOV, HSCR, HSCS, HSCT, HSZ, LOG10, LOGE,
MC, MCR, MIDR, MIDW, MODBUS, MODRW, NOP, RAD, RST, SET, SIN, SORT2, TAN,
TBL. The machine-readable report includes individual reasons, original arities,
candidate pages and every dimension's before/after state. Existing SET/RST/BK+
checks are not removed because these entries were not promoted again.


## Conclusion

Cross-manual evidence expands native signatures and instruction-form coverage. Operand types, device limits, completion conditions and hardware availability retain their recorded evidence states. Per-form results are available through [fx3u_contract_promotions.json](../../resources/instructions/mitsubishi/fx3u_contract_promotions.json) and the audit command. This record includes no live-model, native GX compilation or PLC execution measurements.
