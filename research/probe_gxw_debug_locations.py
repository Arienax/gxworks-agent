"""Compare stored simple-ladder debug locations with the independent native API.

The native lookup operates on a sparse table expanded into source-step slots.
Its binary search can reject points inside a stored interval. Keep interval
observations separate from that native-compatible lookup; neither establishes
cache freshness, execution reachability, or general ST/SFC coordinates.
"""
from __future__ import annotations

import sys
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gxw.compiler_debug import CompilerDebug, CompilerDebugOffsets


def search_maps(debug: CompilerDebug, *, encoding: str) -> dict:
    """Frame five search maps from native LocalSaveSearchMaps 0x639bb.

    Values are indexes into the preceding element list, grouped exactly as
    stored. Extra numeric fields stay raw; resource map fields 1 and 3 are
    rejected positions in native GetPOULocation (0x665b6).
    """
    raw, cursor = debug.raw, debug.tail_offset
    def integer():
        nonlocal cursor
        if cursor+4>len(raw):raise ValueError('truncated debug search map')
        value=struct.unpack_from('<i',raw,cursor)[0];cursor+=4
        return value
    def count():
        value=integer()
        if value<0 or value>(len(raw)-cursor)//4:raise ValueError('unbounded debug search-map count')
        return value
    def name():
        nonlocal cursor
        size=integer()
        if size<1 or cursor+size>len(raw):raise ValueError('unbounded debug search-map string')
        value=raw[cursor:cursor+size];cursor+=size
        if value[-1:]!=b'\0' or b'\0' in value[:-1]:raise ValueError('unsupported debug search-map string')
        return dict(text=value[:-1].decode(encoding),raw_hex=value.hex())
    maps=[]
    for index in range(5):
        entries=[]
        for _ in range(count()):
            start=cursor;key=name();groups=[]
            for _ in range(count()):
                group=[integer() for _ in range(count())]
                if any(i<0 or i>=len(debug.elements) for i in group):raise ValueError('search-map element reference outside list')
                groups.append(group)
            entry=dict(offset=start,key=key,groups=groups,fields=[])
            if index==1:entry['fields']=[integer() for _ in range(5)]
            if index==2:
                entry['fields']=[integer() for _ in range(2)]
                entry['resource']=name()
            entry.update(length=cursor-start,raw_hex=raw[start:cursor].hex())
            entries.append(entry)
        maps.append(entries)
    return dict(offset=debug.tail_offset,maps=maps,opaque_tail_offset=cursor,opaque_tail_hex=raw[cursor:].hex())


def expanded_simple_rows(table: CompilerDebugOffsets) -> tuple[tuple[int, ...], ...]:
    """DataManager_IEC 0x6ac94, version 3 kind 1; untouched bytes stay on table."""
    if table.kind_code != 1 or not table.rows:
        raise ValueError('only nonempty simple-ladder offset tables are observed')
    result, previous_source, previous_start = [], 0, -1
    for row in table.rows:
        source = row[0]
        if source < previous_source or source - previous_source > 1024:
            raise ValueError('source-step order/gap outside native load bound')
        if source > 1000000:
            raise ValueError('expanded source table exceeds probe bound')
        result.extend([(-1, previous_start, -1, -1, -1, -1)] * max(0, source-previous_source-1))
        result.append(row)
        previous_source, previous_start = source, row[1]
    return tuple(result)


def native_simple_row(rows: tuple[tuple[int, ...], ...], step: int) -> int | None:
    """Observed 0x6370d search, including failed lookups in sparse intervals."""
    if not rows or step < rows[0][1] or step > rows[-1][2]:
        return None
    low, high = 0, len(rows)-1
    found = None
    while low <= high:
        mid = (low+high)//2
        row = rows[mid]
        if high-low == 1:
            if rows[low][1] <= step <= rows[low][2]:found = low
            elif rows[high][1] <= step <= rows[high][2]:found = high
            break
        if step == row[1] or row[1] <= step <= row[2]:
            found = mid
            break
        if low == high:break
        if step < row[1]:high = mid-1
        else:low = mid+1
    if found is None:return None
    while found+1 < len(rows) and rows[found+1][1] == rows[found][1]:found += 1
    while found >= 0 and rows[found][0] == -1:found -= 1
    if found < 0:return None
    result = found
    while found+1 < len(rows) and rows[found+1][1] == rows[found][1]:
        found += 1
        if rows[found][0] != -1:result = found
    return result


