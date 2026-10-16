"""Lower logical FBD parameters and verified relay logic to existing GXW objects."""
from copy import deepcopy
import re

from .models import GXWFormatError


def fbd_topology_from_context(context):
    """Project readable connections for model input, without editor coordinates."""
    from .connectivity import build_connectivity_graph
    from .object_model import _ports, _key, source_node_template
    from .models import CONTACT_NODE_KINDS
    model = dict(schema_version=2, program=context.program.logical_name, networks=[], preserve_blocks=[])
    graph = build_connectivity_graph(context.program)
    for index, (block, records) in enumerate(context.program.block_records()):
        all_nodes = [node for node in context.program.nodes if node.offset in block.record_offsets]
        comments=[node.symbol for node in all_nodes if node.kind_code==0x10 and not node.ports]
        nodes=[node for node in all_nodes if not (node.kind_code==0x10 and not node.ports)]
        if any(record in context.program.unknown_records for record in records):
            model['preserve_blocks'].append(block.offset)
            continue
        try:
            for node in nodes:
                source_node_template(_key(node), context.sources)
            ports = {node.offset: _ports(node, context.sources, bind_instance=False) for node in nodes}
            projected = []
            for node in nodes:
                item = dict(id=f'n{node.offset}', template=_key(node), symbol=node.symbol)
                negated = {p['name']: {'negated': True} for p, native in zip(ports[node.offset], node.ports)
                           if native.port_kind_code & 8}
                if negated and node.kind.value in ('function', 'function_block'):
                    item['port_edits'] = negated
                projected.append(item)
            network = dict(source_block=block.offset, nodes=projected, wires=[])
            if comments:
                network['comment']='\r\n'.join(comments)
            for net in graph.nets:
                if net.block_index != index:
                    continue
                if len(net.ports) < 2:
                    rail=[wire for wire in context.program.wires if wire.offset in net.wire_offsets]
                    if not net.ports and rail and all(w.start.x==w.end.x==1 for w in rail):
                        continue  # Native empty left rail has no connected execution input.
                    if net.wire_offsets or any(p.node_kind in CONTACT_NODE_KINDS and p.port_index == 0 and p.point.x == 1
                                               for p in net.ports):
                        raise GXWFormatError('source net has dangling wires or implicit power-rail semantics')
                    continue
                left = [p for p in net.ports if p.point.x == next(n.bbox.right for n in nodes if n.offset == p.node_offset)]
                right = [p for p in net.ports if p not in left]
                if not left or not right:
                    raise GXWFormatError('source net has no unambiguous direction')
                for a in left:
                    for b in right:
                        network['wires'].append({'from':f'n{a.node_offset}.'+ports[a.node_offset][a.port_index]['name'],
                                                 'to':f'n{b.node_offset}.'+ports[b.node_offset][b.port_index]['name']})
            model['networks'].append(network)
        except GXWFormatError:
            model['preserve_blocks'].append(block.offset)
    return model


