"""Extract saved ST start coordinates checked against live native DBG_INFO.

The fixture contains numeric debug metadata and expansion names only. Original
GXW files, normalized FB source, generated program bytes and vendor DLLs stay
in the local experiment evidence. No hashes are generated or used as oracles.
"""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'research/experiments/sfc-graph-20260926/public-corpus-discovery'


def main():
    source=P/'st-reference-contexts-20261001/two-instances/source-step-correspondence.json'
    actual=json.loads(source.read_text(encoding='utf-8'))
    expected={
        'prefix_step_queries':94,'prefix_step_matches':94,'whole_code_native_listing_exact':True,
        'whole_code_instructions':94,'post_compile_source_points':45,'saved_source_point_matches':45,
        'debug_reconstruction_exact':True,
    }
    if actual['summary']!=expected:raise ValueError('native observations differ from this fixture profile')
    rows=[]
    for row,location in zip(actual['saved_rows'],actual['locations'],strict=True):
        if row['source_line']!=location['source']['line'] or row['compiled_step_start']!=location['native_prefix_steps']:
            raise ValueError('fixture start coordinate not independently observed')
        rows.append({k:row[k] for k in ('element_index','row_index','source_line','compiled_step_start','context_names','resource')})
    output=ROOT/'tests/fixtures/gxw_st_source_points_native.json'
    if output.exists():raise FileExistsError(output)
    result=dict(scope='stored source coordinates; no source freshness or statement ownership claim',
        environment=dict(cpu='Q03UDV',compiler_iec='15.50',q_code_generator='15.41',native_cpu=209,native_versions=[25]),
        provenance=dict(original_project='research/experiments/sfc-graph-20260926/public-corpus-discovery/native-allocation-inventory/case-00/input.gxw',
            controlled_project='st-reference-contexts-20261001/two-instances',
            changed_sources=['POU_01.Labels.lh','POU_01.Program.pou'],original_keypad_body_unchanged=True,
            native_check_accepted=True,native_saved_reopen_check_accepted=True,three_reopened_channel_buffers_byte_exact=True),
        validation=actual['summary'],debug_base64=actual['debug_base64'],expected=rows,
        limitations=['Compiler offsets and instance identities are preserved; same-name FB types are not merged.',
            'The native first-generation source position table changes offsets during compilation.',
            'Stored endpoints can reach another expansion or FEND and are not asserted as statement-owned ranges.'])
    output.write_text(json.dumps(result,indent=2,ensure_ascii=True)+'\n',encoding='utf-8')
    print(json.dumps(dict(fixture=str(output),points=len(rows),bytes=output.stat().st_size)))


if __name__=='__main__':main()
