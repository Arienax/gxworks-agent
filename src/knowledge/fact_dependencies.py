"""Follow typed instruction-to-device references after scoped retrieval."""
from collections.abc import Mapping


def nodes(value):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def related_device_facts(records, *, plc_model, task_type='generate', timer_counter_families=('T', 'C')):
    from knowledge.structured_facts import authored_packet, resolve_device_family_records, resolve_device_records
    families, devices = set(), set()
    for record in records:
        packet = authored_packet(record)
        for item in nodes(packet.get('value', {})):
            if item.get('timer_counter_parameters') == 'separate_source_facts':
                families.update(set(item.get('device_families', ())).intersection(timer_counter_families))
            for key in ('timer_device_range', 'device_range'):
                region = item.get(key)
                if isinstance(region, Mapping) and region.get('prefix'):
                    families.add(region['prefix'])
            # These are explicit source relations, not addresses scraped from
            # incidental examples or allocated to the generated program.
            devices.update(str(value) for value in item.get('related_devices', ()) if isinstance(value, str))
    results = resolve_device_family_records(sorted(families), plc_model=plc_model, task_type=task_type) if families else []
    results += resolve_device_records(sorted(devices), plc_model=plc_model, task_type=task_type) if devices else []
    return [record for record in results if authored_packet(record).get('technical_status') != 'candidate_evidence']
