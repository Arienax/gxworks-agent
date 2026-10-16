"""Source-owned device chapters and CPU/table/footnote evidence units.

These are original-source references, not a second device validation registry.
An address occurring in a family chapter never acquires access permission or a
source-verified individual definition through this module.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict


FAMILY_ALIASES = {
    'X': ('input relay', 'input relays', 'input/output relay', 'input/output relays', 'i/o relay', 'i/o relays', '输入继电器'),
    'Y': ('output relay', 'output relays', 'input/output relay', 'input/output relays', 'i/o relay', 'i/o relays', '输出继电器'),
    'M': ('auxiliary relay', 'auxiliary relays', 'internal relay', '辅助继电器', '内部继电器'),
    'S': ('state relay', 'state relays', 'step relay', '状态继电器', '步进继电器'),
    'L': ('latch relay', '锁存继电器'), 'F': ('annunciator', '报警器软元件'),
    'V': ('edge relay', '边沿继电器'), 'B': ('link relay', '链接继电器'),
    'SB': ('link special relay', '链接特殊继电器'),
    'SM': ('special relay', '特殊继电器'),
    'D': ('data register', 'data registers', '数据寄存器'),
    'SD': ('special register', 'special data register', '特殊寄存器', '特殊数据寄存器'),
    'W': ('link register', '链接寄存器'), 'SW': ('link special register', '链接特殊寄存器'),
    'T': ('timer', 'timers', '定时器'), 'ST': ('retentive timer', '累计定时器', '保持定时器'),
    'C': ('counter', 'counters', '计数器'), 'R': ('file register', 'file registers', 'extension register', '文件寄存器'),
    'ZR': ('continuous file register', '连续文件寄存器'),
    'ER': ('extension file register', '扩展文件寄存器'),
    'Z': ('index register', 'index registers', '索引寄存器', '变址寄存器'),
    'P': ('branch pointer', 'pointer', 'pointers', '分支指针'),
    'I': ('interrupt pointer', 'interrupt pointers', '中断指针'),
    'DX': ('direct access input', 'direct input', '直接访问输入'),
    'DY': ('direct access output', 'direct output', '直接访问输出'),
    'TS': ('timer contact', '定时器触点'), 'TC': ('timer coil', '定时器线圈'),
    'CS': ('counter contact', '计数器触点'), 'CC': ('counter coil', '计数器线圈'),
}

_SECTION = re.compile(r'(?m)^\s*(\d+(?:\.\d+){1,3})\s*(?:\|\s*)?([A-Za-z][^\n]{1,140})')
_MODEL_HEADING = re.compile(r'(?m)(?:^|\n)\s*\d+[.)]\s*(?:In\s+)?((?:FX\s*[0-9A-Z]+(?:\s*/\s*FX\s*[0-9A-Z]+)*))\s+PLCs?\b', re.I)


def family_targets(query):
    """Recognize explicitly requested families, including authored name aliases."""
    from knowledge.analysis_router import _device_reference_text
    text = _device_reference_text(str(query or ''))
    found, spans = [], []
    aliases = [(alias, family) for family, values in FAMILY_ALIASES.items() for alias in values]
    for alias, family in sorted(aliases, key=lambda row: -len(row[0])):
        pattern = re.escape(alias)
        if alias.isascii(): pattern = r'(?<![A-Za-z0-9_])' + pattern + r'(?![A-Za-z0-9_])'
        for match in re.finditer(pattern, text, re.I):
            if any(a <= match.start() and match.end() <= b and (a,b)!=match.span() for a, b in spans): continue
            found.append(family); spans.append(match.span())
    for match in re.finditer(r'(?<![A-Za-z0-9_])(?:' + '|'.join(sorted(FAMILY_ALIASES,key=len,reverse=True)) + r')(?![A-Za-z0-9_])', text, re.I):
        nearby = text[max(0,match.start()-15):match.end()+24]
        if re.search(r'软元件|设备族|继电器|寄存器|定时器|计数器|\b(?:device|family|definition)\b',nearby,re.I):
            found.append(match[0].upper())
    return list(dict.fromkeys(found))


def heading_families(title):
    """Literal symbols in a source heading take precedence over name aliases."""
    explicit=[]
    symbols=re.sub(r'\b([A-Z]+)\s+and\s+([A-Z]+)\b',r'\1,\2',title)
    for label in re.findall(r'[\[(]([A-Z]+(?:\s*[,/]\s*[A-Z]+)*)[\])]',symbols):
        explicit += [x for x in re.split(r'\s*[,/]\s*',label) if x in FAMILY_ALIASES]
    if explicit:return list(dict.fromkeys(explicit))
    ranges=re.findall(r'(?<![A-Za-z])('+ '|'.join(sorted(FAMILY_ALIASES,key=len,reverse=True))+r')\s*\d+[0-9A-F]*',title)
    if len(set(ranges))==1:return list(dict.fromkeys(ranges))
    text=title.casefold()
    matches=[]
    for family,aliases in FAMILY_ALIASES.items():
        for alias in aliases:
            if alias.isascii() and re.search(r'\b'+re.escape(alias)+r'\b',text):matches.append((len(alias),family))
    if not matches:return []
    maximum=max(length for length,_ in matches)
    return list(dict.fromkeys(f for length,f in matches if length==maximum))


def source_models(text, allowed):
    """Read explicit CPU spellings from source labels; no family expansion."""
    normalized=re.sub(r'FX\s+(?=[0-9])','FX',str(text),flags=re.I)
    normalized=re.sub(r'\s+',' ',normalized).upper()
    return [name for name in allowed if re.search(r'(?<![A-Z0-9_])'+re.escape(name)+r'(?:CPU)?(?![A-Z0-9_])',normalized)]


def word_lines(words):
    groups=defaultdict(list)
    for word in words:
        # Mitsubishi device pages put navigation outside the content column.
        if word.get('navigation'):continue
        groups[round(float(word.get('top',0))/3)*3].append(word)
    return [(y,' '.join(str(w['text']) for w in sorted(items,key=lambda w:w.get('x0',0)))) for y,items in sorted(groups.items())]


def page_model_scope(page, previous=()):
    """Carry an explicit Device Number List CPU subsection across continuation pages."""
    if not re.search(r'Device Number List',page.get('outline_path',''),re.I):return (), ()
    text=page.get('device_text') or page.get('clean_text') or page.get('plain_text') or ''
    allowed=str(page['plc_models']).split(',')
    labels=list(_MODEL_HEADING.finditer(text))
    included=set(previous) if not labels or labels[0].start()>200 else set()
    last=tuple(previous)
    for label in labels:
        names=source_models(label[1],allowed)
        included.update(names)
        if names:last=tuple(names)
    return tuple(sorted(included)), last


def printed_page_from_source(plain, layout='', words=()):
    """Prefer physical footer geometry; uncertainty is represented by empty text."""
    candidates=[]
    if words:
        bottom=max(float(w.get('bottom',0)) for w in words)
        for y,line in word_lines(words):
            value=line.strip()
            if y>=bottom-24 and re.fullmatch(r'(?:[A-Z0-9]+\s*-\s*)*\d{1,4}',value,re.I):candidates.append(value)
        if len(set(candidates))==1:return re.sub(r'\s+','',candidates[0])
    # Stored layout preserves the physical reading order; plain extraction can
    # append side-navigation numbers after the real footer.
    lines=[line.strip() for line in str(layout).splitlines() if line.strip()]
    if str(layout).strip()!=str(plain).strip() and lines and re.fullmatch(r'(?:[A-Z0-9]+\s*-\s*)*\d{1,4}',lines[-1],re.I):
        return re.sub(r'\s+','',lines[-1])
    lines=[line.strip() for line in str(plain).splitlines() if line.strip()]
    if len(lines)>1 and re.fullmatch(r'\d{1,4}',lines[0]) and not re.fullmatch(r'\d+',lines[1]):return lines[0]
    header='\n'.join(lines[:6])
    if re.search(r'Manual|Series Programmable Controllers',header,re.I):
        for line in lines[:5]:
            if re.fullmatch(r'\d{1,4}',line):return line
    for line in lines[:12]:
        if re.fullmatch(r'(?:[A-Z0-9]+\s*-\s*)+\d{1,4}',line,re.I):return re.sub(r'\s+','',line)
    if lines and re.fullmatch(r'(?:[A-Z0-9]+\s*-\s*)+\d{1,4}',lines[-1],re.I):return re.sub(r'\s+','',lines[-1])
    return ''


def _notes(lines, wanted):
    """Keep complete marked notes; matching is by the literal table marker."""
    result=[]
    starts=[i for i,(_,line) in enumerate(lines) if re.match(r'^\s*\*\d+[.]',line)]
    for offset,index in enumerate(starts):
        end=starts[offset+1] if offset+1<len(starts) else len(lines)
        marker=re.match(r'^\s*\*(\d+)[.]',lines[index][1])[1]
        if marker not in wanted:continue
        paragraph=[]
        for _,line in lines[index:end]:
            if paragraph and (re.match(r'^\d+[.)]\s*(?:In\s+)?FX',line,re.I) or re.match(r'^\d+(?:\.\d+)+\b',line)):break
            if paragraph and re.fullmatch(r'\s*\d{1,4}\s*', line):break
            paragraph.append(line)
        result.append('\n'.join(paragraph))
    return result


def _table_views(page, tables, allowed):
    """Emit whole selected rows with their headers and locally bound notes."""
    lines=word_lines(page.get('words',[]))
    group_starts=[]
    for y,line in lines:
        if re.match(r'^\d+[.)]\s*(?:In\s+)?FX',line,re.I) and re.search(r'PLC',line,re.I):group_starts.append(y)
    views=[]
    for table in tables:
        rows=table.get('rows',[])
        if not rows:continue
        labelled=[(i,source_models(' '.join(str(cell or '') for cell in row[:1]),allowed)) for i,row in enumerate(rows)]
        labelled=[(i,models) for i,models in labelled if models]
        if not labelled:continue
        top=float(table['bbox'][1]); bottom=float(table['bbox'][3])
        previous=max((y for y in group_starts if y<=top),default=0)
        following=min((y for y in group_starts if y>bottom),default=float('inf'))
        local_lines=[(y,text) for y,text in lines if previous<=y<following]
        headers=rows[:labelled[0][0]]
        for index,models in labelled:
            selected=rows[index]
            markers=set(re.findall(r'\*(\d+)',' '.join(str(v or '') for row in [*headers,selected] for v in row)))
            notes=_notes(local_lines,markers)
            if markers and {m for note in notes for m in re.findall(r'^\*(\d+)[.]',note)}!=markers:
                # Missing/cross-page notes must not turn into a partial truth.
                continue
            raw=[' | '.join(str(v or '<blank>') for v in row) for row in [*headers,selected]]
            views.append({'models':models,'text':'\n'.join(raw+notes),'table_index':table.get('table_index'),
                          'bbox':table['bbox'],'status':'cpu_row_with_notes',
                          'source_cells':[{'column':column,'header':' / '.join(str(h[column] or '') for h in headers if column<len(h)).strip(' /'),
                                           'text':str(cell or '')} for column,cell in enumerate(selected)]})
    return views


def _column_views(page, tables, allowed):
    """Bind literal CPU columns and merged number cells in special-device tables.

    Glyphs are preserved as raw source marks. This projects the table rather
    than interpreting a mark as executable device support.
    """
    from plc.device_identity import reference_device_parts
    words=page.get('words',[])
    headers=[];detected=False
    for table in tables:
        rows,cells=table.get('rows',[]),table.get('cell_bboxes',[])
        if not rows:continue
        number_columns=[i for i,v in enumerate(rows[0]) if re.search(r'\bnumber\b|\bdevice\b',str(v or ''),re.I)]
        function_columns=[i for i,v in enumerate(rows[0]) if re.search(r'operation|function|meaning',str(v or ''),re.I)]
        if not number_columns or not function_columns:continue
        for index,row in enumerate(rows):
            labelled=[(column,source_models(value or '',allowed)) for column,value in enumerate(row)]
            labelled=[(column,models) for column,models in labelled if models]
            if len(labelled)<2:continue
            detected=True
            if not cells:continue
            if index>=len(cells):continue
            bounds={column:cells[index][column] for column,models in labelled
                    if column<len(cells[index]) and cells[index][column]}
            if len(bounds)<2:continue
            # Number/name and operation/function are the literal shared columns.
            if max(number_columns[0],function_columns[0])>=len(cells[0]):continue
            common=[cells[0][number_columns[0]],cells[0][function_columns[0]]]
            if len(common)<2 or not all(common):continue
            headers.append((table,labelled,bounds,common))
    page['_cpu_column_table']=detected
    views=[]
    for header,labelled,bounds,common in headers:
        left,right=common[0][0],max(box[2] for box in bounds.values())
        rules=sorted(set(float(y) for x0,x1,y in page.get('horizontal_rules',[])
                         if x0<=left+1 and x1>=right-1 and y>header['bbox'][3]-1))
        for top,bottom in zip(rules,rules[1:]):
            def literal(box):
                selected=[w for w in words if box[0]<=(w['x0']+w['x1'])/2<box[2]
                          and top<=(w['top']+w['bottom'])/2<bottom]
                return '\n'.join(line for _,line in word_lines(selected)).strip()
            number=literal(common[0])
            normalized=re.sub(r'\[([A-Z]+)\]\s*(?=\d)',r'\1',number)
            normalized=re.sub(r'([A-Z]+)\s+(?=\d)',r'\1',normalized)
            addresses=[m[0] for m in re.finditer(r'\b[A-Z]{1,2}[0-9A-F]+\b',normalized)
                       if reference_device_parts(m[0])]
            if len(addresses)!=1:continue
            address=addresses[0]
            # A horizontal divider spanning the number column owns both
            # operation rows for a merged number such as M8028.
            operations=[]
            for table in tables:
                if table['bbox'][1]<header['bbox'][3]-1:continue
                for index,row in enumerate(table.get('row_bboxes',[])):
                    if top-1<=row[1] and row[3]<=bottom+1:
                        operations.append((row[1],row[3],table,index))
            if not operations:continue
            for column,models in labelled:
                rendered=[];excluded=[];complete=True
                for row_top,row_bottom,table,row_index in sorted(operations,key=lambda item:item[0]):
                    def cell(box):
                        selected=[w for w in words if box[0]<=(w['x0']+w['x1'])/2<box[2]
                                  and row_top<=(w['top']+w['bottom'])/2<row_bottom]
                        return '\n'.join(line for _,line in word_lines(selected)).strip()
                    operation,mark=cell(common[1]),cell(bounds[column])
                    if not operation or not mark:
                        complete=False
                        break
                    if re.fullmatch(r'[-–—−]+',mark):
                        excluded.append({'table_index':table.get('table_index'),'row_index':row_index,
                                         'bbox':[bounds[column][0],row_top,bounds[column][2],row_bottom],
                                         'source_mark':mark})
                        continue
                    rendered.append(operation+' | '+mark)
                if not complete or not rendered:continue
                markers=set(re.findall(r'\*(\d+)',number+'\n'+'\n'.join(rendered)))
                notes=_notes(word_lines(words),markers)
                if {m for note in notes for m in re.findall(r'^\*(\d+)[.]',note)}!=markers:continue
                views.append({'models':models,'address':address,
                    'text':f"Device reference: {address}\nNumber and name: {number}\nOperation and function | Applicable model {', '.join(models)}\n"
                           +'\n'.join(rendered+notes)
                           +'\nRows whose selected CPU cell contains a dash are excluded from this CPU function evidence.'
                           +' Other marks remain literal; no address/access certification.',
                    'table_index':header.get('table_index'),'bbox':[left,top,right,bottom],
                    'cpu_column_bbox':bounds[column],'excluded_cpu_cells':excluded,'status':'cpu_column_with_notes'})
    return views


def section_units(page):
    """Split at source section headings, even when several share a PDF page."""
    text=page.get('device_text') or page.get('clean_text') or page.get('plain_text') or ''
    matches=[(m.start(), m[1], m[2]) for m in _SECTION.finditer(text)]
    # Some manuals use unnumbered device headings. Only source outline
    # declarations become boundaries; a symbol appearing in an example does
    # not introduce a new definition. Keep every sibling on a shared page.
    for title in page.get('outline_headings', []):
        if re.match(r'^\d+(?:\.\d+)*\s', title) or not heading_families(title):
            continue
        pattern = r'(?m)^[^\S\n]*' + r'[^\S\n]+'.join(map(re.escape, title.split())) + r'[^\S\n]*$'
        match = re.search(pattern, text)
        if match:
            matches.append((match.start(), '', title))
    matches=sorted(set(matches))
    ancestry={}
    for item in str(page.get('outline_path','')).split(' > '):
        m=re.match(r'(\d+(?:\.\d+)*)\s+(.+)',item)
        if m and heading_families(m[2]):ancestry[m[1]]=heading_families(m[2])
    units=[]
    for index,match in enumerate(matches):
        start,number,title=match;fs=heading_families(title)
        if fs:
            if number:ancestry[number]=fs
        else:
            parents=[key for key in ancestry if number.startswith(key+'.')]
            fs=ancestry[max(parents,key=len)] if parents else []
        if not fs:continue
        if number and index+1<len(matches) and matches[index+1][1]==number:continue
        end=matches[index+1][0] if index+1<len(matches) else len(text)
        content=text[start:end].strip()
        if len(content)<40:continue
        units.append({'section':(number+' '+title.strip()).strip(),'families':fs,'text':content,
                      'start':start,'end':end})
    if not units:
        fs=heading_families(str(page.get('section') or page.get('outline_path','')).split(' > ')[-1])
        if fs and len(text)>40:units=[{'section':page.get('section',''),'families':fs,'text':text,'start':0,'end':len(text)}]
    return units


def build_device_sections(page, tables):
    """Compile source text references; reviewed address facts remain separate."""
    allowed=str(page.get('plc_models','')).split(',')
    chapter=str(page.get('chapter',''))
    is_device=(re.search(r'\bdevices?\b',chapter,re.I)
               or re.search(r'(?:^| > )\d+\s+DEVICES?(?: > |$)',page.get('outline_path',''),re.I))
    direct=re.search(r'Direct (?:mode|processing)',page.get('section',''),re.I)
    if page.get('manual_type') not in {'programming','structured_device','cpu_function'} or not (is_device or direct):return []
    units=section_units(page)
    if direct:
        units=[{'section':page['section'],'families':['DX','DY'],'text':page.get('clean_text') or page['plain_text'],'start':0,'end':len(page.get('clean_text') or page['plain_text'])}]
    views=_table_views(page,tables,allowed)
    column_views=_column_views(page,tables,allowed)
    records=[]
    for index,unit in enumerate(units):
        text=unit['text']
        # Contact/coil families have literal source declarations inside the
        # timer/counter chapter, not inferred applicability from a parent type.
        for symbol in ('TS','TC','CS','CC'):
            if re.search(r'"'+symbol+r'"\s+indicates\s+(?:a\s+)?(?:contact|coil)',re.sub(r'\s+',' ',text),re.I):
                unit['families'].append(symbol)
        if 'T' in unit['families'] and re.search(r'retentive timer',text,re.I) and re.search(r'\b(?:OUT|OUTH|RST)\s+ST(?:\d+)?\b',text):
            unit['families'].append('ST')
        chosen=[]
        if column_views:
            chosen=[v for v in column_views if any(v['address'].startswith(f) for f in unit['families'])]
        elif page.get('_cpu_column_table'):
            chosen=[]
        elif views and (re.search(r'Numbers of|Device Number List',unit['section'],re.I) or _MODEL_HEADING.search(text)):
            # Introductory definition precedes range/CPU tables. A selected row
            # is accompanied by its original column names and referenced notes.
            introduction=re.split(r'(?:^|\n)\s*(?:\d+[.)]\s*(?:In\s+)?FX|\*\d+[.])',text,maxsplit=1)[0].strip()
            owned=[v for v in views if any(re.search(r'(?<![A-Z])'+f+r'\d',v['text']) for f in unit['families'])]
            chosen=[{**v,'text':introduction+'\n\n[SOURCE TABLE ROW]\n'+v['text']} for v in owned]
        else:
            labels=list(_MODEL_HEADING.finditer(text))
            if labels:
                for pos,label in enumerate(labels):
                    finish=labels[pos+1].start() if pos+1<len(labels) else len(text)
                    models=source_models(label[1],allowed)
                    if models:chosen.append({'models':models,'text':text[label.start():finish],'status':'cpu_subsection'})
            elif not re.search(r'Numbers of|Device Number List',unit['section'],re.I) and not (labels and tables):
                chosen=[{'models':allowed,'text':text,'status':'complete_source_section'}]
        for part,view in enumerate(chosen):
            for family in dict.fromkeys(unit['families']):
                records.append({'id':f"device-section:{page['manual_id']}:{page['pdf_page']}:{index}:{part}:{family}",
                  'family':family,'manual_id':page['manual_id'],'manual_number':page['manual_number'],'revision':page['revision'],
                  'manual_type':page['manual_type'],'source':page['manual_title'],'pdf_page':page['pdf_page'],
                  'page':page.get('printed_page',''),'section':unit['section'],'plc_models':','.join(view['models']),
                  'text':view['text'],'table_binding_status':view['status'],'source_chunk_id':page.get('source_chunk_id',''),
                  'source_excerpt':{'page':page['pdf_page'],'section_span':[unit['start'],unit['end']],
                       'text_basis':'physical_word_reading_order' if page.get('device_text') else 'frozen_clean_text',
                       **{key:view[key] for key in ('table_index','bbox','cpu_column_bbox','excluded_cpu_cells','source_cells') if key in view}}})
    return records


def source_range_matches(text, address, *, context=''):
    """Locate a literal source interval, independently of PLC address admission."""
    from plc.device_identity import reference_device_parts
    parsed=reference_device_parts(address)
    if not parsed:return []
    prefix,number=parsed
    combined=text+' '+context
    radix=10
    if prefix in {'X','Y','B','W','SB','SW','DX','DY'}:
        radix=8 if re.search(r'octal',combined,re.I) else 16 if re.search(r'hexadecimal',combined,re.I) else 10
    try:index=int(number,radix)
    except ValueError:return []
    matches=[]
    for match in re.finditer(r'\b'+prefix+r'([0-9A-F]+)\s*(?:to|[-–～]|至)\s*(?:'+prefix+r')?([0-9A-F]+)\b',text,re.I):
        try:inside=int(match[1],radix)<=index<=int(match[2],radix)
        except ValueError:continue
        if inside:matches.append(match[0])
    return matches


def address_relevance(text, address):
    """Prefer literal address/range evidence; never perform PLC validation."""
    from plc.device_identity import reference_device_parts
    parsed=reference_device_parts(address)
    if not parsed:return 0
    prefix,number=parsed
    text=re.sub(r'\[('+re.escape(prefix)+r')\]\s*(?=\d)',r'\1',text,flags=re.I)
    text=re.sub(r'(?<![A-Za-z])'+re.escape(prefix)+r'\s+(?=\d)',prefix,text,flags=re.I)
    if re.search(r'(?<![A-Z0-9])'+re.escape(address)+r'(?![A-Z0-9])',text,re.I):return 2
    return 1 if source_range_matches(text,address) else 0


def resolve_section_records(connection, schema, targets, *, plc_model):
    """Deliver bounded complete references from the shared derived index."""
    from knowledge import core
    from plc.device_identity import reference_device_parts
    table=schema.get('device_sections')
    if not table:return []
    results=[]
    for target in dict.fromkeys(targets):
        parsed=reference_device_parts(target)
        family=parsed[0] if parsed else str(target).upper()
        rows=connection.execute('SELECT * FROM device_sections WHERE family=?',(family,)).fetchall()
        eligible=[row for row in rows if core._scope_matches(row['plc_models'],plc_model)]
        # CPU-labelled rows precede generic operating examples. Actual ranges
        # can enrich an address question without certifying that address.
        eligible.sort(key=lambda row:(
           not (parsed and address_relevance(row['text'],target)==2 and re.search(r'special.{0,24}(?:relay|register)',row['section'],re.I)),
           (row['table_binding_status'] not in {'cpu_row_with_notes','cpu_column_with_notes','cpu_subsection'}) if parsed
           else (row['table_binding_status'] in {'cpu_row_with_notes','cpu_column_with_notes','cpu_subsection'} or bool(re.search(r'Numbers of|Device Number List',row['section'],re.I))),
           -address_relevance(row['text'],target) if parsed else 0,
           bool(re.search(r'example|cautions|handling',row['section'],re.I)),row['pdf_page'],row['manual_id'],row['id']))
        if eligible:
            def is_range(row):
                return row['table_binding_status'] in {'cpu_row_with_notes','cpu_column_with_notes','cpu_subsection'} or bool(re.search(r'Numbers of|Device Number List',row['section'],re.I))
            first=eligible[0]
            complement=[row for row in eligible[1:] if is_range(row)!=is_range(first)]
            eligible=[first,*complement,*[row for row in eligible[1:] if is_range(row)==is_range(first)]]
        seen=set();count=0
        for row in eligible:
            key=(row['manual_id'],row['pdf_page'],row['text'])
            if key in seen:continue
            seen.add(key)
            number=row['page'] or row['pdf_page']
            scope_basis = ('cpu_table_row' if row['table_binding_status']=='cpu_row_with_notes'
                           else 'cpu_table_column' if row['table_binding_status']=='cpu_column_with_notes'
                           else 'cpu_subsection' if row['table_binding_status']=='cpu_subsection'
                           else 'manual_family_section')
            excerpt=json.loads(row['source_excerpt_json'])
            locations=[]
            if parsed:
                for cell in excerpt.get('source_cells',[]):
                    for interval in source_range_matches(cell['text'],target,context=row['text']):
                        locations.append({'address':target,'source_range':interval,'column_header':cell['header'],
                                          'column_text':cell['text'],'basis':'literal_source_range_membership'})
            location_text='\n'.join(f"Source address location: {target} lies in {q['source_range']}; original column header: {q['column_header']}." for q in locations)
            text=(f"SOURCE: {row['source']}\nMANUAL: {row['manual_number']} Rev.{row['revision']}\n"
                  f"SECTION: {row['section']}\nPDF PAGE: {row['pdf_page']}\nCPU SOURCE SCOPE: {row['plc_models']}\n"
                  "Verification: original source chapter; no individual address/access certification.\n\n"+row['text'])
            if location_text:text+='\n\n'+location_text
            results.append({'id':row['id']+(':'+target if parsed else ''),'source_section_id':row['id'],
                'source':row['source'],'manual_id':row['manual_id'],
                'manual_number':row['manual_number'],'revision':row['revision'],'manual_type':row['manual_type'],
                'chunk_type':'device','section':row['section'],'page':number,'pdf_page':row['pdf_page'],
                'text':text,'plc_models':row['plc_models'],'source_chunk_id':row['source_chunk_id'],
                'source_evidence':[excerpt],'source_address_locations':locations,
                'source_applicability':{'basis':'device_source_section','supported_models':row['plc_models'].split(','),
                                        'scope_basis':scope_basis,'individual_address_support':'unverified'},
                'fact_kind':'device_occurrence' if parsed else 'device_family','fact_target':target,
                'structured_fact_kind':'device_occurrence' if parsed else 'device_family','structured_fact_target':target,
                'fact_dimensions':['family_definition','range','conditions'] if parsed else ['definition'],
                'device_definition_status':'unverified' if parsed else 'original_source',
                'device_lookup_basis':'official_family_section','table_binding_status':row['table_binding_status'],
                'evidence_scope':scope_basis,'content_status':'complete','match_type':'structured_direct',
                'structured_lookup':True})
            count+=1
            if count>=2:break
    return results


def project_source_record(connection, schema, record, *, plc_model):
    """Replace a broad table reference with complete eligible CPU columns.

    Broad RRF keeps its original rank and source identity. This only projects
    the selected source; it adds no semantic score or device permission.
    """
    from knowledge import core
    if record.get('table_binding_status')!='cpu_columns_require_projection':return record
    if not schema.get('device_sections'):return None
    first=record.get('pdf_page')
    last=record.get('pdf_page_end') or record.get('page_pdf_end') or first
    if not first:return None
    # Bundled chunks expose printed page_end separately; get the actual PDF
    # span from its original source row, including multiple blocks on a page.
    source=connection.execute('SELECT pdf_page,pdf_page_end FROM chunks WHERE id=?',(record['id'],)).fetchone()
    if source:first,last=source['pdf_page'],source['pdf_page_end']
    rows=connection.execute("SELECT * FROM device_sections WHERE manual_id=? AND pdf_page BETWEEN ? AND ? AND table_binding_status='cpu_column_with_notes' ORDER BY pdf_page,id",
                            (record.get('manual_id'),first,last)).fetchall()
    selected=[row for row in rows if core._scope_matches(row['plc_models'],plc_model)]
    if not selected:return None
    seen=set();parts=[];evidence=[];models=set()
    for row in selected:
        if row['text'] in seen:continue
        seen.add(row['text']);parts.append(row['section']+'\n'+row['text'])
        evidence.append(json.loads(row['source_excerpt_json']))
        models.update(row['plc_models'].split(','))
    return {**record,'source_chunk_id':str(record['id']),
        'text':f"SOURCE: {record['source']}\nCPU source columns: {', '.join(sorted(models))}\n"
               'Original source projection; no individual address/access certification.\n\n'+'\n\n'.join(parts),
        'plc_models':','.join(sorted(models)),'table_binding_status':'cpu_column_projection',
        'evidence_scope':'cpu_table_column','content_status':'complete','source_evidence':evidence,
        'source_applicability':{'basis':'device_cpu_column_projection','supported_models':sorted(models),
                              'individual_address_support':'unverified',
                              'document_applicability':record.get('source_applicability',{})}}
