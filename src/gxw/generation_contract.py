"""Parameter-only FBD generation; gxw owns interface expansion and geometry."""


FBD_PARAMETER_MODEL_SCHEMA = {
    'type': 'object',
    'properties': {
        'schema_version': {'const': 2}, 'program': {'type': 'string'},
        'declaration_edits': {'type': 'object'},
        'preserve_blocks': {'type': 'array', 'items': {'type': 'integer'}},
        'networks': {'type': 'array', 'items': {
            'type': 'object', 'properties': {
                'name': {'type': 'string'}, 'comment': {'type':'string'}, 'source_block': {'type': 'integer'},
                'calls': {'type': 'array', 'items': {
                    'type': 'object', 'properties': {
                        'id': {'type': 'string'}, 'type': {'type': 'string'}, 'instance': {'type': 'string'},
                        'arguments': {'type': 'object', 'additionalProperties': {'type': 'string'}}},
                    'required': ['id', 'type', 'instance'], 'additionalProperties': False}},
                'nodes': {'type': 'array', 'items': {
                    'type': 'object', 'properties': {
                        'id': {'type': 'string'}, 'template': {'type': 'string'}, 'symbol': {'type': 'string'},
                        'arguments': {'type': 'object', 'additionalProperties': {'type': 'string'}},
                        'port_edits': {'type': 'object'}},
                    'required': ['id', 'template'], 'additionalProperties': False}},
                'wires': {'type': 'array', 'items': {
                    'type': 'object', 'properties': {'from': {'type': 'string'}, 'to': {'type': 'string'}},
                    'required': ['from', 'to'], 'additionalProperties': False}}},
            'additionalProperties': False}}},
    'required': ['networks'], 'additionalProperties': False}


FBD_GENERATION_PROMPT = """Generate GX Works2 FBD/structured ladder parameters. Return JSON only:
{"summary":"human-readable summary","model":{"schema_version":2,"program":"supplied program","networks":[...]}}.
Use the supplied parameter_schema. This is a candidate, not a compiled or executed program.
Honor the confirmed specification and explicit I/O. Never invent stop/emergency addresses.
Core creates blocks, current interfaces, dimensions, IN_OUT positions and orthogonal wires.
Do not return coordinates, dimensions, ports, canvas_height, source_offset or wire bend points.
Each network accepts calls, nodes, wires and an optional name. Networks execute in supplied order;
Core separates disconnected diagrams into native blocks while retaining that order.
For a normal FB call use {"id":"power","type":"SourceFb","instance":"Power1",
"arguments":{"Enable":"Enable1","Axis":"Axis1","Status":"Status1"}} inside calls.
Argument keys are current source formal names; values are variable/device/IEC-literal strings.
A single IN_OUT argument binds its input and output to that same actual variable. Explicit
in.NAME/out.NAME endpoint bindings remain available when separate actuals are intentional.
Source definitions and actual symbols must exist or be explicitly declared. Do not invent
FB interfaces, copy a name into an empty implementation, or approximate unsupported behavior.
For contacts, coils, input/output terminals and functions use nodes=[{id,template,symbol}].
Optional arguments on callable nodes use the same source formal mapping as calls.
Connect nodes with wires=[{"from":"node_id.port","to":"node_id.port"}]. Use exact supplied
endpoint names, including in.STATE/out.STATE for IN_OUT. Only output-to-input connections are valid.
Use the current CPU's supplied templates. Unsupported ABI or absent implementation must be
reported as {"unsupported":"specific missing capability"}, not guessed geometry or placeholder code.
FB instance declarations are synchronized by Core. Other variables require explicit
declaration_edits={table:{upserts:[{name,data_type,kind,class_name,initial_value,device,iec_address,comment}],
renames:{old:new},remove:[name]}}. Use current declaration-table identities and valid CPU addresses.
For edits keep unrelated networks and source_block identities in the supplied parameter_model.
Keep preserve_blocks unchanged: these source blocks cannot be safely regenerated from parameters.
Changing declarations or callee implementations requires a fresh source context.
Compilation, complete checking, GUI opening, cold read and execution are distinct results.
"""