def fbd_topology_to_object_model(value, context):
    """Expand source-bound parameters and route them into the existing v2 model."""
    from .object_model import _ports, build_object_program, native_catalog, source_node_template
    from .connectivity import build_connectivity_graph

    if (not isinstance(value, dict) or value.get('schema_version', 2) != 2
            or set(value) - {'schema_version', 'program', 'networks', 'declaration_edits', 'preserve_blocks'}
            or value.get('program', context.program.logical_name) != context.program.logical_name
            or not isinstance(value.get('networks'), list)):
        raise GXWFormatError('FBD parameters require current program, networks and optional declaration_edits')
    edits = deepcopy(value.get('declaration_edits', {}))
    sources = context.draft_sources(edits)
    result = dict(schema_version=2, program=context.program.logical_name,
                  nodes=[], wires=[], blocks=[], declaration_edits=edits)
    identifiers = set()
    expected = []
    requested_ids={item.get('id') for network in value['networks'] if isinstance(network,dict)
        for key in ('nodes','calls') if isinstance(network.get(key,[]),list) for item in network.get(key,[])
        if isinstance(item,dict) and isinstance(item.get('id'),str)}

    def identifier(text):
        if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', text) or text in identifiers:
            raise GXWFormatError('FBD node IDs must be unique identifiers: ' + str(text))
        identifiers.add(text)
        return text
    def internal_id(prefix):
        name = prefix
        while name in identifiers or name in requested_ids:
            name += '_'
        return identifier(name)

    # Opaque blocks have no safe logical projection. Retain their records and
    # source envelope instead of regenerating them from an incomplete graph.
    original = context.object_model()
    membership = {record.offset: index for index, (_, records) in enumerate(context.program.block_records())
                  for record in records}
    opaque = {membership[record.offset] for record in context.program.unknown_records}
    existing_blocks = {block.offset:index for index,(block,_) in enumerate(context.program.block_records())}
    retained = value.get('preserve_blocks', [])
    if not isinstance(retained, list) or any(type(offset) is not int or offset not in existing_blocks for offset in retained):
        raise GXWFormatError('preserve_blocks must refer to current source block identities')
    opaque.update(existing_blocks[offset] for offset in retained)
    emitted, replaced = set(), set()
    def retain_before(before):
        for index, (block, _) in enumerate(context.program.block_records()):
            if index >= before or index not in opaque or index in emitted:
                continue
            emitted.add(index)
            retain(block, index)
    def retain(block, index):
        target = len(result['blocks'])
        result['blocks'].append(dict(source_offset=block.offset, canvas_height=block.canvas_height))
        for node in original['nodes']:
            if membership[node['source_offset']] == index:
                item = deepcopy(node)
                item['block'] = target
                identifier(item['id'])
                result['nodes'].append(item)
        for wire in original['wires']:
            if membership[wire['source_offset']] == index:
                result['wires'].append({**deepcopy(wire), 'block': target})

    for network in value['networks']:
        if (not isinstance(network, dict) or set(network) - {'name', 'comment', 'nodes', 'calls', 'wires', 'source_block'}
                or not all(isinstance(network.get(key, []), list) for key in ('nodes','calls','wires'))):
            raise GXWFormatError('FBD network accepts nodes, calls and named wires')
        comment=network.get('comment',network.get('name',''))
        if not isinstance(comment,str) or '\0' in comment:
            raise GXWFormatError('network comment must be text without NUL')
        if 'source_block' in network:
            offset = network['source_block']
            if (type(offset) is not int or offset not in existing_blocks
                    or existing_blocks[offset] in opaque or offset in replaced):
                raise GXWFormatError('network source_block must select a current non-preserved block')
            replaced.add(offset)
            retain_before(existing_blocks[offset])
        specifications = deepcopy(network.get('nodes', []))
        for call in network.get('calls', []):
            if (not isinstance(call, dict) or set(call) - {'id','type','instance','arguments'}
                    or not all(isinstance(call.get(key), str) and call[key] for key in ('id','type','instance'))):
                raise GXWFormatError('FB call requires id, type, instance and optional arguments')
            specifications.append(dict(id=call['id'], template='function_block:'+call['type'],
                                       symbol=call['instance'], arguments=call.get('arguments', {})))
        nodes, ports, templates, wires = {}, {}, {}, deepcopy(network.get('wires', []))
        bindings = []
        for spec in specifications:
            if (not isinstance(spec, dict) or set(spec) - {'id','template','symbol','arguments','port_edits'}
                    or not isinstance(spec.get('template'), str)):
                raise GXWFormatError('logical FBD nodes accept id/template/symbol/arguments/port_edits; coordinates belong to Core')
            name = identifier(spec.get('id'))
            key = spec['template']
            template = source_node_template(key, sources, symbol=spec.get('symbol'))
            node = dict(id=name, template=key, symbol=spec.get('symbol', template.symbol))
            if not isinstance(node['symbol'], str) or not node['symbol'] or '\0' in node['symbol']:
                raise GXWFormatError('FBD node requires an explicit nonempty symbol')
            if 'port_edits' in spec:
                node['port_edits'] = deepcopy(spec['port_edits'])
            nodes[name], templates[name] = node, template
            ports[name] = {port['name']: port for port in _ports(template, sources, bind_instance=False)}
            arguments = spec.get('arguments', {})
            if not isinstance(arguments, dict):
                raise GXWFormatError('FB/function arguments must map current formal names to symbols')
            bound = set()
            if any(not isinstance(formal, str) for formal in arguments):
                raise GXWFormatError('formal names must be strings')
            for formal, symbol in sorted(arguments.items()):
                selected = [port for port in ports[name].values() if port['name'] == formal]
                if not selected:
                    selected = [port for port in ports[name].values()
                                if port.get('formal_name') == formal and port.get('class_code') == 5]
                if not selected or not isinstance(symbol, str) or not symbol or '\0' in symbol:
                    raise GXWFormatError('unknown current formal or invalid symbol: ' + name + '.' + str(formal))
                for port in selected:
                    if port['name'] in bound:
                        raise GXWFormatError('a current formal is bound more than once: ' + name + '.' + port['name'])
                    bound.add(port['name'])
                    if port.get('side') not in ('in', 'out'):
                        raise GXWFormatError('parameter binding requires a source-resolved callable port')
                    bindings.append((name, port, symbol))
        for call_id, port, symbol in bindings:
            name = internal_id(f'{call_id}_arg{len(nodes)}')
            side = port['side']
            key = 'input' if side == 'in' else 'output'
            template = native_catalog()[key]
            nodes[name] = dict(id=name, template=key, symbol=symbol)
            templates[name] = template
            ports[name] = {p['name']: p for p in _ports(template, sources, bind_instance=False)}
            wires.append({'from': name+'.OUT' if side == 'in' else call_id+'.'+port['name'],
                          'to': call_id+'.'+port['name'] if side == 'in' else name+'.IN'})

        def endpoint(text):
            if not isinstance(text, str) or '.' not in text:
                raise GXWFormatError('wire requires node.port endpoints')
            name, formal = text.split('.', 1)
            if name not in ports or formal not in ports[name]:
                raise GXWFormatError('unknown current FBD endpoint: ' + text)
            return name, ports[name][formal]

        parent = {name: name for name in nodes}
        net_parent = {name+'.'+formal: name+'.'+formal for name in nodes for formal in ports[name]}
        def find(table, key):
            while table[key] != key:
                table[key] = table[table[key]]
                key = table[key]
            return key
        def union(table, left, right):
            table[find(table, right)] = find(table, left)
        predecessors = {name: set() for name in nodes}
        for wire in wires:
            if not isinstance(wire, dict) or set(wire) != {'from','to'}:
                raise GXWFormatError('logical wires require only from/to; routing belongs to Core')
            left, lp = endpoint(wire['from'])
            right, rp = endpoint(wire['to'])
            if lp['x'] != templates[left].bbox.right-templates[left].bbox.left or rp['x'] != 0:
                raise GXWFormatError('logical wire must connect an output to an input')
            predecessors[right].add(left)
            union(parent, left, right)
            union(net_parent, wire['from'], wire['to'])
        components = {}
        for name in nodes:
            components.setdefault(find(parent, name), []).append(name)
        if not components and comment:
            template=source_node_template('comment',sources,symbol=comment)
            name=internal_id('comment'+str(len(result['blocks'])))
            result['nodes'].append(dict(id=name,template='comment',symbol=comment,x=2,y=1,block=len(result['blocks'])))
            result['blocks'].append(dict(canvas_height=template.bbox.bottom+3))
        for component_index,members in enumerate(components.values()):
            block = len(result['blocks'])
            levels, pending = {}, list(members)
            while pending:
                ready = [name for name in pending if predecessors[name] <= levels.keys()]
                if not ready:
                    raise GXWFormatError('cyclic graphic connection needs an explicit supported state/delay block')
                for name in ready:
                    levels[name] = max((levels[p]+1 for p in predecessors[name]), default=0)
                    pending.remove(name)
            net_groups = {}
            for text in net_parent:
                if text.split('.',1)[0] in members:
                    net_groups.setdefault(find(net_parent, text), []).append(text)
            nets = [texts for texts in net_groups.values() if len(texts) > 1]
            expected.extend(frozenset(texts) for texts in net_groups.values())
            _layout_fbd_component(nodes, ports, templates, members, levels, nets, block, result,
                                  comment=comment if component_index==0 else '',
                                  comment_id=internal_id('comment'+str(block)) if component_index==0 and comment else None)
    retain_before(len(existing_blocks))
    if not result['blocks']:
        result['blocks'].append(dict(canvas_height=12))
    result['canvas_height'] = sum(block['canvas_height'] for block in result['blocks'])
    # Reparse the native graph with the existing independent connectivity reader.
    # Matching wire counts would miss accidental endpoint-on-line junctions.
    program = build_object_program(context.program, result, sources=sources, original_sources=context.sources)
    names = {node.offset: item['id'] for item, node in zip(result['nodes'], program.nodes)}
    indexed = {node.offset: list(_ports(node, sources, bind_instance=False)) for node in program.nodes}
    observed = []
    fresh = {text for group in expected for text in group}
    for net in build_connectivity_graph(program).nets:
        group = frozenset(names[p.node_offset]+'.'+indexed[p.node_offset][p.port_index]['name'] for p in net.ports)
        if group & fresh:
            observed.append(group)
    if set(observed) != set(expected):
        raise GXWFormatError('automatic FBD layout changed the requested electrical connections')
    return result


