# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""Correlate scalar FBD outputs with current native code and graphs."""
from __future__ import annotations
from gxw.diagnostic_positions import (
    _native_network_members,
)


from dataclasses import dataclass
from .connectivity import ConnectivityGraph
from plc.device_identity import canonical_operand

def _reject(reason):
    return {'status': 'unresolved', 'public_check_promoted': False, 'reason': reason}


def _device(value):
    return canonical_operand(value)


def _output_chain(records, write):
    # Q03UDV's controlled one-driver/two-terminal case emits LD/OUT/OUT.
    # Follow only contiguous ordinary OUT records; every other opcode,
    # lexical gap or duplicate record leaves the producer unresolved.
    chain,seen = [write],set()
    for _ in range(64):
        current=chain[-1]
        if (any(type(current.get(key)) is not int for key in
                ('record_index','native_step','source_offset','source_end'))
                or current['record_index'] in seen or current['record_index']<0
                or sum(type(r.get('record_index')) is int and r['record_index']==current['record_index']
                       for r in records)!=1
                or not 0<=current['source_offset']<current['source_end']):
            return None
        seen.add(current['record_index'])
        previous=[r for r in records if type(r.get('record_index')) is int
            and r['record_index']==current['record_index']-1]
        if len(previous)!=1:
            return None
        prior=previous[0]
        if (len(prior.get('args',[]))!=1
                or any(type(prior.get(key)) is not int for key in ('native_step','source_offset','source_end'))
                or prior['source_end']!=current['source_offset']
                or not 0<=prior['source_offset']<prior['source_end']
                or not 0<=prior['native_step']<current['native_step']):
            return None
        if prior.get('op')=='LD':
            return [prior,*reversed(chain)]
        if prior.get('op')!='OUT':
            return None
        chain.append(prior)
    return None


def _direct_read(records, write):
    chain=_output_chain(records, write)
    return chain[0] if chain is not None else None


def _bbox(row):
    values = [row.get(key) for key in ('left','top','right','bottom')]
    return values if all(type(value) is int for value in values) else None


def _node_bbox(node):
    return [node['x'],node['y'],node['x']+node['width'],node['y']+node['height']]


def _scalar_terminal(row):
    # Original direct-device references have no declaration type. The
    # independently observed scalar local/global BOOL references do.
    # Other declaration roles, arrays and conflicting types stay unresolved.
    return (type(row.get('array_data_type')) is int and row['array_data_type']==0
        and ((type(row.get('class_code')) is int and row['class_code']==0
              and type(row.get('data_type')) is int and row['data_type']==0)
            or (type(row.get('class_code')) is int and row['class_code'] in (1,8)
                and type(row.get('data_type')) is int and row['data_type']==1)))


