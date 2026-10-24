import json, sqlite3
from knowledge.retriever import retrieve_knowledge, retrieve_fact_aware_knowledge, format_knowledge_reference
from knowledge import index_access

for q in ['PLC 閿欒鐏棯鐑?鍘熷洜', '杩愯涓獊鐒跺仠姝?鎶ヨ', '鎶ヨ 鍘熷洜 澶勭悊']:
    rows = retrieve_knowledge(q, top_k=5)
    print('broad', q, len(rows), [(r.get('chunk_type') or r.get('kind'), (r.get('text') or '')[:30]) for r in rows[:3]])

path = index_access.index_path()
with sqlite3.connect(path) as db:
    for name in ['ERR', '鎶ヨ', 'error']:
        n = db.execute("SELECT count(*) FROM chunks WHERE text LIKE ?", (f'%{name}%',)).fetchone()[0]
        print('chunks containing', name, n)
    row = db.execute("SELECT id, chunk_type, substr(text,1,120) FROM chunks WHERE chunk_type='error' LIMIT 3").fetchall()
    for item in row:
        print('err row', item)

rows = retrieve_fact_aware_knowledge('FX3U 閿欒浠ｇ爜 46', plc_model='FX3U', top_k=2)
print('rows', len(rows))
if rows:
    debug = format_knowledge_reference(rows[0], debug=True)
    text = json.dumps(debug, ensure_ascii=False)
    print('debug bytes', len(text))
    src = debug.get('source') if isinstance(debug, dict) else None
    print('source type', type(src).__name__, 'len', len(src) if hasattr(src, '__len__') else '')
    if isinstance(src, list):
        identity = [(s.get('manual_id'), s.get('revision'), s.get('pdf_page') or s.get('page')) for s in src]
        print('distinct', len(set(identity)), 'total', len(identity), 'first', identity[:3])
