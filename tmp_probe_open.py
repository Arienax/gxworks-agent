import json
from knowledge.retriever import retrieve_fact_aware_knowledge
from knowledge.structured_facts import retrieval_coverage, structured_fact_targets
from knowledge.retriever import format_knowledge_reference

cases = ['FX3U X0 鏄粈涔堣蒋鍏冧欢', 'PLC 閿欒鐏棯鐑?鍘熷洜', '杩愯涓獊鐒跺仠姝?鎶ヨ']
for q in cases:
    rows = retrieve_fact_aware_knowledge(q, plc_model='FX3U', top_k=5)
    cov = retrieval_coverage(q, rows, plc_model='FX3U')
    print('==', q)
    print('   targets', structured_fact_targets(q, plc_model='FX3U')['devices'],
          [(t['kind'], t['target'], t['status'], t.get('reason')) for t in cov['targets']])
    print('   rows', len(rows), [(r.get('fact_kind'), r.get('fact_target')) for r in rows])

rows = retrieve_fact_aware_knowledge('FX3U 閿欒浠ｇ爜 46', plc_model='FX3U', top_k=3)
for row in rows[:1]:
    debug = format_knowledge_reference(row, debug=True)
    text = json.dumps(debug, ensure_ascii=False)
    print('debug bytes', len(text))
    sources = debug.get('source') if isinstance(debug, dict) else None
    print('source entries', len(sources) if isinstance(sources, list) else sources)
