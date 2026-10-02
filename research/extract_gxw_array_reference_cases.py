"""Extract sparse native cache graphs, without project/source redistribution.

Only the records required by previously native-validated references are kept.
Original table-relative identities, record bytes and table extents are retained.
This fixture is a graph projection, not a complete reconstructable CGTable.
No native reruns or hashes are performed.
"""
from pathlib import Path
import base64,json,re,sys

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'
sys.path[:0]=[str(ROOT/'src'),str(P),str(ROOT/'research')]
from probe_minimal_conditional_port_writes import streams
from gxw.compiler_tables import parse_compiler_tables
from gxw.compiler_symbols import compiler_array_reference

def sparse_case(tables, case_path, provenance, requested, *, evidence):
    selected={i:set() for i in (1,2,3,13)};references=[]
    for row in requested:
        indices=tuple(row['indices']);member_indices=tuple(row.get('member_indices',()))
        ref=compiler_array_reference(tables,row['component_offset'],indices,
            member_offset=row.get('member_offset'),member_indices=member_indices)
        assert ref.operand==row['expected_operand']
        selected[2].add(ref.component.record.table_offset)
        if ref.component.global_offset is not None:selected[3].add(ref.component.global_offset)
        selected[13].add(ref.descriptor.record.table_offset)
        if ref.type_pou_offset is not None:
            selected[1].add(ref.type_pou_offset);pou=tables.pou_at(ref.type_pou_offset)
            selected[2].update(m.record.table_offset for m in tables.components(pou))
            if pou.component_end<len(tables.tables[2].raw)-4:selected[2].add(pou.component_end)
        if ref.member_descriptor is not None:selected[13].add(ref.member_descriptor.record.table_offset)
        references.append(dict(row, member_offset=row.get('member_offset'),member_indices=list(member_indices),
            expected_iec_address=ref.iec_address,expected_type_pou_offset=ref.type_pou_offset,
            expected_primitive_type=ref.primitive_type,expected_primitive_width=ref.primitive_width,
            expected_family_stride=ref.family_stride))
    graphs=[]
    for table in tables.tables:
        keep=selected.get(table.index,set())
        records=[dict(table_offset=r.table_offset,absolute_offset=r.offset,raw_base64=base64.b64encode(r.raw).decode())
                 for r in table.records if r.table_offset in keep]
        graphs.append(dict(index=table.index,absolute_offset=table.offset,
                           payload_size=len(table.raw)-4,records=records))
    return dict(case=case_path,provenance=provenance,tables=graphs,references=references,evidence=evidence)


def string_cases():
    """Use native PCode operands, without calculating expected addresses here.

    These are retrospective checks of the recovered native allocation rule.
    The literal writes are unique in the nested case. In the RIGHT case the
    source writes the array before the scalar with the same literal; the
    first native literal write and final array read supply explicit operands.
    """
    specs=[('nested-st-string-holdouts-20260930','outer12-inner5-conditional'),
           ('st-string-intrinsic-index-controls-20260930','right-dynamic-source-static-target')]
    for group,case in specs:
        comparison=next(r for r in json.loads((P/group/'comparison.json').read_text()) if r['case']==case)
        assert comparison['check']['rejected'] is False
        assert comparison['reopen']['check']['rejected'] is False and comparison['reopen']['identical'] is True
        native=P/group/case/'native-1';saved=native/'native-saved.gxw'
        raw=streams(saved.read_bytes())['CGTable.dat'];tables=parse_compiler_tables(raw)
        assert tables.reconstruct()==raw
        listing=next(r for r in json.loads((P/group/'native-listings.json').read_text()) if r['case']==case)
        components=[tables.component_at(r.table_offset) for r in tables.tables[2].records]
        requested=[]
        if group.startswith('nested-'):
            assignments=[('probeArray',i,text) for i,text in enumerate(('AAA','BBB','CCC'))]
            assignments += [('localArray',i,text) for i,text in enumerate(('X','Y','Z'))]
            for name,index,literal in assignments:
                matches=[(i,r) for i,r in enumerate(listing['records'])
                         if r.get('op')=='$MOV' and r['args'][0]=='"'+literal+'"']
                assert len(matches)==1
                record_index,record=matches[0]
                candidates=[c for c in components if c.name_bytes==name.encode()];assert len(candidates)==1
                requested.append(dict(text=f'{name}[{index}]',component_offset=candidates[0].record.table_offset,
                    indices=[index],expected_operand=record['args'][1],
                    native_instruction=dict(record_index=record_index,**record)))
        else:
            component=next(c for c in components if c.name_bytes==b'probeArray')
            writes=[(i,r) for i,r in enumerate(listing['records'])
                    if r.get('op')=='$MOV' and r['args'][0]=='"ABCDEF"']
            assert len(writes)==2
            reads=[(i,r) for i,r in enumerate(listing['records'])
                   if r.get('op')=='$MOV' and r['args'][0].startswith('D')]
            assert len(reads)==1
            for index,(record_index,record),operand_index in ((1,writes[0],1),(2,reads[0],0)):
                requested.append(dict(text=f'probeArray[{index}]',component_offset=component.record.table_offset,
                    indices=[index],expected_operand=record['args'][operand_index],
                    native_instruction=dict(record_index=record_index,**record)))
        yield sparse_case(tables,f'{group}/{case}',saved.relative_to(ROOT).as_posix(),requested,
            evidence=dict(kind='retrospective-native-pcode',check=comparison['check'],
                reopen_check=comparison['reopen']['check'],reopen_pcode_identical=True,
                native_listing=(P/group/'native-listings.json').relative_to(ROOT).as_posix(),
                source_plan=(P/(group.removesuffix('-20260930')+'-plan.json')).relative_to(ROOT).as_posix()))