@dataclass(frozen=True)
class _OutputConnections:
    point: list[int]
    connectivity: ConnectivityGraph | None
    model: dict
    source: dict
    port: dict
    network: int
    nodes: list[dict]
    scoped: list[dict]
    records: list[dict]
    read: dict
    producer: dict
    wires: list[dict]

    def connection(self, node, endpoint):
        target_point = [node['x']+endpoint['x'],node['y']+endpoint['y']]
        if self.connectivity is None:
            return {'kind':'port_overlap','wire_offsets':[]} if target_point == self.point else None
        if self.connectivity.logical_name != self.model.get('program'):
            return None
        source_key = (self.source['source_offset'], self.source['ports'].index(self.port))
        target_key = (node['source_offset'], 0)
        try:
            net = self.connectivity.net_for_port(*source_key)
            target_net = self.connectivity.net_for_port(*target_key)
        except KeyError:
            return None
        # Observed overlap, straight, bent and T-junction forms have one scalar
        # FB driver and only output terminals; expression nodes remain excluded.
        keys = {(p.node_offset,p.port_index) for p in net.ports}
        if (net.block_index != self.network - 1 or target_net.block_index != self.network - 1
                or net.index != target_net.index or len(keys) != len(net.ports)
                or not {source_key,target_key} <= keys):
            return None
        graph_points = {(p.node_offset,p.port_index):[p.point.x,p.point.y] for p in net.ports}
        if graph_points[source_key] != self.point or graph_points[target_key] != target_point:
            return None
        for offset,index in keys - {source_key,target_key}:
            terminals = [n for n in self.nodes if n.get('source_offset') == offset]
            if len(terminals) != 1 or index != 0:
                return None
            terminal = terminals[0]
            ports = terminal.get('ports', [])
            if terminal['template'] != 'output' or len(ports) != 1 or ports[0].get('negated'):
                return None
            if [terminal['x']+ports[0]['x'],terminal['y']+ports[0]['y']] != graph_points[offset,index]:
                return None
            # Direct device terminals report data_type=0; use their original
            # native LD/OUT operands rather than inventing a BOOL type field.
            writes = [r for r in self.scoped if r.get('attribute') == 2 and r.get('address') and _scalar_terminal(r)
                and r.get('name') == terminal['symbol'] and _bbox(r) == _node_bbox(terminal)
                and r.get('task') == self.producer.get('task') and r.get('instance') == self.producer.get('instance')]
            if len(writes) != 1:
                return None
            native_writes = [r for r in self.records if r.get('op') == 'OUT' and len(r.get('args', [])) == 1
                and _device(r['args'][0]) == _device(writes[0]['address'])]
            if not any((previous := _direct_read(self.records, r)) is not None and previous['args'] == self.read['args']
                       for r in native_writes):
                return None
        if any(sum(w.get('source_offset') == offset for w in self.wires) != 1
                for offset in net.wire_offsets):
            return None
        if self.point != target_point and not net.wire_offsets:
            return None
        return {'kind':'port_overlap' if self.point == target_point else 'wire_network',
                'net_index':net.index,'block_index':self.network-1,
                'output_terminal_count':len(keys)-1,'wire_offsets':list(net.wire_offsets),
                'wire_scope':'all conductors in the current Core net, not a unique path'}