def _layout_fbd_component(nodes, ports, templates, members, levels, nets, block, result, *, comment='',comment_id=None):
    """Route separate conductor tracks; crossings have no endpoint junctions."""
    callables = [name for name in members if nodes[name]['template'].startswith(('function:', 'function_block:'))]
    # A single call and its argument terminals use the native compact pattern.
    simple = len(callables) == 1 and all(name in callables or nodes[name]['template'] in ('input','output') for name in members)
    positions, escape = {}, {}
    top=2
    if comment:
        from .object_model import source_node_template
        template=source_node_template('comment',None,symbol=comment)
        result['nodes'].append(dict(id=comment_id,template='comment',symbol=comment,x=2,y=1,block=block))
        top=template.bbox.bottom+3
    if simple:
        call = callables[0]
        related = {}
        for texts in nets:
            if len(texts) != 2 or sum(text.split('.',1)[0] == call for text in texts) != 1:
                simple = False
                break
            target = next(text for text in texts if text.split('.',1)[0] == call)
            terminal = next(text for text in texts if text != target).split('.',1)[0]
            if terminal in related:
                simple = False
                break
            related[terminal] = ports[call][target.split('.',1)[1]]
        if len(related) != len(members)-1:
            simple = False
    if simple:
        label_width = max([2]+[len(nodes[name]['symbol']) for name in related if nodes[name]['template']=='input'])
        x, y = label_width+6, top
        positions[call] = (x,y)
        width = templates[call].bbox.right-templates[call].bbox.left
        for name, port in related.items():
            positions[name] = (x-6 if port['x']==0 else x+width+5, y+port['y']-1)
    else:
        if _layout_callable_component(nodes,ports,templates,members,levels,nets,block,result,top):
            return
        cursor = 2
        for level in sorted(set(levels.values())):
            column = [name for name in members if levels[name] == level]
            left = [(name,p) for name in column for p in ports[name].values() if p['x']==0]
            right = [(name,p) for name in column for p in ports[name].values() if p['x']!=0]
            label_width = max([0]+[len(nodes[name]['symbol'])+2 for name in column
                                  if nodes[name]['template']=='input'])
            x = cursor+2*len(left)+3+label_width
            width = max(templates[name].bbox.right-templates[name].bbox.left for name in column)
            y = top
            for name in column:
                positions[name] = (x,y)
                y += templates[name].bbox.bottom-templates[name].bbox.top+3
            for side, items in [('in',left),('out',right)]:
                items.sort(key=lambda item: positions[item[0]][1]+item[1]['y'])
                for index,(name,port) in enumerate(items):
                    escape[name+'.'+port['name']] = (x-2-2*(len(items)-index-1) if side=='in'
                                                     else x+width+2+2*(len(items)-index-1))
            cursor = x+width+2*len(right)+5
    bottom = 0
    for name in members:
        x,y = positions[name]
        result['nodes'].append({**nodes[name], 'x':x, 'y':y, 'block':block})
        bottom = max(bottom,y+templates[name].bbox.bottom-templates[name].bbox.top)
    for index,texts in enumerate(nets):
        track = bottom+3+index*2
        points = []
        for text in texts:
            name,formal = text.split('.',1)
            p = ports[name][formal]
            x,y = positions[name]
            point = (x+p['x'], y+p['y'])
            points.append((point, escape.get(text)))
        if simple:
            (left,_),(right,_) = points
            if left[1] != right[1]:
                raise GXWFormatError('compact FBD terminals do not align to current source ports')
            left,right=sorted((left,right))
            result['wires'].append(dict(start=list(left), end=list(right), block=block))
            continue
        drops = []
        for point, column in points:
            for start,end in [(point,(column,point[1])),((column,point[1]),(column,track))]:
                if start != end:
                    start,end=sorted((start,end))
                    result['wires'].append(dict(start=list(start), end=list(end), block=block))
            drops.append(column)
        if min(drops) != max(drops):
            result['wires'].append(dict(start=[min(drops),track], end=[max(drops),track], block=block))
    height = bottom+3 if simple or not nets else bottom+2*len(nets)+4
    result['blocks'].append(dict(canvas_height=max(6,height)))


