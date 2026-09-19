"""Model-independent FBD generation instructions owned by Python Core."""

FBD_GENERATION_PROMPT = """Generate a GX Works2 structured ladder/FBD object model. Return JSON only:
{"summary":"human-readable summary", "model":{...}}. This is a candidate, not a compiled program.
Honor the confirmed specification and explicit I/O; never invent stop/emergency addresses.
Use only the supplied native templates and ports. If the requested behavior needs an unsupported
ABI, return {"unsupported":"explanation"} instead of approximating it.
Model: schema_version=1, program=the supplied name, canvas_height=integer,
nodes=[{id,template,symbol,x,y}], wires=[{from:"node_id.port",to:"node_id.port",via:[[x,y],...]}]
or wires=[{start:[x,y],end:[x,y]}]. Coordinates are nonnegative editor grid integers.
Dimensions and local port offsets come from the catalog. Port point=(node x+port x,node y+port y).
Only coincident ports, wire endpoints/T junctions connect; ordinary crossings do not.
Wires must be orthogonal; specify via points for bends. Keep objects separated.
Contacts/coils use symbols such as X0/Y0. INPUT/OUTPUT terminals contain devices or IEC literals,
e.g. T#1s. Functions keep their catalog symbol; FB symbol is its unique instance name.
FB declarations are synchronized automatically. Other variables need explicit declarations:
declaration_edits={table:{upserts:[{name,data_type,kind:"variable"|"function_block",
class_name,initial_value,device,iec_address,comment}],renames:{old:new},remove:[name]}}.
Known basic types: BOOL INT DINT WORD DWORD REAL TIME STRING; ARRAY [a..b] OF base.
Local classes VAR/VAR_CONSTANT; global VAR_GLOBAL/VAR_GLOBAL_CONSTANT.
For edits preserve source_offset on existing nodes/wires and all unrelated objects.
labels,issues,unknown_record_count are read-only source projections; do not change them.
Unknown source objects are retained only when their source_offset/template are unchanged.
Do not assert semantic equivalence or compilation merely because a model was generated.
"""
