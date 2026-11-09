"""Structural parallel-row accounting shared by factoring and GX export.

GX Works2 Simple Project manual SH-080780, section 6.2.8, limits a ladder
block to 24 diagram rows. Instruction-list length is not diagram height.
This conservative count covers vertical branching, not horizontal wrapping
or native instruction-box layout; passing it is not native compilation.
"""

GXWORKS2_MAX_LADDER_BLOCK_ROWS = 24


def parallel_input_rows(elements):
    """Count parallel expansion, conservatively stacking serial OR regions."""
    rows = 1
    for element in elements:
        if isinstance(element, dict) and element.get("type") == "parallel_block":
            arms = element.get("branches") or []
            rows += max(0, sum(parallel_input_rows(arm) for arm in arms) - 1)
    return rows


def parallel_ladder_rows(rung):
    """Include every output branch and the shared prefix in one block."""
    prefix = ([rung["header_element"]] if rung.get("header_element") else [])
    prefix += rung.get("shared_inputs") or []
    branches = rung.get("branches") or []
    branch_rows = sum(parallel_input_rows(branch.get("inputs") or [])
                      + max(1, len(branch.get("outputs") or [])) - 1
                      for branch in branches)
    return max(1, branch_rows) + parallel_input_rows(prefix) - 1