def _layout_callable_component(nodes,ports,templates,members,levels,nets,block,result,top):
    """Stack callable networks with short terminals and separate shared buses.

    EN/ENO chains cross only the empty gap below their source box. This keeps
    the same port nets and execution order without routing every signal below
    the entire diagram or allocating a whole column per terminal.
    """
    calls=[name for name in members if nodes[name]['template'].startswith(('function:', 'function_block:'))]
    if not calls or any(name not in calls and nodes[name]['template'] not in ('input','output') for name in members):
        return False
    classified=[]
    for texts in nets:
        endpoints=[(text,*text.split('.',1)) for text in texts]
        drivers=[item for item in endpoints if ports[item[1]][item[2]]['x']!=0]
        sinks=[item for item in endpoints if ports[item[1]][item[2]]['x']==0]
        if len(drivers)!=1 or not sinks:
            return False
        source=drivers[0]
        if any(name not in calls for _,name,_ in sinks if nodes[name]['template']!='output'):
            return False
        outputs=[item for item in sinks if nodes[item[1]]['template']=='output']
        if len(outputs)>1:
            return False
        classified.append((source,sinks))
    shared=[index for index,(source,sinks) in enumerate(classified)
        if len(sinks)>1 or source[1] in calls and any(name in calls for _,name,_ in sinks)]
    chained=[index for index,(source,sinks) in enumerate(classified)
        if source[1] in calls and any(name in calls for _,name,_ in sinks)]
    padding=max(6,len(shared)+4)
    labels=max([0]+[len(nodes[name]['symbol']) for name in members if nodes[name]['template']=='input'])
    x=labels+padding+4
    positions={}
    ordered=sorted(calls,key=lambda name:(levels[name],members.index(name)))
    y=top
    tracks={}
    for name in ordered:
        positions[name]=(x,y)
        bottom=y+templates[name].bbox.bottom-templates[name].bbox.top
        outgoing=[i for i in chained if classified[i][0][1]==name]
        tracks.update((i,bottom+1+j) for j,i in enumerate(outgoing))
        y=bottom+max(4,len(outgoing)+3)
    right=max(x+templates[name].bbox.right-templates[name].bbox.left for name in calls)
    output_x=right+2*len(chained)+6
    def point(endpoint):
        _,name,formal=endpoint
        px,py=positions[name];port=ports[name][formal]
        return px+port['x'],py+port['y']
    for source,sinks in classified:
        if source[1] not in calls:
            target=next((item for item in sinks if item[1] in calls),None)
            if target is None:return False
            name=source[1];port=ports[name][source[2]]
            positions[name]=(x-padding-port['x'],point(target)[1]-port['y'])
        for sink in sinks:
            if sink[1] not in calls:
                name=sink[1];port=ports[name][sink[2]]
                positions[name]=(output_x-port['x'],point(source)[1]-port['y'])
    if set(positions)!=set(members):
        return False
    for name in members:
        px,py=positions[name]
        result['nodes'].append({**nodes[name], 'x':px, 'y':py, 'block':block})
    for index,(source,sinks) in enumerate(classified):
        segments=[]
        def wire(a,b):
            if a!=b:segments.append(tuple(sorted((a,b))))
        start=point(source)
        if index in chained:
            lane=x-2-shared.index(index)
            escape=right+2+2*chained.index(index)
            track=tracks[index]
            wire(start,(escape,start[1]));wire((escape,start[1]),(escape,track))
            wire((escape,track),(lane,track))
            targets=[point(sink) for sink in sinks if sink[1] in calls]
            wire((lane,track),(lane,max(p[1] for p in targets)))
            for target in targets:wire((lane,target[1]),target)
            for sink in sinks:
                if sink[1] not in calls:wire(start,point(sink))
        elif index in shared:
            lane=x-2-shared.index(index)
            targets=[point(sink) for sink in sinks]
            wire(start,(x,start[1]))
            wire((lane,min(p[1] for p in targets)),(lane,max(p[1] for p in targets)))
            for target in targets:wire((lane,target[1]),target)
        else:
            for sink in sinks:wire(start,point(sink))
        # Coalesce same-net collinear overlaps without adding new junctions.
        lines={}
        for a,b in segments:
            axis=0 if a[1]==b[1] else 1
            lines.setdefault((axis,a[1-axis]),[]).append((a[axis],b[axis]))
        for (axis,fixed),intervals in lines.items():
            merged=[]
            for lo,hi in sorted(intervals):
                if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
                else:merged.append((lo,hi))
            for lo,hi in merged:
                a,b=([lo,fixed],[hi,fixed]) if axis==0 else ([fixed,lo],[fixed,hi])
                result['wires'].append(dict(start=a,end=b,block=block))
    bottom=max(py+templates[name].bbox.bottom-templates[name].bbox.top for name,(px,py) in positions.items())
    result['blocks'].append(dict(canvas_height=max(6,bottom+3)))
    return True


def ladder_to_object_model(program):
    from plc.ir import ir_to_ladder, is_plc_ir, validate_plc_ir
    from plc.validation import validate_ladder_full
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
