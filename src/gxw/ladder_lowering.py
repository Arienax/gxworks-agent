"""Lower verified relay logic from the existing ladder/IR contract to GXW objects."""
from .models import GXWFormatError


def ladder_to_object_model(program):
    from plc_ir import ir_to_ladder, is_plc_ir, validate_plc_ir
    from plc_json_validator import validate_ladder_full
    if is_plc_ir(program):
        validate_plc_ir(program)
        if program["plc"]["cpu"].upper() != "FX3U":
            raise GXWFormatError("the native FBD project template targets FX3U")
    ladder = ir_to_ladder(program)
    validate_ladder_full(ladder, plc_model="FX3U")
    nodes, wires = [], []
    model = {"schema_version": 1, "program": "1.Program.pou", "nodes": nodes, "wires": wires}

    blocks = []
    block_index = 0
    seen_wires = set()
    def wire(a, b):
        key = tuple(sorted((a, b)))
        if a != b and not a[0] == b[0] == 1 and key not in seen_wires:
            wires.append({"start": list(a), "end": list(b), "block": block_index})
            seen_wires.add(key)

    def contact(element, x, y):
        kind = element.get("type")
        if kind not in ("NO", "NC"):
            raise GXWFormatError(f"relay conversion does not support input {kind}; use a verified FBD node")
        nodes.append({"id": f"n{len(nodes)}", "template": "contact" if kind == "NO" else "contact_nc",
                      "symbol": element["address"], "x": x, "y": y-1, "block": block_index})

    def dimensions(inputs):
        width, height = 0, 3
        for element in inputs:
            if element.get("type") == "parallel_block":
                sizes = [dimensions(branch) for branch in element["branches"]]
                width += max((w for w, _ in sizes), default=0) + 4
                height = max(height, sum(h for _, h in sizes))
            else:
                width += 5
        return width, height

    def series(inputs, x, y):
        current = x
        for element in inputs:
            if element.get("type") == "parallel_block":
                branches = element["branches"]
                width = max((dimensions(b)[0] for b in branches), default=0) + 4
                branch_y = y
                for branch in branches:
                    wire((current, y), (current, branch_y))
                    wire((current, branch_y), (current+2, branch_y))
                    end = series(branch, current+2, branch_y)
                    wire((end, branch_y), (current+width, branch_y))
                    wire((current+width, branch_y), (current+width, y))
                    branch_y += dimensions(branch)[1]
                current += width
            else:
                wire((current, y), (current+2, y))
                contact(element, current+2, y)
                current += 4
                wire((current, y), (current+1, y))
                current += 1
        return current

    for block_index, rung in enumerate(ladder["rungs"]):
        y = 3
        seen_wires.clear()
        common = ([rung["header_element"]] if rung.get("header_element") else []) + rung.get("shared_inputs", [])
        junction = series(common, 1, y)
        branch_y = y
        for branch in rung["branches"]:
            wire((junction, y), (junction, branch_y))
            end = series(branch.get("inputs", []), junction, branch_y)
            output_y = branch_y
            if not branch.get("outputs"):
                raise GXWFormatError("relay branch requires an output")
            for output in branch["outputs"]:
                if output.get("type") != "COIL":
                    raise GXWFormatError(f"relay conversion does not support output {output.get('type')}; use a verified FBD node")
                wire((end, branch_y), (end, output_y))
                wire((end, output_y), (end+3, output_y))
                nodes.append({"id": f"n{len(nodes)}", "template": "coil", "symbol": output["address"], "x": end+3, "y": output_y-1, "block": block_index})
                output_y += 3
            branch_y += max(dimensions(branch.get("inputs", []))[1], 3*len(branch["outputs"]))
        y = branch_y+3
        wires.append({"start": [1, 0], "end": [1, y], "block": block_index})
        blocks.append({"canvas_height": y})
    if not nodes:
        raise GXWFormatError("relay conversion requires at least one network")
    model["blocks"] = blocks
    model["canvas_height"] = sum(b["canvas_height"] for b in blocks)
    # Comments are preserved in a companion source artifact by the workbench;
    # no unverified binary comment records are synthesized here.
    return model