def correlate_fbd_instance_output(query, diagnostic, records, references, context, contexts, *, instance, connectivity):
    """Observed FXU/FX2C and FX3G direct BOOL-input/device-output project FBD FB.

    Native lexical reads and instance intervals are bound by the caller. Keep
    the callee terminal and its ordinary Program's call node as separate sites.
    Other CPUs, expressions, fanout and nested callers remain unresolved.
    """

    def bounds(row):
        return [row.get(key) for key in ('left', 'top', 'right', 'bottom')]
    def node_bounds(node):
        return [node['x'], node['y'], node['x'] + node['width'], node['y'] + node['height']]

    model, location = context.object_model(), query.get('location') or {}
    pou, resource, step = location.get('pou'), query.get('resource'), query.get('code_step')
    interval = instance.get('compiled_interval') or {}
    if (model.get('cpu') not in ('FXU/FX2C', 'FX3G') or model.get('schema_version') != 2
            or model.get('unknown_record_count') != 0 or len(context.program.blocks) != 1
            or context.sources.declarations[context.sources.local_table].owner_pou_type != 0x01000002
            or query.get('hresult') != 0 or query.get('code') != 0
            or location.get('program_kind') != 208 or location.get('library') != ''
            or not pou or location.get('network') != 1
            or instance.get('status') != 'instance-resolved'
            or not isinstance(instance.get('instance'), str) or not instance.get('task')
            or diagnostic.get('kind') != 2 or diagnostic.get('code') != 0x050c9300
            or diagnostic.get('name', {}).get('text') != resource or diagnostic.get('step') != step
            or type(interval.get('step_start')) is not int or type(interval.get('step_count')) is not int
            or type(step) is not int or not interval['step_start'] <= step < interval['step_start'] + interval['step_count']):
        return _reject('outside observed FXU/FX2C or FX3G direct FBD instance/output scope')
    arguments = diagnostic.get('arguments', [])
    writes = [r for r in records if r.get('native_step') == step]
    if (len(arguments) != 1 or not arguments[0].get('text') or len(writes) != 1
            or writes[0].get('op') != 'OUT' or len(writes[0].get('args', [])) != 1
            or canonical_operand(writes[0]['args'][0]) != canonical_operand(arguments[0]['text'])):
        return _reject('diagnostic does not select one original native OUT operand')
    write = writes[0]
    prior = [r for r in records if type(r.get('record_index')) is int
             and type(write.get('record_index')) is int and r['record_index'] == write['record_index'] - 1]
    if (len(prior) != 1 or prior[0].get('op') != 'LD' or len(prior[0].get('args', [])) != 1
            or prior[0].get('source_end') != write.get('source_offset')
            or type(prior[0].get('native_step')) is not int or prior[0]['native_step'] + 1 != step):
        return _reject('not the observed adjacent native LD/OUT pair')
    read = prior[0]
    scoped = [r for r in references if r.get('source') == pou and r.get('resource') == resource
              and r.get('library') == '' and r.get('program_kind') == 208 and r.get('network') == 1
              and (r.get('task'), r.get('instance')) == (instance['task'], instance['instance'])]
    inputs = [r for r in scoped if r.get('attribute') == 1 and r.get('class_code') == 3
              and r.get('data_type') == 1 and r.get('array_data_type') == 0 and r.get('address') == read['args'][0]]
    outputs = [r for r in scoped if r.get('attribute') == 2 and r.get('class_code') == r.get('data_type') == 0
               and r.get('array_data_type') == 0 and r.get('address')
               and canonical_operand(r['address']) == canonical_operand(write['args'][0])]
    if len(inputs) != 1 or len(outputs) != 1:
        return _reject('original scalar input or output reference is missing or ambiguous')
    nodes = _native_network_members(model['nodes'], 1, 1)
    if nodes is None:
        return _reject('current callee block membership is unavailable')
    selected = []
    for template, reference in (('input', inputs[0]), ('output', outputs[0])):
        matches = [n for n in nodes if n.get('template') == template and n.get('symbol') == reference.get('name')
                   and node_bounds(n) == bounds(reference) and len(n.get('ports', [])) == 1
                   and not n['ports'][0].get('negated')]
        if len(matches) != 1:
            return _reject('current callee terminal is missing or ambiguous')
        selected.append(matches[0])
    source, terminal = selected
    keys = {(n['source_offset'], 0) for n in selected}
    if connectivity is None or connectivity.logical_name != model['program']:
        return _reject('current callee connectivity is unavailable')
    nets = [n for n in connectivity.nets if n.block_index == 0
            and {(p.node_offset, p.port_index) for p in n.ports} == keys and len(n.ports) == 2]
    if len(nets) != 1:
        return _reject('current callee has no unique two-terminal connection')
    points = {(p.node_offset, p.port_index): [p.point.x, p.point.y] for p in nets[0].ports}
    if any(points[n['source_offset'], 0] != [n['x'] + n['ports'][0]['x'], n['y'] + n['ports'][0]['y']]
           for n in selected):
        return _reject('current callee port coordinates differ from connectivity')
    calls = [r for r in references if r.get('program_kind') == 208 and r.get('class_code') == 1
             and r.get('data_type') == 15 and r.get('type') == pou and r.get('attribute') == 1
             and r.get('library') == '' and r.get('resource') == resource and r.get('task') == instance['task']
             and r.get('instance') and r.get('name') and r.get('source') == r.get('instance')
             and r['instance'] + '.' + r['name'] == instance['instance']]
    if len(calls) != 1 or calls[0]['source'] not in contexts:
        return _reject('original ordinary FBD caller is missing or ambiguous')
    call, caller = calls[0], contexts[calls[0]['source']]
    caller_model = caller.object_model()
    if (caller.sources.cpu != model['cpu'] or caller_model.get('unknown_record_count') != 0
            or caller.sources.declarations[caller.sources.local_table].owner_pou_type != 0x01000000):
        return _reject('current ordinary caller source is unavailable')
    caller_nodes = _native_network_members(caller_model['nodes'], call.get('network'), len(caller.program.blocks))
    objects = [n for n in caller_nodes or [] if n.get('template') == 'function_block:' + pou
               and n.get('symbol') == call['name'] and node_bounds(n) == bounds(call)]
    if len(objects) != 1:
        return _reject('current caller object is missing or ambiguous')
    return {'status': 'uniquely_correlated', 'public_check_promoted': False,
        'resource': resource, 'diagnostic_step': step, 'instance': instance['instance'], 'task': instance['task'],
        'compiled_interval': interval,
        'source': {'pou': pou, 'program_kind': 208, 'network': 1, 'object_id': terminal['id'],
                   'symbol': terminal['symbol'], 'source_offset': terminal['source_offset'], 'bbox': node_bounds(terminal)},
        'caller': {'pou': call['source'], 'program_kind': 208, 'network': call['network'],
                   'symbol': call['name'], 'object_id': objects[0]['id'], 'source_offset': objects[0]['source_offset'], 'bbox': bounds(call)},
        'native_input_reference': inputs[0], 'native_output_reference': outputs[0], 'native_caller_reference': call,
        'connection': {'net_index': nets[0].index, 'block_index': 0, 'wire_offsets': list(nets[0].wire_offsets)},
        'limits': ['observed direct scalar BOOL input/device output in a project FBD FB',
                   'public diagnostic conversion and overall check remain separate']}