def lookup(debug: CompilerDebug, resource: str, step: int, *, encoding: str) -> dict:
    if not debug.elements:
        # Native unlabeled FX1S uses a different location backend and can return
        # passthrough coordinates without any DebugInformation2 element.
        return dict(handling='no-stored-debug-data',location=None,interval_candidates=[])
    indexes=search_maps(debug,encoding=encoding)
    if indexes['opaque_tail_hex']:
        return dict(handling='unsupported-search-map-tail',location=None)
    resources=[r for r in indexes['maps'][1] if r['key']['text']==resource]
    if len(resources)!=1:return dict(handling='unresolved-resource-index',location=None)
    if step in (resources[0]['fields'][1],resources[0]['fields'][3]):
        return dict(handling='native-terminal-step',location=None,expected_native_code=0x50030017)
    selected = [(i,e) for i,e in enumerate(debug.elements)
                if e.resource_bytes.decode(encoding) == resource and e.linked_step_start <= step <= e.linked_step_end]
    if not selected:return dict(handling='no-stored-element',location=None,interval_candidates=[])
    overlap=None
    if len(selected) != 1:
        # Native 0x62f90 searches the resource index in stored order. Real ST
        # controls cover a parent plus one FB, repeated use of the same FB, and
        # two separate FB instances and a nested FB: the last containing element wins. The
        # first repeated instance can retain an end spanning the second call.
        # Require one ordered resource group and the observed two-level instance
        # paths. Q controls also append disjoint @IEC timer bodies to that same
        # group; they must not disable resolution of the overlapping ST prefix.
        # Further nesting and other scope syntax remain undecoded.
        group=resources[0]['groups']
        ordered=[(i,debug.elements[i]) for i in group[0]] if len(group)==1 else []
        st_ordered=[(i,e) for i,e in ordered if e.kind_code==193]
        if (st_ordered and all(e.kind_code==193 for _,e in selected)
                and all(e.names[1]==st_ordered[0][1].names[1]
                            and e.names[3]==e.names[1]
                            and not any(c in e.names[4] for c in (b':',b'\\'))
                            and len(e.names[4].split(b'.'))<=2 for _,e in st_ordered)
                and all(e.kind_code==193 or (e.kind_code==192 and e.names[0]==b'@IEC')
                        for _,e in ordered)
                and [e.linked_step_start for _,e in ordered]==sorted(e.linked_step_start for _,e in ordered)
                and [i for i,_ in selected]==[i for i,e in ordered if e.linked_step_start<=step<=e.linked_step_end]):
            overlap=[i for i,_ in selected];selected=selected[-1:]
        else:return dict(handling='overlapping-elements-preserved',location=None,element_indexes=[i for i,_ in selected])
    index, element = selected[0]
    table = debug.offset_tables[element.offset_table_index]
    library_element=(element.kind_code==192 and element.names[0]==b'@IEC')
    library_il=(library_element and element.fields[0]>=0 and all(
        r[0]>=0 and r[3]>=1 and r[4]==8 and r[5]==0 for r in table.rows))
    library_region=(library_element and element.fields[0]==-1)
    if (element.kind_code not in (1,193,208) and not (library_region or library_il)) or table.kind_code != element.kind_code:
        return dict(handling='unsupported-program-kind',location=None,element_index=index)
    relative = step-element.linked_step_start
    candidates=[dict(source_step=r[0] if library_element else element.fields[0]+r[0],raw_row=list(r))
                for r in table.rows if r[1] <= relative <= r[2]]
    if table.kind_code==193 and any(r[0]<0 or r[3]!=-1 or r[4]&7!=7 for r in table.rows):
        return dict(handling='unsupported-ST-offset-row',location=None,element_index=index)
    if table.kind_code==208 and any(r[0]<0 or r[3]<1 or r[4]!=8 or r[5]!=0 for r in table.rows):
        return dict(handling='unsupported-graph-network-offset-row',location=None,element_index=index)
    rows=expanded_simple_rows(table) if table.kind_code==1 else table.rows
    matched=native_simple_row(rows,relative)
    result=dict(element_index=index,element_offset=element.offset,table_offset=table.offset,
                stored_names=[n.decode(encoding) for n in element.names],interval_candidates=candidates)
    if overlap is not None:result['ordered_ST_overlap']=overlap
    if matched is None:return dict(result,handling='native-compatible-search-gap',location=None)
    row=rows[matched]
    if row[4]&7 != 7 and element.kind_code!=208 and not library_il:
        return dict(result,handling='unsupported-row-kind',location=None,raw_row=list(row))
    point=row[0] if library_element else element.fields[0]+row[0]
    location=dict(library=element.names[0].decode(encoding),pou=element.names[2].decode(encoding),
        program_kind=element.kind_code,network=row[3],start_step=point,step_count=-1,
        element_id=0 if element.kind_code==208 or library_il else -1,action_transition_present=False)
    kind={1:'simple',193:'ST',208:'graph-network',192:'library-IL' if library_il else 'library-region'}[element.kind_code]
    return dict(result,handling='native-compatible-'+kind+'-location',location=location,raw_row=list(row))