def primitive_cases():
    """Keep unique literal writes from the prospective extent/offset controls.

    The source-first predictions cover relative offsets and reservations, not
    the complete compiler output. Absolute bases are read after native use.
    BOOL presence-only checks stay in the experiment, not this address fixture.
    """
    for group in ('primitive-array-geometry-types-20261001',
                  'primitive-array-geometry-strings-time-20261001',
                  'primitive-array-geometry-multirank-20261001',
                  'primitive-array-geometry-axes-20261001'):
        comparison=json.loads((P/group/'comparison.json').read_text(encoding='utf-8'))
        assert comparison['check']['rejected'] is False
        assert comparison['reopen']['check']['rejected'] is False and comparison['reopen']['pcode_identical'] is True
        native=P/group/'native-1';saved=native/'native-saved.gxw'
        raw=streams(saved.read_bytes())['CGTable.dat'];tables=parse_compiler_tables(raw)
        assert tables.reconstruct()==raw
        requested=[]
        for array in comparison['arrays']:
            assert array['extent_equal']
            for point in array['points']:
                assert point['address_present']
                if point['handling']!='unique-native-literal-write':continue
                assert len(point['native_records'])==1
                requested.append(dict(text=array['name']+'['+','.join(map(str,point['indices']))+']',
                    component_offset=array['component_offset'],indices=point['indices'],
                    expected_operand=point['actual_operand'],native_instruction=point['native_records'][0],
                    before_native_relative_offset=point['expected_relative']))
        yield sparse_case(tables,group,saved.relative_to(ROOT).as_posix(),requested,
            evidence=dict(kind='source-first-relative-geometry/native-pcode',check=comparison['check'],
                reopen_check=comparison['reopen']['check'],reopen_pcode_identical=True,
                source_prediction=(P/group/'prediction-before-native.json').relative_to(ROOT).as_posix(),
                comparison=(P/group/'comparison.json').relative_to(ROOT).as_posix(),
                boundary='Relative offsets/reservations were prospective. Absolute bases and final cache references are retrospective.'))


def main():
    cases=[]
    for group in sorted(P.glob('fx3g-*-20260930')):
        for source in sorted(group.glob('*/independent-cache-member-references.json')):
            observed=json.loads(source.read_text(encoding='utf-8'))
            saved=list(source.parent.glob('native-*/native-saved.gxw'));assert len(saved)==1
            raw=streams(saved[0].read_bytes())['CGTable.dat'];tables=parse_compiler_tables(raw)
            assert tables.reconstruct()==raw
            requested=[]
            for kind in ('references','primitive_array_references'):
                for row in observed.get(kind,[]):
                    groups=re.findall(r'\[([^]]+)\]',row['text'])
                    indices=tuple(map(int,groups[0].split(',')))
                    member_indices=tuple(map(int,groups[1].split(','))) if len(groups)>1 else ()
                    requested.append(dict(text=row['text'],component_offset=row['variable_component_offset'],
                        indices=list(indices),member_offset=row.get('member_component_offset'),member_indices=list(member_indices),
                        expected_operand=f"{row['family']}{row['number']}",
                        prior_native_address=dict(family=row['family'],number=row['number'])))
            if not requested:continue
            cases.append(sparse_case(tables,source.parent.relative_to(P).as_posix(),
                saved[0].relative_to(ROOT).as_posix(),requested,evidence=dict(kind='saved-cache-crosscheck',
                    source=source.relative_to(ROOT).as_posix())))
    cases.extend(string_cases())
    cases.extend(primitive_cases())
    output=ROOT/'tests/fixtures/gxw_array_references_native.json'
    output.write_text(json.dumps(dict(scope='Sparse native compiler cache graph projection. This is not a complete CGTable or original source/project file.',
        validation='Addresses agree with the saved independent cache cross-checks from original native compile/check/save/reopen experiments. Those cross-checks and this reader share cache-layout assumptions; native generated PCode and the source-only predictor supplied the separate evidence path.',
        boundaries='One FX3G lineage for flat BOOL/INT/WORD/DWORD members; one Q03UDV lineage for primitive DINT/REAL/TIME and STRING arrays with parameters 1, 4, 5, 6, 12. Root rank <=3, member rank <=2. Original unknown bytes within included records survive. Omitted records are not claimed preserved.',
        cases=cases),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(cases=len(cases),references=sum(len(c['references']) for c in cases),bytes=output.stat().st_size)))

if __name__=='__main__':main()