def correlate_fbd_bool_output(query, diagnostic, records, references, model, *, selected_pou, connectivity=None, instance=None):
    """Correlate observed FX3U/FX3UC and Q03UDV scalar FB output diagnostics.

    records must already agree with native lexical reads and native prefix
    GetStepSize. The caller binds all inputs to one current owned compilation.
    Optional connectivity is the existing Core graph read from that same POU.
    Only the observed scalar duplicate-output form is projected. Q03UDV native
    network ordinals select source block membership, including empty blocks;
    FX3U/FX3UC retains the observed single-network scope.
    """
    location = query.get('location') or {}
    resource, step = query.get('resource'), query.get('code_step')
    blocks = model.get('blocks', [{}])
    if (model.get('schema_version') != 2 or model.get('cpu') not in ('FX3U/FX3UC','Q03UDV')
            or model.get('unknown_record_count') != 0
            or not isinstance(blocks, list) or not blocks
            or any(not isinstance(block, dict) for block in blocks)
            or (model.get('cpu') == 'FX3U/FX3UC' and len(blocks) != 1)):
        return _reject('outside observed FX3U/FX3UC or Q03UDV source/graph scope')
    if (query.get('hresult') != 0 or query.get('code') != 0
            or location.get('program_kind') != 208 or location.get('library') != ''
            or not selected_pou or location.get('pou') != selected_pou):
        return _reject('no selected native FBD source location')
    network = location.get('network')
    nodes = _native_network_members(model.get('nodes', []), network, len(blocks))
    wires = _native_network_members(model.get('wires', []), network, len(blocks))
    if nodes is None or wires is None:
        return _reject('native FBD network or current block membership is unavailable')
    arguments = diagnostic.get('arguments', [])
    if (diagnostic.get('kind') != 2 or diagnostic.get('code') != 0x050c9300
            or diagnostic.get('name', {}).get('text') != resource
            or diagnostic.get('step') != step or len(arguments) != 1):
        return _reject('outside observed duplicate-output diagnostic form')
    at = [r for r in records if r.get('native_step') == step]
    if len(at) != 1 or at[0].get('op') != 'OUT' or len(at[0].get('args', [])) != 1:
        return _reject('diagnostic does not select one native OUT')
    output = at[0]
    if not arguments[0].get('text') or _device(output['args'][0]) != _device(arguments[0]['text']):
        return _reject('native diagnostic operand differs from OUT')
    chain=_output_chain(records, output)
    if chain is None:
        return _reject('not a contiguous native LD/OUT output chain')
    read=chain[0]
    scoped = [r for r in references if r.get('resource') == resource and r.get('source') == selected_pou
        and r.get('program_kind') == 208 and r.get('library') == ''
        and r.get('network') == location.get('network')
        and (instance is None or (r.get('task'), r.get('instance')) == instance)]
    producers = [r for r in scoped if r.get('class_code') == 4 and r.get('data_type') == 1
        and type(r.get('array_data_type')) is int and r['array_data_type'] == 0
        and isinstance(r.get('name'), str) and r['name']
        and r.get('attribute') == 1 and r.get('address') == read['args'][0]]
    if len(producers) != 1:
        return _reject('FB BOOL output address is missing or ambiguous')
    producer = producers[0]
    sources = []
    for node in nodes:
        if not node['template'].startswith('function_block:') or _node_bbox(node) != _bbox(producer):
            continue
        for port in node.get('ports', []):
            if (port.get('side') == 'out' and port.get('class_code') == 4 and port.get('data_type') == 'BOOL'
                    and not port.get('negated')
                    and producer['name'] == node['symbol'] + '.' + port.get('formal_name', port['name'])):
                sources.append((node,port))
    if len(sources) != 1:
        return _reject('current graph FB output interface is missing or ambiguous')
    source, port = sources[0]
    calls = [r for r in scoped if r.get('name') == source['symbol'] and r.get('class_code') == 1
        and r.get('type') == source['template'].split(':',1)[1] and _bbox(r) == _node_bbox(source)
        and r.get('task') == producer.get('task') and r.get('instance') == producer.get('instance')]
    if len(calls) != 1:
        return _reject('current native FB instance is missing or ambiguous')
    point = [source['x']+port['x'],source['y']+port['y']]
    connections = _OutputConnections(
        point=point,
        connectivity=connectivity,
        model=model,
        source=source,
        port=port,
        network=network,
        nodes=nodes,
        scoped=scoped,
        records=records,
        read=read,
        producer=producer,
        wires=wires,
    )
    destinations = []
    for reference in scoped:
        if (reference.get('attribute') != 2 or not reference.get('address') or not _scalar_terminal(reference)
                or _device(reference['address']) != _device(output['args'][0])
                or reference.get('task') != producer.get('task')
                or reference.get('instance') != producer.get('instance')):
            continue
        for node in nodes:
            if (node['template'] != 'output' or node['symbol'] != reference['name']
                    or _node_bbox(node) != _bbox(reference)):
                continue
            ports = node.get('ports', [])
            if len(ports) == 1 and not ports[0].get('negated'):
                link = connections.connection(node, ports[0])
                if link is not None:
                    destinations.append((node,reference,link))
    if len(destinations) != 1:
        result = _reject('directly connected current output object is missing or ambiguous')
        result.update(original_kind=diagnostic['kind'],original_code=diagnostic['code'],resource=resource,
            source_pou=selected_pou,diagnostic_step=step,source_status='uniquely_correlated',
            network=network,
            source={'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
                'port_name':port['name'],'side':'out','point':point,'block_index':network-1},
            output_status='ambiguous' if destinations else 'missing',
            output_candidates=sorted({node['id'] for node,_,_ in destinations}))
        if len(chain)>2:
            result['native_output_chain']=chain
        return result
    destination, reference, link = destinations[0]
    result = {'status':'uniquely_correlated','public_check_promoted':False,
        'original_kind':diagnostic['kind'],'original_code':diagnostic['code'],
        'resource':resource,'source_pou':selected_pou,'diagnostic_step':step,'network':network,
        'native_pair':{'read':read,'write':output},'native_member':producer,
        'source':{'object_id':source['id'],'instance':source['symbol'],'formal':port['formal_name'],
            'port_name':port['name'],'side':'out','point':point,'block_index':network-1},
        'output':{'object_id':destination['id'],'source_offset':destination['source_offset'],
            'symbol':destination['symbol'],'bbox':_node_bbox(destination),'native_reference':reference,
            'block_index':network-1},
        'connection':link,
        'limits':['contiguous native LD/OUT chain and direct scalar FB BOOL/output connection only',
            'one selected current native FBD network; arrays and unknown records unresolved',
            'current native facts and source context required; no execution claim']}
    if len(chain)>2:
        result['native_output_chain']=chain
    return result
