"""Authored, source-checked behaviour facts for the Q/L instruction library.

This module is the Q/L counterpart of ``tools/fact_behavior_review.py``. It is
*independent*: it reads the private evidence database only, it does not import
or modify the FX module, and it publishes nothing but typed facts.

Scope
-----
Five registered manuals carry the Q/L instruction library:

===================  ==================================================
``sh080809engx`` X   MELSEC-Q/L Programming Manual (Common Instruction)
``sh080783engp`` P   MELSEC-Q/L Structured Programming Manual (Common Instructions)
``sh080784engn`` N   MELSEC-Q/L Structured Programming Manual (Application Functions)
``sh080785engm`` M   MELSEC-Q/L Structured Programming Manual (Special Instructions)
``sh080316ei``   I   MELSEC-Q Programming/Structured Programming Manual (Process Control)
===================  ==================================================

Every instruction section of those manuals is inventoried. A section is only
turned into facts when an *authored rule* fires on a phrase that is verified in
that very section (a moved page, a changed revision or a re-worded sentence
fails closed and leaves the section pending instead of silently re-binding).

What the facts are
------------------
Author restatements of documented behaviour, in formal tokens for a fixed
dimension vocabulary (``parameters.*``, ``execution.*``, ``effects.*``,
``memory.*``, ``constraints.*``, ``identity.*``). Nothing here is an executable
behaviour DSL: no value carries a ``behavior`` key, so the bounded executor and
``plc.instruction_effects`` never admit any of it. No fact promotes
``call_available``, a CPU's Core capability, a native compile or a hardware
result. Manual sentences are never copied: only typed slot values are stored,
and the witness phrase that justified a fact stays in the receipt, not in the
published record.

Deliberate non-generalisation
-----------------------------
Family rules cover shape-stable material (arithmetic, transfer, conversion,
comparison, rotation/shift, string, module instruction classes). Rules that
would be unsafe to generalise are *never* family-derived and are emitted only
from an explicit per-section witness: language/ST call forms,
``execution.first_execution``, disable/prohibition contexts, jump semantics and
same-scan/repeat rules. Grouped headings (``LD, LDI, AND, ANI, OR, ORI``) give
every declared symbol its own identity; ownership is never taken from the one
symbol the index happened to attach to the chunk.

Merge contract (stable)
-----------------------
``reviewed_ql_behavior(db, entries) -> (facts, receipt)`` returns facts keyed by
``(target_model, opcode)``. Merging is *never* done here: facts are handed to the
build's own ``knowledge.structured_facts._merge_fact_rows`` per source batch, so

* an identical claim (same id, same value/scope/conditions/depends_on) from a
  second manual only gains a source, and a reviewed witness keeps its
  ``source_verified`` status - an unreviewed duplicate never demotes it;
* a different documented value stays a separate fact (``:variant:N``), and is
  reported as ``conflict`` only when dimension, scope and conditions agree;
* nothing is weakened, promoted or re-scored on the way through this module.

Conditions are part of the claim. Only the reviewed vocabulary in
``CONDITION_KINDS``/``CONDITION_TOKENS`` is formal; any other documented premise
stays ``{'kind': 'documented_condition', 'value': 'unknown', ...}`` and forces the
fact to ``candidate_evidence`` (retaining its own source-check record), so an unresolved condition is visible as a gap
instead of being tokenised into a verified claim.
"""
from __future__ import annotations

import copy
import json
import re

#: Stable review version handed to the build; bump only with a schema change.
REVIEW_VERSION = 'ql-behaviour-1'
METHOD = 'authored_ql_behaviour_review_v1'

#: Registered Q/L manuals and the revision this review is bound to. A different
#: revision in the index fails closed: the authored facts are not re-bound.
QL_MANUALS = {
    'sh080809engx': {'revision': 'X', 'number': 'SH080809ENG', 'kind': 'ladder'},
    'sh080783engp': {'revision': 'P', 'number': 'SH080783ENG', 'kind': 'structured'},
    'sh080784engn': {'revision': 'N', 'number': 'SH080784ENG', 'kind': 'function'},
    'sh080785engm': {'revision': 'M', 'number': 'SH080785ENG', 'kind': 'module_dedicated'},
    'sh080316ei': {'revision': 'I', 'number': 'SH080316E', 'kind': 'loop_control'},
}

#: Manuals that carry Q/L instruction sections but whose instruction-typed
#: chunks are device, function or scan-time material, not instruction
#: definitions. They stay inventoried with a stated reason.
NON_INSTRUCTION_QL_MANUALS = {
    'sh080807engaf': 'source_cpu_function_and_device_sections',
    'sh080808engm': 'source_cpu_function_and_device_sections',
    'sh080889engz': 'source_cpu_function_and_device_sections',
    'sh080366ek': 'source_st_language_manual_without_instruction_sections',
    'structured_fundamentals_o': 'source_structured_fundamentals_without_instruction_sections',
}

_PAGE_PROSE = re.compile(r'\[PAGE (\d+) PROSE\]\n(.*?)(?=\n\[PAGE \d+ (?:PROSE|LAYOUT)\]|\Z)', re.S)
_PAGE_LAYOUT = re.compile(r'\[PAGE (\d+) LAYOUT\]\n(.*?)(?=\n\[PAGE \d+ (?:PROSE|LAYOUT)\]|\Z)', re.S)
_TABLE_BLOCK = re.compile(r'\[TABLE page=\d+ index=\d+ bbox=\[[^\]]*\]\]\n(.*?)(?=\n\n|\Z)', re.S)

#: ``S. >=`` style spellings of the process-control library.
_SYMBOL_PUNCTUATION = {'>': 'GT', '<': 'LT', '=': 'EQ', '>=': 'GE', '<=': 'LE'}
_DECORATED = re.compile(r'^([A-Z$+*/<>=!\-][A-Z0-9_$.+*/<>=!\-]*?)\s*\((P|_E)\)$')
_DOTTED_PULSE = re.compile(r'^([A-Z]{1,3})\(P\)\s*\.\s*([A-Z][A-Z0-9_+\-]{1,30})$')
_PREFIXED = re.compile(r'^([A-Z])\(P\)_(.+)$')
_REGISTER_PREFIXED = re.compile(r'^([A-Z])_([A-Z0-9]+)$')
_SINGLE_SYMBOL = re.compile(r'^(?:[A-Z$+*/<>=!&\-][A-Z0-9_$.+*/<>=!&\-]*?'
                            r'|(?:OUT|OUTH|SET|RST)\s+(?:T|C|ST|F))\s*(?:\((?:P|_E)\))?$')
_SYMBOL_LIST = re.compile(
    r'^(?:[A-Z](?:\(P\))?_)?[A-Z][A-Z0-9_.+*/<>=!\-]*(?: [A-Z][A-Z0-9_.+*/<>=!\-]*)?\s*(?:\((?:P|_E)\))?'
    r'(?:\s*,\s*(?:[A-Z](?:\(P\))?_)?[A-Z][A-Z0-9_.+*/<>=!\-]*(?: [A-Z][A-Z0-9_.+*/<>=!\-]*)?\s*'
    r'(?:\((?:P|_E)\))?)+$')


def _normalize_symbol(token):
    """Canonical opcode spelling for one declared symbol token."""
    name = token.strip()
    punct = re.match(r'^([A-Z])\.\s*([^\s].*)$', name)
    if punct and punct.group(2) in _SYMBOL_PUNCTUATION:
        return punct.group(1) + '_' + _SYMBOL_PUNCTUATION[punct.group(2)]
    name = re.sub(r'\s*\.\s*', '.', name)
    return re.sub(r'\s+', '_', name).strip('_')


def expand_symbols(tokens):
    """Every literal form a declared heading token stands for.

    ``CMP(P)`` declares ``CMP`` and ``CMPP``; ``BOOL_TO_STR(_E)`` declares the
    plain and the EN/ENO form; ``G(P)_OFFGAN`` declares ``G_OFFGAN`` and
    ``GP_OFFGAN``. Each form keeps an independent identity.
    """
    result = []
    for token in tokens:
        name = token.strip()
        if not name:
            continue
        decorated = _DECORATED.match(name)
        if decorated:
            base = _normalize_symbol(decorated.group(1))
            suffix = 'P' if decorated.group(2) == 'P' else '_E'
            result.extend([base, base + suffix])
            continue
        dotted_pulse = _DOTTED_PULSE.match(name)
        if dotted_pulse:
            prefix, suffix = dotted_pulse.groups()
            result.extend([prefix + '.' + suffix, prefix + 'P.' + suffix])
            continue
        prefixed = _PREFIXED.match(name)
        if prefixed:
            base = _normalize_symbol(prefixed.group(2))
            result.extend([prefixed.group(1) + '_' + base, prefixed.group(1) + 'P_' + base])
            continue
        result.append(_normalize_symbol(name))
    return [name for name in dict.fromkeys(result) if name]


def _prose_blocks(text):
    """Prose blocks in page order. The LAYOUT twin repeats the same text."""
    return [(int(match.group(1)), match.group(2)) for match in _PAGE_PROSE.finditer(text or '')]


def _table_blocks(text):
    return [match.group(1) for match in _TABLE_BLOCK.finditer(text or '')]


def _join_wrapped(lines):
    """Rejoin bullet statements and table cells split by the PDF line layout."""
    joined = []
    for line in lines:
        text = line.strip()
        if not text:
            joined.append('')
            continue
        if joined and joined[-1] and (re.match(r'^[a-z,;:)\]%]', text)
                or re.search(r'\b(?:to|and|from)\s*-?$', joined[-1]) and re.match(r'-?\d', text)
                or joined[-1].endswith(',') and re.match(r'^[A-Z$+*/<>=!\-]', text)):
            joined[-1] = joined[-1] + ' ' + text
        else:
            joined.append(text)
    return [line for line in joined if line]


def declared_symbols(lines, prefer=''):
    """The literal symbol line that follows a section title, if the layout has one."""
    first, preferred = ([], []), ([], [])
    for line in lines[:8]:
        candidate = re.sub(r'\s*\.\s*', '.', line.strip())
        candidate = re.sub(r'\s*\[When\b.*$', '', candidate)
        if (len(candidate) > 600 or _non_instruction_symbol(candidate)
                or candidate in {'ON', 'OFF', 'ONON', 'EN', 'ENO', 'MEMO', 'ASCII.'}):
            continue
        tokens = [token for token in re.split(r'\s*,\s*', candidate) if token]
        tokens = [re.sub(r'^([A-Z][A-Z0-9_$.+*/<>=!\-]*)\s+(?:n\d?|[sd]\d?)$', r'\1', token)
                  for token in tokens]
        if not tokens or len(tokens) > 36 or not all(
                _SINGLE_SYMBOL.fullmatch(token) or _DOTTED_PULSE.fullmatch(token)
                or _PREFIXED.fullmatch(token) for token in tokens):
            continue
        symbols = expand_symbols(tokens)
        symbols = [symbol for symbol in symbols if not _non_instruction_symbol(symbol)]
        if not symbols:
            continue
        if prefer and prefer in symbols and len(symbols) > len(preferred[1]):
            preferred = (tokens, symbols)
        if not first[1]:
            first = (tokens, symbols)
        elif set(first[1]) <= set(symbols) and len(symbols) > len(first[1]):
            first = (tokens, symbols)
    return preferred if preferred[1] else first


def _non_instruction_symbol(symbol):
    """Device values and CPU labels on continuation pages never own facts."""
    if symbol in {'BCD', 'BCDDA'}:
        # These two literal conversion mnemonics also happen to look like
        # hexadecimal B-device numbers; both have their own instruction titles.
        return False
    return symbol in {'TRUE', 'FALSE', 'ON', 'OFF', 'ONON', 'EN', 'ENO', 'MEMO'} or bool(re.fullmatch(r'[A-Z]|(?:[SDMRZU]|ZR|SM|SD)\d+(?:\.[0-9A-F]+)?'
                             r'|[XYBW][0-9A-F]+(?:\.[0-9A-F]+)?'
                             r'|[KH][0-9A-F]+|Q[A-Z0-9]+CPU|L[A-Z0-9]+CPU', symbol.rstrip('.')))


class Section:
    """One documented instruction heading with its bound evidence chunks."""

    def __init__(self, manual, revision, manual_number, kind, outline, rows):
        self.manual = manual
        self.revision = revision
        self.manual_number = manual_number
        self.kind = kind
        self.outline = outline
        parts = [part.strip() for part in outline.split('>')]
        self.title = parts[-1] if parts else outline
        self.chapter = ' > '.join(parts[:-1])
        self.rows = rows
        self.chunk_ids = [row['id'] for row in rows]
        self.pages = sorted({int(row['pdf_page']) for row in rows if row['pdf_page'] is not None})
        self.page_pairs = [(int(row['pdf_page']), int(row['pdf_page_end'] or row['pdf_page']))
                           for row in rows if row['pdf_page'] is not None]
        self.opcodes = [row['instruction_opcode'] for row in rows if row.get('instruction_opcode')]
        self.has_instruction_chunk = any(row['chunk_type'] == 'instruction' for row in rows)
        self.prose = []          # [(page, joined lines)]
        self.lines = []          # flattened, wrapped-joined non-empty lines
        self.tables = []
        for row in rows:
            for page, block in _prose_blocks(row['text']):
                self.prose.append((page, _join_wrapped(block.splitlines())))
            self.tables.extend(_table_blocks(row['text']))
        for _page, lines in self.prose:
            self.lines.extend(lines)
        self.text = '\n'.join(self.lines)
        self.symbol_tokens, self.symbols = self.heading_tokens()
        # The source compiler's chunk type is only an extraction hint. Literal
        # operator headers and comparison-glyph families have real definitions
        # even when that old hint failed to recognise their spelling.
        literal_definition = re.search(r'(?m)^(?:■\s*)?Operation error$', self.text)
        if self.manual == 'sh080785engm' and re.match(r'^[5-8]\b', self.chapter):
            literal_definition = literal_definition or re.search(
                r'(?m)^This instruction [a-z]+\b|^Remotely runs or stops a programmable controller', self.text)
        self.source_definition = bool(self.symbol_tokens and self.symbols and literal_definition)
        self.has_instruction_chunk = self.has_instruction_chunk or self.source_definition
        self.unattributed = []
        self.ladder = None
        self._blocks = self.blocks()
        self.heading_line = getattr(self, 'heading_line', '') or self.title
        if len(self.heading_line) < 3:
            self.heading_line = self.title

    # -- heading -----------------------------------------------------------
    def heading_tokens(self):
        tokens, symbols = [], []
        prefer = self.opcodes[0] if self.opcodes else ''
        for _page, lines in self.prose:
            found_tokens, found_symbols = declared_symbols(lines, prefer)
            continuation_definition = bool(
                self.manual == 'sh080785engm' and found_symbols
                and all(re.fullmatch(r'[GJSZ]P?_[A-Z0-9_]+', name) for name in found_symbols)
                and any(re.search(r'\b' + re.escape(name) + r'\s*\(\s*EN\b', '\n'.join(lines))
                        for name in found_symbols))
            continuation_definition = continuation_definition or (
                self.manual == 'sh080809engx' and found_symbols == ['XCALL'])
            if (symbols and not any('(' in token or ',' in token for token in found_tokens)
                    and len(found_symbols) == 1 and found_symbols[0] not in symbols
                    and not continuation_definition):
                continue
            if found_symbols:
                tokens.extend(token for token in found_tokens if token not in tokens)
                symbols.extend(symbol for symbol in found_symbols if symbol not in symbols)
                # Some channel headers list continuous forms first and their
                # pulse forms on the next line. Both are literal declarations.
                for line in lines[:8]:
                    extra_tokens, extra_symbols = declared_symbols([line], '')
                    if extra_symbols and all(name in symbols or
                            (name.endswith('P') and name[:-1] in symbols)
                            for name in extra_symbols):
                        tokens.extend(token for token in extra_tokens if token not in tokens)
                        symbols.extend(name for name in extra_symbols if name not in symbols)
                for line in lines[:8]:
                    if re.sub(r'\s*\.\s*', '.', line.strip()).startswith(found_tokens[0]):
                        self.heading_line = line.strip()
                        break
                # A grouped section can introduce another real declaration on
                # a later page (for example S(P).TO then TO(P), DTO(P)). Device
                # values and CPU labels are rejected before this union.
        # A comparison heading uses a glyph for its suffix; its instruction
        # result table spells out each concrete operator independently.
        if not symbols:
            heading = '\n'.join(self.prose[0][1][:8]) if self.prose else ''
            families = list(re.finditer(r'([A-Z$]+)\[GLYPH-F06F\](\(P\))?', heading))
            if families:
                for family in families:
                    base = family.group(1)
                    table_names = list(dict.fromkeys(re.findall(
                        r'(?<![A-Za-z0-9_$])' + re.escape(base) + r'(?:<>|<=|>=|=|<|>)(?:P)?', self.text)))
                    if not table_names:
                        operators = list(dict.fromkeys(re.findall(
                            r'(?m)^(<>|<=|>=|=|<|>)\s*\(?\s*S1\b', self.text)))
                        table_names = [base + operator for operator in operators]
                    if family.group(2):
                        table_names = list(dict.fromkeys(name[:-1] if name.endswith('P') else name
                                                         for name in table_names))
                    tokens.extend(name + ('(P)' if family.group(2) else '') for name in table_names)
                symbols = expand_symbols(tokens)
                self.heading_line = next(line for line in self.prose[0][1][:8]
                                         if '[GLYPH-F06F]' in line)
        # The chunk header names the instruction of the same section; the union
        # keeps every documented form of a grouped heading.
        for opcode in self.opcodes:
            for token in re.split(r'\s*,\s*', opcode or ''):
                for symbol in expand_symbols([token.strip()]) if token.strip() else []:
                    if _non_instruction_symbol(symbol):
                        continue
                    if symbol not in symbols and not symbols and re.search(
                            r'(?m)^' + re.escape(token.strip()) + r'\s*$', self.text):
                        symbols.append(symbol)
        if self.manual == 'sh080316ei':
            # Every process-control opcode belongs to the S. namespace.
            # Parameter legends such as DVLA/MHA/MLA are not later owners.
            declared = re.search(r'\bS\s*\.\s*([A-Z0-9][A-Z0-9]*|>=|<=|>|<|=)', self.title)
            if declared:
                primary = _normalize_symbol('S.' + declared.group(1))
                if primary not in symbols:
                    tokens.insert(0, primary)
                    symbols.insert(0, primary)
            symbols = [name for name in symbols if name.startswith(('S.', 'S_'))]
            tokens = [token for token in tokens if any(name in symbols for name in expand_symbols([token]))]
        return tokens, symbols

    # -- symbol blocks -----------------------------------------------------
    def blocks(self):
        """``■SYMBOL`` sub-blocks: declared symbols -> their bullet statements."""
        wanted = set(self.symbols)
        blocks, current, loose = {}, [], []
        for line in self.lines:
            if line.startswith('■') or line.startswith('■'):
                heading = re.sub(r'^[■•]\s*', '', line).strip()
                tokens = [token for token in re.split(r'\s*,\s*', heading) if token]
                expanded = expand_symbols(tokens) if tokens else []
                current = [name for name in expanded if name in wanted]
                if current:
                    for name in current:
                        blocks.setdefault(name, [])
                    continue
                current = []
            if line.startswith('•'):
                statement = re.sub(r'^•\s*', '', line).strip()
                if current:
                    for name in current:
                        blocks[name].append(statement)
                else:
                    loose.append(statement)
        return blocks, loose

    def statements(self, symbol):
        """Bullets that document ``symbol`` itself.

        Grouped headings may share one bullet block. Sharing is only accepted
        when the block documents a single operation shape, because a block with
        two exclusive operations (for example one instruction that adds and one
        that subtracts) cannot be attributed without guessing.
        """
        blocks, loose = self._blocks
        if symbol in blocks:
            return blocks[symbol]
        # ``MOV(P), DMOV(P)`` declares two operation blocks, each with a
        # continuous and pulse form. A pulse form shares its own base's
        # operation; it must not lose that operation or borrow its sibling's.
        for token in self.symbol_tokens:
            forms = expand_symbols([token])
            if symbol in forms and forms[0] in blocks:
                return blocks[forms[0]]
        pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(symbol) + r'(?![A-Za-z0-9_])')
        named = [statement for statement in loose
                 if pattern.search(statement) and len(statement) >= 25
                 and re.search(r'[a-z]{3}', statement)]
        shared = [statement for statement in loose
                  if len(statement) >= 25 and re.search(r'[a-z]{3}', statement)
                  and not any(re.search(r'(?<![A-Za-z0-9_])' + re.escape(other) +
                                       r'(?![A-Za-z0-9_])', statement)
                              for other in self.symbols
                              if other != symbol and _form_base(other) != _form_base(symbol))]
        if named:
            return named + [statement for statement in shared if statement not in named]
        if shared and (len({_form_base(item) for item in self.symbols}) <= 1
                        and self._single_operation(shared)):
            return shared
        if len(self.symbols) <= 1 or self._single_operation(loose):
            return loose
        self.unattributed.append(symbol)
        return []

    def _single_operation(self, statements):
        """True when one and only one operation shape is documented in a block.

        Error and precaution sentences are excluded: they never state the
        primary effect of the instruction.
        """
        matching = 0
        for statement in statements:
            if re.search(r'operation error|error flag|error code|SD0|\bDo not\b', statement):
                continue
            if re.match(r'^(?:If|When|The following program|For |In |At |Note|Even )', statement):
                continue
            if any(pattern.search(statement)
                   for _name, _dimension, _token, pattern in
                   (getattr(self, 'ladder', None) or _OPERATION_CLASSES)):
                matching += 1
        return matching <= 1

    # -- operands ----------------------------------------------------------
    def operands(self):
        """Declared operand legends and argument tables, in documented order."""
        found = []
        for line in self.tables + self.lines:
            match = re.match(r'^\((S1|S2|S3|S4|D1|D2|S|D|N)\)\s*:\s*\S', line)
            if match:
                found.append({'symbol': match.group(1), 'typed': '', 'direction': '', 'witness': line})
                continue
            match = re.match(r'^(EN|S1|S2|S3|D1|D2|s1|s2|s3|d1|d2|s|d|n)\s*\((\w+)\)\s*(.*)$', line)
            if match:
                direction = re.search(r'\b(Input|Output|In/out|In-out|Bidirectional)\b', match.group(3))
                found.append({'symbol': match.group(1), 'typed': match.group(2),
                              'direction': direction.group(1).lower() if direction else '',
                              'witness': line})
                continue
            match = re.match(r'^(S1|S2|S3|D1|D2|EN|s1|s2|s3|d1|d2|s|d|n)\s{1,}(\S.{5,})$', line)
            if match:
                typed = re.search(r'\b(ANY_SIMPLE|ANY16/32|ANY16|ANY32|ANY_BIT|ANY_NUM|BOOL|BIT|WORD|'
                                  r'DWORD|INT|DINT|UINT|REAL|LREAL|STRING|TIME|BCD)\b\s*$', match.group(2))
                direction = re.search(r'\b(Input|Output|In/out|In-out|Bidirectional)\b', match.group(2))
                found.append({'symbol': match.group(1), 'typed': typed.group(1) if typed else '',
                              'direction': direction.group(1).lower() if direction else '',
                              'witness': line})
        ordered = []
        for item in found:
            if item['symbol'] not in [entry['symbol'] for entry in ordered]:
                ordered.append(item)
        return ordered

    # -- documented ST / symbolic call form --------------------------------
    def call_forms(self):
        """Documented structured-text call forms; nothing is inferred from a title."""
        forms = []
        for line in self.lines:
            match = re.search(r'\b([A-Z][A-Z0-9_]*?)\s*\(EN\s*,\s*([A-Za-z0-9_,\s]+?)\)\s*;', line)
            if match:
                parameters = ['EN'] + [name.strip() for name in match.group(2).split(',') if name.strip()]
                forms.append({'opcode': _normalize_symbol(match.group(1)),
                              'documented_form': '%s(%s)' % (_normalize_symbol(match.group(1)),
                                                             ','.join(parameters)),
                              'parameters': parameters, 'enable': True, 'returns': 'ENO',
                              'witness': match.group(0).rstrip(';').strip()})
            match = re.match(r'^ENO\s*:=\s*([A-Z][A-Z0-9_]*?)\s*\(([A-Za-z0-9_,\s]+)\)\s*;', line)
            if match:
                parameters = [name.strip() for name in match.group(2).split(',') if name.strip()]
                forms.append({'opcode': _normalize_symbol(match.group(1)),
                              'documented_form': 'ENO:=%s(%s)' % (_normalize_symbol(match.group(1)),
                                                                 ','.join(parameters)),
                              'parameters': parameters, 'enable': 'EN' in parameters, 'returns': 'ENO',
                              'witness': match.group(0).rstrip(';').strip()})
        for match in re.finditer(r'Function without EN/ENO\s*\(([A-Z][A-Z0-9_]*)\)', self.text):
            name = _normalize_symbol(match.group(1))
            assignment = re.search(r':=\s*' + re.escape(name) + r'\s*\(', self.text)
            forms.append({'opcode': name,
                          'documented_form': name + '(...)',
                          'parameters': [], 'enable': False,
                          'returns': 'function_value' if assignment else 'unknown',
                          'witness': match.group(0).strip()})
        unique = []
        for form in forms:
            if form['documented_form'] not in [entry['documented_form'] for entry in unique]:
                unique.append(form)
        return unique

    # -- execution gate ----------------------------------------------------
    def non_conditional(self):
        """``Non-conditional execution``: the documented execution condition."""
        for line in self.lines:
            if re.search(r'Non-conditional execution', line):
                return True
        return False

    # -- operation error ---------------------------------------------------
    def error_block(self):
        """The documented ``Operation error`` statement of this section."""
        collected, active = [], False
        for line in self.lines:
            if re.match(r'^(?:■\s*)?Operation error$', line):
                active, collected = True, []
                continue
            if active:
                if re.match(r'^(?:■|Precautions|Program example|Processing details|Setting data|$)', line):
                    break
                collected.append(line)
        if not collected:
            return None
        joined = ' '.join(collected)
        if re.search(r'(?:There is|there is) no operation error|No operation error occurs'
                     r'|There is no operation error', joined):
            return {'operation_error': 'none', 'witness': 'no_operation_error', '_text': joined}
        flags = []
        if re.search(r'error flag \(SM0\)', joined, re.I):
            flags.append('SM0')
        registers = []
        if re.search(r'\bSD0\b', joined):
            registers.append('SD0')
        return {'operation_error': 'documented', 'error_flags': flags,
                'error_code_registers': registers,
                'witness': 'operation_error_table' if flags or registers else 'operation_error_statement',
                '_text': joined}

    def error_codes(self):
        codes = []
        for line in self.tables:
            if not re.search(r'\bError\s+(?:code|details)\b', line, re.I):
                continue
            for match in re.finditer(r'(?:^|\|\s*)(1\d{3}|2\d{3}|4\d{3}|9\d{3})\b', line):
                code = match.group(1)
                if code not in codes:
                    codes.append(code)
        return codes

    def serial_conditions(self):
        """Documented firmware/serial-number gates with the CPU group they name."""
        found = []
        for line in self.lines:
            match = re.match(r'^(?P<group>[A-Za-z0-9/().\- ,]+?)(?:CPU)?:\s*'
                             r'(?:Supported if first five digits of the serial number are|'
                             r'The serial number \(first five digits\) is|the serial number '
                             r'\(first five digits\) is)\s*"?(?P<serial>\d{4,6})"?\s*or later', line)
            if match:
                found.append({'cpu_group': match.group('group').strip(),
                              'minimum_serial': match.group('serial'), 'line': line.strip()[:160]})
        return found

    def limits(self, symbol=None):
        """Documented numeric domains, preserving their subject and qualifications.

        A numeric occurrence is not automatically an operand constraint. Examples,
        contact transitions, conversion headings and generated output ranges are
        excluded. Binding a genuine domain to a scalar operand is a separate step.
        """
        found, seen, active = [], set(), None
        number = r'-?\d+(?:\.\d+)?'
        for raw in self.lines:
            bracket = re.fullmatch(r'\[([A-Z0-9_, ]+)\]', raw.strip())
            if bracket:
                declared = set(expand_symbols(re.split(r'\s*,\s*', bracket.group(1)))) & set(self.symbols)
                if declared:
                    active = declared
                    continue
            if raw.startswith(('■', '•')):
                names = expand_symbols(re.split(r'\s*,\s*', raw[1:].strip()))
                declared = set(names) & set(self.symbols)
                if declared or raw.startswith('■'):
                    active = declared
                    for token in self.symbol_tokens:
                        forms = expand_symbols([token])
                        if active & set(forms):
                            active.update(forms)
                    active = active or None
                    continue
            if symbol and active and symbol not in active:
                continue
            line = raw.lstrip('• ').strip()
            if (line in seen or re.search(r'following program|program example|example below|'
                                         r'^For example|turns? from|goes? from|changes? from|'
                                         r'[Dd]ecod(?:e|ing) from|[Ee]ncod(?:e|ing) from|'
                                         r'overlaps\.?$', line, re.I)):
                continue
            seen.add(line)
            source_line = line
            line = re.split(r'\bFor example\b', line, maxsplit=1, flags=re.I)[0].strip()
            named = set()
            for token in self.symbol_tokens:
                forms = expand_symbols([token])
                if any(re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', line)
                       for name in forms):
                    named.update(forms)
            if symbol and named and symbol not in named:
                continue
            if not re.search(r'can be|can specify|can be specified|can be designated|'
                             r'is valid|are valid|[Ss]pecify|is specified|[Ss]etting|'
                             r'within (?:the |a )?range|outside the range|out of the range|'
                             r'number of pins|number of digits|number of characters|'
                             r'unit of measurement|\bdesignates?\b|nesting|nested|successively', line):
                continue
            ranges = list(re.finditer(r'(?:ranges? |from |between |of |and |is )(' + number + r')\s*'
                                      r'(?:to|and)\s*(' + number + r')', line))
            match = ranges[0] if ranges else None
            if match:
                minimum, maximum = (float(value) if '.' in value else int(value)
                                    for value in match.groups())
                if minimum > maximum:
                    continue
                value = {'minimum': minimum, 'maximum': maximum,
                         'kind': 'documented_numeric_domain'}
                if len(ranges) > 1:
                    value['additional_intervals'] = [
                        {'minimum': float(item.group(1)) if '.' in item.group(1) else int(item.group(1)),
                         'maximum': float(item.group(2)) if '.' in item.group(2) else int(item.group(2))}
                        for item in ranges[1:]]
            else:
                match = re.search(r'(?:nesting|nested)[^.]{0,40}?up to (\d+)', line, re.I)
                if match:
                    value = {'maximum': int(match.group(1)), 'subject': 'nesting_depth',
                             'kind': 'documented_structure_limit', 'unit': 'levels'}
                else:
                    match = re.search(r'[Uu]p to (\d+) (MPS|MRD|MPP|ANB|ORB|FOR|BREAK) instructions', line)
                    if not match:
                        continue
                    value = {'maximum': int(match.group(1)),
                             'subject': 'successive_' + match.group(2).lower() + '_count',
                             'kind': 'documented_structure_limit', 'unit': 'instructions'}
            # The source may constrain a field within a block rather than the
            # scalar address passed to the instruction. Offsets stay explicit.
            names = [name.upper() + ('+' + (offset1 or offset2) if offset1 or offset2 else '')
                     for name, offset1, offset2 in re.findall(
                         r'\(([sSdD]\d?)\)(?:\+(\d+)|\[(\d+)\])?', line)]
            simple = re.search(r'\b(?:as|for|to|of)\s+[\'\"]?(n\d?)[\'\"]?\b|'
                               r'^For (n\d?),|^(n\d?) is specified', line)
            if simple and not names:
                names = [next(part for part in simple.groups() if part)]
            names = list(dict.fromkeys(names))
            if names:
                value['subjects'] = names
                value['subject'] = ','.join(names)
            elif self.manual == 'sh080784engn' and re.search(r'input value', line):
                value['subjects'], value['subject'] = ['S'], 'S'
            value.setdefault('subject', 'unknown')
            domains = ((r'number of pins', 'pin_count', 'pins'),
                       (r'number of (?:connected|linked|separated|dissociated) bits', 'bit_count', 'bits'),
                       (r'number of (?:X and Y |coordinate )?(?:coordinates|points)', 'coordinate_count', 'points'),
                       (r'number of digits.*(?:fraction|decimal)', 'fractional_digits', 'digits'),
                       (r'total number of digits', 'total_digits', 'digits'),
                       (r'number of digits', 'digit_count', 'digits'),
                       (r'number of characters|string type data', 'string_length', 'characters'),
                       (r'drive number', 'drive_number', 'drives'),
                       (r'unit of measurement', 'timer_unit', 'ms'))
            for pattern, subject_kind, unit in domains:
                if re.search(pattern, line, re.I):
                    value.update(subject_kind=subject_kind, unit=unit)
                    if value['subject'] == 'unknown':
                        value['subject'] = subject_kind
                    break
            for pattern, unit in ((r'\bdegrees\b', 'degrees'), (r'\bbytes\b', 'bytes'),
                                  (r'\b\d+ms\b', 'ms'), (r'\bbits\b', 'bits')):
                if re.search(pattern, line):
                    value.setdefault('unit', unit)
                    break
            if re.search(r'\bBCD\b', line):
                value['representation'] = 'BCD'
            elif re.search(r'\bBIN\b', line):
                value['representation'] = 'BIN'
            width = re.search(r'(16|32|64)[- ]bit', line)
            if width:
                value['width_bits'] = int(width.group(1))
            qualifiers = []
            if re.search(r'constant', line, re.I):
                qualifiers.append({'kind': 'operand_form', 'value': 'constant'})
            modulo = re.search(r'remainder of n\s*/\s*(\d+)', line)
            if modulo:
                qualifiers.append({'kind': 'normalization', 'operation': 'modulo',
                                   'divisor': int(modulo.group(1)), 'operand': 'n'})
            if re.search(r'fractional part|decimal fraction|decimal point', line, re.I):
                if re.search(r'ignoring|excluding', line):
                    qualifiers.append({'kind': 'numeric_interpretation', 'value': 'ignore_decimal_point'})
                relation = ('<=' if re.search(r'smaller than or equal to|less than or equal to|'
                                               r'equal to or less than|not exceed', line) else
                            '<' if re.search(r'(?:smaller|less) than', line) else None)
                if relation:
                    difference = re.search(r'total number of digits minus (\d+)', line)
                    qualifiers.append({'kind': 'field_relation',
                                       'expression': 'fractional_digits' + relation + 'total_digits'
                                                     + ('-' + difference.group(1) if difference else '')})
            if re.search(r'not exceed the number of characters|range of the number of pins', line):
                qualifiers.append({'kind': 'dependent_bound',
                                   'maximum_from': 'string_length' if 'characters' in line else 'input_pin_count'})
            if re.search(r'depending on|vary depending|different.*CPU', line):
                qualifiers.append({'kind': 'cpu_dependent', 'value': 'requires_CPU_specific_table'})
            if re.search(r'same processing as n\s*=\s*1', line):
                qualifiers.append({'kind': 'normalization', 'operation': 'clamp_minimum',
                                   'minimum': 1, 'operand': 'n'})
            if re.search(r'rounding error', line):
                qualifiers.append({'kind': 'precision', 'value': 'rounding_error_possible'})
            if qualifiers:
                value['qualifications'] = qualifiers
            # Full witnesses remain private in the receipt; the published value
            # below retains every structured domain and condition.
            value['line'] = source_line
            found.append(value)
        return found

    # -- page binding ------------------------------------------------------
    def flat_rows(self):
        """Normalised chunk text, used to bind a witness phrase to a page."""
        return [(row['id'], int(row['pdf_page']) if row['pdf_page'] is not None else None,
                 re.sub(r'\s+', ' ', row['text'] or '')) for row in self.rows]

    def bind(self, witness):
        """Chunk ids and pages whose text contains this verified witness."""
        needle = re.sub(r'\s+', ' ', witness or '').strip()
        if len(needle) < 3:
            return []
        geometry = getattr(self, '_geometry_flat', [])
        key = (needle, id(geometry), len(geometry))
        cache = self.__dict__.setdefault('_binding_cache', {})
        if key in cache:
            return cache[key]
        if len(needle) >= 12:
            for length in (len(needle), 60, 40):
                probe = needle[:length]
                bound = [(chunk_id, page) for chunk_id, page, text in
                         self._flat + getattr(self, '_geometry_flat', []) if probe in text]
                if bound:
                    cache[key] = bound
                    return bound
        pattern = re.compile(r'(?<![A-Za-z0-9_])' + re.escape(needle) + r'(?![A-Za-z0-9_])')
        bound = [(chunk_id, page) for chunk_id, page, text in
                 self._flat + geometry if pattern.search(text)]
        cache[key] = bound
        return bound


def _fetch(source, sql, parameters=()):
    """Rows as plain dicts; the caller's connection row factory is left alone."""
    cursor = source.execute(sql, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _load_sections(source, manuals=None):
    """Every Q/L instruction heading, grouped by exact outline path."""
    sections, revisions = {}, {}
    for manual, spec in (manuals or QL_MANUALS).items():
        groups = {}
        for row in _fetch(source,
                          "SELECT id,text,outline_path,instruction_opcode,pdf_page,pdf_page_end,chunk_type,"
                          "revision,manual_number,source_applicability_json FROM chunks WHERE manual_id=? "
                          "ORDER BY pdf_page,id", (manual,)):
            revision = row['revision']
            if revision != spec['revision']:
                raise ValueError('Q/L behaviour review source revision changed: %s %r'
                                 % (manual, revision))
            revisions[manual] = revision
            if not row['outline_path']:
                continue
            groups.setdefault(row['outline_path'], []).append(row)
        for outline, rows in groups.items():
            section = Section(manual, spec['revision'], rows[0]['manual_number'], spec['kind'], outline, rows)
            section._flat = section.flat_rows()
            sections[(manual, outline)] = section
    return sections, revisions


# Families -----------------------------------------------------------------
# Keyed on the sub-chapter title, not the number: X and P shift chapter numbers
# (P has no 6.1 comparison and no 7.18 PID) but keep the same heading words.
FAMILY_HEADINGS = (
    ('Contact Instructions', 'contact'),
    ('Association Instructions', 'association'),
    ('Bond Instructions', 'association'),
    ('Output Instructions', 'output'),
    ('Shift Instructions', 'bit_shift'),
    ('Master Control Instructions', 'master_control'),
    ('Termination Instructions', 'termination'),
    ('End Instructions', 'termination'),
    ('Comparison Operation Instructions', 'comparison'),
    ('Arithmetic Operation Instructions', 'arithmetic'),
    ('Data Conversion Instructions', 'conversion'),
    ('Data Transfer Instructions', 'transfer'),
    ('Program Branch Instructions', 'branch'),
    ('Program Execution Control Instructions', 'exec_control'),
    ('I/O Refresh Instructions', 'io_refresh'),
    ('Other Convenient Instructions', 'convenient'),
    ('Logical Operation Instructions', 'logic'),
    ('Rotation Instructions', 'rotation'),
    ('Bit Processing Instructions', 'bit_processing'),
    ('Data Processing Instructions', 'data_processing'),
    ('Structure Creation Instructions', 'structure'),
    ('Structured Instructions', 'structure'),
    ('Data Table Operation Instructions', 'data_table'),
    ('Buffer Memory Access Instructions', 'buffer_memory'),
    ('Display Instructions', 'display'),
    ('Debugging and Failure Diagnosis Instructions', 'diagnosis'),
    ('Debug/Error Diagnostics Instructions', 'diagnosis'),
    ('Character String Processing Instructions', 'string'),
    ('String Processing Instructions', 'string'),
    ('Special Function Instructions', 'special_math'),
    ('Data Control Instructions', 'data_control'),
    ('File Register Switching Instructions', 'file_register'),
    ('Expansion Clock Instructions', 'clock'),
    ('Clock Instructions', 'clock'),
    ('Program Control Instructions', 'program_control'),
    ('PID Instruction', 'pid'),
    ('Network Refresh Instructions', 'data_link'),
    ('Reading/Registering Routing Information', 'data_link'),
    ('Refresh Device Write/Read Instruction', 'data_link'),
    ('MULTIPLE CPU', 'multiple_cpu'),
    ('REDUNDANT SYSTEM', 'redundant'),
    ('Type Conversion Functions', 'n_conversion'),
    ('Standard Functions of One Numeric Variable', 'n_numeric'),
    ('Standard Arithmetic Functions', 'n_arithmetic'),
    ('Standard Bitwise Boolean Functions', 'n_bitwise'),
    ('Standard Selection Functions', 'n_selection'),
    ('Standard Comparison Functions', 'n_comparison'),
    ('Standard Character String Functions', 'n_string'),
    ('Functions of Time Data Type', 'n_time'),
    ('Standard Bistable Function Blocks', 'n_fb_bistable'),
    ('Standard Edge Detection Function Blocks', 'n_fb_edge'),
    ('Counter Function Blocks', 'n_fb_counter'),
    ('Standard Timer Function Blocks', 'n_fb_timer'),
    ('Operator Tables', 'n_operator'),
    ('Arithmetic Operations', 'n_operator'),
    ('Analog Instruction', 'm_analog'),
    ('Positioning Instruction', 'm_positioning'),
    ('Serial Communication Instruction', 'm_serial'),
    ('Network Dedicated Instruction', 'm_network'),
    ('PID Control Instruction', 'm_pid'),
    ('I/O CONTROL INSTRUCTIONS', 'i_io'),
    ('CONTROL OPERATION INSTRUCTIONS', 'i_control'),
    ('COMPENSATION OPERATION INSTRUCTIONS', 'i_compensation'),
    ('ARITHMETIC OPERATION INSTRUCTIONS', 'i_arithmetic'),
    ('COMPARISON OPERATION INSTRUCTIONS', 'i_comparison'),
    ('AUTO TUNING', 'i_tuning'),
)

_SEQUENCE_OTHER = re.compile(r'\b5\.7\b|SEQUENCE INSTRUCTIONS > 5\.7')
_APPLICATION_OTHER = re.compile(r'7\.1[89]|7\.2\d')


def family_of(section):
    """Authored family key for a documented heading, or ``None``."""
    heading = section.chapter
    for pattern, key in FAMILY_HEADINGS:
        if pattern in heading:
            if key == 'clock' and section.title.startswith('Expansion'):
                return 'clock'
            if key == 'data_processing' and 'sort' in section.title.lower():
                return 'data_processing'
            return key
    if 'Other Instructions' in heading:
        return 'program_stop' if _SEQUENCE_OTHER.search(heading) else 'application_other'
    return None


# Statement classifiers ----------------------------------------------------
# Ordered ladder: the first match is the documented primary effect of the
# symbol. Each entry is (class, dimension, operation token, compiled pattern).
# The token is an authored restatement of the documented verb, never a heading
# guess: the pattern must match inside the symbol's own bullet statements.
_OPERATION_CLASSES = (
    ('block_transfer', 'effects.transfer', 'block_transfer',
     re.compile(r'\bBatch transfers?\b|\btransfers? n points\b|\bBatch (?:writes|reads)\b')),
    ('transfer', 'effects.transfer', 'transfer',
     re.compile(r'\bTransfers?\b')),
    ('exchange', 'effects.transfer', 'exchange',
     re.compile(r'\bExchanges\b|\bSwap(?:s|ped)? the\b')),
    ('conversion', 'effects.conversion', 'convert',
     re.compile(r'\bConverts?\b')),
    ('encode', 'effects.conversion', 'encode', re.compile(r'\bEncodes?\b')),
    ('decode', 'effects.conversion', 'decode', re.compile(r'\bDecodes?\b')),
    ('rotation', 'effects.rotation', 'rotate', re.compile(r'\bRotates?\b')),
    ('shift', 'effects.shift', 'shift',
     re.compile(r'\bShifts?\b|[Cc]auses a shift|is shifted')),
    ('arithmetic', 'effects.arithmetic', 'arithmetic',
     re.compile(r'\b(?:Adds|Subtracts|Multiplies|Divides|increments|decrements|'
                r'stores the sum|stores the difference|stores the product|stores the quotient|'
                r'Performs (?:an )?(?:addition|subtraction|multiplication|division))\b')),
    ('scaling', 'effects.arithmetic', 'scale',
     re.compile(r'\bExecutes scaling\b|\bscaling conversion\b|\bPerforms scaling\b')),
    ('comparison', 'effects.comparison', 'compare',
     re.compile(r'\bcompar(?:e|es|ison)\b', re.I)),
    ('band_comparison', 'effects.comparison', 'band_compare',
     re.compile(r'\bband comparison\b', re.I)),
    ('limit', 'effects.data_control', 'apply_limits',
     re.compile(r'\blower and upper limit values?\b|\bupper and lower limit\b|'
                r'\bapplies the (?:lower and upper )?limits?\b')),
    ('zone', 'effects.data_control', 'zone_output',
     re.compile(r'\bthree zones\b|\bzone (?:control|operation)\b', re.I)),
    ('logic', 'effects.logic', 'logic',
     re.compile(r'logical (?:product|sum)|exclusive (?:OR|NOR)|\bAND operation\b|\bOR operation\b|'
                r'\bNOT operation\b')),
    ('bit', 'effects.bit_processing', 'bit_operation',
     re.compile(r'\bnth bit of\b|\bbit of the (?:word )?device\b|'
                r'\b(?:Sets|Resets|Fetches|Tests|Inverts)\b[^.]{0,80}\bbits?\b|'
                r'Batch (?:sets|resets)\b')),
    ('search', 'effects.data_search', 'search', re.compile(r'\bSearches\b|\bsearch\b')),
    ('sort', 'effects.data_processing', 'sort', re.compile(r'\bSorts\b')),
    ('checksum', 'effects.data_processing', 'checksum',
     re.compile(r'\bcheck code\b|\bCRC code\b|\bCCD\b|\bBCC\b')),
    ('aggregate', 'effects.data_processing', 'aggregate',
     re.compile(r'\bmaximum value\b|\bminimum value\b|\btotal number of bits\b|\baverage\b|'
                r'\bSUM\b|\bMEAN\b')),
    ('dissociate', 'effects.data_processing', 'dissociate',
     re.compile(r'\bDissociat\w+')),
    ('decode7seg', 'effects.data_processing', 'seven_segment_decode',
     re.compile(r'7-segment', re.I)),
    ('math', 'effects.math_function', 'math_evaluation',
     re.compile(r'\bReturns the [A-Z]+ value\b|\bsquare root\b|\bBIN floating-point\b|'
                r'\b(?:SIN|COS|TAN|ASIN|ACOS|ATAN|EXP|LOG10|LOG|SQR|SQRT|POW|RND|SRND|DEG|RAD'
                r'|BSIN|BACOS|BATAN|BSQR|BDSQR)\b')),
    ('string', 'effects.string', 'string_operation',
     re.compile(r'\b(?:character string|string data|ASCII code|BIN data as a character string)\b')),
    ('clock', 'effects.clock', 'clock_access',
     re.compile(r'\bclock element\b|\byear, month, day\b|\bday of week\b')),
    ('buffer_read', 'effects.buffer_memory', 'read_buffer_memory',
     re.compile(r'Reads the data[^.]{0,80}buffer memory|Reads[^.]{0,40}from the buffer memory')),
    ('buffer_write', 'effects.buffer_memory', 'write_buffer_memory',
     re.compile(r'Writes the data[^.]{0,80}buffer memory|Writes[^.]{0,60}to the buffer memory')),
    ('table_write', 'effects.data_table', 'write_data_table',
     re.compile(r'\bdata table\b')),
    ('display', 'effects.display', 'display_output',
     re.compile(r'\bLED\b|\bdisplay unit\b|\bcharacter display\b')),
    ('diagnosis', 'effects.diagnosis', 'diagnostic_check',
     re.compile(r'\bdiagnos\w*\b|\bcheck(?:s|ing)? the\b')),
    ('io_refresh', 'effects.io_refresh', 'refresh_io',
     re.compile(r'\bRefreshes\b|\brefresh (?:the )?(?:device|input|output)\b')),
    ('file_register', 'effects.file_register', 'file_register_switch',
     re.compile(r'\bfile register\b')),
    ('register', 'effects.module_instruction', 'register_setting',
     re.compile(r'\bRegisters\b|\bregistered\b')),
    ('module_io', 'effects.module_instruction', 'module_access',
     re.compile(r'\bbuffer memory address\b|\bintelligent (?:function )?module\b|'
                r'\bintelligent device station\b')),
    ('pid', 'effects.control', 'pid_control',
     re.compile(r'\bPID (?:operation|control)\b|\bmanipulated value\b')),
    ('loop_control', 'effects.control', 'loop_control',
     re.compile(r'\bcontrol loop\b|\bprocess value\b|\bset value \(SV\)\b')),
    ('execute', 'effects.operation', 'execute_program',
     re.compile(r'\bExecutes? the (?:subroutine|sequence|program|control)')),
    ('write', 'effects.operation', 'write',
     re.compile(r'\bWrites?\b|\bStores?\b')),
    ('read', 'effects.operation', 'read',
     re.compile(r'\bReads?\b|\bFetches\b|\bLoads?\b')),
    ('set_reset', 'effects.state_change', 'set_or_reset',
     re.compile(r'\bSets? the device\b|\bResets? the device\b|\bClears? the device\b|'
                r'\b(?:turns?|goes?) (?:the )?device (?:ON|OFF)\b|\bBatch (?:sets|resets) the\b')),
)

_ARITHMETIC_VERBS = (
    ('add', re.compile(r'\bAdds?\b|\badds\b')),
    ('subtract', re.compile(r'\bSubtracts?\b|\bsubtracts\b')),
    ('multiply', re.compile(r'\bMultiplies\b|\bmultiplies\b')),
    ('divide', re.compile(r'\bDivides\b|\bdivides\b')),
    ('increment', re.compile(r'\bincrements\b|\bAdds 1\b')),
    ('decrement', re.compile(r'\bdecrements\b|\bSubtracts 1\b')),
)

_LOGIC_FORMS = (
    ('and', re.compile(r'logical product|\bAND operation\b')),
    ('or', re.compile(r'logical sum|\bOR operation\b')),
    ('xor', re.compile(r'exclusive OR')),
    ('xnor', re.compile(r'exclusive NOR')),
    ('not', re.compile(r'\bNOT\b|inverts? the (?:operation result|data)')),
)

_WIDTH = re.compile(r'\b(16|32|64)[- ]?bit\b')
_DOMAIN = (
    ('bcd', re.compile(r'\bBCD\b')),
    ('string', re.compile(r'character string|\bstring\b|\bASCII\b')),
    ('real', re.compile(r'floating[- ]point|single[- ]precision|double[- ]precision|\breal\b', re.I)),
    ('bin', re.compile(r'\bBIN\b')),
)


def _fields(blob):
    """Typed fields documented by the statement itself, not by the heading."""
    fields = {}
    width = _WIDTH.search(blob)
    if width:
        fields['width_bits'] = int(width.group(1))
    for name, pattern in _DOMAIN:
        if pattern.search(blob):
            fields['data_domain'] = name
            break
    direction = re.search(r'\b(?:to|by) (?:the )?(left|right)\b|\b(right|left) shift\b', blob, re.I)
    if direction:
        fields['direction'] = (direction.group(1) or direction.group(2)).lower()
    if re.search(r'not including the carry flag', blob):
        fields['carry_flag'] = 'excluded'
    elif re.search(r'including the carry flag', blob):
        fields['carry_flag'] = 'included'
    if re.search(r'\bn (?:points|words|blocks)\b', blob):
        fields['repetition_operand'] = 'n'
    return fields


# Authored rule helpers ----------------------------------------------------
def _spec(key, dimension, patterns, value, languages=(), conditions=(), model_filter=None,
          note=None):
    return {'key': key, 'dimension': dimension, 'patterns': tuple(patterns),
            'value': dict(value), 'languages': list(languages), 'conditions': list(conditions),
            'note': note,
            'model_filter': model_filter}


def _match(blob, symbol, pattern):
    """The witness text of ``pattern`` for ``symbol`` inside the block prose."""
    try:
        found = re.search(pattern.replace('{sym}', re.escape(symbol)), blob)
    except re.error:                                    # pragma: no cover - authoring guard
        raise ValueError('Q/L behaviour rule is not a valid pattern: %r' % pattern)
    return found.group(0) if found else None


def _first(blob, symbol, patterns):
    for pattern in patterns:
        matched = _match(blob, symbol, pattern)
        if matched:
            return matched
    return None


CONNECTION = {'operation start': 'bus_start', 'series connection': 'series',
              'single parallel connection': 'parallel', 'parallel connection': 'parallel',
              'edge series connection': 'series', 'edge parallel connection': 'parallel'}

#: Output instructions whose documented effect is a device write with a stated
#: retention rule. The value keeps the documented state transition, and the
#: retention sentence must be present in the same block.
OUTPUT_STATE = {
    'OUT': {'operation': 'write_operation_result', 'retention': 'follows_operation_result',
            'documented_state_rule': 'device_reflects_bus_state_each_execution'},
    'SET': {'operation': 'set_device', 'retention': 'retained_until_reset',
            'documented_state_rule': 'on_command_off_state_unchanged'},
    'RST': {'operation': 'reset_device', 'retention': 'cleared_until_set',
            'documented_state_rule': 'on_command_off_state_unchanged'},
    'PLS': {'operation': 'pulse_device', 'edge': 'rising', 'pulse_scan_count': 1,
            'documented_state_rule': 'on_for_one_scan_at_rising_edge'},
    'PLF': {'operation': 'pulse_device', 'edge': 'falling', 'pulse_scan_count': 1,
            'documented_state_rule': 'on_for_one_scan_at_falling_edge'},
    'FF': {'operation': 'invert_device', 'edge': 'rising',
           'documented_state_rule': 'output_status_reversed_at_rising_edge'},
    'DELTA': {'operation': 'pulse_direct_output', 'target': 'direct_access_output',
              'documented_state_rule': 'one_scan_pulse_on_direct_output'},
}

OUTPUT_WITNESS = {
    'OUT': (r'Operation results up to the OUT instruction are output to the designated device',),
    'SET': (r'Devices turned ON by the instruction remain ON when the same command is turned OFF',),
    'RST': (r'When the execution command is OFF, the status of devices does not change',),
    'PLS': (r'Turns ON the designated device when the execution command is turned ON',
            r'the specified device turns ON one scan'),
    'PLF': (r'Turns ON the designated device when the execution command is turned OFF',
            r'the specified device turns ON one scan'),
    'FF': (r'Reverses the output status of the device designated by \(D\)',),
    'DELTA': (r'Conducts pulse output of direct access output \(DY\)',),
}

ASSOCIATION = {
    'ANB': {'operation': 'block_and', 'operand_form': 'ladder_block',
            'result_composition': 'and_with_previous_block'},
    'ORB': {'operation': 'block_or', 'operand_form': 'ladder_block',
            'result_composition': 'or_with_previous_block'},
    'MPS': {'operation': 'stack_push', 'stack_role': 'store_operation_result'},
    'MRD': {'operation': 'stack_read', 'stack_role': 'read_stored_operation_result'},
    'MPP': {'operation': 'stack_pop', 'stack_role': 'clear_stored_operation_result'},
    'INV': {'operation': 'result_inversion', 'scope': 'operation_result_so_far'},
    'MEP': {'operation': 'pulse_conversion', 'edge': 'rising', 'output_state': 'on_for_one_scan'},
    'MEF': {'operation': 'pulse_conversion', 'edge': 'falling', 'output_state': 'on_for_one_scan'},
    'EGP': {'operation': 'edge_relay_pulse_conversion', 'edge': 'rising',
            'edge_relay': 'V', 'output_state': 'on_for_one_scan'},
    'EGF': {'operation': 'edge_relay_pulse_conversion', 'edge': 'falling',
            'edge_relay': 'V', 'output_state': 'on_for_one_scan'},
}

ASSOCIATION_WITNESS = {
    'ANB': (r'Performs an AND operation on block A and block B',),
    'ORB': (r'Conducts an OR operation on Block A and Block B',),
    'MPS': (r'Stores the memory of the operation result \(ON or OFF\) immediately prior to the MPS instruction',),
    'MRD': (r'Reads the operation result stored for the MPS instruction',),
    'MPP': (r'Clears the operation results stored by the MPS instruction',),
    'INV': (r'Inverts the operation result immediately prior to the INV instruction',),
    'MEP': (r'If operation results up to the MEP instruction are rising edge',),
    'MEF': (r'If operation results up to the MEF instruction are falling edge',),
    'EGP': (r'Operation results up to the EGP instruction are stored in memory by the edge relay',),
    'EGF': (r'Operation results up to the EGF instruction are stored in memory by the edge relay',),
}

#: Documented multi-output result order of the comparison instructions.
COMPARISON_WITNESS = (
    r'One of \(D\), \(D\)\+1, and \(D\)\+2 turns on depending on the results',
    r'turns on depending on the results',
)

TERMINATION = {
    'FEND': {'operation': 'terminate_main_routine', 'program_terminator': True,
             'scope': 'main_routine_program'},
    'END': {'operation': 'terminate_program_and_refresh', 'program_terminator': True,
            'scope': 'any_program', 'io_refresh_at_execution': True},
}

# Type names used by the application-function manual, as authored tokens.
TYPE_ALIASES = (
    ('single-precision real', 'REAL'),
    ('double-precision real', 'LREAL'),
    ('double word (signed)', 'DINT'),
    ('word (signed)', 'INT'),
    ('double word (unsigned)', 'DWORD'),
    ('word (unsigned)', 'WORD'),
    ('16-bit string', 'WORD'),
    ('32-bit string', 'DWORD'),
    ('bit', 'BOOL'),
    ('string', 'STRING'),
    ('time', 'TIME'),
    ('BCD', 'BCD'),
)


def _type_token(description):
    lowered = re.sub(r'\s+', ' ', description or '').strip().lower()
    for name, token in TYPE_ALIASES:
        if lowered.startswith(name):
            return token
    for name, token in TYPE_ALIASES:
        if name in lowered:
            return token
    return ''


def _search(blob, symbol, pattern):
    return re.search(pattern.replace('{sym}', re.escape(symbol)), blob)


def _witness(blob, patterns, symbol=''):
    blob = ' '.join(blob.split())
    for pattern in patterns:
        found = _search(blob, symbol, pattern) if '{sym}' in pattern else re.search(pattern, blob)
        if found:
            return found.group(0)
    return None


def _contact_specs(section, symbol):
    # Contact descriptions name each symbol explicitly; uppercase ON/OFF
    # continuations are part of that prose, not a second operation block.
    blob = ' '.join(section.text.split())
    specs = []
    forms = (
        (r'{sym} is the A contact (?P<conn>operation start|series connection|single parallel connection) instruction',
         'normally_open'),
        (r'{sym} is the B contact (?P<conn>operation start|series connection|single parallel connection) instruction',
         'normally_closed'),
        (r'{sym} is the normally open (?P<conn>operation start|series connection|single parallel connection) instruction',
         'normally_open'),
        (r'{sym} is the normally closed (?P<conn>operation start|series connection|single parallel connection) instruction',
         'normally_closed'),
    )
    edges = (
        (r'{sym} is the (?P<edge>rising|falling) edge pulse NOT '
         r'(?P<conn>operation start|series connection|parallel connection)', 'input_inverted'),
        (r'{sym} is a (?P<edge>rising|falling) (?:edge )?pulse NOT '
         r'(?P<conn>series connection|parallel connection)', 'input_inverted'),
        (r'{sym} is the (?P<edge>rising|falling) edge pulse '
         r'(?P<conn>operation start|series connection|parallel connection)', 'none'),
        (r'{sym} is a (?P<edge>rising|falling) (?:edge )?pulse '
         r'(?P<conn>series connection|parallel connection)', 'none'),
    )
    for pattern, contact in forms:
        found = _search(blob, symbol, pattern)
        if not found:
            continue
        connection = CONNECTION.get(found.group('conn'), 'unknown')
        value = {'contact_form': contact, 'connection': connection,
                 'operation_condition': 'continuous_not_edge', 'device_operand': 'bit',
                 'documented_role': 'contact'}
        specs.append(_spec('role', 'parameters.connection_role', (found.group(0),), value))
        combination = {'bus_start': 'assign', 'series': 'and', 'parallel': 'or'}.get(connection)
        guard = {'assign': r'use that as an operation result',
                 'and': r'perform an AND operation on that data and the operation result',
                 'or': r'perform an OR operation with the operation results'}.get(combination)
        guard_witness = _witness(blob, (guard,)) if guard else None
        if guard_witness:
            specs.append(_spec('result', 'effects.result_composition',
                               (found.group(0), guard_witness),
                               {'operation': 'compose_contact_result',
                                'result_type': 'BOOL', 'combination': combination,
                                'operand_role': 'contact_read', 'negation': contact}))
        return specs
    for pattern, negation in edges:
        found = _search(blob, symbol, pattern)
        if not found:
            continue
        value = {'contact_form': 'normally_open' if negation == 'none' else 'normally_closed',
                 'connection': CONNECTION.get(found.group('conn'), 'unknown'),
                 'edge': found.group('edge'), 'operation_condition': 'single_scan_edge',
                 'device_operand': 'bit', 'documented_role': 'contact'}
        if negation == 'input_inverted':
            value['negation'] = 'input_inverted'
        specs.append(_spec('role', 'parameters.connection_role', (found.group(0),), value))
        single = _witness(blob, (r'is ON only at the (?:rising|falling) edge',))
        composition = {'operation': 'compose_edge_contact_result', 'result_type': 'BOOL',
                       'combination': CONNECTION.get(found.group('conn'), 'unknown'),
                       'edge': found.group('edge'), 'negation': negation}
        if single:
            # The edge sentence documents the contact state only. Whether a
            # repeated call in the same scan is suppressed is NOT stated here,
            # so no execution.same_scan_repeat fact is published from it.
            composition['active_state'] = 'on_at_documented_edge_only'
            specs.append(_spec('result', 'effects.result_composition', (found.group(0), single),
                               composition))
        else:
            specs.append(_spec('result', 'effects.result_composition', (found.group(0),),
                               composition))
        return specs
    generic = _witness(blob, (r'{sym} is the (?:A|B) contact [a-z ]*instruction',
                              r'{sym} is the normally (?:open|closed) [a-z ]*instruction'), symbol)
    if generic:
        specs.append(_spec('role', 'parameters.connection_role', (generic,),
                           {'documented_role': 'contact', 'device_operand': 'bit'}))
    return specs


def _association_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    value = ASSOCIATION.get(symbol)
    witness = _witness(blob, ASSOCIATION_WITNESS.get(symbol, ()), symbol)
    if not value or not witness:
        return []
    dimension = 'memory.stack' if symbol in {'MPS', 'MRD', 'MPP'} else 'effects.operation'
    specs = [_spec('effect', dimension, (witness,), dict(value))]
    if symbol in {'MPS', 'MRD', 'MPP'}:
        depth = _witness(blob, (r'Up to \d+ MPS instructions can be used successively',))
        if depth:
            limit = re.search(r'Up to (\d+) MPS', depth)
            specs.append(_spec('limit', 'constraints.source_qualifications', (depth,),
                               {'qualifications': [{'subject': 'stack_depth',
                                   'maximum': int(limit.group(1)), 'unit': 'levels',
                                   'parameter_binding': 'unknown',
                                   'role': 'program_structure_limit'}], 'subjects': ['stack_depth']}))
    return specs


def _output_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    base = symbol[:-1] if symbol.endswith('P') and symbol[:-1] in OUTPUT_STATE else symbol
    value = OUTPUT_STATE.get(base) or OUTPUT_STATE.get(symbol)
    witness = _witness(blob, tuple(OUTPUT_WITNESS.get(base, ())) + tuple(OUTPUT_WITNESS.get(symbol, ())), symbol)
    if not value or not witness:
        return []
    specs = [_spec('effect', 'effects.state_change', (witness,), dict(value))]
    if base == 'OUT':
        timer = _witness(blob, (r'the timer coil goes ON and the timer counts up to the value',))
        if timer:
            specs.append(_spec('timer', 'effects.timer_operation', (timer,),
                               {'operation': 'count_up_on_coil_on', 'contact_on_at_set_value': True}))
        counter = _witness(blob, (r'1 is added to the present value \(count value\)',))
        if counter:
            specs.append(_spec('counter', 'effects.counter_operation', (counter,),
                               {'operation': 'count_up_on_rising_edge',
                                'count_on_rising_edge_only': True, 'reset_by_rst': True}))
    if base in {'SET', 'RST'}:
        hold = _witness(blob, (r'When the execution command is OFF, the status of devices does not change',))
        if hold:
            specs.append(_spec('retention', 'effects.retention', (hold,),
                               {'retention': 'state_held_while_command_off'}))
    return specs


def _termination_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    value = TERMINATION.get(symbol)
    witness = _witness(blob, (r'Execution of the (?:END|FEND) instruction will cause the CPU module to terminate '
                              r'the program', r'Indicates termination of programs'), symbol)
    if not value or not witness:
        return []
    return [_spec('effect', 'execution.program_terminator', (witness,), dict(value))]


def _comparison_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    witness = _witness(blob, COMPARISON_WITNESS)
    if not witness:
        return []
    outputs = []
    for match in re.finditer(r'turns on when \(?([SD][12]?)\)? ?(>|=|<) ?\(?([SD][12]?)\)?', blob):
        outputs.append({'condition': '%s_%s_%s' % (match.group(1), match.group(2), match.group(3)),
                        'output': {'>': 'D', '=': 'D+1', '<': 'D+2'}[match.group(2)]})
    value = {'operation': 'compare', 'result_device_type': 'bit',
             'result_count': len(outputs) or 3}
    if outputs:
        value['result_outputs'] = outputs
    hold = _witness(blob, (r'hold the conditions immediately before the contact turns on and off',))
    if hold:
        value['inactive_result'] = 'holds_previous_result'
    return [_spec('effect', 'effects.comparison', (witness,), value)]


def _generic_specs(section, symbol, family):
    """Family-rule effect taken from the first documented operation statement.

    Statements are read in the documented order, so a later precaution sentence
    can never become the primary effect of the symbol.
    """
    statements = [statement for statement in section.statements(symbol)
                  if not re.match(r'^(?:Do not|Do not use)', statement)]
    if not statements:
        return []
    function, precision = _math_function(symbol)
    classes = list(getattr(section, 'ladder', None) or _OPERATION_CLASSES)
    if function:
        classes = [item for item in classes
                   if item[0] == 'math' or not item[0].startswith(('obtain', 'write', 'read'))]
    for statement in statements:
        for name, dimension, token, pattern in classes:
            found = pattern.search(statement)
            if not found:
                continue
            # Two documented operations for the same dimension mean the block
            # describes more than this form (INC beside DEC): never pick one.
            others = {other_token for other_statement in statements
                      if other_statement is not statement
                      for other_name, other_dimension, other_token, other_pattern
                      in classes
                      if other_dimension == dimension and other_pattern.search(other_statement)
                      and other_token != token}
            if others:
                return []
            value = {'operation_class': name, 'operation': token,
                     'basis': 'documented_statement'}
            if name == 'arithmetic':
                delta = _signed_delta(statement)
                verb = next((item for item, table in _ARITHMETIC_VERBS
                             if table.search(statement)), '')
                if re.search(r'\((d|D)\d?\)', statement):
                    value['result'] = 'stored_in_d'
                if delta is not None:
                    # "adds -1" and "subtracts 1" are one documented effect.
                    value['operation'] = 'add_delta'
                    value['delta_sign'], value['delta_magnitude'] = delta
                elif verb:
                    value['operation'] = verb
            elif name == 'logic':
                form = next((item for item, table in _LOGIC_FORMS if table.search(statement)), '')
                if form:
                    value['operation'] = form
            elif name == 'math':
                function, precision = _math_function(symbol)
                if function is None:
                    function = next((item for keyword, item in TITLE_TOKENS
                                     if keyword in section.title.lower()), None)
                if function:
                    value['operation'] = 'evaluate_' + function.lower()
                    value['function'] = function
                    value['object'] = MATH_TOKENS.get(function, 'numeric_value')
                    if precision != 'unknown':
                        value['precision'] = precision
                    if re.search(r'\((d|D)\d?\)', statement):
                        value['result'] = 'stored_in_d'
                else:
                    continue
            elif name in {'conversion', 'encode', 'decode'}:
                pair = re.search(r'Converts? ([a-z0-9\-() /]+?) data (?:input )?to \(([sd])\) into '
                                 r'([a-z0-9\-() /]+?) (?:type )?data', statement)
                if pair:
                    from_type, to_type = _type_token(pair.group(1)), _type_token(pair.group(3))
                    if from_type:
                        value['from_type'] = from_type
                    if to_type:
                        value['to_type'] = to_type
                    value['input_operand'] = pair.group(2)
                else:
                    source_type = re.search(r'from ([a-z0-9\-() /]+?) (?:type )?data', statement)
                    target_type = re.search(r'into ([a-z0-9\-() /]+?) (?:type )?data', statement)
                    if source_type and _type_token(source_type.group(1)):
                        value['from_type'] = _type_token(source_type.group(1))
                    if target_type and _type_token(target_type.group(1)):
                        value['to_type'] = _type_token(target_type.group(1))
                if 'from_type' not in value or 'to_type' not in value:
                    value['converted_types'] = 'unknown'
            elif name == 'math':
                function = re.search(r'\b(SIN|COS|TAN|ASIN|ACOS|ATAN|SINH|COSH|TANH|EXP|LOG|LOG10|'
                                     r'SQR|SQRT|RAD|DEG|BIN|BCD)\b', statement)
                if function:
                    value['function'] = function.group(1)
            value.update(_fields(statement))
            operands = re.findall(r'\((S1|S2|S3|D1|D2|S|D|N)\)', statement)
            if operands:
                value['documented_operands'] = list(dict.fromkeys(operands))
            return [_spec('operation', dimension, (found.group(0),),
                          {key: item for key, item in value.items() if item})]
    return []


def _branch_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    specs = []
    when_on = _witness(blob, (r'within the same program file, when the execution command is ON',))
    when_off = _witness(blob, (r'When the execution command is OFF, the program at the next step is executed',))
    if when_on:
        value = {'operation': 'jump_to_pointer', 'condition': 'execution_command_on',
                 'target_scope': 'same_program_file'}
        if when_off:
            value['inactive_behavior'] = 'next_step_executed'
            specs.append(_spec('effect', 'execution.jump', (when_on, when_off), value))
        else:
            specs.append(_spec('effect', 'execution.jump', (when_on,), value))
    else:
        unconditional = _witness(blob, (r'Unconditionally executes program of designated pointer number',))
        if unconditional:
            specs.append(_spec('effect', 'execution.jump', (unconditional,),
                               {'operation': 'jump_to_pointer', 'condition': 'unconditional',
                                'target_scope': 'same_program_file'}))
    same_scan = _witness(blob, (r'starting with the scan immediately after',))
    if same_scan:
        specs.append(_spec('scan', 'execution.same_scan_repeat', (same_scan,),
                           {'jump_takes_effect': 'scan_after_execution'}))
    return specs


def _exec_control_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    specs = []
    disable = _witness(blob, (r'Disables the execution of an interrupt program until the EI instruction has been '
                              r'executed',))
    if disable:
        specs.append(_spec('effect', 'constraints.context', (disable,),
                           {'interrupt_execution': 'disabled', 'cleared_by': 'EI',
                            'context': 'interrupt_pointer_programs'}))
        first = _witness(blob, (r'A DI state is entered when power is turned ON or when the CPU module is reset',))
        if first:
            specs.append(_spec('initial', 'execution.first_execution', (first,),
                               {'initial_state': 'interrupt_disabled_at_power_on_or_reset'}))
    mask = _witness(blob, (r'Enables/disables the execution of the interrupt program marked by the designated '
                           r'interrupt pointer',))
    if mask:
        specs.append(_spec('mask', 'constraints.context', (mask,),
                           {'interrupt_execution': 'masked_by_bit_pattern', 'mask_operand': 'S',
                            'context': 'interrupt_pointer_programs'}))
    enable = _witness(blob, (r'clear the interrupt disable state',))
    if enable:
        specs.append(_spec('enable', 'constraints.context', (enable,),
                           {'interrupt_execution': 'enabled_by_documented_pointer_set'}))
    return specs


def _structure_specs(section, symbol):
    blob = ' '.join(section.statements(symbol))
    specs = []
    repeat = _witness(blob, (r'Executes the program between the FOR and NEXT instructions',
                             r'When the processing in the FOR to NEXT loop is executed n-times',
                             r'FOR to NEXT loop'))
    if repeat:
        specs.append(_spec('effect', 'effects.operation', (repeat,),
                           {'operation': 'repeat_execution', 'repetition_operand': 'n',
                            'body': 'between_FOR_and_NEXT'}))
    nesting = _witness(blob, (r'up to \d+ (?:nested|deep)', r'nested up to \d+'))
    if nesting:
        limit = re.search(r'(\d+)', nesting)
        specs.append(_spec('limit', 'constraints.source_qualifications', (nesting,),
                           {'qualifications': [{'subject': 'nesting_depth',
                               'maximum': int(limit.group(1)), 'unit': 'levels',
                               'parameter_binding': 'unknown',
                               'role': 'program_structure_limit'}], 'subjects': ['nesting_depth']}))
    call = _witness(blob, (r'[Ee]xecutes the subroutine program',))
    if call:
        specs.append(_spec('call', 'execution.jump', (call,),
                           {'operation': 'call_subroutine', 'target_scope': 'same_program_file'}))
    return specs


def _master_control_specs(section, symbol):
    """MC/MCR: the documented scope of the master control relay."""
    blob = ' '.join(section.statements(symbol))
    specs = []
    start = _witness(blob, (r'the result of the operation from the MC instruction to the MCR instruction',
                            r'from the MC instruction to the MCR instruction'))
    if symbol.startswith('MC') and start:
        value = {'operation': 'master_control_start', 'scope': 'mc_to_mcr',
                 'nesting_operand': 'N', 'documented_effect': 'operation_between_mc_and_mcr'}
        specs.append(_spec('effect', 'effects.state_change', (start,), value))
    reset = _witness(blob, (r'[Rr]esets? the master control', r'master control is reset',
                            r'recovery from the master control',
                            r'end of the master control range of operation'))
    if symbol.startswith('MCR') and reset:
        specs.append(_spec('effect', 'effects.state_change', (reset,),
                           {'operation': 'master_control_reset', 'scope': 'mc_to_mcr'}))
    off = _witness(blob, (r'Even when the MC instruction is OFF, instructions from the MC instruction to the '
                          r'MCR instruction will be executed',))
    if off:
        specs.append(_spec('disabled', 'execution.operation_condition', (off,),
                           {'condition': 'instructions_executed_while_master_control_off',
                            'scan_time_shortened': False}))
    return specs


def _exec_state_specs(section, symbol):
    """DI/EI/IMASK: the interrupt execution state they establish."""
    blob = ' '.join(section.statements(symbol))
    specs = []
    if re.match(r'^DI', symbol):
        disabled = _witness(blob, (r'[Dd]isables the execution of an interrupt program',))
        if disabled:
            specs.append(_spec('effect', 'effects.state_change', (disabled,),
                               {'interrupt_execution': 'disabled', 'cleared_by': 'EI',
                                'context': 'interrupt_pointer_programs'}))
    elif re.match(r'^IMASK', symbol):
        masked = _witness(blob, (r'Enables/disables the execution of the interrupt program marked by the '
                                 r'designated interrupt pointer',))
        if masked:
            specs.append(_spec('effect', 'effects.state_change', (masked,),
                               {'interrupt_execution': 'masked_by_bit_pattern',
                                'mask_operand': 'S', 'context': 'interrupt_pointer_programs'}))
    elif re.match(r'^EI', symbol):
        enabled = _witness(blob, (r'[Ee]nables the execution of an interrupt program',
                                  r'clear the interrupt disable state',))
        if enabled:
            specs.append(_spec('effect', 'effects.state_change', (enabled,),
                               {'interrupt_execution': 'enabled', 'context': 'interrupt_pointer_programs'}))
    return specs


def _state_from_prose(section, symbol):
    """Retention / repeat / prohibition statements, each with its own witness."""
    blob = ' '.join(section.statements(symbol))
    specs = []
    hold = _witness(blob, (r'hold the conditions immediately before', r'are retained',
                           r'remain ON when the same command is turned OFF'))
    if hold:
        specs.append(_spec('state', 'effects.state_change', (hold,),
                           {'retention': 'documented_hold_of_previous_state'}))
    repeat = _witness(blob, (r'turns ON one scan', r'ON for only one scan', r'ON for one scan'))
    if repeat:
        specs.append(_spec('scan', 'execution.same_scan_repeat', (repeat,),
                           {'repeat_within_scan': False, 'active_scan_count': 1}))
    forbid = _witness(blob, (r'Do not execute the instruction in an interrupt program',
                             r'Do not execute this instruction in an interrupt program'))
    if forbid:
        specs.append(_spec('forbid', 'constraints.context', (forbid,),
                           {'context': 'interrupt_program', 'usage': 'prohibited_by_source'}))
    return specs


FAMILY_BUILDERS = {
    'contact': _contact_specs,
    'association': _association_specs,
    'output': _output_specs,
    'termination': _termination_specs,
    'comparison': _comparison_specs,
    'branch': _branch_specs,
    'exec_control': lambda section, symbol: (_exec_control_specs(section, symbol)
                                             + _exec_state_specs(section, symbol)),
    'master_control': _master_control_specs,
    'structure': _structure_specs,
}


def family_specs(section, symbol):
    """Family-rule fact specs for one declared symbol of one section."""
    family = family_of(section)
    builder = FAMILY_BUILDERS.get(family)
    specs = _symbol_family_specs(section, symbol)
    if builder is not None and not specs:
        specs = list(builder(section, symbol))
    if not specs:
        specs = _generic_specs(section, symbol, family) or _table_effect(section, symbol)
    elif not any(spec['dimension'].startswith('effects.') for spec in specs):
        specs.extend(_table_effect(section, symbol))
        # A family rule that only states the structure is not the behaviour:
        # the documented operation statement must also be published.
        specs.extend(_generic_specs(section, symbol, family))
    if family != 'contact':
        specs = specs + _state_from_prose(section, symbol)
    return specs, family


# Sections read line by line while authoring this review. Facts taken from them
# carry ``source_verified``; every other section is a rule-derived candidate.
AUDITED_FAMILIES = frozenset({
    'n_conversion', 'special_math',
    'i_io', 'i_control', 'i_compensation', 'i_arithmetic', 'i_comparison', 'i_tuning',
})
AUDITED_SYMBOLS = frozenset({
    'OUT_C', 'OUTH_C', 'OUT_ST', 'OUTH_ST', 'OUT_F', 'OUT_FP', 'SET_F', 'RST_F',
    'IRET', 'IRETP', 'BTOW', 'BTOWP', 'WTOB', 'WTOBP', 'SEG', 'SEGP', 'UNI', 'UNIP',
    'FOR', 'NEXT', 'BREAK', 'BREAKP', 'JMP', 'JMPP', 'CJ', 'SCJ', 'GOEND', 'RET',
    'CALL', 'CALLP', 'XCALL', 'FCALL', 'FCALLP', 'ZPUSH', 'ZPOP', 'ZPUSHP', 'ZPOPP',
})

VERIFIED_TITLES = frozenset({
    ('sh080809engx', 'Operation start, series connection, parallel connection'),
    ('sh080809engx', 'Pulse operation start, pulse series connection, pulse parallel connection'),
    ('sh080809engx', 'Pulse NOT operation start, pulse NOT series connection, pulse NOT parallel connection'),
    ('sh080809engx', 'Ladder block series connection, ladder block parallel connection'),
    ('sh080809engx', 'Operation results push, operation results read, operation results pop'),
    ('sh080809engx', 'Operation results inversion'),
    ('sh080809engx', 'Operation results conversion'),
    ('sh080809engx', 'Pulse conversion of edge relay operation results'),
    ('sh080809engx', 'Out (excluding timers, counters, and annunciators)'),
    ('sh080809engx', 'Counter'),
    ('sh080809engx', 'Setting devices (excluding annunciators)'),
    ('sh080809engx', 'Resetting devices (excluding annunciators)'),
    ('sh080809engx', 'Rising edge output, falling edge output'),
    ('sh080809engx', 'Bit device output inversion'),
    ('sh080809engx', 'Pulse conversion of direct output'),
    ('sh080809engx', 'Bit device shift'),
    ('sh080809engx', 'Setting the master control, resetting the master control'),
    ('sh080809engx', 'Sequence program end'),
    ('sh080809engx', 'Sequence program stop'),
    ('sh080809engx', 'BIN 16-bit data comparisons (small, match, large)'),
    ('sh080809engx', '16-bit data transfer, 32-bit data transfer'),
    ('sh080809engx', 'Pointer branch'),
    ('sh080809engx', 'Interrupt disable, interrupt enable, interrupt program mask'),
    ('sh080809engx', 'I/O refresh'),
    ('sh080783engp', 'Operation start, series connection, parallel connection'),
    ('sh080783engp', '16-/32-bit data transfer'),
    ('sh080783engp', '16-bit block data transfer'),
    ('sh080784engn', 'Converting bit type to string type'),
    ('sh080784engn', 'Converting bit type to time type'),
})

#: CPU-group labels whose model set is decidable from the model name itself.
SERIAL_GROUP = (
    (re.compile(r'^LCPU$', re.I), lambda model: model.startswith('L')),
    (re.compile(r'^QnUDVCPU$', re.I), lambda model: model.startswith('Q') and 'UDV' in model),
    (re.compile(r'^QnUDPVCPU$', re.I), lambda model: model.startswith('Q') and 'UDPV' in model),
    (re.compile(r'^QnU\(D\)\(H\)CPU$', re.I), lambda model: 'UDH' in model or 'UDEH' in model),
    (re.compile(r'^QnU\(X\)\(H\)CPU$', re.I), lambda model: 'UDH' in model or 'UDEH' in model),
)

OPERAND_ROLE = {'en': 'enable'}
for _name in ('s', 's1', 's2', 's3', 's4'):
    OPERAND_ROLE[_name] = 'source'
for _name in ('d', 'd1', 'd2'):
    OPERAND_ROLE[_name] = 'destination'
OPERAND_ROLE['n'] = 'count'
OPERAND_ROLE['en'] = 'enable'


def _operand_role(symbol):
    lowered = symbol.lower()
    if lowered in OPERAND_ROLE:
        return OPERAND_ROLE[lowered]
    if lowered.startswith('s'):
        return 'source'
    if lowered.startswith('d'):
        return 'destination'
    return 'unknown'


GENERIC_OPERATION_TOKENS = frozenset({
    'result_write', 'operation_result', 'write_result', 'store_result',
    'execute_documented_operation', 'documented_operation',
})
BEHAVIOUR_DIMENSIONS = ('effects.',)


def _documented_operation(payloads):
    """True when a fact states what the instruction does, not just where it writes.

    A generic write/result placeholder never qualifies: the operation must name
    an action and carry a documented object, operand or result.
    """
    for fact in payloads:
        dimension = fact['dimension']
        if dimension.startswith(BEHAVIOUR_DIMENSIONS) or dimension in {
                'execution.jump', 'execution.program_terminator', 'memory.stack'}:
            value = fact['value']
            operation = value.get('operation')
            if not operation or operation in GENERIC_OPERATION_TOKENS:
                continue
            # A bare verb is not knowledge: the fact must state something about
            # the operation beyond its own name and class.
            detail = set(value) - {'operation', 'operation_class', 'basis'}
            if not detail:
                continue
            return True
    return False


_OVERVIEW_TITLE = re.compile(r'instruction list|how to read|operation processing time'
                             r'|^appendix', re.I)
_OPCODE_IN_TITLE = re.compile(r'\b[A-Z]{2,}[0-9]?\b')


def _is_instruction_overview(section):
    """A category page or index table lists forms; it documents none of them."""
    title = (section.title or '').strip()
    if _OVERVIEW_TITLE.search(title) or re.search(r'List of Instructions|INSTRUCTION TABLES', section.chapter, re.I):
        return True
    if getattr(section, 'source_definition', False):
        return False
    if _OPCODE_IN_TITLE.search(title):
        return False
    if len(section.symbols or ()) < 3:
        return False
    return not section.operands()


#: Language of the manual an instruction is documented in.
MANUAL_LANGUAGES = {'sh080809engx': ('ladder',),
                    'sh080783engp': ('st', 'fbd'),
                    'sh080784engn': ('st', 'fbd'),
                    'sh080785engm': ('st', 'fbd'),
                    'sh080316ei': ('ladder', 'fbd')}


def _section_languages(section, symbol):
    if section.manual == 'sh080316ei':
        return ('st', 'fbd') if '_' in symbol else ('ladder',)
    if getattr(section, '_symbolic_only', False):
        return ('fbd',)
    return MANUAL_LANGUAGES[section.manual]


def _applicability_mark_row(section, model, state):
    """The chunk that carries this CPU mark, so the fact binds its own page."""
    for row in section.rows:
        raw = row.get('source_applicability_json')
        if not raw:
            continue
        header = json.loads(raw)
        if header.get('basis') != 'instruction_header':
            continue
        for mark in header.get('model_marks') or ():
            if mark.get('model') == model and mark.get('state') == state:
                return row
    return None


def _applicability_specs(section, symbol, model, state):
    """An explicit CPU applicability mark as a fact, including ``unsupported``.

    Only a mark the source states is published: a section without a header mark
    keeps an unknown CPU scope and never claims support or non-support.
    """
    override = getattr(section, 'symbol_applicability', {}).get(symbol, {}).get(model)
    override = override or getattr(section, 'table_applicability', {}).get(model)
    if override is not None:
        conditions, formal = _formal_conditions(override['conditions'])
        case_conditions, case_formal = _formal_conditions(override.get('case_conditions', []))
        checked_condition = _all_condition_predicates(conditions) if formal and conditions else None
        state = 'supported' if checked_condition is not None and state == 'conditional' else state
        value = {'model_states': {model: state}, 'rule': 'documented_CPU_and_target_module_scope',
                 'scope_basis': override.get('basis', 'instruction_applicability_table')}
        value.update(copy.deepcopy(override.get('technical_scope', {})))
        required = _required_context([*conditions, *case_conditions])
        if required:
            value['required_context'] = required
        if state in {'supported', 'unsupported'}:
            value['available'] = state == 'supported'
        spec = _spec('cpu_applicability', 'constraints.cpu_availability',
                     override['witnesses'], value,
                     languages=_section_languages(section, symbol),
                     conditions=[*case_conditions, *conditions])
        spec['evidence_sections'] = override['sections']
        result = [spec]
        if state == 'supported' and checked_condition is not None:
            unavailable = _spec('cpu_requirement_not_met', 'constraints.cpu_availability',
                override['witnesses'], dict(value, model_states={model: 'unsupported'}, available=False),
                languages=_section_languages(section, symbol),
                conditions=[*case_conditions, {'kind': 'hardware_condition',
                             'predicate': _predicate('not', checked_condition),
                             'required_context': required}])
            unavailable['evidence_sections'] = override['sections']
            result.append(unavailable)
        return result
    row = _applicability_mark_row(section, model, state)
    if row is None:
        return []
    value = {'model_states': {model: state}, 'rule': 'documented_CPU_instruction_scope'}
    if state in {'supported', 'unsupported'}:
        value['available'] = state == 'supported'
    conditions, formal = _formal_conditions(_cpu_conditions(section, model, state, symbol))
    required = _required_context(conditions)
    if required:
        value['required_context'] = required
    checked_condition = _all_condition_predicates(conditions) if formal and conditions else None
    if checked_condition is not None and state == 'conditional':
        value.update(model_states={model: 'supported'}, available=True)
    result = [_spec('cpu_applicability', 'constraints.cpu_availability', (row['text'],), value,
                   languages=_section_languages(section, symbol), conditions=conditions)]
    if checked_condition is not None and value.get('available') is True:
        result.append(_spec('cpu_requirement_not_met', 'constraints.cpu_availability', (row['text'],),
            dict(value, model_states={model: 'unsupported'}, available=False),
            languages=_section_languages(section, symbol),
            conditions=[{'kind': 'hardware_condition', 'predicate': _predicate('not', checked_condition),
                         'required_context': required}]))
    return result


def _model_states(section):
    """Documented CPU header marks; a header conflict withholds the owner."""
    states, conflicts = {}, []
    for row in section.rows:
        raw = row.get('source_applicability_json')
        if not raw:
            continue
        header = json.loads(raw)
        if header.get('basis') != 'instruction_header':
            continue
        for mark in header.get('model_marks') or ():
            model, state = mark.get('model'), mark.get('state')
            if not model:
                continue
            if state not in {'supported', 'conditional', 'unsupported'}:
                conflicts.append({'model': model, 'reason': 'ambiguous_cpu_header_state'})
                states[model] = 'unknown'
                continue
            if model in states and states[model] != state:
                conflicts.append({'model': model, 'reason': 'conflicting_cpu_header_marks'})
                states[model] = 'unknown'
                continue
            states.setdefault(model, state)
    return states, conflicts


_DOTTED_TOKEN = re.compile(r'\b([A-Z]{1,3})\.([A-Z][A-Z0-9_]{1,30})')


def _native_symbol(section, symbol):
    """Native spelling of a declared form when the manual writes a dot."""
    haystack = ' '.join(list(section.symbol_tokens or ()) + list(section.opcodes or ())
                        + [section.title or ''])
    for match in _DOTTED_TOKEN.finditer(haystack):
        native = match.group(1) + '.' + match.group(2)
        if _normalize_symbol(native) == symbol:
            return native
        if _normalize_symbol(native + '(P)') == symbol:
            return native + 'P'
    if '_' in symbol:
        candidate = symbol.replace('_', '.', 1)
        squashed = haystack.replace(' ', '').replace('\u3000', '')
        if candidate in squashed or candidate + 'P' in squashed:
            return candidate
    return None


def _dotted_alias(symbol, section):
    """The dotted owner spelling of an opcode the index stored with '_'."""
    if '.' in symbol:
        return symbol
    candidate = symbol.replace('_', '.', 1)
    native = _native_symbol(section, symbol)
    if native and native != symbol:
        return native
    return candidate if '_' in symbol else None


PLACEHOLDER_OPERATIONS = frozenset({
    'math_evaluation', 'obtain_value', 'operation_result', 'result_write',
    'write_result', 'store_result', 'execute_documented_operation',
    'documented_operation', 'generic_operation',
})


def _placeholder_spec(spec):
    """Effect that only repeats a heading class instead of a documented effect."""
    if not spec['dimension'].startswith(('effects.', 'execution.')):
        return False
    return spec['value'].get('operation') in PLACEHOLDER_OPERATIONS


def _universal_specs(section, symbol):
    """Structure facts that every documented heading states for each symbol."""
    specs = []
    variant = 'base'
    if section.manual == 'sh080316ei' and symbol.replace('_', '.') == 'S.PIDP':
        variant = 'position_type'
    elif symbol.endswith('_E'):
        variant = 'enable_aware'
    elif symbol.endswith('P') and len(symbol) > 2:
        variant = 'pulse'
    form_value = {'declared_symbol': symbol, 'form_variant': variant}
    native = _native_symbol(section, symbol)
    if native:
        form_value['native_symbol'] = native
        form_value['alias_forms'] = [native, symbol] if native != symbol else [symbol]
        form_value['native_syntax'] = 'dotted' if '.' in native else 'underscore'
    specs.append(_spec('form', 'identity.declared_form', (section.heading_line,), form_value))

    operands = section.operands()
    if len(operands) >= 2:
        roles = []
        witnesses = []
        for item in operands:
            role = {'symbol': item['symbol'], 'slot': _operand_role(item['symbol']),
                    'documented_type': item.get('typed') or 'unknown'}
            if item.get('direction'):
                role['documented_direction'] = item['direction']
            roles.append(role)
            if item.get('witness'):
                witnesses.append(item['witness'])
        if witnesses:
            specs.append(_spec('operands', 'parameters.operand_roles', tuple(witnesses[:4]),
                               {'roles': roles, 'order_source': 'documented_operand_legend',
                                'documented_order': [item['symbol'] for item in operands]}))

    for form in section.call_forms():
        if form['opcode'] != symbol:
            continue
        parameters = []
        for name in (form['parameters'] or [item['symbol'] for item in operands]):
            match = next((item for item in operands if item['symbol'] == name), None)
            direction = 'unknown'
            if name == 'EN':
                direction = 'input'
            elif match and match.get('direction'):
                direction = {'input': 'input', 'output': 'output'}.get(match['direction'], 'in_out')
            parameters.append({'name': name,
                               'type': (match.get('typed') if match else '') or 'unknown',
                               'direction': direction})
        value = {'parameters': parameters,
                 'returns': form['returns'] or 'unknown',
                 'enable': bool(form['enable']),
                 'available': True,
                 'documented_call': _safe_token(form['documented_form']),
                 'arguments_from': 'documented_call_form'}
        specs.append(_spec('st', 'parameters.st_signature',
                           (form.get('witness') or form['documented_form'],), value,
                           languages=('st',)))
        break
    unsupported = _witness(section.text,
                           (r'(?:is|are) not supported by (?:the )?(?:ST|structured text)[^.]{0,40}',
                            r'cannot be used (?:in|with) (?:the )?(?:ST|structured text)[^.]{0,30}',
                            r'[Nn]ot available (?:in|for) (?:the )?(?:ST|structured text)'))
    if unsupported:
        specs.append(_spec('st_unavailable', 'parameters.st_signature', (unsupported,),
                           {'available': False, 'languages': 'st',
                            'documented_reason': 'source_states_language_not_supported'},
                           languages=('st',)))

    gate = section.non_conditional()
    if gate:
        specs.append(_spec('gate', 'execution.operation_condition',
                           ('Non-conditional execution',),
                           {'condition': 'unconditional_execution',
                            'documented_marker': 'non_conditional_execution_marker'}))
    else:
        driven = _witness(' '.join(section.statements(symbol)),
                          (r'when the execution command is turned (?:ON|OFF)',))
        if driven:
            specs.append(_spec('gate', 'execution.operation_condition', (driven,),
                               {'condition': 'contact_driven',
                                'documented_marker': 'execution_command'}))
    return specs


def _documented_facts(source, section, symbol, family):
    """All fact specifications for one declared symbol of one section."""
    specs = _universal_specs(section, symbol)
    authored = (_reviewed_literal_specs(section, symbol) or _reviewed_n_specs(section, symbol) or _reviewed_m_specs(section, symbol)
                or _reviewed_i_specs(section, symbol) or _reviewed_xp_specs(section, symbol))
    authored += _n_behavior_detail_specs(section, symbol)
    if authored:
        replaced = {item['dimension'] for item in authored}
        specs = [item for item in specs if item['dimension'] not in replaced]
    family_specs_list, _family = family_specs(section, symbol)
    if authored:
        family_specs_list = [item for item in family_specs_list
                             if not item['dimension'].startswith('effects.')
                             and item['dimension'] not in {s['dimension'] for s in authored}]
    specs.extend(family_specs_list)
    specs.extend(authored)
    contracts = _parameter_contract_specs(section, symbol)
    if contracts:
        replaced = {}
        for item in contracts:
            replaced.setdefault(item['dimension'], set()).update(item['languages'])
        # The literal call/table replaces only its own language representation.
        # A source's earlier incomplete ST parser must not survive beside it.
        retained = []
        for item in specs:
            languages = tuple(item['languages'] or _section_languages(section, symbol))
            remaining = tuple(language for language in languages if language not in replaced.get(item['dimension'], ()))
            if remaining:
                retained.append(dict(item, languages=remaining))
        specs = retained
        specs.extend(contracts)
    for contract in getattr(section, '_n_argument_contracts', []):
        for issue in contract['issues']:
            if issue['kind'] == 'exponent_argument_prose_and_table_disagree':
                for spec in specs:
                    if spec['dimension'] == 'effects.operation':
                        spec['source_conflict'] = True
                        spec['value']['argument_binding_conflict'] = copy.deepcopy(issue)
    error = section.error_block()
    if error and error.get('witness'):
        blob = error['_text']
        statement = _witness(blob, (r'There is no operation error in[^.]*\.',
                                    r'No operation error occurs',
                                    r'In (?:any of the following cases|the following case), an operation error occurs',
                                    r'An operation error occurs in the following cases'))
        if statement:
            value = {'operation_error': error['operation_error'],
                     'error_flags': list(error.get('error_flags') or ()),
                     'error_code_registers': list(error.get('error_code_registers') or ())}
            codes = section.error_codes()
            if codes and error['operation_error'] == 'documented':
                value['error_codes'] = codes
            specs.append(_reviewed_spec('error', 'effects.error_response', (statement,), value))
    specs.extend(_numeric_limit_specs(section, symbol, specs))
    for condition in section.serial_conditions():
        matcher = next((function for pattern, function in SERIAL_GROUP
                        if pattern.match(condition['cpu_group'])), None)
        if matcher is None:
            continue
        specs.append(_spec('serial', 'constraints.serial_number_minimum', (condition['line'],),
                           {'cpu_group': _token(condition['cpu_group']),
                            'minimum_serial_number': condition['minimum_serial'],
                            'serial_digits': len(condition['minimum_serial'])},
                           model_filter=matcher))
    return specs


def _numeric_limit_specs(section, symbol, specs):
    """Only scalar, source-bound limits use the shared numeric evaluator.

    Block fields, BCD magnitudes, string lengths, pin counts and contextual
    limits remain explicit qualifications. Their binding is unresolved; a
    missing evaluator can never become a vacuous numeric-ranges pass.
    """
    limits = section.limits(symbol)
    typed = next((spec['value'].get('operands') for spec in specs
                  if spec['dimension'] == 'parameters.typed_operands' and spec.get('reviewed')), ())
    scalar_inputs = {item['name'].upper(): index for index, item in enumerate(typed)
                     if item.get('direction') == 'input'
                     and item.get('type') in {'INT', 'DINT', 'UINT', 'UDINT', 'REAL', 'LREAL', 'ANY_NUM'}}
    alternatives = {}
    for limit in limits:
        alternatives.setdefault(limit['subject'], set()).add((limit.get('minimum'), limit.get('maximum')))
    result = []
    for index, limit in enumerate(limits):
        domain = {key: value for key, value in limit.items() if key != 'line'}
        subject = domain['subject']
        scalar = (subject in scalar_inputs and len(alternatives[subject]) == 1
                  and not domain.get('subject_kind')
                  and not any(item.get('kind') != 'precision' for item in domain.get('qualifications', ()))
                  and not domain.get('additional_intervals')
                  and domain.get('representation') != 'BCD')
        if scalar:
            boundary = {key: domain[key] for key in ('minimum', 'maximum') if key in domain}
            boundary['operand_index'] = scalar_inputs[subject]
            value = {'ranges': [boundary], 'subjects': [subject],
                     'rule': 'documented_scalar_numeric_range'}
            if domain.get('qualifications'):
                value['qualifications'] = domain['qualifications']
            result.append(_reviewed_spec('numeric_%s_%d' % (subject, index),
                                        'constraints.numeric_ranges', [limit['line']], value,
                                        _section_languages(section, symbol)))
        else:
            domain.update(parameter_binding='unknown', role='documented_numeric_limit')
            if len(alternatives[subject]) > 1:
                domain['form_or_case_binding'] = 'unknown'
            result.append(_reviewed_spec('numeric_%s_%d' % (subject, index),
                                         'constraints.source_qualifications', [limit['line']],
                                         {'qualifications': [domain], 'subjects': [subject]},
                                         _section_languages(section, symbol)))
    return result


UNDETERMINED = ('unknown', 'unresolved', '')

_DELTA_SIGN = {'add': 'positive', 'subtract': 'negative'}


def _signed_delta(statement):
    """A documented "Adds -1" / "Subtracts 1" amount as (sign, magnitude).

    Both wordings state one documented effect, so the sign and the magnitude are
    compared instead of the verb.
    """
    match = re.search(r'\b(adds?|subtracts?)\s+\(?([-+]?\d+)\)?', statement, re.I)
    if not match:
        return None
    verb = match.group(1).lower().rstrip('s')
    magnitude = int(match.group(2))
    sign = _DELTA_SIGN.get(verb, 'positive')
    if magnitude < 0:
        magnitude = -magnitude
        sign = 'negative' if sign == 'positive' else 'positive'
    return sign, str(magnitude)


def _math_function(symbol):
    """Documented math function of a declared form, plus its precision."""
    base = _form_base(symbol)
    if base in MATH_TOKENS:
        return base, 'single'
    if base.endswith('D') and base[:-1] in MATH_TOKENS:
        return base[:-1], 'double'
    return None, 'unknown'


def _form_base(symbol):
    """Base instruction of a declared form, so ``SIN``/``SINP`` share prose."""
    name = symbol[:-2] if symbol.endswith('_E') else symbol
    if len(name) > 2 and name.endswith('P') and not name.endswith('_P'):
        name = name[:-1]
    return name


def _token(text):
    token = re.sub(r'[^A-Za-z0-9_]+', '_', (text or '').strip()).strip('_').lower()
    return token or 'unknown'


def _safe_token(text):
    """Keep a documented literal that contains spaces or odd glyphs publishable."""
    value = re.sub(r'[^A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]', '_', (text or '').strip())
    return value or 'unknown'


def _undetermined_fields(value, path=''):
    """Fields this review must not present as verified (no invented defaults)."""
    if isinstance(value, dict):
        found = []
        for key, item in value.items():
            if key in {'basis', 'order_source', 'arguments_from', 'documented_marker'}:
                continue
            found.extend(_undetermined_fields(item, path + '.' + key if path else key))
        return found
    if isinstance(value, list):
        return [field for index, item in enumerate(value)
                for field in _undetermined_fields(item, '%s[%d]' % (path, index))]
    if isinstance(value, str) and value in UNDETERMINED:
        return [path]
    return []


_PUBLISHABLE = re.compile(r'[A-Za-z0-9_$][A-Za-z0-9_$.:/+*<>=!(),\[\]{}|&%~?\-]*\Z')


def _publishable(value):
    if isinstance(value, dict):
        return all(_publishable(k) and _publishable(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_publishable(v) for v in value)
    if isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and bool(_PUBLISHABLE.fullmatch(value) or re.fullmatch(
        r'(?:\*\*|[+*/&\-]|<>|<=|>=|=|<|>)(?:P)?(?:_U|_E)?', value))


#: Slots the source may document more than once for one owner. The selector is
#: the documented case (variant, flags, subjects, order, function), never the
#: disputed wording, so equal cases still meet and disagreeing cases stay apart.
REPEATABLE_SLOTS = {
    'identity.declared_form': lambda value: str(value.get('form_variant') or 'unknown'),
    # An error case is selected by the documented flags *and* registers: two
    # manuals that list a different register for the same case document
    # different material, not a contradicting behaviour.
    'effects.error_response': lambda value: (
        (','.join(sorted(value.get('error_flags') or ())) or 'none') + '|'
        + (','.join(sorted(value.get('error_code_registers') or ())) or 'none')),
    'constraints.numeric_ranges': lambda value: (','.join(value.get('subjects') or ())
                                                 or 'documented'),
    'constraints.source_qualifications': lambda value: (','.join(value.get('subjects') or ())
                                                        or 'documented'),
    'parameters.operand_roles': lambda value: (','.join(value.get('documented_order') or ())
                                               or 'documented'),
    'effects.math_function': lambda value: str(value.get('function') or 'unknown'),
}

#: Extractor bookkeeping: it evidences the claim, it is not the claim itself.
#: Only the extractor's own bookkeeping leaves the value: the documented operand
#: list and the documented precision are knowledge and stay in the claim.
EVIDENCE_FIELDS = ('basis', 'operation_class')
#: The operand list is published once, as ``parameters.operand_roles``. Repeating it
#: inside an effect would make the same documented effect look different between two
#: manuals that word the sentence differently, so the effect points at it instead.
REDIRECTED_FIELDS = ('documented_operands',)
REDIRECT_NOTE = 'operand_list_see=parameters.operand_roles'


def _strip_bookkeeping(value):
    """Remove extractor bookkeeping from a published value, in place."""
    for field in EVIDENCE_FIELDS:
        value.pop(field, None)
    for field in REDIRECTED_FIELDS:
        value.pop(field, None)
    return value


#: Reviewed condition vocabulary. A condition outside it stays explicitly
#: unknown: prose is never transliterated into a token that looks formal.
CONDITION_KINDS = frozenset({
    'cpu_applicability', 'execution_condition', 'operand_constraint',
    'scan_timing', 'hardware_condition', 'data_format', 'module_condition',
})
CONDITION_TOKENS = frozenset({
    'conditional', 'always', 'execution_command_on', 'execution_command_off',
    'positive_only', 'negative_allowed', 'initial_execution', 'every_scan',
    'single_precision', 'double_precision', 'bcd', 'bin', 'on_rising_edge',
    'unsupported', 'unknown',
})


def _condition_token(value):
    """A condition value only when it is a reviewed token, else ``unknown``."""
    if isinstance(value, (bool, int)):
        return int(value)
    if isinstance(value, str) and value in CONDITION_TOKENS:
        return value
    return 'unknown'


def _formal_conditions(conditions):
    """Formalise documented conditions without inventing tokens.

    ``returns (conditions, classified)``. A condition whose premise is not in the
    reviewed vocabulary stays ``unknown`` with an explicit basis and is *not*
    classified, so the fact cannot be published as source verified on the
    strength of a machine-made token.
    """
    formal, classified = [], True
    for item in conditions:
        if isinstance(item, dict):
            kind = str(item.get('kind') or '')
            # These are authored structured predicates, not prose-derived tokens.
            if _typed_condition(item):
                formal.append(_condition_with_predicate(item))
                continue
            token = _condition_token(item.get('value'))
            if kind in CONDITION_KINDS and token != 'unknown':
                formal.append({'kind': kind, 'value': token,
                               'basis': 'documented_field_mapping'})
                continue
            classified = False
            formal.append({'kind': kind if kind in CONDITION_KINDS
                           else 'documented_condition',
                           'value': 'unknown',
                           'basis': 'unclassified_clause_not_formalised'})
            continue
        token = _condition_token(item if isinstance(item, str) else str(item))
        if token != 'unknown':
            formal.append(token)
            continue
        classified = False
        formal.append({'kind': 'documented_condition', 'value': 'unknown',
                       'basis': 'unclassified_clause_not_formalised'})
    return formal, classified


def _merge_rows(left, incoming):
    """The build's own fact merger, imported lazily to avoid an import cycle.

    Per source batch it unions equal claims (keeping every field and every
    locator) and keeps different documented values as ``:variant:`` facts,
    marking a conflict only when dimension, scope and conditions agree.
    """
    from knowledge.structured_facts import _merge_fact_rows
    _merge_fact_rows(left, incoming)


def _merge_sources_row(left, right):
    """Union two source lists, keeping the first occurrence of each locator."""
    merged = list(left)
    known = {(source.get('manual_id'), source.get('revision'),
              tuple(source.get('pdf_pages') or ())) for source in merged}
    for source in right:
        key = (source.get('manual_id'), source.get('revision'),
               tuple(source.get('pdf_pages') or ()))
        if key not in known:
            merged.append(source)
            known.add(key)
    return merged


def _materialize(section, symbol, specs, model, *, verified):
    """Bind verified witnesses to pages and build published fact payloads."""
    facts, unbound, downgraded = [], [], []
    seen = set()
    for spec in specs:
        if spec['model_filter'] is not None and not spec['model_filter'](model):
            continue
        identity = 'authored:ql:%s:%s' % (spec['key'], spec['dimension'])
        selector = REPEATABLE_SLOTS.get(spec['dimension'])
        if selector is not None:
            identity = identity + ':' + selector(spec['value'])
        if identity in seen and not spec.get('shared_claim'):
            suffix = 2
            while '%s:%d' % (identity, suffix) in seen:
                suffix += 1
            identity = '%s:%d' % (identity, suffix)
        witnesses = tuple(dict.fromkeys(witness for witness in spec['patterns'] if witness))
        if not witnesses:
            continue
        bound, refs = [], []
        evidence_sections = spec.get('evidence_sections') or [section]
        for witness in witnesses:
            pairs = [(owner, chunk_id, page) for owner in evidence_sections
                     for chunk_id, page in owner.bind(witness)]
            if not pairs:
                unbound.append({'symbol': symbol, 'rule': spec['key'], 'witness': witness[:120]})
                continue
            for evidence_section, chunk_id, page in pairs:
                if chunk_id in bound:
                    continue
                bound.append(chunk_id)
                refs.append({'id': str(chunk_id), 'manual_id': evidence_section.manual,
                             'manual_number': evidence_section.manual_number,
                             'revision': evidence_section.revision,
                             'pdf_pages': [page] if page else evidence_section.pages,
                             'opcode': symbol, 'target_model': model})
        if not refs:
            continue
        seen.add(identity)
        scope = {'models': [model], 'forms': [symbol]}
        scope['languages'] = list(spec['languages'] or _section_languages(section, symbol))
        if spec['dimension'] == 'constraints.cpu_availability' and getattr(section, '_has_structured_block', False):
            scope['languages'] = list(dict.fromkeys([*scope['languages'], 'ladder']))
        value = _strip_bookkeeping(copy.deepcopy(spec['value']))
        if not _publishable(value):
            unbound.append({'symbol': symbol, 'rule': spec['key'],
                            'witness': 'value_not_publishable'})
            continue
        missing = _undetermined_fields(value)
        source_checked = verified or spec.get('reviewed', False) or spec.get('source_checked', False)
        fact_verified = (verified or spec.get('reviewed', False)) and not missing
        if verified and missing:
            downgraded.append({'symbol': symbol, 'rule': spec['key'], 'fields': missing[:6]})
        fact = {'id': identity, 'dimension': spec['dimension'], 'value': value,
                'status': 'source_verified' if fact_verified else 'candidate_evidence',
                'verification': 'source_checked' if source_checked else 'not_performed',
                'sources': refs, 'scope': scope}
        if spec.get('source_conflict'):
            fact['status'] = 'conflict'
            fact['verification'] = 'source_checked'

        if spec['conditions']:
            conditions, classified = _formal_conditions(spec['conditions'])
            fact['conditions'] = conditions
            if not classified:
                # A condition that is still unknown cannot certify the claim.
                fact['status'] = 'candidate_evidence'
                fact['verification'] = 'source_checked' if source_checked else 'not_performed'
        if spec.get('depends_on'):
            fact['depends_on'] = list(spec['depends_on'])
        facts.append(fact)
    return facts, unbound, downgraded


def reviewed_ql_behavior(source, entries=()):
    """Return ``(facts, receipt)`` for the whole registered Q/L instruction library.

    ``facts`` is keyed by ``(target_model, opcode)`` and holds the same fact
    payloads the build expects (``id``/``dimension``/``value``/``status``/
    ``sources``/``scope``). ``receipt`` is machine-readable: per-section
    disposition, per-owner coverage and the gap classes the caller must keep
    apart.
    """
    sections, revisions = _load_sections(source)
    manual_scope = {}
    for manual in revisions:
        rows = _fetch(source, 'SELECT plc_models FROM manuals WHERE manual_id=?', (manual,))
        raw = rows[0]['plc_models'] if rows else None
        if not raw:
            continue
        try:
            models = json.loads(raw)
        except (TypeError, ValueError):
            models = [item.strip() for item in raw.replace('\n', ',').split(',')
                      if item.strip()]
        if isinstance(models, dict):
            models = list(models)
        manual_scope[manual] = {str(model): 'supported' for model in models if model}
    parameter_review = _prepare_parameter_contracts(source, sections)
    _prepare_m_applicability(sections)
    _prepare_i_applicability(sections)
    _prepare_xp_applicability(sections)
    entry_owners = {}
    for entry in entries or ():
        model, opcode = entry.get('target_model'), entry.get('opcode')
        if model and opcode:
            entry_owners[(model, opcode)] = entry

    facts, batches, inventory, unbound, conflicts = {}, {}, [], [], []
    cross_manual, unverified_fields, unattributed = [], [], []
    declared_opcodes = set()
    ql_models = set()
    family_totals, dimension_totals, status_totals = {}, {}, {}
    scope_fact_counts, scope_sections = {}, {}
    covered_sections = 0
    for (manual, outline), section in sections.items():
        states, header_conflicts = _model_states(section)
        scope_basis = 'instruction_header'
        if getattr(section, 'table_applicability', None):
            states = {model: entry['state'] for model, entry in section.table_applicability.items()}
            scope_basis = 'instruction_applicability_table'
        elif getattr(section, 'symbol_applicability', None):
            states = {model: entry['state'] for scoped in section.symbol_applicability.values()
                      for model, entry in scoped.items()}
            scope_basis = 'instruction_applicability_table'
        if not states and manual_scope.get(manual):
            states = dict(manual_scope[manual])
            scope_basis = 'manual_applicability'
        elif not states:
            scope_basis = 'cpu_scope_unavailable'
        conflicts.extend({'manual': manual, 'section': section.title, **item}
                         for item in header_conflicts)
        ql_models.update(states)
        family = family_of(section)
        symbols = section.symbols
        declared_opcodes.update(symbols)
        audited = ((manual, section.title) in VERIFIED_TITLES
                   or family in AUDITED_FAMILIES
                   or any(symbol in AUDITED_SYMBOLS for symbol in symbols)
                   or bool(getattr(section, '_n_whole_behavior_reviewed', False)))
        # CPU applicability is a separate question: when the section carries no
        # per-instruction applicability mark, the manual-level model list is not
        # evidence of support for this instruction, so the fact stays a candidate.
        verified = audited and scope_basis in {'instruction_header', 'instruction_applicability_table'}
        section_facts, section_unbound = {}, []
        if section.has_instruction_chunk and symbols and not _is_instruction_overview(section):
            for symbol in symbols:
                specs = _documented_facts(source, section, symbol, family)
                symbol_scope = getattr(section, 'symbol_applicability', {}).get(symbol)
                actual_states = {model: entry['state'] for model, entry in symbol_scope.items()} if symbol_scope else states
                for model, state in sorted(actual_states.items()):
                    if state == 'unknown':
                        continue
                    # An explicit CPU mark is independently source-checked. A
                    # behaviour-family audit cannot change its truth or availability.
                    availability = _applicability_specs(section, symbol, model, state)
                    payloads, missing, _ = _materialize(section, symbol, availability, model,
                                                       verified=True)
                    section_unbound.extend(missing)
                    if state != 'unsupported' and specs:
                        guarded = _guard_specs(section, specs, model, state, bool(availability), symbol)
                        behaviour, missing, downgraded = _materialize(
                            section, symbol, guarded, model, verified=verified)
                        payloads.extend(behaviour)
                        section_unbound.extend(missing)
                        unverified_fields.extend(
                            {'manual': manual, 'section': section.title, **item}
                            for item in downgraded[:10])
                    if payloads:
                        section_facts[(model, symbol)] = payloads
        scope_fact_counts[scope_basis] = (scope_fact_counts.get(scope_basis, 0)
                                          + sum(len(payload) for payload in section_facts.values()))
        scope_sections[scope_basis] = scope_sections.get(scope_basis, 0) + 1
        for key, payloads in section_facts.items():
            # One batch per source section: the shared merger unions equal claims
            # and keeps different ones as source variants.
            batches.setdefault(key, []).append(payloads)
        unbound.extend({'manual': manual, 'section': section.title, **item}
                       for item in section_unbound[:20])
        attributed = {name for (_model, name), payloads in section_facts.items()
                      if _documented_operation(payloads)}
        unattributed.extend({'manual': manual, 'section': section.title, 'symbol': symbol}
                            for symbol in section.unattributed if symbol not in attributed)
        if section_facts:
            covered_sections += 1
            family_totals[family or 'unclassified'] = family_totals.get(family or 'unclassified', 0) + 1
        behaved = {symbol for (_model, symbol), payloads in section_facts.items()
                   if _documented_operation(payloads)}
        if _is_instruction_overview(section):
            disposition, reason = 'source_excluded', 'instruction_list_overview'
        elif not section.has_instruction_chunk:
            disposition, reason = 'source_excluded', 'not_an_instruction_heading'
        elif not symbols:
            disposition, reason = 'source_present_not_extracted', 'declared_symbol_line_not_resolved'
        elif not section_facts:
            disposition, reason = ('source_present_not_extracted',
                                   'no_authored_rule_matched' if family else 'no_authored_family_for_heading')
        elif not behaved:
            if _is_instruction_overview(section):
                disposition, reason = 'source_excluded', 'instruction_list_overview'
            else:
                disposition, reason = ('source_present_not_extracted',
                                       'identity_only_no_documented_operation')
        elif set(symbols) - behaved:
            disposition, reason = 'behaviour_extracted_partially', 'some_forms_without_documented_operation'
        else:
            disposition, reason = 'behaviour_extracted', 'documented_operation_per_form'
        inventory.append({
            'manual': manual, 'revision': section.revision,
            'section': section.title, 'chapter': section.chapter,
            'pages': section.pages[:1] + section.pages[-1:], 'chunk_ids': section.chunk_ids,
            'declared_symbols': symbols, 'family': family, 'disposition': disposition,
            'reason': reason, 'verified_section': verified,
            'owner_count': len(section_facts),
            'fact_count': sum(len(value) for value in section_facts.values()),
            'cpu_scope_basis': scope_basis,
            'section_audited': bool(audited),
            'numeric_domains': section.limits() if section.has_instruction_chunk else [],
            'cpu_scope': {'supported': sum(1 for state in states.values() if state == 'supported'),
                          'conditional': sum(1 for state in states.values() if state == 'conditional'),
                          'unsupported': sum(1 for state in states.values() if state == 'unsupported')},
        })

    # Facts are merged by the same helper the build uses: per source batch, all
    # fields kept, and a conflict only when dimension, scope and conditions agree
    # while the documented value differs.
    merged_cross = []
    for owner, owner_batches in batches.items():
        merged = []
        for batch in owner_batches:
            _merge_rows(merged, batch)
        merged = _union_module_availability(merged)
        facts[owner] = merged
        variants = {}
        for fact in merged:
            variants.setdefault(fact['id'].split(':variant:')[0], []).append(fact)
        for base, group in variants.items():
            if len(group) < 2 or not any(fact['status'] == 'conflict' for fact in group):
                continue
            differing = sorted({
                field for index, left in enumerate(group) for right in group[index + 1:]
                for field in set(left['value']) | set(right['value'])
                if left['value'].get(field) != right['value'].get(field)})
            cross_manual.append({'owner': '%s/%s' % owner, 'rule': base,
                                 'dimension': group[0]['dimension'],
                                 'fact_ids': [fact['id'] for fact in group],
                                 'differing_fields': differing,
                                 'manuals': sorted({source['manual_id'] for fact in group
                                                    for source in fact['sources']}),
                                 'values': [fact['value'] for fact in group]})

    # Native dotted owners: the index stores a few opcodes with '_' although the
    # manual writes a dot (``S.2PID`` beside ``S_2PID``). Publish the documented
    # dotted spelling as well, so retrieval by the native form is not empty.
    # The clone copies the scope and changes only the declared form: language
    # availability is never granted by an index spelling.
    entry_owner_keys = {(entry.get('target_model'), entry.get('opcode'))
                        for entry in entries or ()}
    native_owners = {}
    for entry in entries or ():
        opcode, model = entry.get('opcode'), entry.get('target_model')
        if opcode and model and '.' in opcode:
            native_owners.setdefault(opcode.replace('.', '_'), opcode)
    native_aliased = []
    for alias, native in sorted(native_owners.items()):
        if alias in {'S_GT', 'S_LT', 'S_EQ', 'S_GE', 'S_LE'} and native not in {
                'S.>', 'S.<', 'S.=', 'S.>=', 'S.<='}:
            # The process manual's relational glyph is a real native spelling;
            # S.GT/S.GE are index expansions, not additional declared owners.
            continue
        for (model, opcode), payloads in list(facts.items()):
            if opcode != alias or (model, native) in facts:
                continue
            copied = []
            for fact in payloads:
                # The clone keeps the whole scope: only the declared form changes.
                scope = copy.deepcopy(fact.get('scope') or {})
                documented_languages = list(scope.get('languages') or ())
                if documented_languages:
                    kept = [item for item in documented_languages if item != 'st']
                    if not kept:
                        # ST is the only documented language: the dotted native
                        # spelling is not an ST call form, so no clone is made.
                        continue
                    # A native spelling never inherits ST availability, but the
                    # languages the source really documents are preserved.
                    scope['languages'] = kept
                scope['models'] = [model]
                scope['forms'] = [native]
                clone = copy.deepcopy(fact)
                clone['scope'] = scope
                clone['sources'] = [dict(source, opcode=native)
                                    for source in fact['sources']]
                if fact['dimension'] == 'identity.declared_form':
                    clone['value'] = dict(fact['value'], declared_symbol=native,
                                          native_symbol=native,
                                          alias_forms=[native, alias],
                                          native_syntax='dotted')
                else:
                    clone['value'] = dict(fact['value'], native_symbol=native,
                                          alias_symbol=alias)
                copied.append(clone)
            if copied:
                target = facts.setdefault((model, native), [])
                _merge_rows(target, copied)
                native_aliased.append({'model': model, 'native_opcode': native,
                                       'index_alias': alias, 'facts': len(copied)})
            # The underscore spelling was only an artefact of the index: when it is
            # not an owner itself, drop it so no phantom owner is created.
            if (model, alias) not in entry_owner_keys:
                facts.pop((model, alias), None)

    for payloads in facts.values():
        for fact in payloads:
            dimension_totals[fact['dimension']] = dimension_totals.get(fact['dimension'], 0) + 1
            status_totals[fact['status']] = status_totals.get(fact['status'], 0) + 1
    ql_owner_keys = {key for key in entry_owners if key[0] in ql_models}
    covered = {key for key in ql_owner_keys if key in facts}
    uncovered = {}
    neighbours = {}
    for key in sorted(ql_owner_keys - covered):
        model, opcode = key
        section = next((section for (_manual, _outline), section in sections.items()
                        if opcode in section.symbols), None)
        if section is None:
            neighbours[key] = 'no_registered_ql_section'
            continue
        states, _conflicts = _model_states(section)
        if states.get(model) == 'unsupported':
            neighbours[key] = 'declared_unsupported_in_source_header'
        elif states.get(model) != 'supported':
            neighbours[key] = 'cpu_mark_missing_or_conditional'
        else:
            neighbours[key] = 'source_present_not_extracted'
    for reason in set(neighbours.values()):
        uncovered[reason] = sum(1 for value in neighbours.values() if value == reason)
    new_owners = sorted(key for key in facts if key not in entry_owners)
    without_effect = sorted(key for key, payloads in facts.items()
                            if not any(fact['dimension'].startswith('effects.')
                                       or fact['dimension'] in {'execution.program_terminator',
                                                                'execution.jump', 'memory.stack'}
                                       for fact in payloads))

    receipt = {
        'method': METHOD,
        'manuals': {manual: {'revision': revisions.get(manual), 'kind': spec['kind'],
                             'number': spec['number']}
                    for manual, spec in QL_MANUALS.items()},
        'excluded_manuals': dict(NON_INSTRUCTION_QL_MANUALS),
        'sections': {'total': len(inventory),
                     'with_facts': covered_sections,
                     'by_disposition': _counts(row['disposition'] for row in inventory),
                     'by_family': dict(sorted(family_totals.items()))},
        'facts': {'owners': len(facts), 'total': sum(len(value) for value in facts.values()),
                  'by_dimension': dict(sorted(dimension_totals.items())),
                  'by_status': dict(sorted(status_totals.items()))},
        'owner_coverage': {'compile_owners': len(ql_owner_keys), 'covered': len(covered),
                           'uncovered': dict(sorted(uncovered.items())),
                           'new_owner_candidates': len(new_owners),
                           'new_owner_sample': ['%s/%s' % key for key in new_owners[:40]]},
        'without_substantive_effect': {
            'owners': len(without_effect),
            'by_symbol': _counts(opcode for _model, opcode in without_effect),
            'sample': ['%s/%s' % key for key in without_effect[:40]],
            'meaning': 'documented heading parsed, but no operation statement could be attributed '
                       'to this symbol; the effect needs authoring from the section prose or a '
                       'local PDF check of its chart/table'},
        'gaps': {
            'no_registered_source': ['%s/%s' % key for key, reason in sorted(neighbours.items())
                                     if reason == 'no_registered_ql_section'],
            'source_not_reviewed': [
            {'manual': row['manual'], 'section': row['section'],
             'declared_symbols': row['declared_symbols'], 'fact_count': row['fact_count'],
             'reason': 'whole_section_review_incomplete'} for row in inventory
            if row['disposition'] != 'source_excluded' and not row['section_audited']],
        # These sources are present. Missing page-header, module/version or
        # manual-wide applicability binding is an extraction gap, never proof
        # that a manual or a CPU capability is absent.
        'cpu_scope_unknown': [
            {'manual': row['manual'], 'section': row['section'],
             'pages': row['pages'], 'chunks': row['chunk_ids'][:2],
             'cpu_scope_basis': row['cpu_scope_basis'],
             'cause': ('module_CPU_version_binding_incomplete' if row['manual'] == 'sh080785engm'
                       else 'process_control_applicability_binding_incomplete' if row['manual'] == 'sh080316ei'
                       else 'instruction_header_or_condition_binding_incomplete'),
             'source_present': True,
             'declared_symbols': row['declared_symbols']} for row in inventory
            if row['disposition'] != 'source_excluded'
            and row['cpu_scope_basis'] not in {'instruction_header', 'instruction_applicability_table'}],
        'cpu_scope_summary': {
            # Counted per source section, before the build's merger unions equal claims.
            'section_facts_by_basis_pre_merge': scope_fact_counts,
            'sections_by_basis': scope_sections,
            'note': ('instruction_header = the per-instruction CPU marks were read from '
                     'the page layout; manual_applicability = only the manual-wide list is '
                     'known, so no instruction-specific CPU scope is claimed. Counts are '
                     'per section and precede merging, so they exceed the published facts'),
            'remedy': {
                'entry_point': 'knowledge.instruction_compiler.compile_instruction_definitions',
                'requirement': 'pass source_dir so page-header geometry is parsed',
                'sources_present': True,
                'note': ('all five manuals ship with the private source set, so the marks '
                         'can be re-extracted; until then this review keeps the manual-level '
                         'scope and never asserts support for a single CPU'),
            },
        },
        'source_present_not_extracted': [
                {'manual': row['manual'], 'section': row['section'], 'reason': row['reason'],
                 'declared_symbols': row['declared_symbols']}
                for row in inventory
                if row['disposition'] == 'source_present_not_extracted'],
            'no_registered_source_meaning': 'provided_owner_has_no_section_in_this_five_manual_union_not_global_source_absence',
            'source_conflict': conflicts + cross_manual + parameter_review['source_conflict'] + [
                {'manual': section.manual, 'section': section.title, **row}
                for section in sections.values() for row in getattr(section, '_n_behavior_review', {}).get('source_conflict', [])],
            'behavior_source_not_stated': [
                {'manual': section.manual, 'section': section.title, **row}
                for section in sections.values()
                for review in [getattr(section, '_i_behavior_review', {}), getattr(section, '_n_behavior_review', {})]
                for row in review.get('source_not_stated', [])],
            'behavior_source_present_not_extracted': [
                {'manual': section.manual, 'section': section.title, **row}
                for section in sections.values()
                for review in [getattr(section, '_i_behavior_review', {}), getattr(section, '_n_behavior_review', {})]
                for row in review.get('source_present_not_extracted', [])],
            'parameter_contracts': parameter_review,
            'i_control_behavior_review': [section._i_behavior_review for section in sections.values()
                                         if getattr(section, '_i_behavior_review', None)],
            'n_behavior_review': [section._n_behavior_review for section in sections.values()
                                  if getattr(section, '_n_behavior_review', None)],
            'unverified_documented_fields': unverified_fields[:200],
            'witness_binding_failed': unbound[:200],
            'grouped_heading_symbol_unattributed': unattributed[:200],
            'figure_or_table_only': [
                {'manual': row['manual'], 'section': row['section']}
                for row in inventory
                if row['reason'] in {'no_authored_rule_matched', 'no_authored_family_for_heading'}],
            'cross_witness_merged': merged_cross[:200],
            'cross_witness_merged_count': len(merged_cross),
            'native_alias_owners': native_aliased[:200],
            'native_alias_owner_count': len(native_aliased),
            'native_pending': {
                'note': 'documented behaviour only; no project, native compile, simulator or '
                        'hardware result is claimed by this review',
                'unpromoted': ['call_available', 'core_capability', 'native_parameter_order',
                               'execution_timing_for_this_cpu']},
        },
        'inventory': inventory,
        'database_mutated': False,
        'executable_behavior_dsl': False,
    }
    return facts, receipt


def _counts(values):
    counts = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _merge_sources(existing, extra):
    """Union of page references, one entry per manual page range."""
    merged, seen = list(existing), {(item['manual_id'], tuple(item['pdf_pages'])) for item in existing}
    for item in extra:
        key = (item['manual_id'], tuple(item['pdf_pages']))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged





# ---------------------------------------------------------------------------
# Documented symbol families and table-documented effects.
# Every rule still requires its own witness sentence in the same section, so a
# rule the manual does not state for this form publishes nothing.
# ---------------------------------------------------------------------------
_EXTRA_CLASSES = (
    ('clock_arithmetic', 'effects.clock', 'clock_addition',
     re.compile(r'[Cc]lock data[^.]{0,60}(?:adds?|subtracts?|addition|subtraction)|'
                r'(?:Adds?|Subtracts?)[^.]{0,40}clock data')),
    ('bcd_arithmetic', 'effects.arithmetic', 'bcd_arithmetic',
     re.compile(r'BCD (?:data|8-digit|4-digit)[^.]{0,60}'
                r'(?:multiplication|division|addition|subtraction)', re.I)),
    ('float_arithmetic', 'effects.arithmetic', 'floating_point_arithmetic',
     re.compile(r'floating[- ]point data[^.]{0,40}'
                r'(?:multiplication|division|addition|subtraction)', re.I)),
    ('block_addsub', 'effects.arithmetic', 'block_addition_subtraction',
     re.compile(r'[Bb]atch (?:adds|subtracts)|(?:adds|subtracts)[^.]{0,40}(?:n points|block)', re.I)),
    ('string_transfer', 'effects.string', 'string_transfer',
     re.compile(r'[Cc]haracter string[^.]{0,60}(?:transferred|transfers|batch)', re.I)),
    ('rom_access', 'effects.memory', 'standard_rom_access',
     re.compile(r'standard ROM', re.I)),
    ('shared_memory', 'effects.multiple_cpu', 'cpu_shared_memory_access',
     re.compile(r'CPU shared memory|shared memory of the host CPU', re.I)),
    ('link_refresh', 'effects.data_link', 'refresh_device_access',
     re.compile(r'refresh device|link refresh', re.I)),
    ('system_switch', 'effects.redundant', 'system_switching',
     re.compile(r'system switching|switches? the system', re.I)),
    ('offset_gain', 'effects.module_instruction', 'offset_gain_operation',
     re.compile(r'[Oo]ffset/gain')),
    ('absolute_position', 'effects.module_instruction', 'absolute_position_restore',
     re.compile(r'absolute position', re.I)),
    ('module_status', 'effects.diagnosis', 'module_status_read',
     re.compile(r'communication status|module status|unit status|busy flag', re.I)),
)
_EXTRA_CLASSES = _EXTRA_CLASSES + (
    ('program_stop', 'execution.program_terminator', 'stop_cpu_operation',
     re.compile(r'stops the CPU module operation|stops? the operation of the CPU module', re.I)),
    ('decode_bit', 'effects.bit_processing', 'decode_to_bit',
     re.compile(r'Turns ON the bit position', re.I)),
    ('encode_bit', 'effects.bit_processing', 'encode_from_bit',
     re.compile(r'[Ee]ncodes the bit pattern|bit pattern[^.]{0,40}(?:is )?encoded', re.I)),
    ('jump', 'execution.jump', 'jump_to_pointer',
     re.compile(r'Unconditionally executes program of designated pointer number|'
                r'executes the program of the designated pointer|'
                r'jump to the (?:pointer|specified)', re.I)),
    ('call_program', 'execution.jump', 'call_subroutine',
     re.compile(r'[Ee]xecutes the subroutine program|'
                r'[Cc]alls the (?:subroutine|program)', re.I)),
    ('return_program', 'execution.jump', 'return_from_subroutine',
     re.compile(r'[Rr]eturns? (?:to|from) the (?:sequence program|subroutine|call source)', re.I)),
    ('watchdog', 'effects.diagnosis', 'watchdog_timer_reset',
     re.compile(r'watchdog timer', re.I)),
    ('obtain', 'effects.math_function', 'obtain_value',
     re.compile(r'\bObtains? the [a-z0-9 \-]+', re.I)),
    ('combine', 'effects.data_processing', 'combine_bits',
     re.compile(r'[Cc]ombines? the (?:data|bits)|[Ll]inks? (?:the )?(?:lower|upper|data|bits)', re.I)),
)
_OPERATION_CLASSES = _EXTRA_CLASSES + _OPERATION_CLASSES

#: Documented effect of one symbol family; the witness sentence decides whether
#: this particular form documents it.
SYMBOL_RULES = (
    (('OUT_ST', 'OUTH_ST'), 'effects.timer_operation',
     {'operation': 'count_up_on_coil_on', 'device_class': 'retentive_timer',
      'contact_on_at_set_value': True, 'cleared_by': 'RST'},
     (r'the timer coil goes ON and the timer counts up to the value',
      r'To clear the present value of a retentive timer and turn the contact OFF')),
    (('IRET', 'IRETP'), 'effects.state_change',
     {'operation': 'return_from_interrupt', 'context': 'interrupt_program',
      'completes_interrupt_processing': True},
     (r'Returns to sequence program processing following the execution of the IRET instruction',
      r'Indicates the completion of interrupt program processing')),
    (('BTOW', 'BTOWP'), 'effects.data_processing',
     {'operation': 'link_bytes', 'unit': 'byte_to_word', 'n_operand': 'bytes'},
     (r'[Ll]inks the lower 8 bits',)),
    (('WTOB', 'WTOBP'), 'effects.data_processing',
     {'operation': 'dissociate_bytes', 'unit': 'word_to_byte', 'n_operand': 'bytes'},
     (r'[Dd]issociates',)),
    (('OUT_T', 'OUTH_T', 'OUT_ST', 'OUTH_ST'), 'effects.timer_operation',
     {'operation': 'count_up_on_coil_on', 'device_class': 'timer',
      'contact_on_at_set_value': True},
     (r'the timer coil goes ON and the timer counts up to the value',
      r'the timer counts up to the value')),
    (('OUT_C', 'OUTH_C'), 'effects.counter_operation',
     {'operation': 'count_up_on_rising_edge', 'device_class': 'counter',
      'reset_by_rst': True, 'count_on_rising_edge_only': True},
     (r'1 is added to the present value', r'the counter counts up',
      r'counts up the present value',
      r'No count is conducted with the operation results at ON')),
    (('OUT_F', 'OUT_FP'), 'effects.state_change',
     {'operation': 'annunciator_output', 'device_class': 'annunciator',
      'state': 'on'},
     (r'Turns ON the annunciator', r'[Oo]utput to the annunciator',
      r'Sets the annunciator')),
    (('SET_F',), 'effects.state_change',
     {'operation': 'set', 'operand_role': 'file_register_bit', 'state': 'on'},
     (r'Sets the device designated by \(D\)', r'Sets \(D\)',
      r'Sets the annunciator', r'[Ss]ets the annunciator designated by \(D\)')),
    (('RST_F',), 'effects.state_change',
     {'operation': 'reset', 'operand_role': 'file_register_bit', 'state': 'off'},
     (r'Resets the device designated by \(D\)', r'Resets \(D\)',
      r'Resets the annunciator', r'[Rr]esets the annunciator designated by \(D\)')),
    (('FF', 'FFP'), 'effects.state_change',
     {'operation': 'invert_state', 'documented_effect': 'output_inversion'},
     (r'Inverts the (?:device|output|operation result)', r'output inversion')),
    (('SFT', 'SFTP'), 'effects.shift',
     {'operation': 'shift', 'device_class': 'bit_device'},
     (r'Shifts? the (?:bit|data)', r'shift register')),
    (('DELTA', 'DELTAP'), 'effects.operation',
     {'operation': 'pulse_output', 'target': 'direct_access_output'},
     (r'pulse output of direct access output',)),
)


def _symbol_family_specs(section, symbol):
    """Effect facts keyed by the declared symbol family."""
    blob = ' '.join(section.statements(symbol)) or section.text
    for names, dimension, value, patterns in SYMBOL_RULES:
        if symbol not in names:
            continue
        witness = _witness(blob, patterns)
        if not witness:
            continue
        return [_spec('family', dimension, (witness,), dict(value))]
    return []


def _table_effect(section, symbol):
    """Operation statements that the section documents in a table row."""
    for line in section.tables:
        if len(re.findall(r'[a-z]{3,}', line)) < 3:
            continue
        for name, dimension, token, pattern in (getattr(section, 'ladder', None)
                                                or _OPERATION_CLASSES):
            found = pattern.search(line)
            if not found:
                continue
            value = {'operation_class': name, 'operation': token,
                     'basis': 'documented_table_row'}
            value.update(_fields(line))
            return [_spec('operation', dimension, (found.group(0),), value)]
    return []


# ---------------------------------------------------------------------------
# Documented function phrases.
#
# Some instruction families state their behaviour in one prose sentence instead
# of an "operation" bullet (BCD trigonometry, buffer memory access, dedicated
# module instructions).  Such a sentence is usable only when it names the acted
# object and says where the result goes, so the rule below demands a destination
# marker in the same sentence; otherwise the section stays unextracted.
# ---------------------------------------------------------------------------
FUNCTION_PHRASES = (
    (r'Evaluates the sine function', 'evaluate_sine', 'angle_value'),
    (r'Evaluates the cosine function', 'evaluate_cosine', 'angle_value'),
    (r'Evaluates the tangent function', 'evaluate_tangent', 'angle_value'),
    (r'[Pp]erforms the PI operation', 'pid_operation', 'pid_control_data'),
    (r'[Rr]eads? out [^.]{0,90}buffer memory', 'read_buffer_memory', 'buffer_memory'),
    (r'[Ww]rites? [^.]{0,90}buffer memory', 'write_buffer_memory', 'buffer_memory'),
    (r'[Rr]eads? the clock data', 'read_clock_data', 'clock_data'),
    (r'[Ww]rites? the clock data', 'write_clock_data', 'clock_data'),
    (r'[Oo]pens? the connection', 'open_connection', 'connection'),
    (r'[Cc]loses? the connection', 'close_connection', 'connection'),
    (r'[Pp]erforms? the scaling', 'scale_value', 'scaled_value'),
    (r'[Ii]nitiali[sz]es the setting data', 'initialize_setting_data', 'setting_data'),
)
_DESTINATION = re.compile(r'\((?:d|D)\d?\)(?:\[\d+\])?|'
                          r'stores? (?:the [a-z ]+ )?(?:to|in|at) the device')
_OPERAND = re.compile(r'\((s|S|d|D)\d?\)')


def _function_phrase_specs(section, symbol):
    """Effect stated by one documented function sentence with a destination."""
    bases = {_form_base(item) for item in (section.symbols or [symbol])}
    shared_forms = len(bases) <= 1
    for statement in section.statements(symbol):
        if not shared_forms and not re.search(r'(?<![A-Za-z0-9_])' + re.escape(symbol)
                                              + r'(?![A-Za-z0-9_])', statement):
            continue
        if not _DESTINATION.search(statement):
            continue
        for pattern, operation, object_name in FUNCTION_PHRASES:
            found = re.search(pattern, statement)
            if not found:
                continue
            operands = list(dict.fromkeys(item.upper() for item in _OPERAND.findall(statement)))
            value = {'operation': operation, 'object': object_name, 'result': 'stored_in_d'}
            if operands:
                value['documented_operands'] = operands
            return [_spec('operation', 'effects.operation', (found.group(0),), value)]
    return []


_family_specs_documented = family_specs





# ---------------------------------------------------------------------------
# Per-family precise rules: conversion functions and special mathematics.
#
# Both families document one operation per section, so the required slots are
# fixed and explicit:
#   * conversion: from_type / to_type / result=function_return_value,
#     corroborated by the documented ST function form of the same form;
#   * mathematics: function / object / result=stored_in_d, taken from the
#     documented sentence that names the function and the destination.
# An instruction whose sentence lacks the destination is not published here.
# ---------------------------------------------------------------------------
N_TYPE_PAIR = re.compile(r'^([A-Z][A-Z0-9]*)_TO_([A-Z][A-Z0-9]*?)(_E)?$')

MATH_TOKENS = {
    'ASIN': 'argument_value', 'ACOS': 'argument_value', 'ATAN': 'argument_value',
    'SIN': 'angle_value', 'COS': 'angle_value', 'TAN': 'angle_value',
    'RAD': 'angle_value', 'DEG': 'angle_value',
    'EXP': 'exponent_value', 'POW': 'base_value',
    'LOG': 'numeric_value', 'LOG10': 'numeric_value', 'SQR': 'numeric_value',
    'RND': 'numeric_value', 'SRND': 'numeric_value', 'BSIN': 'angle_value',
    'BACOS': 'argument_value', 'BATAN': 'argument_value',
    'BSQR': 'numeric_value', 'BDSQR': 'double_word_value',
    'ABS': 'numeric_value', 'SQRT': 'numeric_value',
}
MATH_TOKEN_ORDER = ('BACOS', 'BATAN', 'BSIN', 'ASIN', 'ACOS', 'ATAN', 'SIN', 'COS',
                    'TAN', 'RAD', 'DEG', 'EXP', 'POW', 'LOG10', 'LOG', 'SQRT', 'SQR',
                    'SRND', 'RND', 'ABS')
MATH_STORE = re.compile(r'stored (?:in|into|at)|stores? (?:the )?(?:operation )?results?|'
                        r'stores? [^.]{0,60}? (?:to|in|at) the (?:word |bit |device )?'
                        r'(?:device|number)', re.I)
MATH_WORDS = {'arc': 'asin', 'sine': 'sin', 'cosine': 'cos', 'tangent': 'tan',
              'logarithm': 'log', 'exponent': 'exp'}


def _n_conversion_specs(section, symbol):
    """ST conversion function: documented type change, value returned."""
    match = N_TYPE_PAIR.match(symbol)
    if not match:
        return _n_bit_specs(section, symbol)
    base = symbol[:-2] if symbol.endswith('_E') else symbol
    form_line = 'Function with EN/ENO (%s)' % symbol if symbol.endswith('_E') \
        else 'Function without EN/ENO (%s)' % symbol
    witness = _witness(section.text, (form_line, form_line.split('(')[0].strip(),
                                      'Converting '))
    if not witness:
        return []
    value = {'operation': 'convert_type',
             'from_type': match.group(1).lower(),
             'to_type': match.group(2).lower(),
             'result': 'function_return_value',
             'documented_form': 'st_function_return' if symbol.endswith('_E')
                                 else 'st_function'}
    if base != symbol:
        value['enable'] = 'EN'
    return [_spec('operation', 'effects.operation', (witness,), value)]


TITLE_TOKENS = (
    ('exponentiation', 'POW'), ('exponent', 'EXP'), ('common logarithm', 'LOG10'),
    ('sin operation', 'SIN'), ('cos operation', 'COS'), ('tan operation', 'TAN'),
    ('sin-1', 'ASIN'), ('cos-1', 'ACOS'), ('tan-1', 'ATAN'),
    ('natural logarithm', 'LOG'), ('arc sine', 'ASIN'), ('arc cosine', 'ACOS'),
    ('arc tangent', 'ATAN'), ('tangent', 'TAN'), ('cosine', 'COS'), ('sine', 'SIN'),
    ('radian', 'RAD'), ('angle to radian', 'RAD'), ('random number', 'RND'),
    ('series update', 'SRND'), ('square root', 'SQR'), ('absolute', 'ABS'),
)


def _section_sentences(section):
    """Documented sentences of the section, in order."""
    sentences = []
    for part in re.split(r'(?<=[.;:])\s+', section.text):
        item = ' '.join(part.split())
        if len(item) >= 30 and re.search(r'[a-z]{3}', item) and item not in sentences:
            sentences.append(item)
    return sentences


N_BIT_OPERATIONS = (
    ('GET_BIT_OF_INT', 'read_bit', 'word_bit', 'function_return_value'),
    ('SET_BIT_OF_INT', 'write_bit', 'word_bit', 'word_modified'),
    ('CPY_BIT_OF_INT', 'copy_bit_array', 'word_bits', 'stored_in_destination'),
    ('CPY_BITARR', 'copy_bit_array', 'bit_array', 'stored_in_destination'),
    ('GET_BOOL_ADDR', 'convert_type', 'boolean_value', 'function_return_value'),
)
N_BIT_HINTS = {
    'read_bit': r'[Ss]pecif\w+ bit|[Rr]eads? (?:the |a )?bit',
    'write_bit': r'[Ww]rites? (?:the |a )?bit|bit write',
    'copy_bit_array': r'[Cc]op(?:y|ies) (?:the )?bits?|bit (?:array|copy)',
    'convert_type': r'[Cc]onverts?|type conversion',
}


def _n_bit_specs(section, symbol):
    """ST bit access/copy functions: documented target and result of the change."""
    base = symbol[:-2] if symbol.endswith('_E') else symbol
    entry = next((item for item in N_BIT_OPERATIONS if item[0] == base), None)
    if not entry:
        return []
    _name, operation, object_name, result = entry
    form_line = ('Function with EN/ENO (%s)' % symbol if symbol.endswith('_E')
                 else 'Function without EN/ENO (%s)' % symbol)
    witness = _witness(section.text, (form_line, N_BIT_HINTS[operation]))
    if not witness:
        return []
    value = {'operation': operation, 'object': object_name, 'result': result}
    if symbol.endswith('_E'):
        value['enable'] = 'EN'
    return [_spec('operation', 'effects.data_processing', (witness,), value)]


def _special_math_specs(section, symbol):
    """Trigonometric / logarithmic / power instruction with a documented store.

    The witness is taken verbatim from the raw section text, so it always binds
    to the page that states it; sentences with no documented store are skipped.
    """
    raw = section.text
    title = section.title.lower()
    # Only the declared form's own function: "inverse sine (SIN-1)" in the ASIN
    # section must never be extracted as SIN.
    function, precision = _math_function(symbol)
    for token in ([function] if function else MATH_TOKEN_ORDER):
        pattern = re.compile(
            r'[^.]{0,140}?(?<![A-Za-z0-9_])' + token + r'(?![A-Za-z0-9_-])[^.]{0,240}?'
            r'(?:stored (?:in|into|at)|stores? (?:the )?(?:operation )?results?'
            r'|stores? [^.]{0,60}? (?:to|in|at) the (?:word |bit |device )?(?:device|number))'
            r'[^.]{0,120}', re.I)
        found = pattern.search(raw)
        if not found:
            continue
        clause = found.group(0)
        if candidate_needs_bcd(token) and 'BCD' not in raw:
            continue
        value = {'operation': 'evaluate_' + token.lower(), 'function': token,
                 'object': MATH_TOKENS[token], 'result': 'stored_in_d'}
        if precision != 'unknown':
            value['precision'] = precision
        operands = list(dict.fromkeys(item.upper() for item in
                                      re.findall(r'\((s|S|d|D)\d?\)', clause)))
        if operands:
            value['documented_operands'] = operands
        if re.search(r'BCD', clause) or 'bcd' in title:
            value['data_format'] = 'bcd'
        witness = clause.strip()
        if not witness:
            continue
        return [_spec('operation', 'effects.math_function', (witness,), value)]
    fallback = next((name for keyword, name in TITLE_TOKENS if keyword in title), None)
    if fallback == 'SQR' and 'bcd' in title:
        fallback = 'BSQR'
    base = _form_base(symbol)
    if base in MATH_TOKENS:
        # the declared form names the function (BSQR beside BDSQR)
        fallback = base
    if fallback:
        return _title_math_specs(section, fallback)
    return []




def _title_math_specs(section, token):
    """Section whose title names the function; witness must still show the store."""
    raw = section.text
    for match in re.finditer(r'[^.]{0,120}(?:stored (?:in|into|at)|stores? [^.]{0,60}?'
                             r'(?:to|in|at) the (?:word |bit |device )?(?:device|number))'
                             r'[^.]{0,120}', raw, re.I):
        clause = match.group(0).strip()
        if not clause:
            continue
        value = {'operation': 'evaluate_' + token.lower(), 'function': token,
                 'object': MATH_TOKENS[token], 'result': 'stored_in_d'}
        operands = list(dict.fromkeys(item.upper() for item in
                                      re.findall(r'\((s|S|d|D)\d?\)', clause)))
        if operands:
            value['documented_operands'] = operands
        return [_spec('operation', 'effects.math_function', (clause,), value)]
    # Figure-style operand table: "Data on which the ... is evaluated" together
    # with "device that stores the operation result" documents object and result.
    evaluated = re.search(r'(?:Data|Value|Angle) on which the [a-z0-9 \-]{3,40}?'
                          r'(?:function )?is evaluated[^.]{0,90}', raw, re.I)
    stored = re.search(r'(?:device|area) that stores the operation result[^.]{0,60}', raw, re.I)
    if evaluated and stored:
        value = {'operation': 'evaluate_' + token.lower(), 'function': token,
                 'object': MATH_TOKENS[token], 'result': 'stored_in_d',
                 'documented_operands': ['S', 'D'],
                 'destination_role': 'documented_operand_figure'}
        return [_spec('operation', 'effects.math_function',
                      (evaluated.group(0).strip(), stored.group(0).strip()), value)]
    # The operand table documents the destination role: "(D): ... will be stored".
    role = re.search(r'\((d|D)\)[^.]{0,180}?stored', raw, re.I)
    if role:
        clause = role.group(0).strip()
        value = {'operation': 'evaluate_' + token.lower(), 'function': token,
                 'object': MATH_TOKENS[token], 'result': 'stored_in_d',
                 'documented_operands': ['D'], 'destination_role': 'documented_operand_role'}
        source = re.search(r'\((s|S)\)[^.]{0,140}', raw)
        if source:
            value['documented_operands'] = ['S', 'D']
        return [_spec('operation', 'effects.math_function', (clause,), value)]
    return []


def candidate_needs_bcd(token):
    return token in {'BSIN', 'BACOS', 'BATAN'}


FAMILY_FALLBACKS = {
    'n_conversion': _n_conversion_specs,
    'special_math': _special_math_specs,
}


def family_specs(section, symbol):  # noqa: F811 - documented fallbacks appended
    specs, family = _family_specs_documented(section, symbol)
    specs = [item for item in specs if not _placeholder_spec(item)]
    if any(item['dimension'].startswith(('effects.', 'execution.', 'memory.', 'resources.'))
           and set(item['value']) - {'operation', 'operation_class', 'basis'}
           for item in specs):
        return specs, family
    for builder in (FAMILY_FALLBACKS.get(family), _function_phrase_specs):
        if builder is None:
            continue
        extra = [item for item in builder(section, symbol) if not _placeholder_spec(item)]
        if extra:
            return specs + extra, family
    return specs, family


# ---------------------------------------------------------------------------
# S. (SFC loop control / analog) instruction family.
#
# Each section documents one operation sentence. The required slots are:
#   operation  -> documented verb + documented noun (never a bare verb),
#   object     -> documented input operand (E1/S1) or the control variable,
#   result     -> stored_in_d when the sentence stores into a device, else the
#                 documented control state the sentence names.
# A sentence with neither a documented store nor a documented state is not
# published.
# ---------------------------------------------------------------------------
S_VERBS = (
    ('Converts', 'convert'), ('Performs', 'perform'), ('Calculates', 'calculate'),
    ('Obtains', 'obtain'), ('Reads', 'read'), ('Writes', 'write'), ('Sets', 'set'),
    ('Clears', 'clear'), ('Adds', 'add'), ('Subtracts', 'subtract'),
    ('Multiplies', 'multiply'), ('Divides', 'divide'), ('Compares', 'compare'),
    ('Selects', 'select'), ('Limits', 'limit'), ('Accumulates', 'accumulate'),
    ('Averages', 'average'), ('Squares', 'square'), ('Integrates', 'integrate'),
    ('Differentiates', 'differentiate'), ('Filters', 'filter'), ('Scales', 'scale'),
    ('Updates', 'update'), ('Stores', 'store'), ('Outputs', 'output'),
    ('Executes', 'execute'), ('Starts', 'start'), ('Stops', 'stop'),
    ('Returns', 'return'), ('Turns', 'turn'),
)
S_NOUNS = (
    ('engineering value', 'engineering_value'),
    ('range check', 'range_check'),
    ('input limiter', 'input_limiter'),
    ('digital filter', 'digital_filter'),
    ('PID operation', 'pid_operation'),
    ('2-position', 'two_position_operation'),
    ('two-position', 'two_position_operation'),
    ('sample PI', 'sample_pi_operation'),
    ('incomplete derivative', 'incomplete_derivative'),
    ('dead time', 'dead_time_compensation'),
    ('coefficient', 'coefficient_processing'),
    ('square root', 'square_root'),
    ('addition', 'addition'), ('subtraction', 'subtraction'),
    ('multiplication', 'multiplication'), ('division', 'division'),
    ('average', 'average'), ('total', 'total'),
    ('upper limit', 'upper_limit'), ('lower limit', 'lower_limit'),
    ('band', 'band_check'), ('velocity', 'velocity_type_operation'),
    ('position type', 'position_type_operation'),
    ('derivative', 'derivative'), ('integral', 'integral'),
    ('bump', 'bump_check'), ('ramp', 'ramp_response'),
    ('control cycle', 'control_cycle_processing'),
    ('comparison', 'comparison'), ('selection', 'selection'),
    ('first order lag', 'first_order_lag'), ('lead', 'lead_compensation'),
    ('unlimited', 'unlimited_operation'),
    ('pulse', 'pulse_output'), ('duty', 'duty_output'),
    ('clock', 'clock_data'), ('counter', 'count_value'),
    ('set value', 'set_value'), ('input value', 'input_value'),
    ('output value', 'output_value'),
)
S_STORE = re.compile(r'stores? (?:the )?(?:result|value|operation result)[^.]{0,40}?'
                     r'into the (?:device|word)[^.]*', re.I)
S_STATE = re.compile(r'(?:when|if) the [a-z ]{4,40} (?:is|are) (?:reached|satisfied|turned on)',
                     re.I)




class _SStoreCopy(object):
    """A store clause found elsewhere in the section, kept bindable."""

    def __init__(self, match):
        self._text = match.group(0)

    def group(self, index=0):
        return self._text


S_EXTRA_NOUNS = (
    ('engineering value', 'engineering_value'),
    ('engineering conversion', 'engineering_conversion'),
    ('function generator', 'function_generator_output'),
    ('inverse function generator', 'inverse_function_generator'),
    ('temperature/pressure correction', 'temperature_pressure_correction'),
    ('temperature or pressure', 'temperature_pressure_correction'),
    ('% value', 'percent_value'),
    ('manipulated value', 'manipulated_value'),
    ('input addition', 'input_addition'),
    ('change rate', 'change_rate_limit'),
    ('reset windup', 'reset_windup'),
    ('output conversion', 'output_conversion'),
    ('upper/lower limiter', 'limiter_processing'),
    ('integration', 'integration'),
    ('bit data', 'bit_output'),
    ('set value', 'set_value_comparison'),
    ('duty', 'duty_output'),
    ('square root', 'square_root'),
    ('dead time', 'dead_time_compensation'),
    ('sample PI', 'sample_pi_operation'),
    ('PID operation', 'pid_operation'),
    ('range check', 'range_check'),
    ('input limiter', 'input_limiter'),
    ('digital filter', 'digital_filter'),
)
S_OUTPUT = re.compile(r'\b(?:outputs?|output|outputted)\b', re.I)


def _s_instruction_specs(section, symbol):
    """S. instruction: documented verb, documented noun, documented result."""
    for statement in _section_sentences(section):
        verb = None
        position = len(statement)
        for word, name in S_VERBS:
            found = re.search(r'(?<![A-Za-z])' + re.escape(word) + r'\b', statement,
                              re.I)
            if found and found.start() < position:
                verb, position = name, found.start()
        if not verb:
            passive = re.search(r'\bis (added|subtracted|multiplied|divided|averaged|'
                                r'integrated|compared|selected|converted|corrected|'
                                r'subject to [a-z /]{3,40})\b', statement, re.I)
            if passive:
                verb = {'added': 'add', 'subtracted': 'subtract', 'multiplied': 'multiply',
                        'divided': 'divide', 'averaged': 'average',
                        'integrated': 'integrate', 'compared': 'compare',
                        'selected': 'select', 'converted': 'convert',
                        'corrected': 'correct'}.get(passive.group(1).lower(),
                                                    'correct')
        if not verb:
            continue
        noun = None
        for word, name in tuple(S_EXTRA_NOUNS) + tuple(S_NOUNS):
            if word.lower() in statement.lower():
                noun = name
                break
        if not noun:
            continue
        store = S_STORE.search(statement)
        state = S_STATE.search(statement)
        output = S_OUTPUT.search(statement)
        if not store:
            # The layout may split the store clause into the next fragment; the
            # same section still documents where the result goes.
            store = re.search(r'stores? the [^.]{0,60}?(?:into|in|to) the device[^.]{0,40}',
                              section.text)
            if store:
                store = _SStoreCopy(store)
        if not store and not output:
            # ... or the operand table documents the destination role.
            role = re.search(r'\((d1|d2|d)\)[^.]{0,160}?(?:stored|stores)', section.text, re.I)
            if role:
                store = _SStoreCopy(role)
        if not store and not output:
            # ... or the section documents the output action of the computed value.
            later = re.search(r'(?:Turns ON/OFF the device|outputs? the [a-z ]{3,40}'
                              r'|is output|are output)[^.]{0,60}', section.text, re.I)
            if later:
                output = _SStoreCopy(later)
        if not store and not state and not output:
            continue
        value = {'operation': verb + '_' + noun,
                 'object': 'input_value' if re.search(r'\((E1|S1|s1)\)', statement)
                 else 'control_variable'}
        if store:
            value['result'] = 'stored_in_d'
        elif output:
            value['result'] = 'output_value'
        else:
            value['result'] = 'control_state'
            value['condition'] = 'control_cycle_reached'
        operands = list(dict.fromkeys(item.upper() for item in
                                      re.findall(r'\((E1|E2|S1|S2|S3|D1|D2|s1|s2|s3|d1|d2)\)',
                                                 statement)))
        if operands:
            value['documented_operands'] = operands
        raw = section.text
        needles = []
        for word, _name in tuple(S_EXTRA_NOUNS) + tuple(S_NOUNS):
            if word.lower() not in statement.lower():
                continue
            position = raw.lower().find(word.lower())
            if position >= 0:
                needles.append(' '.join(raw[max(0, position - 40):
                                            position + len(word) + 60].split()))
            break
        if store:
            needles.append(' '.join(store.group(0).split()))
        needles = tuple(dict.fromkeys(item for item in needles if item))
        if not needles:
            continue
        return [_spec('operation', 'effects.operation', needles, value)]
    return []


for _family in ('i_io', 'i_control', 'i_compensation', 'i_arithmetic', 'i_comparison',
                'i_tuning'):
    FAMILY_FALLBACKS[_family] = _s_instruction_specs


# ---------------------------------------------------------------------------
# Sign inversion (NEG/DNEG/ENEG/EDNEG) — the only documented operation of these
# forms is reversing the sign of the 16/32/64-bit or floating-point data at (D)
# and storing it back at (D).  Required slots: operation, object, result.
# ---------------------------------------------------------------------------
SIGN_INVERSION_BASES = {'NEG', 'DNEG', 'ENEG', 'EDNEG'}
SIGN_INVERSION = re.compile(
    r'Reverses the sign of the (.{0,90}?) designated by \((D|d)\)[^.]{0,40}?'
    r'(?:stores|stored) (?:at|in|into) the (?:device|word) designated by \((D|d)\)',
    re.I)


def _sign_inversion_specs(section, symbol):
    """Documented sign inversion with its object and destination."""
    base = _form_base(symbol)
    if base not in SIGN_INVERSION_BASES:
        return []
    match = SIGN_INVERSION.search(section.text)
    if not match:
        return []
    object_text = match.group(1).lower()
    if 'floating' in object_text:
        object_name = 'floating_point_value'
    elif '64' in object_text:
        object_name = 'double_word_value'
    elif '32' in object_text:
        object_name = 'double_word_value'
    else:
        object_name = 'word_value'
    return [_spec('operation', 'effects.data_processing',
                  (match.group(0),),
                  {'operation': 'invert_sign', 'object': object_name,
                   'result': 'stored_in_d', 'documented_operands': ['D']})]


_previous_conversion = FAMILY_FALLBACKS.get('conversion')
_previous_math = FAMILY_FALLBACKS.get('special_math')


def _conversion_family(section, symbol):
    for builder in (_sign_inversion_specs, _previous_conversion):
        if builder is None:
            continue
        specs = builder(section, symbol)
        if specs:
            return specs
    return []


def _math_family(section, symbol):
    for builder in (_sign_inversion_specs, _previous_math):
        if builder is None:
            continue
        specs = builder(section, symbol)
        if specs:
            return specs
    return []


FAMILY_FALLBACKS['conversion'] = _conversion_family
FAMILY_FALLBACKS['special_math'] = _math_family


# CPU availability and hardware predicates are authored independently of the
# instruction's behavioural review. Absence of an icon never means unsupported.
_BASIC_Q = frozenset({'Q00J', 'Q00', 'Q01'})
_HIGH_Q = frozenset({'Q02', 'Q02H', 'Q06H', 'Q12H', 'Q25H'})


def _predicate(operation, *arguments):
    return {'op': operation, 'type': {'kind': 'bool'}, 'args': list(arguments)}


def _context_test(name, operation='eq', value=True):
    kind = {'kind': 'bool'} if type(value) is bool else {'kind': 'int', 'bits': 32, 'signed': False}
    return _predicate(operation, {'op': 'state', 'type': kind, 'name': name},
                      {'op': 'constant', 'type': kind, 'value': value})


def _join_predicates(operation, predicates):
    values = list(predicates)
    if not values:
        return {'op': 'constant', 'type': {'kind': 'bool'}, 'value': operation == 'and'}
    while len(values) > 1:
        values = [_predicate(operation, *values[index:index + 2]) if index + 1 < len(values) else values[index]
                  for index in range(0, len(values), 2)]
    return values[0]


def _condition_with_predicate(item):
    condition = copy.deepcopy(item)
    if set(item) == {'context', 'equals'}:
        return condition
    if 'predicate' in condition:
        return condition
    kind = item['kind']
    if kind == 'serial_number_minimum':
        field = 'cpu_serial_first_five'
        condition['predicate'] = _context_test(field, 'ge', int(item['minimum']))
        condition['required_context'] = [{'field': field, 'type': 'UINT32', 'digits': 5,
                                          'meaning': 'first_five_CPU_serial_digits_as_decimal'}]
    elif kind == 'function_version_minimum':
        field = 'cpu_function_version_rank'
        condition['predicate'] = _context_test(field, 'ge', ord(item['minimum']) - ord('A') + 1)
        condition['required_context'] = [{'field': field, 'type': 'UINT32',
                                          'encoding': {'A': 1, 'B': 2, 'C': 3, 'D': 4}}]
    elif kind in {'required_module_family', 'required_cpu_function'}:
        field = ('selected_module_family_' if kind == 'required_module_family' else 'cpu_function_') + item['value']
        condition['predicate'] = _context_test(field)
        condition['required_context'] = [{'field': field, 'type': 'BOOL'}]
    return condition


def _all_condition_predicates(conditions):
    if not all(isinstance(item, dict) and 'predicate' in item for item in conditions):
        return None
    return _join_predicates('and', [item['predicate'] for item in conditions])


def _required_context(conditions):
    requirements = [item for condition in conditions if isinstance(condition, dict)
                    for item in _condition_with_predicate(condition).get('required_context', ())]
    return list({json.dumps(item, sort_keys=True): copy.deepcopy(item) for item in requirements}.values())


def _typed_condition(item):
    if set(item) == {'context', 'equals'}:
        return item['context'] == 'project_mode' and item['equals'] == 'structured_ladder_block'
    kind = item.get('kind')
    if kind in {'module_condition', 'hardware_condition', 'cpu_revision_condition',
                'serial_number_minimum', 'function_version_minimum',
                'required_module_family', 'required_cpu_function'} and isinstance(item.get('predicate'), dict):
        return True
    if kind == 'serial_number_minimum':
        return (set(item) <= {'kind', 'minimum', 'digits'}
                and re.fullmatch(r'\d{5}', str(item.get('minimum', ''))) is not None
                and item.get('digits') == 5)
    if kind == 'function_version_minimum':
        return set(item) == {'kind', 'minimum'} and item['minimum'] in {'A', 'B', 'C', 'D'}
    if kind == 'required_module_family':
        return (set(item) == {'kind', 'value'} and item['value'] in {
            'analog', 'positioning', 'serial_communication', 'network'})
    if kind == 'required_cpu_function':
        return (set(item) == {'kind', 'value'} and item['value'] in {
            'pid_control', 'built_in_ethernet', 'built_in_io', 'data_logging', 'sfc_control'})
    return False


# Literal argument contracts -------------------------------------------------
# Geometry is an offline source input, not an inference from a neighbouring
# operand. A merged cell supplies a value only to names inside its actual span.
def _parameter_word_lines(words):
    rows = []
    for word in sorted((w for w in words if w['x0'] < 550),
                       key=lambda w: ((w['top'] + w['bottom']) / 2, w['x0'])):
        center = (word['top'] + word['bottom']) / 2
        row = next((row for row in reversed(rows[-3:]) if abs(row[0] - center) < 3), None)
        if row is None:
            rows.append([center, [word]])
        else:
            row[1].append(word)
    return [' '.join(word['text'] for word in sorted(row, key=lambda w: w['x0'])) for _, row in rows]


def _parameter_call_lines(words):
    """Keep the ST column's actual baseline separate from adjacent FBD pins."""
    limit = min((w['top'] for w in words if w['text'].startswith(('■Argument', '■Executing'))), default=1000)
    result = []
    for anchor in words:
        if not anchor['text'].startswith('ENO:=') or anchor['top'] >= limit:
            continue
        baseline = anchor['bottom']
        selected = [w for w in words if w['x0'] >= anchor['x0'] - 1
                    and abs(w['bottom'] - baseline) < 1.5]
        result.append(' '.join(w['text'] for w in sorted(selected, key=lambda w: w['x0'])))
    return result


def _parameter_type(text):
    text = re.sub(r'\s+', ' ', text).strip()
    if re.fullmatch(r'Array of ANY16 \(1\.\.4\), array of ANY32 \(1\.\.2\)', text, re.I):
        return {'type': 'UNION', 'alternatives': [_parameter_type('Array of ANY16 (1..4)'),
                                                  _parameter_type('Array of ANY32 (1..2)')]}
    implicit_array = re.fullmatch(r'(ANY16|ANY32)\s*\((-?\d+)\.\.(-?\d+)\)', text, re.I)
    if implicit_array:
        return _parameter_type('Array of %s (%s..%s)' % implicit_array.groups())
    array = re.fullmatch(r'Array of (.+?)\s*[\[(]\s*(-?\d+)\s*\.\.\s*(-?\d+)\s*[\])]', text, re.I)
    if array:
        element = _parameter_type(array.group(1))
        lower, upper = int(array.group(2)), int(array.group(3))
        return {'type': 'ARRAY', 'element_type': element['type'],
                **({'element_type_alternatives': element['type_alternatives']} if element.get('type_alternatives') else {}),
                'array': {'lower': lower, 'upper': upper, 'length': upper - lower + 1}}
    string = re.fullmatch(r'String\s*\((\d+)\)(?:/\((\d+)\))?', text, re.I)
    if string:
        return {'type': 'STRING', 'declared_capacity_choices': [int(n) for n in string.groups() if n]}
    if text == 'ANY*1 String*2, *3':
        return {'type': 'ANY_OR_STRING', 'type_alternatives': ['ANY', 'STRING'],
                'string_addressing': {'representation': 'device_name_string', 'indexed_device_allowed': False}}
    normalized = re.sub(r'\s+', '', text).upper().lstrip(':')
    normalized = normalized.replace('DATATYPE', '').replace('DATA', '')
    kinds = {'BIT': 'BOOL', 'BOOL': 'BOOL', 'REAL': 'REAL', 'STRING': 'STRING',
             'ANY16-BIT': 'ANY16', 'ANY32-BIT': 'ANY32', 'ANY64-BIT': 'ANY64',
             'ANY16': 'ANY16', 'ANY32': 'ANY32', 'ANY64': 'ANY64', 'ANY': 'ANY',
             'INT': 'INT', 'DINT': 'DINT', 'LINT': 'LINT', 'UINT': 'UINT', 'UDINT': 'UDINT',
             'WORD': 'WORD', 'DWORD': 'DWORD', 'LREAL': 'LREAL', 'TIME': 'TIME',
             'ANY_BIT': 'ANY_BIT', 'ANY_NUM': 'ANY_NUM', 'ANY_INT': 'ANY_INT',
             'ANY_REAL': 'ANY_REAL', 'ANY_SIMPLE': 'ANY_SIMPLE'}
    kinds.update({'SINGLE-PRECISIONREAL': 'REAL', 'SINGLE-PRECISIONREALNUMBER': 'REAL',
                  'DOUBLE-PRECISIONREAL': 'LREAL', 'WORD(SIGNED)': 'INT', 'DOUBLEWORD(SIGNED)': 'DINT',
                  'WORD(UNSIGNED)/16-BITSTRING': 'WORD', 'DOUBLEWORD(UNSIGNED)/32-BITSTRING': 'DWORD'})
    alternatives = {'ANY16/32': ['ANY16', 'ANY32'], 'ANY16/STRING': ['ANY16', 'STRING'],
                    'WORD[SIGNED]/DOUBLEWORD[SIGNED]': ['INT', 'DINT'],
                    'WORD(SIGNED),DOUBLEWORD(SIGNED)': ['INT', 'DINT'], 'ANY16,ANY32': ['ANY16', 'ANY32'],
                    'WORD(UNSIGNED)/16-BITSTRING,DOUBLEWORD(UNSIGNED)/32-BITSTRING': ['WORD', 'DWORD'],
                    'BIT,WORD(SIGNED),WORD(UNSIGNED)/16-BITSTRING': ['BOOL', 'INT', 'WORD']}
    if normalized in alternatives:
        choices = alternatives[normalized]
        return {'type': '_OR_'.join(choices), 'type_alternatives': choices}
    return {'type': kinds.get(normalized, 'unknown')}


def _parameter_role(description, name, direction):
    description = re.sub(r'\s+', ' ', description)
    if name.upper() == 'EN':
        return 'enable'
    if name.upper() == 'ENO':
        return 'execution_status'
    patterns = (
        (r'dummy', 'dummy_device'), (r'loop tag memory', 'loop_tag_memory'),
        (r'loop work memory', 'loop_work_memory'), (r'local work memory', 'local_work_memory'),
        (r'block memory', 'block_memory'), (r'operation constant', 'operation_constants'),
        (r'set value start', 'setpoint'), (r'input data.*start device', 'input_data'),
        (r'execution result|error completion', 'execution_status'),
        (r'completion device|completion.*array|array.*completion|process is completed|process completion|execution completion', 'completion_status'),
        (r'timer number', 'timer_number'), (r'counter number', 'counter_number'),
        (r'timer set value|counter set value|set value', 'preset_value'),
        (r'annunciator number', 'annunciator_number'), (r'edge relay number', 'edge_relay_number'),
        (r'devices? used as contacts', 'contact_device'), (r'\btable\b', 'table_data'),
        (r'shared memory address', 'shared_memory_address'),
        (r'nesting', 'nesting_level'), (r'pointer number|ladder block label', 'program_pointer'),
        (r'file name', 'file_name'), (r'drive specification', 'drive_number'),
        (r'serial byte number', 'file_byte_offset'), (r'indirect address', 'indirect_address'),
        (r'(?:start|head) I/O number|dividing the start I/O number', 'module_IO_number'),
        (r'head address|start address|buffer memory address', 'buffer_address'),
        (r'index registers', 'index_register_storage'), (r'module information|module type|module model name', 'module_information'),
        (r'user timing clock', 'clock_device'), (r'clock|time data', 'clock_data'),
        (r'comparison data|data to be compared', 'comparison_value'),
        (r'operation result|conversion result|converted data|converted value', 'result_data'),
        (r'passed as an argument', 'subroutine_argument'),
        (r'(?<!start )(?<!head )(?<!lead )\bnumber of(?! (?:the )?device\b)|remaining numbers of', 'count'),
        (r'^Page decimal', 'page_number'), (r'valid bit length', 'bit_length'),
        (r'bit number|position of bit', 'bit_position'),
        (r'insert position|deletion start position|search start position|position of the table', 'position'),
        (r'position.*first character|number of characters', 'string_position_and_length'),
        (r'initial value', 'initial_value'), (r'final value', 'final_value'),
        (r'measurement time', 'measurement_time'), (r'ON-time', 'on_time'),
        (r'^Cycle', 'cycle'), (r'multiplier of the measurement', 'measurement_multiplier'),
        (r'fractional part', 'fractional_part'), (r'angle|sine value|cosine value|tangent value', 'math_argument'),
        (r'exponent data', 'exponent'), (r'random number series', 'random_seed'),
        (r'characters.*decimal|digits.*decimal', 'numeric_string_format'),
        (r'interrupt mask', 'interrupt_mask'), (r'write offset|read offset', 'file_offset'),
        (r'device for system use', 'system_work_area'), (r'^Search data$', 'search_value'),
        (r'to be turned ON or OFF|to be turned ON|to be set|to be reset|to be reversed|whose status to be inverted', 'device_target'),
        (r'start I/O number', 'module_IO_number'), (r'network number', 'network_number'),
        (r'turns? ON.*completion|turned on for one scan.*completion|completion.*flag', 'completion_status'),
        (r'control data', 'control_data'), (r'channel|CH number', 'channel_number'),
        (r'communication status', 'communication_status'),
        (r'registration data', 'user_frame_registration_data'), (r'interlock signal', 'interlock_signal'),
        (r'slave station setting', 'slave_station_settings'), (r'reserved station', 'reserved_station_settings'),
        (r'error invalid station', 'error_invalid_station_settings'), (r'buffer assignment', 'buffer_assignment'),
        (r'network configuration', 'network_configuration'), (r'I/O data area', 'PID_IO_data'),
        (r'request data', 'request_data'), (r'response data', 'response_data'),
        (r'connection information', 'connection_information'),
        (r'send data|sent e-mail|data to be sent', 'send_data'),
        (r'read data|readout data|receiv.*data|received e-mail', 'received_data'),
        (r'write data|data to be written|data are written|data to which.*written', 'write_data'),
        (r'data.*read|read clock|write clock', 'clock_data' if 'clock' in description.lower() else 'data_source'),
        (r'channel|CH number', 'channel_number'), (r'connection number', 'connection_number'),
        (r'target station.*CPU type', 'target_CPU_type'), (r'station number', 'station_number'),
        (r'number of consecutive protocol executions', 'protocol_execution_count'),
        (r'protocol number', 'protocol_number'), (r'loop number', 'loop_number'),
        (r'logging (?:setting|configuration)', 'logging_setting_number'), (r'positioning data number', 'positioning_data_number'),
        (r'read data length|number of (?:data|words|read points|write points|points)|data length', 'data_count'),
        (r'buffer memory address|start address|offset value', 'buffer_address'),
        (r'direction specification', 'direction_selection'),
        (r'\bmode\b|switching', 'mode_selection'), (r'offset/gain|user range', 'offset_gain_data'),
        (r'parameter', 'parameter_data'), (r'user frame', 'user_frame_data'),
        (r'comment', 'comment_data'), (r'step number', 'step_number'),
        (r'block number', 'block_number'), (r'speed', 'speed'),
        (r'positioning address|movement amount|target position', 'position'),
        (r'preset value', 'preset_value'), (r'lower limit', 'lower_limit'),
        (r'upper limit', 'upper_limit'), (r'duty', 'duty_ratio'),
        (r'frequency', 'frequency'), (r'rotation speed', 'rotation_speed'),
        (r'sampling count|latch count|count value', 'counter_value'),
        (r'coincidence', 'coincidence_output'), (r'pulse', 'pulse_value'),
        (r'ON/OFF', 'boolean_state'), (r'input', 'input_value'), (r'output', 'output_value'),
    )
    role = next((role for pattern, role in patterns if re.search(pattern, description, re.I)), None)
    if role:
        return role
    if re.search(r'\b(?:data|value|code|device|array|result)\b', description, re.I):
        return {'input': 'source_data', 'output': 'destination_data', 'in_out': 'in_out_data'}.get(direction, 'unknown')
    return 'unknown'


def _parameter_operand(name, data_type, direction, description):
    name = re.sub(r'\s+', '', name).rstrip('*')
    qualifier = re.fullmatch(r'([A-Za-z]+\d*)\(([A-Za-z]+)\)', name)
    if qualifier:
        name = qualifier.group(1)
    dtype = _parameter_type(data_type)
    result = {'name': name, **dtype, 'direction': direction,
              'role': _parameter_role(description, name, direction)}
    if qualifier:
        result['device_class'] = qualifier.group(2)
    if re.search(r'\bdevice\b', description, re.I):
        result['passing'] = ('value_or_device_reference' if re.search(r'constant', description, re.I)
                             else 'device_reference')
        if re.search(r'start (?:number|device)|device.*start|start.*device', description, re.I):
            result['addressing'] = 'start_device'
    return result


def _parameter_table_rows(table):
    """Read true cell containment; a blank adjacent cell never supplies a type."""
    cells = table.get('cells', ())
    headers = {re.sub(r'\s+', ' ', c.get('text', '')).strip(): c for c in cells[:10]}
    if not {'Name', 'Description', 'Data type', 'Input/output argument'} <= set(headers):
        return []
    name_col, desc_col, type_col, dir_col = [headers[n]['bbox'] for n in
        ('Name', 'Description', 'Data type', 'Input/output argument')]
    result = []
    for cell in cells:
        box, name = cell['bbox'], cell.get('text', '').strip()
        if (not name or abs(box[0] - name_col[0]) > 2 or abs(box[2] - name_col[2]) > 2
                or box[1] < name_col[3] - 1):
            continue
        center = (box[1] + box[3]) / 2
        def covering(column):
            return next((item.get('text', '') for item in cells
                         if abs(item['bbox'][0] - column[0]) < 2 and abs(item['bbox'][2] - column[2]) < 2
                         and item['bbox'][1] <= center < item['bbox'][3]), '')
        direction_text, description, data_type = covering(dir_col), covering(desc_col), covering(type_col)
        direction = {'Input argument': 'input', 'Output argument': 'output',
                     'Input/output argument': 'in_out'}.get(re.sub(r'\s+', ' ', direction_text), 'unknown')
        # These columns are the physical argument table, and each name cell is
        # one formal operand. Multiple labels inside one printed cell remain a
        # source qualification rather than being split by an invented rule.
        explicit_names = [value.strip().rstrip(':') for value in name.split(',')]
        for explicit in explicit_names:
            result.append({'operand': _parameter_operand(explicit, data_type, direction, description),
                           'witness': ' '.join((name, description, data_type)).strip(),
                           'source_type': data_type, 'description': description, 'bbox': box})
    return result


def _parameter_legend_rows(lines):
    """The process manual prints colon-separated, unruled argument legends."""
    result, direction = [], 'unknown'
    for line in lines:
        match = re.match(r'(?:(Input|Output) argument )?(EN|ENO|S\d*|D\d*|n\d*)\s*:(.+)$', line)
        if not match:
            continue
        if match.group(1):
            direction = match.group(1).lower()
        parts = [part.strip() for part in match.group(3).split(':') if part.strip()]
        if len(parts) < 2:
            continue
        description, data_type = ':'.join(parts[:-1]), parts[-1]
        result.append({'operand': _parameter_operand(match.group(2), data_type, direction, description),
                       'witness': line, 'source_type': data_type, 'description': description})
    return result


def _parameter_shared_names(text, declared):
    match = re.search(r'(?:The following|Any of the following) instruction can go in the dotted squares\.(.*?)(?:■\s*Executing|\Z)', text, re.S)
    if not match:
        return []
    body = match.group(1)
    names = [name for name in declared if re.search(r'(?<![A-Za-z0-9_])' + re.escape(name)
                                                   + r'(?![A-Za-z0-9_])', body)]
    for token in re.split(r'[\s,]+', body):
        if not _SINGLE_SYMBOL.fullmatch(token) or _non_instruction_symbol(token):
            continue
        for name in expand_symbols([token]):
            if (name not in names and any(_parameter_pulse_base(name) == _parameter_pulse_base(known)
                                          for known in declared)):
                names.append(name)
    names.extend(name for name in declared if name not in names
                 and any(_parameter_pulse_base(name) == _parameter_pulse_base(known) for known in names))
    return names


def _parameter_block_forms(words, allowed, operands):
    captions = [w for w in words if 'Structured' in w['text'] and w['x0'] < 180]
    starts = [w['top'] for w in captions]
    ends = [w['top'] for w in words if w['text'] in {'Any', 'The'} and w['x0'] < 70]
    if not starts:
        return {}
    top = min(starts)
    bottom = min((y for y in ends if y > top), default=1000)
    call_left = [w['x0'] for w in words if w['text'].startswith('ENO:=') and top < w['top'] < bottom]
    unsupported_left = [w['x0'] for w in words if w['text'] == 'Not' and top < w['top'] < bottom and w['x0'] > 170]
    right_edge = min(call_left) - 4 if call_left else min(unsupported_left) - 4 if unsupported_left else 170
    region = [w for w in words if top < w['top'] < bottom and 50 <= w['x0'] < right_edge]
    titles = [dict(w, text=w['text'].replace('–', '-').replace('−', '-')) for w in region
              if w['text'].replace('–', '-').replace('−', '-') in allowed]
    names = {op['operand']['name'].upper() for op in operands} | {'EN', 'ENO'}
    result = {}
    for index, title in enumerate(sorted(titles, key=lambda w: w['top'])):
        center_x = (title['x0'] + title['x1']) / 2
        same_row = sorted(((w['x0'] + w['x1']) / 2 for w in titles if abs(w['top'] - title['top']) < 8))
        lower = max((x for x in same_row if x < center_x - 2), default=None)
        upper = min((x for x in same_row if x > center_x + 2), default=None)
        left = (lower + center_x) / 2 if lower is not None else 50
        right = (upper + center_x) / 2 if upper is not None else right_edge
        next_title = min((w['top'] for w in titles if w['top'] > title['bottom'] + 2
                          and left <= (w['x0'] + w['x1']) / 2 < right), default=bottom)
        pins = [w for w in region if title['bottom'] - 1 < w['top'] < next_title
                and left <= (w['x0'] + w['x1']) / 2 < right and w['text'].rstrip('*').upper() in names]
        result[title['text']] = [w['text'].rstrip('*') for w in sorted(pins, key=lambda w: (w['top'], w['x0']))]
    return {name: pins for name, pins in result.items() if pins}


def _parameter_pulse_base(name):
    name = re.sub(r'^([GJSZ])P_', r'\1_', name)
    return name[:-1] if name.endswith('P') and not name.endswith(('STOP', 'SWAP')) else name


def _prepare_parameter_contracts(source, sections):
    """Bind M/I argument contracts to the original page geometry.

    Page tables are deliberately an input requirement: stale, missing geometry
    leaves a source-present gap. Nothing here imports pdfplumber at build time.
    """
    report = {'sections': [], 'source_present_not_extracted': [], 'source_conflict': [],
              'source_not_stated': [], 'native_pending': []}
    all_declared = {manual: {symbol for section in sections.values() if section.manual == manual
                            for symbol in section.symbols} for manual in QL_MANUALS}
    for section in sections.values():
        if (section.manual not in {'sh080785engm', 'sh080316ei', 'sh080783engp'} or not section.has_instruction_chunk
                or not section.symbols or _is_instruction_overview(section)):
            continue
        section._parameter_contracts, section._geometry_flat = [], []
        section._i_behavior_tables = []
        section._has_structured_block = True
        first, last = min(section.pages), max(end for _, end in section.page_pairs)
        page_rows = _fetch(source, 'SELECT pdf_page,word_geometry_json,visual_json FROM page_artifacts '
                                  'WHERE manual_id=? AND pdf_page>=? AND pdf_page<=? ORDER BY pdf_page',
                           (section.manual, first, last))
        active = None
        for page in page_rows:
            number = int(page['pdf_page'])
            words = json.loads(page['word_geometry_json'] or '[]')
            lines = _parameter_word_lines(words)
            text = '\n'.join(lines)
            owner = next((row for row in section.rows if int(row['pdf_page']) <= number <= int(row['pdf_page_end'] or row['pdf_page'])), None)
            if owner is None:
                continue
            section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', text)))
            visual = json.loads(page['visual_json'] or '{}')
            if section.manual == 'sh080316ei':
                for table in visual.get('behavior_tables', ()):
                    section._i_behavior_tables.append(dict(table, page=number))
                    section._geometry_flat.extend((owner['id'], number,
                        re.sub(r'\s+', ' ', cell['text']).strip())
                        for cell in table.get('cells', ()) if cell.get('text'))
            header = re.split(r'■\s*(?:Argument|Executing condition)', text, maxsplit=1)[0]
            call_lines = _parameter_call_lines(words)
            if section.manual == 'sh080783engp' and number == 327:
                # The visible, rendered revision-P panel is WOR. The PDF also
                # contains covered legacy WOR_3_M/WOR_M text; that hidden layer
                # must not invent a second instruction or a source conflict.
                call_lines = ['ENO:=WOR(EN,s1,s2,d1);']
            calls = [match for line in call_lines for match in re.finditer(
                r'ENO\s*:=\s*([A-Z][A-Z0-9_]*)\s*\(([^;\n]+)\)\s*;', line)]
            section._geometry_flat.extend((owner['id'], number, re.sub(r'\s+', ' ', line)) for line in call_lines)
            allowed = _parameter_shared_names(text, section.symbols)
            is_panel = bool('Structured ladder/FBD' in header and (calls or
                section.manual == 'sh080783engp' and allowed and 'Not supported' in header))
            if is_panel:
                shared_allowed = list(allowed)
                if section.manual == 'sh080316ei':
                    native = re.search(r'(?<![A-Za-z0-9_])S\.[ \t]*([A-Z0-9]+|>=|<=|>|<|=)[ \t]+'
                                       r'((?:S\d*|D\d*|n\d*)(?:[ \t]+(?:S\d*|D\d*|n\d*))*)[ \t]*(?=\n|$)', header)
                    native_name = 'S.' + native.group(1) if native else ''
                    native_order = native.group(2).split() if native else []
                    allowed = list(dict.fromkeys([native_name, *(m.group(1) for m in calls)]))
                    if native_name and native_name not in section.symbols:
                        section.symbols.append(native_name)
                    for call in calls:
                        if call.group(1) not in section.symbols:
                            section.symbols.append(call.group(1))
                else:
                    native_name, native_order = '', []
                    allowed = list(dict.fromkeys([*allowed, *(m.group(1) for m in calls
                        if m.group(1) in section.symbols)]))
                    for name in allowed:
                        if name not in section.symbols:
                            section.symbols.append(name)
                active = {'page': number, 'allowed': [n for n in allowed if n], 'rows': [],
                          'calls': [{'name': m.group(1),
                                     'arguments': [a.strip().rstrip('*') for a in m.group(2).split(',') if a.strip()],
                                     'source_empty_arguments': [i for i, a in enumerate(m.group(2).split(','), 1) if not a.strip()],
                                     'witness': m.group(0)} for m in calls],
                          'native_name': native_name, 'native_order': native_order, 'issues': [],
                          'words': words, 'shared_allowed': shared_allowed,
                          'st_available': True if calls else False if 'Not supported' in header else None}
                if section.manual == 'sh080783engp':
                    listed = re.search(r'(?:The following|Any of the following) instruction can go in the dotted squares\.(.*?)(?:■\s*Executing|\Z)', text, re.S)
                    literals = [name for token in re.split(r'[\s,]+', listed.group(1))
                                if _SINGLE_SYMBOL.fullmatch(token) and not _non_instruction_symbol(token)
                                for name in expand_symbols([token])] if listed else []
                    active['unresolved_list_spellings'] = [name for name in literals if name not in allowed]
                section._parameter_contracts.append(active)
            elif (section.manual == 'sh080783engp' and number == first
                  and 'Symbolic description' in header and family_of(section) in {'contact', 'association'}):
                section._symbolic_only = True
                contact = family_of(section) == 'contact'
                symbolic_rows = [{'operand': {'name': 'S', 'type': 'BOOL', 'direction': 'input',
                                               'role': 'contact_device', 'passing': 'device_reference'},
                                  'witness': 'Bit device number/bit-specified word device',
                                  'description': 'contact_device', 'source_type': 'Bit', 'page': number}] if contact else []
                active = {'page': number, 'allowed': list(section.symbols), 'rows': symbolic_rows,
                          'calls': [], 'native_name': '', 'native_order': [], 'issues': [],
                          'words': words, 'st_available': None, 'symbolic': True,
                          'symbolic_kind': 'contact' if contact else 'block_connection'}
                section._parameter_contracts.append(active)
                report['source_not_stated'].append({'manual': section.manual, 'section': section.title,
                    'page': number, 'forms': list(section.symbols), 'fields': ['ST_function_call'],
                    'reason': 'source_describes_symbolic_graphical_representation_without_ST_function'})
            if active is None:
                continue
            visual = json.loads(page['visual_json'] or '{}')
            actual = (_parameter_legend_rows(lines) if section.manual == 'sh080316ei' else
                      [row for table in visual.get('parameter_tables', ()) for row in _parameter_table_rows(table)])
            if number != active['page'] and actual and not all(row['operand']['direction'] == 'output' for row in actual):
                # A call-panel can be followed immediately by its complete
                # repeated-header argument table; arbitrary later tables cannot.
                if active['rows'] or number != active['page'] + 1:
                    actual = []
            for row in actual:
                row['page'] = number
                previous = next((old for old in active['rows'] if old['operand']['name'].upper() == row['operand']['name'].upper()), None)
                if previous is None:
                    active['rows'].append(row)
                elif previous['operand'] != row['operand']:
                    active['issues'].append({'kind': 'same_form_argument_rows_disagree', 'name': row['operand']['name']})
                section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', row['witness'])))
            if section.manual == 'sh080316ei':
                active = None
        forms = set()
        for contract in section._parameter_contracts:
            forms.update(contract['allowed'])
            operands = [row['operand'] for row in contract['rows']]
            if section.manual == 'sh080783engp':
                contract['block_forms'] = _parameter_block_forms(contract['words'], contract['allowed'], contract['rows'])
                if contract['page'] == 327:
                    contract['block_forms'] = {'WOR': ['EN', 'ENO', 's1', 'd1', 's2']}
                if contract.get('unresolved_list_spellings'):
                    report['source_conflict'].append({'manual': section.manual, 'section': section.title,
                        'page': contract['page'], 'forms': contract['allowed'],
                        'kind': 'dotted_square_list_spelling_disagrees_with_heading_and_panel',
                        'unresolved_list_spellings': contract['unresolved_list_spellings']})
            for call in contract['calls']:
                actual_names = {name.upper() for name in call['arguments']}
                formal_names = {op['name'].upper() for op in operands if op['name'].upper() != 'ENO'
                                and (op['name'].upper() not in {'JN', 'UN'} or op['name'].upper() in actual_names)}
                if section.manual == 'sh080783engp':
                    blocks = contract['block_forms']
                    names = blocks.get(call['name']) or (next(iter(blocks.values())) if len(blocks) == 1 else None)
                    if names:
                        formal_names = {name.upper() for name in names if name.upper() != 'ENO'}
                if call.get('source_empty_arguments'):
                    contract['issues'].append({'kind': 'call_panel_contains_empty_argument',
                                               'positions': call['source_empty_arguments']})
                unstated = actual_names - {op['name'].upper() for op in operands}
                common_unstated = unstated & {'EN', 'ENO'}
                if section.manual == 'sh080783engp' and 'ENO' not in {op['name'].upper() for op in operands}:
                    common_unstated.add('ENO')
                if common_unstated:
                    report['source_not_stated'].append({'manual': section.manual, 'section': section.title,
                        'page': contract['page'], 'forms': contract['allowed'],
                        'fields': [name + '.type' for name in sorted(common_unstated)],
                        'reason': 'enable_pin_printed_but_type_omitted_from_argument_table'})
                if formal_names - actual_names or unstated - common_unstated:
                    contract['issues'].append({'kind': 'call_panel_and_argument_table_disagree',
                        'missing_from_call': sorted(formal_names - actual_names),
                        'absent_from_table': sorted(unstated - common_unstated)})
                if call['name'] not in contract['allowed'] and call['name'] in all_declared[section.manual]:
                    contract['issues'].append({'kind': 'call_panel_names_another_declared_instruction',
                                               'source_panel_name': call['name']})
            for row in contract['rows']:
                if row['operand']['name'].upper() == 'EN' and row['operand']['type'] != 'BOOL':
                    contract['issues'].append({'kind': 'enable_type_disagrees_with_enable_usage',
                                               'name': 'EN', 'documented_type': row['operand']['type']})
                if 'unknown' in {row['operand']['type'], row['operand']['direction'], row['operand']['role']}:
                    report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                        'page': row['page'], 'forms': contract['allowed'], 'operand': row['operand'],
                        'source_description': row['description'], 'source_type': row['source_type']})
            if not operands and not contract.get('symbolic'):
                report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                    'page': contract['page'], 'forms': contract['allowed'], 'reason': 'argument_geometry_not_bound'})
            for issue in contract['issues']:
                report['source_conflict'].append({'manual': section.manual, 'section': section.title,
                    'page': contract['page'], 'forms': contract['allowed'], **issue})
        missing = sorted(set(section.symbols) - forms)
        if missing:
            report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                'reason': 'declared_forms_without_bound_argument_panel', 'forms': missing})
        if section.manual == 'sh080783engp':
            for symbol in section.symbols:
                actual_specs = _parameter_p_specs(section, symbol)
                if not any(spec['dimension'] == 'parameters.fbd_signature' for spec in actual_specs):
                    report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                        'form': symbol, 'reason': 'physical_FBD_pin_groups_not_bound'})
        report['sections'].append({'manual': section.manual, 'section': section.title,
            'panels': len(section._parameter_contracts), 'declared_forms': len(section.symbols), 'bound_forms': len(forms)})
        report['native_pending'].extend({'manual': section.manual, 'section': section.title,
            'form': symbol, 'languages': list(_section_languages(section, symbol)),
            'meaning': 'source_documented_no_native_compile_or_execution_claimed'} for symbol in section.symbols)
    native = _prepare_native_parameter_contracts(source, sections)
    for key in ('sections', 'source_present_not_extracted', 'source_not_stated', 'source_conflict', 'native_pending'):
        report[key].extend(native[key])
    standard = _prepare_n_parameter_contracts(source, sections)
    for key in ('sections', 'source_present_not_extracted', 'source_not_stated', 'source_conflict', 'native_pending'):
        report[key].extend(standard[key])
    return report


def _parameter_contract_specs(section, symbol):
    if section.manual == 'sh080784engn':
        return _n_parameter_specs(section, symbol)
    if section.manual == 'sh080809engx':
        return _parameter_use_case(section, _native_parameter_specs(section, symbol))
    if section.manual == 'sh080783engp':
        return _parameter_use_case(section, _parameter_p_specs(section, symbol))
    result = []
    for contract in getattr(section, '_parameter_contracts', ()):
        if symbol not in contract['allowed'] or not contract['rows']:
            continue
        by_name = {row['operand']['name'].upper(): row for row in contract['rows']}
        if section.manual == 'sh080316ei' and symbol == contract['native_name']:
            order = contract['native_order']
            languages, calls = ('ladder',), []
        else:
            calls = [call for call in contract['calls']
                     if call['name'] == symbol or _parameter_pulse_base(call['name']) == _parameter_pulse_base(symbol)]
            if not calls and len(contract['calls']) == 1:
                # One physical call/table, plus its literal dotted-square form
                # list, documents axis/pulse variants without inventing owners.
                calls = contract['calls']
            order = calls[0]['arguments'] if calls else []
            languages = ('st', 'fbd')
        selected = [row['operand'] for row in contract['rows']
                    if row['operand']['name'].upper() not in {'JN', 'UN'}
                    or row['operand']['name'].upper() in {name.upper() for name in order}]
        witnesses = [row['witness'] for row in contract['rows']]
        source_conflict = bool(contract['issues'])
        if languages == ('ladder',):
            pins = [copy.deepcopy(by_name[name.upper()]['operand']) if name.upper() in by_name else
                    {'name': name, 'type': 'unknown', 'direction': 'unknown', 'role': 'unknown'} for name in order]
            result += _parameter_representation_specs(symbol, pins, order, witnesses, 'ladder',
                source_conflict=any(issue['kind'] == 'same_form_argument_rows_disagree' for issue in contract['issues']))
            continue
        for language in ('st', 'fbd', 'ladder'):
            pins = copy.deepcopy(selected)
            actual_order = list(order)
            issues = [copy.deepcopy(issue) for issue in contract['issues']
                      if language == 'st' or issue['kind'] != 'call_panel_and_argument_table_disagree']
            if language == 'st':
                formal = [op['name'] for op in pins if op['name'].upper() != 'ENO']
                missing = [name for name in formal if name.upper() not in {a.upper() for a in actual_order}]
                extra = [name for name in actual_order if name.upper() not in by_name]
                if missing or extra:
                    issues.append({'kind': 'call_panel_and_argument_table_disagree',
                                   'missing_from_call': missing, 'absent_from_table': extra})
                pins = [copy.deepcopy(by_name[name.upper()]['operand']) if name.upper() in by_name else
                        {'name': name, 'type': 'unknown', 'direction': 'unknown', 'role': 'unknown'}
                        for name in actual_order]
                if section.manual == 'sh080316ei':
                    for pin in pins:
                        if pin['name'].upper() not in {'EN', 'ENO'}:
                            pin['name'] = pin['name'].lower()
                    actual_order = [pin['name'] for pin in pins]
            else:
                actual_order = [pin['name'] for pin in pins]
                if section.manual == 'sh080316ei':
                    for pin in pins:
                        if pin['name'].upper() not in {'EN', 'ENO'}:
                            pin['name'] = pin['name'].lower()
                    actual_order = [pin['name'] for pin in pins]
            specs = _parameter_representation_specs(symbol, pins, actual_order, witnesses, language,
                                                     source_conflict=bool(issues), structured=language == 'ladder')
            if language == 'st':
                signature = {'name': symbol, 'parameters': pins, 'returns': 'BOOL', 'enable': True,
                             'available': True, 'instance_required': False, 'call_kind': 'instruction',
                             'source_panel_name': calls[0]['name'] if calls else 'unknown'}
                if issues:
                    signature['source_disagreements'] = issues
                specs.append(_reviewed_spec('argument_table_st', 'parameters.st_signature', witnesses,
                                             signature, ('st',)))
            else:
                signature = {'name': symbol, 'pins': pins, 'available': True, 'instance_required': False,
                             'call_kind': 'instruction_block',
                             'representation': 'structured_ladder_block' if language == 'ladder' else 'fbd_instruction_block'}
                specs.append(_reviewed_spec('argument_table_' + language, 'parameters.' + language + '_signature',
                                             witnesses, signature, (language,)))
            if issues:
                for spec in specs:
                    spec['source_conflict'] = True
            if language == 'ladder':
                for spec in specs:
                    spec['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                    spec['value']['required_context'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
            result.extend(specs)
    return result


def _parameter_representation_specs(symbol, pins, order, witnesses, language, *, source_conflict=False, structured=False):
    specs = [
        _reviewed_spec('argument_table_' + language, 'parameters.typed_operands', witnesses,
                       {'operands': pins, 'operand_count': len(pins)}, (language,)),
        _reviewed_spec('argument_table_' + language, 'parameters.arity', witnesses,
                       {'minimum': len(pins), 'maximum': len(pins),
                        'count_kind': 'call_arguments' if language == 'st' else 'block_pins' if structured or language == 'fbd' else 'native_operands'}, (language,)),
        _reviewed_spec('argument_table_' + language, 'parameters.order', witnesses,
                       {'symbols': order, 'order_source': 'literal_call_panel' if language == 'st' or not structured and language == 'ladder' else 'argument_table_pin_order'}, (language,)),
    ]
    unstated = [pin['name'] + '.type' for pin in pins if pin.get('gap_kind') == 'source_not_stated']
    if unstated:
        specs[0]['value'].update(source_disposition='source_not_stated', gap_kind='source_not_stated',
                                  unstated_fields=unstated)
    if source_conflict:
        for spec in specs:
            spec['source_conflict'] = True
    return specs


def _parameter_p_specs(section, symbol):
    result = []
    for contract in getattr(section, '_parameter_contracts', ()):
        if symbol not in contract['allowed'] or not contract['rows'] and not contract.get('symbolic'):
            continue
        if contract.get('symbolic'):
            pins = [copy.deepcopy(row['operand']) for row in contract['rows']]
            witnesses = [row['witness'] for row in contract['rows']] or ['Structured ladder/FBD (Symbolic description)']
            for language in ('fbd', 'ladder'):
                specs = _parameter_representation_specs(symbol, pins, [p['name'] for p in pins], witnesses,
                    language, structured=language == 'ladder')
                specs.append(_reviewed_spec('symbolic_' + language, 'parameters.' + language + '_signature', witnesses,
                    {'name': symbol, 'pins': pins, 'available': True, 'instance_required': False,
                     'call_kind': 'symbolic_' + contract['symbolic_kind']}, (language,)))
                if language == 'ladder':
                    for spec in specs:
                        spec['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                        spec['value']['required_context'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
                result.extend(specs)
            continue
        by_name = {row['operand']['name'].upper(): row for row in contract['rows']}
        witnesses = [section.heading_line, *(row['witness'] for row in contract['rows'])]
        if contract.get('unresolved_list_spellings'):
            spelling = _reviewed_spec('source_spelling', 'identity.source_spelling', contract['unresolved_list_spellings'],
                {'declared_forms': contract['allowed'], 'unresolved_list_forms': contract['unresolved_list_spellings'],
                 'resolution': 'unknown'}, _section_languages(section, symbol))
            spelling['source_conflict'] = True
            result.append(spelling)
        calls = contract['calls']
        matching = [c for c in calls if c['name'] == symbol or _parameter_pulse_base(c['name']) == _parameter_pulse_base(symbol)]
        if not matching and symbol in contract.get('shared_allowed', []):
            matching = [c for c in calls if c['name'] in contract['shared_allowed']][:1]
        if not matching and len(calls) == 1 and len(contract.get('block_forms', {})) <= 1:
            matching = calls
        if matching:
            order = matching[0]['arguments']
            pins = [copy.deepcopy(by_name[name.upper()]['operand']) if name.upper() in by_name else
                    _missing_contract_parameter(name, contract) for name in order]
            pins = [_parameter_form_type(section, symbol, pin) for pin in pins]
            specs = _parameter_representation_specs(symbol, pins, order, witnesses, 'st',
                                                     source_conflict=bool(contract['issues']))
            specs.append(_reviewed_spec('argument_table_st', 'parameters.st_signature', witnesses,
                {'name': symbol, 'parameters': pins, 'returns': by_name.get('ENO', {}).get('operand', {}).get('type', 'unknown'),
                 'enable': any(p['name'] == 'EN' for p in pins),
                 'available': True, 'instance_required': False, 'call_kind': 'instruction',
                 'source_panel_name': matching[0]['name']}, ('st',)))
            unstated = [pin['name'] + '.type' for pin in pins if pin.get('gap_kind') == 'source_not_stated']
            if 'ENO' not in by_name:
                unstated.append('ENO.type')
            if unstated:
                specs[-1]['value'].update(source_disposition='source_not_stated', gap_kind='source_not_stated',
                                          unstated_fields=unstated)
            if contract['issues']:
                for spec in specs:
                    spec['source_conflict'] = True
            result.extend(specs)
        elif contract['st_available'] is False:
            result.append(_reviewed_spec('argument_table_st_unavailable', 'parameters.st_signature', witnesses,
                {'available': False, 'reason': 'source_states_ST_function_not_supported'}, ('st',)))
        forms = contract.get('block_forms', {})
        selected = forms.get(symbol) or next((pins for name, pins in forms.items()
                      if _parameter_pulse_base(name) == _parameter_pulse_base(symbol)), None)
        if selected is None and symbol in contract.get('shared_allowed', []):
            selected = next((pins for name, pins in forms.items() if name in contract['shared_allowed']), None)
        if selected is None and len(forms) == 1:
            selected = next(iter(forms.values()))
        if not selected:
            continue
        pins = [copy.deepcopy(by_name[name.upper()]['operand']) if name.upper() in by_name else
                _missing_contract_parameter(name, contract) for name in selected]
        pins = [_parameter_form_type(section, symbol, pin) for pin in pins]
        for language in ('fbd', 'ladder'):
            specs = _parameter_representation_specs(symbol, pins, [p['name'] for p in pins], witnesses, language,
                source_conflict=any(i['kind'] != 'call_panel_and_argument_table_disagree' for i in contract['issues']),
                structured=language == 'ladder')
            specs.append(_reviewed_spec('argument_table_' + language, 'parameters.' + language + '_signature', witnesses,
                {'name': symbol, 'pins': pins, 'available': True, 'instance_required': False,
                 'call_kind': 'instruction_block', 'representation':
                    'structured_ladder_block' if language == 'ladder' else 'fbd_instruction_block'}, (language,)))
            unstated = [pin['name'] + '.type' for pin in pins if pin.get('gap_kind') == 'source_not_stated']
            if unstated:
                specs[-1]['value'].update(source_disposition='source_not_stated', gap_kind='source_not_stated',
                                          unstated_fields=unstated)
            if language == 'ladder':
                for spec in specs:
                    spec['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
                    spec['value']['required_context'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
            result.extend(specs)
    return result


def _parameter_use_case(section, specs):
    title = section.title.lower()
    case = ('annunciator' if 'annunciator' in title and 'excluding' not in title else
            'CPU_shared_memory' if 'cpu shared memory' in title else None)
    if case is None:
        return specs
    result = copy.deepcopy(specs)
    for spec in result:
        if not spec['dimension'].startswith('parameters.'):
            continue
        spec['key'] += '_' + case
        spec['value']['use_case'] = case
        if case == 'annunciator':
            for key in ('operands', 'pins', 'parameters'):
                for pin in spec['value'].get(key, []):
                    if isinstance(pin, dict) and pin['name'].upper() == 'D':
                        pin['device_class'] = 'F'
    return result


def _unstated_parameter(name):
    return {'name': name, 'type': 'unknown',
            'direction': 'input' if name.upper() == 'EN' else 'output' if name.upper() == 'ENO' else 'unknown',
            'role': 'enable' if name.upper() == 'EN' else 'execution_status' if name.upper() == 'ENO' else 'unknown',
            'missing_source_field': 'argument_table_type_not_stated',
            'source_disposition': 'source_not_stated', 'gap_kind': 'source_not_stated'}


def _missing_contract_parameter(name, contract):
    value = _unstated_parameter(name)
    if name.upper() not in {'EN', 'ENO'}:
        value.update(source_disposition='source_conflict', gap_kind='source_conflict',
                     missing_source_field='call_or_pin_label_disagrees_with_argument_table')
    return value


def _parameter_form_type(section, symbol, operand):
    """Bind only the source's explicit word/double-word sibling declaration."""
    roots = {_parameter_pulse_base(name) for name in section.symbols}
    form = _parameter_pulse_base(symbol)
    width = (16 if 'D' + form in roots else 32 if form.startswith('D') and form[1:] in roots else
             16 if form.startswith('W') and 'D' + form[1:] in roots else
             32 if form.startswith('D') and 'W' + form[1:] in roots else None)
    if {'FROM', 'DFRO'} <= roots:
        width = {'FROM': 16, 'DFRO': 32}.get(form, width)
    if {'BSQR', 'BDSQR'} <= roots:
        width = {'BSQR': 16, 'BDSQR': 32}.get(form, width)
    if width is None:
        return operand
    if operand.get('type_alternatives') == ['ANY16', 'ANY32']:
        operand['type'] = 'ANY' + str(width)
        operand['documented_type_alternatives'] = operand.pop('type_alternatives')
        operand['form_width_bits'] = width
    elif operand.get('type_alternatives') == ['INT', 'DINT']:
        operand['type'] = 'INT' if width == 16 else 'DINT'
        operand['documented_type_alternatives'] = operand.pop('type_alternatives')
        operand['form_width_bits'] = width
    if operand.get('element_type_alternatives') == ['ANY16', 'ANY32']:
        operand['element_type'] = 'ANY' + str(width)
        operand['documented_element_type_alternatives'] = operand.pop('element_type_alternatives')
        operand['form_width_bits'] = width
    if len(operand.get('declared_capacity_choices', [])) == 2:
        operand['declared_capacity'] = operand['declared_capacity_choices'][0 if width == 16 else 1]
        operand['form_width_bits'] = width
    return operand


def _n_argument_rows(table):
    """N uses formal-name aliases and literal variadic ranges in name cells."""
    actual = copy.deepcopy(table)
    originals = {}
    for cell in actual.get('cells', []):
        original = cell.get('text', '')
        normalized = re.sub(r'\s+', ' ', original).strip()
        if normalized == 'Input argument/ Output argument':
            cell['text'] = 'Input/output argument'
            continue
        if not re.match(r'^(?:s\d*|d\d*|n\d*|EN|ENO)(?:\b|\()', normalized):
            continue
        stripped = re.sub(r'\([^)]*\)', '', normalized)
        ranged = re.fullmatch(r'([sdn])(\d+) to \1(\d+)', stripped.strip())
        names = ([ranged.group(1) + str(i) for i in range(int(ranged.group(2)), int(ranged.group(3)) + 1)]
                 if ranged else re.findall(r'(?<![A-Za-z0-9_])(?:EN|ENO|[sdn]\d*)(?![A-Za-z0-9_])', stripped))
        if names:
            cell['text'] = ', '.join(names)
            originals[tuple(cell['bbox'])] = original
    rows = _parameter_table_rows(actual)
    for row in rows:
        original = originals.get(tuple(row['bbox']), row['operand']['name'])
        row['source_name'] = original
        row['witness'] = ' '.join((original, row['description'], row['source_type']))
    return rows


def _prepare_n_parameter_contracts(source, sections):
    report = {key: [] for key in ('sections', 'source_present_not_extracted', 'source_not_stated', 'source_conflict', 'native_pending')}
    for section in sections.values():
        if (section.manual != 'sh080784engn' or not section.has_instruction_chunk or not section.symbols
                or _is_instruction_overview(section)):
            continue
        section._n_argument_contracts, section._geometry_flat = [], []
        section._has_structured_block = True
        first = min(section.pages)
        page = _fetch(source, 'SELECT word_geometry_json,visual_json FROM page_artifacts WHERE manual_id=? AND pdf_page=?',
                      (section.manual, first))[0]
        words = json.loads(page['word_geometry_json'] or '[]')
        rows = [row for table in json.loads(page['visual_json'] or '{}').get('parameter_tables', [])
                for row in _n_argument_rows(table)]
        owner = next(row for row in section.rows if int(row['pdf_page']) <= first <= int(row['pdf_page_end'] or row['pdf_page']))
        lines = _parameter_word_lines(words)
        section._geometry_flat.append((owner['id'], first, re.sub(r'\s+', ' ', '\n'.join(lines))))
        section._geometry_flat.extend((owner['id'], first, re.sub(r'\s+', ' ', row['witness'])) for row in rows)
        contract = {'page': first, 'rows': rows, 'words': words, 'lines': lines, 'issues': []}
        section._n_argument_contracts.append(contract)
        if first == 138:
            contract['issues'].append({'kind': 'exponent_argument_prose_and_table_disagree',
                'table_bindings': {'s1': 'ANY_REAL', 's2': 'ANY_NUM'},
                'prose_bindings': {'s1': 'ANY_NUM', 's2': 'ANY_REAL'}, 'resolution': 'unknown'})
        if first == 215:
            contract['issues'].append({'kind': 'operator_ST_panel_contains_function_call_tail',
                'trailing_output_parameter': 'd', 'unmatched_closing_parenthesis': True, 'resolution': 'unknown'})
        if not rows:
            report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                'page': first, 'reason': 'literal_argument_table_not_bound'})
        for row in rows:
            if row['operand']['type'] == 'unknown' or row['operand']['direction'] == 'unknown':
                report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                    'page': first, 'operand': row['operand']['name'], 'source_type': row['source_type'],
                    'reason': 'literal_argument_type_or_direction_not_bound'})
        for symbol in section.symbols:
            specs = _n_parameter_specs(section, symbol)
            if not any(spec['dimension'] == 'parameters.typed_operands' for spec in specs):
                report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                    'page': first, 'form': symbol, 'reason': 'literal_parameter_contract_not_bound'})
        report['source_conflict'].extend({'manual': section.manual, 'section': section.title,
            'page': first, 'forms': section.symbols, **issue} for issue in contract['issues'])
        report['sections'].append({'manual': section.manual, 'section': section.title, 'panels': 1,
            'declared_forms': len(section.symbols), 'bound_forms': len(section.symbols) if rows else 0})
        report['native_pending'].extend({'manual': section.manual, 'section': section.title, 'form': symbol,
            'languages': ['st', 'fbd'], 'meaning': 'source_documented_no_native_compile_or_execution_claimed'}
            for symbol in section.symbols)
    _prepare_n_behavior_sources(source, sections)
    return report


def _n_refined_pin(pin, rows, symbol):
    row = next((row for row in rows if row['operand']['name'].upper() == pin['name'].upper()), None)
    if row is None:
        return copy.deepcopy(pin)
    result = copy.deepcopy(pin)
    dtype = {key: value for key, value in row['operand'].items() if key not in {'name', 'direction', 'role', 'device_class'}}
    choices = dtype.get('type_alternatives', [])
    if (choices and result['type'] in choices) or dtype.get('type') == 'ANY' and result['type'] != 'ANY':
        dtype.pop('type', None)
        if choices:
            dtype['documented_type_alternatives'] = dtype.pop('type_alternatives')
    result.update(dtype)
    result['direction'] = row['operand']['direction']
    aliases = [name.strip() for group in re.findall(r'\(([^)]*)\)', row.get('source_name', ''))
               for name in group.split(',') if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name.strip())]
    if aliases:
        # A declared alias is an identifier, not a type assertion. For example
        # the source calls a REAL input _LREAL and a DWORD input _WORD; keep the
        # exact alias beside the explicitly declared type without inferring a
        # datatype conflict from the spelling of an otherwise legal name.
        result['source_aliases'] = list(dict.fromkeys(aliases))
    if 'BitArr' in row.get('source_name', ''):
        result.update(representation='bit_array_element_reference', passing='bit_array_element_reference', length_operand='n')
    conversion = N_TYPE_PAIR.fullmatch(symbol)
    if conversion and result.get('type_alternatives') == ['ANY16', 'ANY32']:
        form = conversion.group(2) if pin['name'] == 'd' else conversion.group(1)
        if form in {'INT', 'WORD', 'DINT', 'DWORD'}:
            result.update(type='ANY32' if form in {'DINT', 'DWORD'} else 'ANY16',
                          documented_type_alternatives=result.pop('type_alternatives'),
                          form_width_bits=32 if form in {'DINT', 'DWORD'} else 16)
    capacities = result.get('declared_capacity_choices', [])
    if len(capacities) == 2 and conversion:
        source, destination = conversion.group(1), conversion.group(2)
        width = source if pin['name'] == 'd' else destination
        if width in {'INT', 'DINT', 'WORD', 'DWORD'}:
            result['declared_capacity'] = capacities[1 if width in {'DINT', 'DWORD'} else 0]
    return result


def _n_parameter_specs(section, symbol):
    if not getattr(section, '_n_argument_contracts', None):
        return []
    contract = section._n_argument_contracts[0]
    rows = contract['rows']
    witnesses = [row['witness'] for row in rows]
    if not rows:
        return []
    operator = re.search(r'operator', section.chapter, re.I) is not None
    if not operator:
        result = [copy.deepcopy(spec) for spec in _reviewed_n_specs(section, symbol)
                  if spec['dimension'].startswith('parameters.')]
        if not result:
            enabled = symbol.endswith('_E')
            pins = [copy.deepcopy(row['operand']) for row in rows
                    if enabled or row['operand']['name'] not in {'EN', 'ENO'}]
            inputs = [pin for pin in pins if pin['direction'] == 'input']
            outputs = [pin for pin in pins if pin['direction'] == 'output' and pin['name'] != 'ENO']
            result = [_reviewed_spec('literal_function_arguments', 'parameters.typed_operands', witnesses,
                {'operands': pins}, ('st', 'fbd')),
                _reviewed_spec('literal_function_block', 'parameters.fbd_signature', witnesses,
                {'name': symbol, 'pins': pins, 'available': True, 'instance_required': False}, ('fbd',)),
                _reviewed_spec('literal_function_call', 'parameters.st_signature', witnesses,
                {'name': symbol, 'parameters': inputs + outputs if enabled else inputs,
                 'returns': 'BOOL' if enabled else outputs[0]['type'], 'enable': enabled, 'available': True,
                 'call_kind': 'function', 'instance_required': False}, ('st',))]
        for spec in result:
            spec['patterns'] = tuple(witnesses)
            value = spec['value']
            for key in ('operands', 'pins', 'parameters'):
                if key in value:
                    value[key] = [_n_refined_pin(pin, rows, symbol) for pin in value[key]]
            if 'returns' in value and value['returns'] not in {'BOOL', 'VOID'}:
                output = next((row for row in rows if row['operand']['name'] == 'd'), None)
                if output and output['operand']['type'] not in {'unknown', 'ANY'}:
                    # A grouped conversion table declares alternatives; the
                    # concrete source form already binds its return type.
                    if not output['operand'].get('type_alternatives'):
                        value['returns'] = output['operand']['type']
    else:
        literal = symbol in {'+', '-', '*', '/', '**', '>', '>=', '=', '<=', '<', '<>', '&', 'AND', 'OR', 'XOR', 'NOT', 'MOD'}
        comparison = symbol in {'GT', 'GE', 'EQ', 'LE', 'LT', 'NE', '>', '>=', '=', '<=', '<', '<>'}
        maximum = (1 if symbol == 'NOT' else 2 if symbol in {'SUB', '-', 'DIV', '/', 'MOD', '**', 'NE', '<>'} else 28)
        inputs = [_n_refined_pin(_n_operand('s' + str(i), 'ANY'), rows, symbol) for i in range(1, maximum + 1)]
        for pin in inputs[2:]:
            pin['optional'] = True
        output = _n_refined_pin(_n_operand('d', 'BOOL' if comparison else 'ANY', 'output'), rows, symbol)
        result = []
        if literal:
            result += [_reviewed_spec('literal_typed', 'parameters.typed_operands', witnesses,
                {'operands': inputs, 'operand_count': maximum, 'representation': 'ST_expression'}, ('st',)),
                _reviewed_spec('literal_expression', 'parameters.language_expression', witnesses,
                {'operator': symbol, 'operands': [pin['name'] for pin in inputs], 'returns': output['type'],
                 'available': True, 'representation': 'ST_expression'}, ('st',)),
                _reviewed_spec('literal_not_function', 'parameters.st_signature', witnesses,
                {'available': False, 'reason': 'literal_ST_operator_is_an_expression', 'representation': 'ST_expression'}, ('st',))]
        block = not literal or symbol in {'AND', 'OR', 'XOR', 'MOD'}
        if block:
            pins = inputs + [output]
            result += [_reviewed_spec('operator_block_typed', 'parameters.typed_operands', witnesses,
                {'operands': pins, 'representation': 'structured_operator_block'}, ('fbd',)),
                _reviewed_spec('operator_block', 'parameters.fbd_signature', witnesses,
                {'name': symbol, 'pins': pins, 'available': True, 'instance_required': False,
                 'representation': 'structured_operator_block'}, ('fbd',))]
            if not literal:
                result.append(_reviewed_spec('operator_name_not_ST_function', 'parameters.st_signature', witnesses,
                    {'available': False, 'reason': 'symbolic_ST_operator_required'}, ('st',)))
        elif symbol != 'NOT':
            result.append(_reviewed_spec('literal_no_FBD_block', 'parameters.fbd_signature', witnesses,
                {'available': False, 'reason': 'literal_operator_has_no_FBD_block', 'representation': 'literal_operator'}, ('fbd',)))
    derived = []
    for spec in result:
        value, dimension = spec['value'], spec['dimension']
        if dimension == 'parameters.st_signature' and value.get('available'):
            pins = value['parameters']
            kind = 'ST_call_arguments'
        elif dimension == 'parameters.fbd_signature' and value.get('available'):
            pins = value['pins']
            kind = 'FBD_pins'
        elif dimension == 'parameters.typed_operands' and value.get('representation') == 'ST_expression':
            pins = value['operands']
            kind = 'ST_expression_operands'
        else:
            continue
        for dim, detail in [('parameters.arity', {'minimum': sum(not pin.get('optional') for pin in pins),
                'maximum': len(pins), 'count_kind': kind}), ('parameters.order', {'symbols': [pin['name'] for pin in pins],
                'order_source': kind})]:
            derived.append(_reviewed_spec('literal_argument_' + kind, dim, witnesses, detail, spec['languages']))
    result.extend(derived)
    # The same physical block panel is the structured-Ladder representation.
    for spec in list(result):
        if 'fbd' not in spec['languages'] or not spec['value'].get('available', True):
            continue
        item = copy.deepcopy(spec)
        item['languages'] = ('ladder',)
        if item['dimension'] == 'parameters.fbd_signature':
            item['dimension'] = 'parameters.ladder_signature'
        item['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
        item['value']['required_context'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
        result.append(item)
    for issue in contract['issues']:
        if issue['kind'] == 'operator_ST_panel_contains_function_call_tail':
            for spec in result:
                if spec['dimension'] in {'parameters.language_expression', 'parameters.st_signature'}:
                    spec['source_conflict'] = True
                    spec['value']['source_syntax_conflict'] = copy.deepcopy(issue)
            continue
        if issue['kind'] == 'exponent_argument_prose_and_table_disagree':
            continue
        item = _reviewed_spec('formal_alias_conflict', 'identity.source_parameter_alias', witnesses, issue)
        item['source_conflict'] = True
        result.append(item)
    return result


_NATIVE_ARGUMENT = re.compile(r'(?:[SDR]\d*|[nN]\d*|Pn|P\*\*|Un|Jn|U0)\Z')


def _native_argument_name(text):
    value = re.sub(r'\([^)]*\)$', '', text.strip()) if not text.strip().startswith('(') else text.strip()
    value = value.strip('(),:').rstrip('*')
    return 'Pn' if text.strip('(),:') == 'P**' else value


def _native_legend_rows(lines):
    result, active = [], None
    for index, line in enumerate(lines):
        match = re.match(r'^(?:(?:SET|RST)\s+)?(\([A-Za-z]\d*\)(?:\*\d+)?(?:\s*(?:,|to|and)\s*\([A-Za-z]\d*\))?'
                         r'|[nN]\d*|Pn|P\*\*|Un|Jn)\s*:\s*(.+)$', line)
        if match:
            if active:
                result.append(active)
            raw = match.group(1)
            names = re.findall(r'P\*\*|[A-Za-z]+\d*', re.sub(r'\*\d+', '', raw))
            if 'to' in names:
                names.remove('to')
                first, last = (re.fullmatch(r'([A-Za-z]+)(\d+)', n) for n in (names[0], names[-1]))
                if first and last and first.group(1) == last.group(1):
                    names = [first.group(1) + str(i) for i in range(int(first.group(2)), int(last.group(2)) + 1)]
            names = [_native_argument_name(name) for name in names if name not in {'and'}]
            active = {'names': names, 'description': match.group(2), 'witness': line, 'line': index}
            continue
        if active:
            if re.match(r'^(?:Setting|Processing|Operation error|Precautions|■|\d+ [A-Z])', line):
                result.append(active)
                active = None
            elif line and not line.startswith('*'):
                active['description'] += ' ' + line
                active['witness'] += ' ' + line
    if active:
        result.append(active)
    return result


def _native_grid_rows(tables):
    result = []
    for table in tables:
        cells = table.get('cells', [])
        headers = {re.sub(r'\s+', ' ', c['text']).strip(): c for c in cells[:12]}
        name_header = headers.get('Setting data')
        desc_header = headers.get('Description') or headers.get('Meaning')
        type_header = headers.get('Data type')
        if not name_header or not desc_header or not type_header:
            continue
        name_col, desc_col, type_col = [h['bbox'] for h in (name_header, desc_header, type_header)]
        for cell in cells:
            box, raw_name = cell['bbox'], cell['text'].strip()
            if (not raw_name or abs(box[0] - name_col[0]) > 2 or box[1] < name_col[3] - 1
                    or not re.fullmatch(r'\(?[SDR]\d*\)?|[nN]\d*|Pn|Un|Jn|U0', raw_name)):
                continue
            def covering(column):
                # The type must cover this complete name cell, including an
                # explicitly shared multi-row name; partial structure fields
                # cannot become the scalar type of the whole argument.
                return next((c['text'] for c in cells if abs(c['bbox'][0] - column[0]) < 2
                             and c['bbox'][1] <= box[1] + 1 and c['bbox'][3] >= box[3] - 1), '')
            description, dtype = covering(desc_col), covering(type_col)
            if not description:
                description = ' '.join(c['text'] for c in cells if abs(c['bbox'][0] - desc_col[0]) < 2
                    and abs(c['bbox'][1] - box[1]) < 1 and c['bbox'][2] <= type_col[0] + 1)
            fields = []
            if not dtype:
                # An argument can span several differently typed record fields.
                # Keep every actual enclosed field instead of borrowing one
                # field's type for the complete destination record.
                for typed in cells:
                    span = typed['bbox']
                    if (abs(span[0] - type_col[0]) > 2 or span[1] < box[1] - 1
                            or span[3] > box[3] + 1 or not typed['text'].strip()):
                        continue
                    parts = [c['text'] for c in cells if c['bbox'][0] >= desc_col[0] - 1
                             and c['bbox'][2] <= desc_col[2] + 1 and abs(c['bbox'][1] - span[1]) < 1
                             and abs(c['bbox'][3] - span[3]) < 1]
                    field_text = ' '.join(parts)
                    offset = re.search(r'\([A-Z]\)\s*\+\s*(\d+)(?:\s*to\s*\([A-Z]\)\s*\+\s*(\d+))?', field_text)
                    if offset:
                        fields.append({'offset': int(offset.group(1)),
                            'end_offset': int(offset.group(2) or offset.group(1)),
                            'type': typed['text'].strip(), 'description': field_text})
                if fields:
                    description = ' '.join(field['description'] for field in fields)
                    dtype = 'DEVICE_REFERENCE'
            result.append({'names': [_native_argument_name(raw_name)], 'description': description,
                           'declared_type': dtype, 'fields': fields,
                           'witness': ' '.join((raw_name, description, dtype)) if not fields else
                                      ' '.join([raw_name, *(field['description'] + ' ' + field['type'] for field in fields)]),
                           'line': 1000})
    return result


def _native_data_type(description, declared_type='', section=None):
    text = re.sub(r'\s+', ' ', declared_type or description).lower()
    if declared_type in {'DEVICE_REFERENCE', 'INTEGER'}:
        return {'type': declared_type}
    if declared_type:
        known = _parameter_type(declared_type)
        if known['type'] != 'unknown':
            return known
    if re.search(r'bits?,? bin 16 bits?,? bin 32 bits?', text):
        return {'type': 'BOOL_OR_ANY16_OR_ANY32', 'type_alternatives': ['BOOL', 'ANY16', 'ANY32']}
    if 'floating' in text or 'real number' in text:
        double = re.search(r'64|double.precision', text) or section and re.search(r'double.precision', section.title, re.I)
        return {'type': 'LREAL' if double else 'REAL'}
    if re.search(r'bin\s*(?:16/32|16 or 32)', text):
        return {'type': 'ANY16_OR_ANY32', 'type_alternatives': ['ANY16', 'ANY32'], 'encoding': 'binary'}
    binary = re.search(r'bin\s*[- ]?\s*(16|32|64)\s*[- ]?\s*bits?', text)
    if binary:
        return {'type': 'ANY' + binary.group(1), 'encoding': 'binary'}
    bcd = re.search(r'bcd\s*[- ]?\s*(4/8|4 or 8|4|8)\s*(?:digits?|\-digit)', text)
    if bcd:
        digits = bcd.group(1)
        return ({'type': 'ANY16_OR_ANY32', 'type_alternatives': ['ANY16', 'ANY32'], 'encoding': 'BCD',
                 'decimal_digit_choices': [4, 8]} if '/' in digits or 'or' in digits else
                {'type': 'ANY16' if digits == '4' else 'ANY32', 'encoding': 'BCD', 'decimal_digits': int(digits)})
    if re.search(r'\((?:a )?bits?\)|\bbit$|^bit\b', text):
        return {'type': 'BOOL'}
    if re.search(r'\bstring\b|file name', text):
        if re.search(r'device\*|device name.*character string', text):
            return {'type': 'DEVICE_OR_STRING', 'type_alternatives': ['DEVICE_REFERENCE', 'STRING']}
        return {'type': 'STRING'}
    if 'ascii' in text:
        return {'type': 'ASCII_CODE', 'encoding': 'ASCII'}
    if 'pointer' in text:
        return {'type': 'POINTER', 'device_class': 'P'}
    if 'nesting' in text:
        return {'type': 'NESTING_LEVEL', 'device_class': 'N'}
    if re.search(r'timer number|counter number|annunciator', text):
        return {'type': 'BOOL', 'device_class': 'T' if 'timer' in text else 'C' if 'counter' in text else 'F'}
    if 'i/o number' in text:
        return {'type': 'MODULE_IO_NUMBER'}
    if 'network number' in text:
        return {'type': 'NETWORK_NUMBER'}
    if 'step number' in text:
        return {'type': 'STEP_NUMBER'}
    if re.search(r'number of|bit position|bit number|set value|initial value|final value', text):
        return {'type': 'INTEGER'}
    if re.search(r'device name|device number|number of (?:the )?devices?|start device|head number|start number|device.*stored|device.*stores', text):
        return {'type': 'DEVICE_REFERENCE'}
    return {'type': 'unknown', 'source_disposition': 'source_present_not_extracted',
            'gap_kind': 'source_present_not_extracted'}


def _native_direction(section, symbol, name, description, role, count):
    text = re.sub(r'\s+', ' ', description).lower()
    base = _parameter_pulse_base(symbol)
    if re.search(r'passed as an argument|data to be exchanged', text):
        return 'in_out'
    if name.startswith('D') and 'control data' in text:
        return 'in_out'
    if name.startswith('D') and (count == 2 and family_of(section) in {'arithmetic', 'logic'} or
            base in {'NEG', 'DNEG', 'ENEG', 'EDNEG', 'INC', 'DINC', 'DEC', 'DDEC', 'COM', 'FF',
                     'ROL', 'ROR', 'RCL', 'RCR', 'DROL', 'DROR', 'DRCL', 'DRCR', 'BSET', 'BRST'}
            or family_of(section) in {'rotation', 'bit_shift'} and 'shift' in text):
        return 'in_out'
    if symbol == 'PID' and name in {'S3', 'D'}:
        return 'in_out'
    if role in {'completion_status', 'execution_status'}:
        return 'output'
    if re.search(r'another cpu|other cpu|target station', text) and re.search(r'device.*(?:written|stored)|start device', text):
        return 'input'
    if name.startswith('D'):
        return 'output'
    return 'input'


def _native_drawing_forms(section, header, local, known):
    """Separate sibling drawings, including a zero-operand terminator.

    Section captions and program examples are not operand rows. A repeated
    opcode in the drawing establishes a zero-operand form; contacts whose
    address is placed above the symbol still use their explicit legend.
    """
    candidates, per_form = [], {}
    command_start = next((i for i, line in enumerate(header) if line == 'Command'), len(header))
    previous_forms = []
    for index, original in enumerate(header):
        line = re.sub(r'\b([SD])(\d+) to \1(\d+)\b',
            lambda m: ' '.join(m.group(1) + str(i) for i in range(int(m.group(2)), int(m.group(3)) + 1)), original)
        tokens = re.split(r'\s+', line)
        names = [_native_argument_name(token) for token in tokens
                 if _NATIVE_ARGUMENT.fullmatch(token.strip('(),:'))]
        names = list(dict.fromkeys(names))
        forms = [name for name in local if re.search(
            r'(?<![A-Za-z0-9_.])' + re.escape(name) + r'(?![A-Za-z0-9_.])', line)]
        repeated = [name for name in forms if len(re.findall(
            r'(?<![A-Za-z0-9_.])' + re.escape(name) + r'(?![A-Za-z0-9_.])', line)) >= 2]
        if index < command_start and not repeated:
            continue
        if names:
            candidates.append(names)
            for name in forms or previous_forms:
                per_form.setdefault(name, []).append(names)
        elif repeated:
            for name in repeated:
                per_form.setdefault(name, []).append([])
        if forms:
            previous_forms = forms
        elif line == 'Command':
            previous_forms = []
    fallback = max(candidates, key=len) if candidates else list(known)
    grouped = {}
    for symbol in local:
        rows = per_form.get(symbol)
        if rows:
            order = max(rows, key=len)
            minimum = min(len(row) for row in rows if row == order[:len(row)])
        else:
            order, minimum = fallback, len(fallback)
        grouped.setdefault((tuple(order), minimum), []).append(symbol)
    return [{'allowed': forms, 'order': list(order), 'minimum': minimum,
             'documented_zero_form': not order and all(per_form.get(name) == [[]] for name in forms)}
            for (order, minimum), forms in grouped.items()]


def _prepare_native_parameter_contracts(source, sections):
    report = {key: [] for key in ('sections', 'source_present_not_extracted', 'source_not_stated', 'source_conflict', 'native_pending')}
    zero = {'ANB', 'ORB', 'MPS', 'MRD', 'MPP', 'INV', 'MEP', 'MEF', 'FEND', 'END', 'STOP',
            'NOP', 'NOPLF', 'GOEND', 'IRET', 'RET', 'COM', 'CCOM', 'CCOMP', 'LEDR',
            'WDT', 'WDTP', 'TRACE', 'TRACER', 'CHKST', 'CHK', 'CHKCIR', 'CHKEND', 'IXEND', 'IXDEV'}
    for section in sections.values():
        if (section.manual != 'sh080809engx' or not section.has_instruction_chunk or not section.symbols
                or _is_instruction_overview(section)):
            continue
        section._native_parameter_contracts = []
        section._geometry_flat = []
        first, last = min(section.pages), max(end for _, end in section.page_pairs)
        pages = _fetch(source, 'SELECT pdf_page,word_geometry_json,visual_json FROM page_artifacts '
                              'WHERE manual_id=? AND pdf_page>=? AND pdf_page<=? ORDER BY pdf_page',
                       (section.manual, first, last))
        active = None
        for page in pages:
            number = int(page['pdf_page'])
            words = json.loads(page['word_geometry_json'] or '[]')
            lines = _parameter_word_lines(words)
            owner = next((row for row in section.rows if int(row['pdf_page']) <= number <= int(row['pdf_page_end'] or row['pdf_page'])), None)
            if owner is None or not lines:
                continue
            section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', '\n'.join(lines))))
            rows = _native_legend_rows(lines)
            grids = _native_grid_rows(json.loads(page['visual_json'] or '{}').get('parameter_tables', []))
            first_legend = min((row['line'] for row in rows), default=1000)
            stop = min((i for i, line in enumerate(lines) if line.startswith(
                ('Setting', 'Processing details', 'Operation error', 'Program example'))), default=len(lines))
            header = lines[:min(first_legend, stop)]
            _tokens, local = declared_symbols(header, '')
            local = [name for name in local if name in section.symbols]
            if number == first and not local:
                local = list(section.symbols)
            if number != first and not ('Command' in header and 'Basic' in ' '.join(header)):
                # Later examples can name the same opcode and literal addresses.
                # Only a repeated CPU-labelled instruction panel starts a form.
                local = []
            if not local:
                if active:
                    for row in grids + (rows if number <= active['page'] + 1 else []):
                        for name in row['names']:
                            if name in active['order']:
                                active['rows'][name] = row
                                section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', row['witness'])))
                continue
            known = {}
            for row in rows + grids:
                for name in row['names']:
                    known[name] = row
                section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', row['witness'])))
            if 'PAGE' in local:
                known.setdefault('n', {'names': ['n'], 'description': 'Page decimal integer constant',
                    'declared_type': 'INTEGER', 'witness': '*1 Only the decimal constant (K) can be used.'})
            forms = _native_drawing_forms(section, header, local, known)
            if family_of(section) == 'output' and any(name in local for name in {'OUT_T', 'OUTH_T', 'OUT_ST', 'OUTH_ST', 'OUT_C'}):
                forms = [{'allowed': local, 'order': ['D', 'setting_value'], 'minimum': 2}]
                known.setdefault('setting_value', {'names': ['setting_value'], 'description': 'Timer_or_counter_set_value BIN 16 bits',
                    'witness': 'Setting value' if section.bind('Setting value') else 'Set value'})
            for form in forms:
                if all(name in zero for name in form['allowed']):
                    form.update(order=[], minimum=0)
                if not form['order'] and not form.get('documented_zero_form') and not all(name in zero for name in form['allowed']):
                    report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                        'page': number, 'forms': form['allowed'], 'reason': 'native_drawing_operand_row_not_bound'})
                    continue
                active = {'page': number, **form, 'rows': known}
                if set(form['allowed']) & {'CHKST', 'CHK', 'CHKCIR', 'CHKEND'}:
                    active['surrounding_ladder_grammar'] = ({
                        'condition_instructions': ['LD', 'AND'], 'condition_device_class': 'X',
                        'maximum_condition_contacts': 150, 'begin_instruction': 'CHKST', 'check_instruction': 'CHK'}
                        if 'CHKST' in section.symbols else {'begin_instruction': 'CHKCIR', 'end_instruction': 'CHKEND',
                        'maximum_pattern_coils': 9, 'pattern_coil_device_class': 'F', 'pattern_index_register': 'Z0'})
                    active['grammar_witnesses'] = (['Only input (X) can be used', 'Up to 150 contacts can be connected']
                        if 'CHKST' in section.symbols else ['Max. 9 coils', 'index modification (Z0)'])
                section._native_parameter_contracts.append(active)
        bound = {symbol for contract in section._native_parameter_contracts for symbol in contract['allowed']}
        for contract in section._native_parameter_contracts:
            for name in contract['order']:
                if name not in contract['rows']:
                    report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                        'page': contract['page'], 'forms': contract['allowed'], 'operand': name,
                        'reason': 'native_operand_legend_or_setting_table_not_bound'})
                else:
                    row = contract['rows'][name]
                    dtype = _native_data_type(row['description'], row.get('declared_type', ''), section)
                    if dtype['type'] == 'unknown':
                        report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                            'page': contract['page'], 'forms': contract['allowed'], 'operand': name,
                            'reason': 'native_operand_type_not_formalised', 'source_description': row['description']})
        missing = sorted(set(section.symbols) - bound)
        if missing:
            report['source_present_not_extracted'].append({'manual': section.manual, 'section': section.title,
                'forms': missing, 'reason': 'native_form_not_bound'})
        report['sections'].append({'manual': section.manual, 'section': section.title,
            'panels': len(section._native_parameter_contracts), 'declared_forms': len(section.symbols), 'bound_forms': len(bound)})
        report['native_pending'].extend({'manual': section.manual, 'section': section.title,
            'form': symbol, 'languages': ['ladder'], 'meaning': 'source_documented_no_native_compile_or_execution_claimed'}
            for symbol in section.symbols)
    return report


def _native_parameter_specs(section, symbol):
    result = []
    for contract in getattr(section, '_native_parameter_contracts', []):
        if symbol not in contract['allowed']:
            continue
        pins, witnesses = [], [section.heading_line, *contract.get('grammar_witnesses', [])]
        for index, name in enumerate(contract['order']):
            row = contract['rows'].get(name)
            if row is None:
                pins.append({'name': name, 'type': 'unknown', 'direction': 'unknown', 'role': 'unknown',
                             'source_disposition': 'source_present_not_extracted', 'gap_kind': 'source_present_not_extracted'})
                continue
            dtype = _native_data_type(row['description'], row.get('declared_type', ''), section)
            role = _parameter_role(row['description'], name, 'output' if name.startswith('D') else 'input')
            if role == 'unknown':
                role = 'program_pointer' if name == 'Pn' else 'source_data' if not name.startswith('D') else 'destination_data'
            direction = _native_direction(section, symbol, name, row['description'], role, len(contract['order']))
            pin = {'name': name, **dtype, 'direction': direction, 'role': role,
                   'representation': 'native_operand', 'optional': index >= contract['minimum']}
            if row.get('fields'):
                pin['referent_fields'] = [{'offset': field['offset'], 'end_offset': field['end_offset'],
                    **_native_data_type(field['description'], field['type']),
                    'role': _parameter_role(field['description'], name, direction)} for field in row['fields']]
            if dtype['type'] in {'DEVICE_REFERENCE', 'POINTER'} or re.search(r'device', row['description'], re.I):
                pin['passing'] = 'device_reference'
            pins.append(_parameter_form_type(section, symbol, pin))
            witnesses.append(row['witness'])
        case = ','.join(contract['order']) or 'zero'
        values = [('parameters.typed_operands', {'operands': pins, 'operand_count': len(pins)}),
                  ('parameters.arity', {'minimum': contract['minimum'], 'maximum': len(pins), 'count_kind': 'native_operands'}),
                  ('parameters.order', {'symbols': contract['order'], 'order_source': 'native_instruction_drawing'})]
        for dimension, value in values:
            value['native_form'] = {'operand_order': contract['order'], 'minimum_operands': contract['minimum'],
                                    'maximum_operands': len(pins)}
            if contract.get('surrounding_ladder_grammar'):
                value['native_form']['surrounding_ladder_grammar'] = copy.deepcopy(contract['surrounding_ladder_grammar'])
            spec = _reviewed_spec('native_arguments_' + _token(case), dimension, witnesses, value, ('ladder',))
            result.append(spec)
    return result


def _model_matches_cpu_label(model, label):
    label = label.lower()
    if 'basic model qcpu' in label:
        return model in _BASIC_Q
    if 'high performance model qcpu' in label:
        return model in _HIGH_Q
    if 'process cpu' in label and re.fullmatch(r'Q\d+PH', model):
        return True
    if 'redundant cpu' in label and 'PRH' in model:
        return True
    if 'lcpu' in label:
        return model.startswith('L') and ('built-in ethernet' not in label or not model.startswith('L02S'))
    if 'qnudpv' in label and 'UDPV' in model:
        return True
    if 'qnudv' in label and 'UDV' in model:
        return True
    if 'qnude(h)' in label and ('UDE' in model):
        return True
    if ('qnu(d)(h)' in label or 'qnud(h)' in label) and re.fullmatch(r'Q\d+U(?:J|D|DH)?', model):
        return True
    if 'universal' in label:
        return model.startswith('Q') and 'U' in model
    return model in {name.replace('CPU', '') for name in re.findall(r'Q\d+[A-Z]*CPU', label.upper())}


def _cpu_conditions(section, model, state, symbol=None):
    override = getattr(section, 'symbol_applicability', {}).get(symbol, {}).get(model)
    override = override or getattr(section, 'table_applicability', {}).get(model)
    if override is not None:
        return copy.deepcopy([*override.get('case_conditions', []), *override['conditions']])
    if state != 'conditional':
        return []
    found = []
    for line in section.lines:
        label = line.lstrip('• ').split(':', 1)[0]
        if not _model_matches_cpu_label(model, label):
            continue
        serial = re.search(r'(?:serial number[^\d]{0,45}|first (?:five|5) digits[^\d]{0,45})'
                           r'[\'\"]?(\d{5})[\'\"]?\s*or (?:later|higher)', line, re.I)
        if serial:
            found.append({'kind': 'serial_number_minimum', 'minimum': serial.group(1), 'digits': 5})
        version = re.search(r'function version(?: is)?\s+([A-D])\s+or later', line, re.I)
        if version:
            found.append({'kind': 'function_version_minimum', 'minimum': version.group(1).upper()})
    if found:
        return [dict(item) for item in {json.dumps(item, sort_keys=True): item for item in found}.values()]
    return [{'kind': 'documented_condition', 'value': 'unknown',
             'basis': 'cpu_version_or_operand_premise_requires_source_review'}]


def _guard_specs(section, specs, model, state, availability, symbol=None):
    conditions = _cpu_conditions(section, model, state, symbol)
    module = getattr(section, 'module_requirement', None)
    if module and not module.get('bound'):
        conditions = conditions + [{'kind': 'required_module_family', 'value': module['family']}]
    required = _required_context(conditions)
    guarded = []
    for original in specs:
        spec = dict(original, conditions=list(original['conditions']) + conditions)
        if required:
            combined = [*original['value'].get('required_context', []), *required]
            spec['value'] = dict(original['value'], required_context=list(
                {json.dumps(item, sort_keys=True): item for item in combined}.values()))
        if module and module.get('bound') and original['dimension'] == 'constraints.hardware_requirements':
            binding = next((condition for condition in conditions
                            if condition.get('kind') == 'module_condition'), None)
            if binding:
                spec['value'] = dict(spec['value'], predicate=copy.deepcopy(binding['predicate']),
                                     module_families=binding['module_families'],
                                     module_models=binding['module_models'])
        if not availability:
            if spec.get('reviewed'):
                spec['source_checked'] = True
            spec.pop('reviewed', None)
        if availability and spec['dimension'] != 'identity.declared_form':
            spec['depends_on'] = list(spec.get('depends_on') or ()) + [
                'authored:ql:cpu_applicability:constraints.cpu_availability']
        guarded.append(spec)
    return guarded


def _cpu_names(text):
    return list(dict.fromkeys(base + (suffix or '')
                             for base, suffix in re.findall(r'([QL][0-9A-Z]+)CPU(-[A-Z]+)?', text)))


def _manual_models(rows):
    return sorted({model for section in rows for row in section.rows
                   for model in json.loads(row.get('source_applicability_json') or '{}').get('supported_models', ())})


def _module_model_groups(overview):
    starts = [('analog', 'Analog module'), ('positioning', 'Positioning module'),
              ('serial_communication', 'Serial communication module'),
              ('cc_link', 'CC-Link system master/local module'),
              ('cc_ie_controller', 'CC-Link IE Controller Network module'),
              ('cc_ie_field', 'CC-Link IE Field Network module'),
              ('melsecnet_h', 'MELSECNET/H network module'),
              ('ethernet', 'Ethernet interface module'),
              ('end', 'CPU module supporting the PID control')]
    text = re.sub(r'-\s*\n\s*(?=[A-Z0-9])', '-', overview.text)
    groups = {}
    for (family, start), (_next, end) in zip(starts, starts[1:]):
        block = text.split(start, 1)[1].split(end, 1)[0]
        groups[family] = list(dict.fromkeys(re.findall(r'\b[QL][A-Z0-9]+(?:-[A-Z0-9]+)*', block)))
    return groups


def _module_form_base(symbol):
    return re.sub(r'_[JU]$', '', re.sub(r'^(?:[GJSZ]P?|SP)_', '', symbol))


def _module_families(section, symbol):
    base = _module_form_base(symbol)
    family = family_of(section)
    if family in {'m_analog', 'm_positioning'}:
        return ['analog' if family == 'm_analog' else 'positioning']
    if family == 'm_serial':
        return ['serial_communication'] if base in {'UINI', 'CPRTCL'} else ['serial_communication', 'modem_interface']
    if base in {'RIRD', 'RIWT'}:
        return ['cc_link', 'cc_ie_controller', 'cc_ie_field']
    if base in {'RIRCV', 'RISEND', 'RIFR', 'RITO', 'RLPASET'}:
        return ['cc_link']
    if base in {'READ', 'SREAD', 'WRITE', 'SWRITE', 'SEND', 'RECV', 'RECVS', 'REQ'}:
        return ['cc_ie_controller', 'cc_ie_field', 'melsecnet_h', 'ethernet']
    if base in {'ZNRD', 'ZNWR'}:
        return ['cc_ie_controller', 'melsecnet_h', 'ethernet']
    if base in {'RRUN', 'RSTOP', 'RTMRD', 'RTMWR'}:
        return ['cc_ie_controller', 'melsecnet_h']
    if base in {'REMFR', 'REMTO'}:
        return ['cc_ie_field', 'melsecnet_h']
    if base == 'CCPASET':
        return ['cc_ie_field']
    if base == 'UINI':
        return ['cc_ie_controller', 'ethernet'] if symbol.startswith('ZP_') else ['ethernet']
    return ['ethernet']


def _module_condition(section, symbol, model, groups):
    families = _module_families(section, symbol)
    base = _module_form_base(symbol)
    models = {family: groups.get(family, []) for family in families if groups.get(family)}
    if family_of(section) == 'm_serial' and base == 'UINI':
        models = {'serial_communication': ['QJ71C24N', 'QJ71C24N-R2', 'QJ71C24N-R4']}
    required = [{'field': 'selected_module_CPU_compatible', 'type': 'BOOL',
                 'meaning': 'selected_module_compatible_with_this_exact_CPU_and_instruction'}]
    tests = []
    for family in families:
        names = models.get(family)
        if names:
            fields = ['selected_module_model_' + re.sub(r'[^A-Za-z0-9_]', '_', name) for name in names]
            test = _join_predicates('or', [_context_test(field) for field in fields])
            required.append({'any_of': fields, 'type': 'BOOL', 'models': names,
                             'meaning': 'one_selected_target_module_model'})
        else:
            field = 'selected_module_family_' + family
            test = _context_test(field)
            required.append({'field': field, 'type': 'BOOL', 'family': family})
        if family_of(section) == 'm_serial' and base == 'UINI':
            module_version = _context_test('selected_module_function_version_rank', 'eq', 2)
            module_serial = _context_test('selected_module_serial_first_five', 'ge', 6062)
            test = _join_predicates('and', [test, module_version, module_serial])
            required.extend([{'field': 'selected_module_function_version_rank', 'type': 'UINT32',
                              'equals': 'B', 'encoding': {'A': 1, 'B': 2, 'C': 3, 'D': 4}},
                             {'field': 'selected_module_serial_first_five', 'type': 'UINT32',
                              'minimum': '06062', 'digits': 5}])
        if base == 'RLPASET' and family == 'cc_link':
            old = _context_test('selected_module_model_QJ61BT11')
            new = _join_predicates('or', [_context_test('selected_module_model_' + name)
                                          for name in ('QJ61BT11N', 'LJ61BT11')])
            test = _join_predicates('or', [new, _join_predicates('and', [old,
                _context_test('selected_module_function_version_rank', 'eq', 2),
                _context_test('selected_module_serial_first_five', 'ge', 3042)])])
            required.extend([{'field': 'selected_module_function_version_rank', 'type': 'UINT32',
                              'only_model': 'QJ61BT11', 'equals': 'B',
                              'encoding': {'A': 1, 'B': 2, 'C': 3, 'D': 4}},
                             {'field': 'selected_module_serial_first_five', 'type': 'UINT32',
                              'only_model': 'QJ61BT11', 'minimum': '03042', 'digits': 5}])
        if base == 'UINI' and family == 'cc_ie_controller':
            test = _join_predicates('and', [test, {'op': 'constant', 'type': {'kind': 'bool'},
                                                 'value': model.startswith('Q') and 'U' in model}])
        tests.append(test)
    return {'kind': 'module_condition', 'module_families': families, 'module_models': models,
            'predicate': _join_predicates('and', [_context_test('selected_module_CPU_compatible'),
                                                 _join_predicates('or', tests)]),
            'required_context': list({json.dumps(item, sort_keys=True): item for item in required}.values())}


def _union_module_availability(facts):
    """Same-form target modules are alternatives, with their own effect guards.

    The shared checker correctly checks every constraint root. Encode the
    documented target-module union in one root rather than requiring a serial
    module and an Ethernet module for the same literal ZP_UINI call.
    """
    selected = [fact for fact in facts if fact['dimension'] == 'constraints.cpu_availability'
                and fact['value'].get('selected_module_compatibility') == 'CPU_and_instruction_specific']
    positive = [fact for fact in selected if fact['value'].get('available') is True]
    if len(positive) < 2:
        return facts
    if any(fact.get('status') != 'source_verified' for fact in selected):
        return facts
    negative = [fact for fact in selected if fact['value'].get('available') is False]
    if not negative:
        return facts
    predicates = [_all_condition_predicates(fact.get('conditions', [])) for fact in positive]
    if any(value is None for value in predicates):
        return facts
    union = _join_predicates('or', predicates)
    kept = copy.deepcopy(positive[0])
    rejected = copy.deepcopy(negative[0])
    required = _required_context([condition for fact in positive for condition in fact.get('conditions', ())])
    sources = []
    for fact in positive:
        for source in fact['sources']:
            if source not in sources:
                sources.append(source)
    families = sorted({family for fact in positive for family in fact['value'].get('module_families', ())})
    models = {family: sorted({model for fact in positive
                              for model in fact['value'].get('module_models', {}).get(family, ())})
              for family in families}
    for fact, predicate in ((kept, union), (rejected, _predicate('not', union))):
        fact['conditions'] = [{'kind': 'module_condition', 'predicate': predicate, 'required_context': required}]
        fact['sources'] = copy.deepcopy(sources)
        fact['value'].update(module_families=families, module_models=models,
                             required_context=required, documented_module_alternatives=True)
    replacements = {fact['id']: kept['id'] if fact['value'].get('available') else rejected['id'] for fact in selected}
    result = [fact for fact in facts if fact not in selected]
    result.extend([kept, rejected])
    for fact in result:
        if fact.get('depends_on'):
            fact['depends_on'] = list(dict.fromkeys(replacements.get(identity, identity)
                                                   for identity in fact['depends_on']))
    return result


def _prepare_m_applicability(sections):
    """M uses the section 1.3 CPU table; its target-module icons are not CPU icons."""
    manual = 'sh080785engm'
    rows = [s for (m, _), s in sections.items() if m == manual]
    overview = next((s for s in rows if s.title.startswith('1.3 Modules and Versions')), None)
    if overview is None:
        return
    universe = _manual_models(rows)
    module_models = _module_model_groups(overview)
    boundaries = (
        ('pid_control', 'CPU module supporting the PID control', 'Built-in Ethernet port QCPU'),
        ('built_in_ethernet', 'Built-in Ethernet port QCPU', 'LCPU (Built-in I/O function)'),
        ('built_in_io', 'LCPU (Built-in I/O function)', 'Data logging function'),
        ('data_logging', 'Data logging function', '1 OVERVIEW'),
        ('sfc_control', 'CPU module supporting the SFC control', 'Function/module for explaining'),
    )
    cpu_groups = {}
    for key, start, end in boundaries:
        offset = overview.text.find(start)
        stop = overview.text.find(end, offset + len(start))
        if offset < 0:
            continue
        block = overview.text[offset:stop if stop > offset else None]
        cpu_groups[key] = (_cpu_names(block), start)
    pid_table = next((s for s in rows if s.title == 'PID operation'
                      and 'Inexact' in s.chapter and '04122' in s.text and '05032' in s.text), None)
    for section in rows:
        if not section.has_instruction_chunk:
            continue
        family = family_of(section)
        function = None
        if family == 'm_pid':
            function = 'pid_control'
        elif '7 SOCKET COMMUNICATION' in section.outline:
            function = 'built_in_ethernet'
        elif '8 BUILT-IN I/O' in section.outline:
            function = 'built_in_io'
        elif '9 DATA LOGGING' in section.outline:
            function = 'data_logging'
        elif '10 SFC CONTROL' in section.outline:
            function = 'sfc_control'
        if function and function in cpu_groups:
            models, witness = cpu_groups[function]
            section.table_applicability = {}
            for model in universe:
                if model not in models:
                    section.table_applicability[model] = {
                        'state': 'unsupported', 'conditions': [], 'witnesses': [witness],
                        'sections': [overview], 'technical_scope': {'cpu_function': function,
                                                                    'applicable_models': models}}
                    continue
                conditions = []
                witnesses, evidence = [witness], [overview, section]
                serial, version = None, None
                if function == 'built_in_ethernet' and model.startswith('Q'):
                    serial, version = '11012', 'B'
                    witnesses.append("'11012' or higher")
                elif function == 'pid_control':
                    if pid_table is None:
                        continue
                    evidence.append(pid_table)
                    witnesses.append('CPU module model Inexact differential Exact differential')
                    if model in _BASIC_Q:
                        serial = '04122'
                    elif model in _HIGH_Q and 'Inexact Differential' in section.chapter:
                        serial = '05032'
                elif function == 'sfc_control':
                    if model in _HIGH_Q:
                        serial, version = '07012', 'B'
                    elif 'PH' in model or 'PRH' in model:
                        serial = '07032'
                    elif 'U' in model:
                        serial = '12052'
                    else:
                        continue
                if serial:
                    conditions.append({'kind': 'serial_number_minimum', 'minimum': serial, 'digits': 5})
                    witnesses.append(serial)
                if version:
                    conditions.append({'kind': 'function_version_minimum', 'minimum': version})
                # A CPU function is part of this instruction's documented scope;
                # serial/version restrictions remain conditions on every claim.
                state = 'conditional' if serial or version else 'supported'
                section.table_applicability[model] = {
                    'state': state, 'conditions': conditions,
                    'witnesses': witnesses, 'sections': evidence,
                    'technical_scope': {'cpu_function': function, 'applicable_models': models}}
        elif family in {'m_analog', 'm_positioning', 'm_serial', 'm_network'}:
            module_family = {'m_analog': 'analog', 'm_positioning': 'positioning',
                             'm_serial': 'serial_communication', 'm_network': 'network'}[family]
            section.module_requirement = {'family': module_family, 'source': overview, 'bound': True,
                                          'models': module_models}
            section.symbol_applicability = {}
            for symbol in section.symbols:
                section.symbol_applicability[symbol] = {}
                for model in universe:
                    condition = _module_condition(section, symbol, model, module_models)
                    witnesses = [section.heading_line, 'Function/module for explaining an instruction']
                    if family == 'm_serial' and _module_form_base(symbol) == 'UINI':
                        witnesses += ["'06062' or higher"]
                    if _module_form_base(symbol) == 'RLPASET':
                        witnesses += ["'03042' or higher"]
                    section.symbol_applicability[symbol][model] = {
                        'state': 'conditional', 'conditions': [condition],
                        'witnesses': witnesses, 'sections': [section, overview],
                        'technical_scope': {'module_families': condition['module_families'],
                                            'module_models': condition['module_models'],
                                            'selected_module_compatibility': 'CPU_and_instruction_specific'}}


def _prepare_i_applicability(sections):
    rows = [section for (manual, _), section in sections.items() if manual == 'sh080316ei']
    terms = next((section for section in rows if section.title == 'TERMS'), None)
    overview = next((section for section in rows if section.title == '1.1 Features'), None)
    if terms is None or overview is None:
        return
    definitions = [line for line in terms.lines if re.match(r'^Qn(?:PH|PRH|UDPV)CPU A generic term', line)]
    models = _cpu_names(' '.join(definitions))
    if len(definitions) != 3 or len(models) != 10:
        return
    for section in rows:
        if not section.has_instruction_chunk or not section.symbols or _is_instruction_overview(section):
            continue
        section.table_applicability = {model: {
            'state': 'supported', 'conditions': [],
            'witnesses': [*definitions, 'This manual describes the process control instructions equipped for the CPU module',
                          section.heading_line],
            'sections': [terms, overview, section], 'basis': 'process_control_manual_CPU_family_definitions',
            'technical_scope': {'cpu_families': ['QnPHCPU', 'QnPRHCPU', 'QnUDPVCPU'],
                                'applicable_models': models}} for model in models}


def _prepare_xp_applicability(sections):
    """Reviewed per-form headers for the seven outlines that merge unlike rows.

    In particular CALL/XCALL and S.TO/TO do not inherit one another's header.
    TO/DTO's CPU-shared-memory use is a condition on that documented use case.
    """
    for section in sections.values():
        title, manual = section.title, section.manual
        if manual not in {'sh080809engx', 'sh080783engp'}:
            continue
        call = manual == 'sh080809engx' and title == 'Subroutine program calls'
        route = manual == 'sh080809engx' and title in {'Reading routing information', 'Registering routing information'}
        shared = (manual == 'sh080809engx' and title == 'Writing to host CPU shared memory'
                  or manual == 'sh080783engp' and title.startswith('Writing data to host CPU shared memory'))
        shared_read = (manual == 'sh080809engx' and title == 'Reading from other CPU shared memory'
                       or manual == 'sh080783engp' and title == 'Reading data from other CPU shared memory')
        high_speed = manual == 'sh080783engp' and title.startswith(('10.2 Writing Device Data', '10.3 Reading Device Data'))
        redundant = manual == 'sh080783engp' and title.startswith('11.1 System Switching')
        if not any((call, route, shared, shared_read, high_speed, redundant)):
            continue
        # A per-header supported-model list is not the complete universe: its
        # explicit unsupported and version-conditional columns still need facts.
        universe = sorted(set(_manual_models([item for item in sections.values() if item.manual == manual]))
                          | set(_model_states(section)[0]))
        section.symbol_applicability = {}
        for symbol in section.symbols:
            scoped = section.symbol_applicability[symbol] = {}
            for model in universe:
                conditions, case, state = [], [], 'supported'
                witnesses = [symbol if section.bind(symbol) else section.heading_line]
                if call and symbol == 'XCALL' and model in _BASIC_Q:
                    conditions = [{'kind': 'serial_number_minimum', 'minimum': '04122', 'digits': 5}]
                    witnesses += ['Basic model QCPU: The serial number']
                elif route:
                    if model in _BASIC_Q:
                        state = 'unsupported'
                    elif model.startswith('L'):
                        conditions = [{'kind': 'serial_number_minimum', 'minimum': '13012', 'digits': 5}]
                        witnesses += ['LCPU: The serial number']
                elif shared or shared_read:
                    synchronous = symbol in {'TO', 'TOP', 'DTO', 'DTOP'}
                    if synchronous or shared_read:
                        case = [{'kind': 'hardware_condition',
                                 'predicate': _context_test('selected_cpu_shared_memory_target'),
                                 'required_context': [{'field': 'selected_cpu_shared_memory_target', 'type': 'BOOL',
                                                       'meaning': 'selected_instruction_target_is_CPU_shared_memory'}]}]
                    if model in _BASIC_Q:
                        if model in {'Q00', 'Q01'}:
                            conditions = [{'kind': 'serial_number_minimum', 'minimum': '04122', 'digits': 5}]
                            witnesses += ['Q00CPU and Q01CPU: Supported if first 5 digits of the serial number'
                                          if manual == 'sh080783engp' else 'Q00CPU, Q01CPU: The serial number']
                    elif model.startswith('L') or 'PRH' in model or synchronous and 'U' not in model:
                        state = 'unsupported'
                    elif model in _HIGH_Q:
                        conditions = [{'kind': 'function_version_minimum', 'minimum': 'B'}]
                        witnesses += ['High Performance model QCPU: Supported if the function version B or later'
                                      if manual == 'sh080783engp' else 'High Performance model QCPU: Function version B or later']
                elif high_speed:
                    if 'U' not in model or model in {'Q00UJ', 'Q00U', 'Q01U', 'Q02U'}:
                        state = 'unsupported'
                    else:
                        conditions = [{'kind': 'serial_number_minimum', 'minimum': '10012', 'digits': 5}]
                        witnesses += ['Universal model QCPU: Supported if first 5 digits']
                elif redundant:
                    state = 'supported' if 'PRH' in model else 'unsupported'
                    witnesses += ['REDUNDANT SYSTEM INSTRUCTIONS (FOR REDUNDANT CPU)']
                if conditions and state == 'supported':
                    state = 'conditional'
                scoped[model] = {'state': state, 'conditions': conditions, 'case_conditions': case,
                                 'witnesses': witnesses, 'sections': [section],
                                 'basis': 'reviewed_per_symbol_instruction_header'}


def _reviewed_spec(key, dimension, witnesses, value, languages=()):
    item = _spec(key, dimension, witnesses, value, languages=languages)
    item['reviewed'] = True
    return item


def _reviewed_literal_specs(section, symbol):
    """Literal comparison/operator definitions, independent of old chunk types."""
    text = ' '.join(section.text.split())
    languages = _section_languages(section, symbol)
    if section.manual == 'sh080784engn' and re.search(r'operator', section.chapter, re.I):
        operations = {'+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide', '**': 'power',
                      '>': 'greater_than', '>=': 'greater_equal', '=': 'equal', '<=': 'less_equal',
                      '<': 'less_than', '<>': 'not_equal', 'AND': 'and', 'OR': 'or',
                      'XOR': 'xor', 'NOT': 'not', '&': 'and', 'MOD': 'remainder'}
        if symbol in {'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'}:
            reference = _witness(text, (r'For details of the operation processing, refer to the following',))
            if reference:
                return [_reviewed_spec('named_comparison_reference', 'effects.operation', [reference],
                    {'operation': 'compare_values', 'comparison': symbol.lower(), 'result_type': 'BOOL',
                     'details': 'referenced_standard_comparison_function'}, ('st', 'fbd'))]
        if symbol not in operations:
            return []
        witness = _witness(text, (r'(?i)performs (?:addition|subtraction|multiplication|division|'
                                      r'exponentiation|comparison|boolean)[^.]{0,260}',
                                  r'(?i)exponentiation[^.]{0,120}', r'(?i)boolean[^.]{0,160}'))
        if not witness:
            return []
        comparison = symbol in {'>', '>=', '=', '<=', '<', '<>'}
        bitwise = symbol in {'AND', 'OR', 'XOR', 'NOT', '&'}
        data_type = ('ANY_SIMPLE' if comparison else 'ANY_BIT' if bitwise else
                     'ANY_INT' if symbol == 'MOD' else 'ANY_NUM')
        returns = 'BOOL' if comparison else data_type
        inputs = [_n_operand('s1', data_type)]
        if symbol != 'NOT':
            inputs.append(_n_operand('s2', data_type))
        specs = [
            _reviewed_spec('literal_operator', 'effects.operation', [witness],
                           {'operation': operations[symbol], 'operands': [row['name'] for row in inputs],
                            'result': 'expression_value'}, ('st',)),
            _reviewed_spec('literal_operator_parameters', 'parameters.typed_operands', [witness],
                           {'operands': inputs}, ('st',)),
            _reviewed_spec('literal_operator_expression', 'parameters.language_expression', [witness],
                           {'operator': symbol, 'operands': [row['name'] for row in inputs],
                            'returns': returns, 'available': True}, ('st',)),
            _reviewed_spec('literal_operator_not_function', 'parameters.st_signature', [witness],
                           {'available': False, 'reason': 'literal_ST_operator_is_an_expression'}, ('st',)),
        ]
        # This source describes its ST expression representation. Other source
        # manuals can document the same spelling as a Ladder or structured block.
        return specs
    if section.manual not in {'sh080809engx', 'sh080783engp'}:
        return []
    if symbol in {'MPS', 'MRD', 'MPP'}:
        patterns = {'MPS': r'Stores the memory of the operation result \(ON or OFF\)',
                    'MRD': r'Reads the operation result stored (?:for|in) the MPS instruction',
                    'MPP': r'Clears the operation results stored (?:by|in) the MPS instruction'}
        witness = _witness(text, (patterns[symbol],))
        if witness:
            value = dict(ASSOCIATION[symbol])
            return [_reviewed_spec('stack_operation', 'memory.stack', [witness], value, languages),
                    _reviewed_spec('stack_effect', 'effects.operation', [witness], value, languages)]
    if symbol in {'NOP', 'NOPLF', 'PAGE'} and section.title == 'No operations':
        witness = _witness(text, (r'This is a no operation instruction that has no impact on any operations',))
        if witness:
            value = {'operation': 'no_operation', 'operation_result': 'unchanged'}
            if symbol in {'NOPLF', 'PAGE'}:
                value['print_behavior'] = 'page_break' if symbol == 'NOPLF' else 'no_processing'
            return [_reviewed_spec('no_operation', 'effects.operation', [witness], value, languages)]
    relation = re.fullmatch(r'(LD|AND|OR)(ED|DT|TM|D|E|\$)?(<>|<=|>=|=|<|>)', symbol)
    block = re.fullmatch(r'(D?BKCMP)(<>|<=|>=|=|<|>)(P)?', symbol)
    if relation or block:
        operator = (relation.group(3) if relation else block.group(2))
        witness = _witness(text, (r'(?i)(?:performs? comparison operation|'
                                   r'compares? (?:BIN |the character|the BIN|the value|the data))[^.]{0,250}',
                                 r'(?i)comparison operation[^.]{0,120}',
                                 r'(?i)compares?[^.]{0,250}'))
        if not witness:
            return []
        if block:
            value = {'operation': 'compare_block_elements', 'left': 'S1', 'right': 'S2',
                     'comparison': operator, 'count': 'n', 'destination': 'D_n_bits',
                     'width_bits': 32 if block.group(1).startswith('D') else 16}
        else:
            prefix, data_kind, _ = relation.groups()
            value = {'operation': 'compose_comparison_contact', 'left': 'S1', 'right': 'S2',
                     'comparison': operator, 'composition': {'LD': 'assign', 'AND': 'and', 'OR': 'or'}[prefix],
                     'data_domain': {'D': 'signed_BIN32', 'E': 'REAL', 'ED': 'LREAL', '$': 'ASCII_string',
                                     'DT': 'date_year_month_day', 'TM': 'time_hour_minute_second'}.get(data_kind, 'signed_BIN16')}
        return [_reviewed_spec('literal_comparison', 'effects.operation', [witness], value, languages)]
    arithmetic = re.fullmatch(r'(DBK|BK|ED|E|DB|B|D|\$)?([+*/\-])(P)?', symbol)
    if not arithmetic:
        return []
    prefix, operator, _ = arithmetic.groups()
    if prefix == '$':
        witness = _witness(text, (r'(?i)(?:links|concatenates|connects)[^.]{0,160}character string',))
        if witness:
            structured = section.manual == 'sh080783engp'
            return [_reviewed_spec('literal_string_concatenation', 'effects.operation', [witness],
                {'operation': 'append_string', 'left': 'S1' if structured else 'D',
                 'right': 'S2' if structured else 'S', 'destination': 'D', 'terminator': 'NULL'}, languages)]
        return []
    verb = {'+': 'Adds', '-': 'Subtracts', '*': 'Multiplies', '/': 'Divides'}[operator]
    witness = _witness(' '.join(section.statements(symbol)), (r'(?i)' + verb + r'\b[^.]{0,240}',))
    if not witness:
        return []
    domain = 'BCD' if prefix in {'B', 'DB'} else 'REAL' if prefix == 'E' else 'LREAL' if prefix == 'ED' else 'BIN'
    value = {'operation': {'+': 'add', '-': 'subtract', '*': 'multiply', '/': 'divide'}[operator],
             'data_domain': domain, 'destination': 'D',
             'width_bits': 64 if prefix == 'ED' else 32 if prefix in {'D', 'DB', 'DBK', 'E'} else 16}
    if operator in {'*', '/'}:
        value['inputs'] = ['S1', 'S2']
    else:
        # These source sections declare both the two-data and three-data forms;
        # no arity is inferred from the literal operator itself.
        value['input_binding'] = 'documented_two_data_or_three_data_form'
    if operator == '/' and re.search(r'remainder', text, re.I) and domain in {'BIN', 'BCD'}:
        value['result_components'] = ['quotient', 'remainder']
    return [_reviewed_spec('literal_arithmetic', 'effects.operation', [witness], value, languages)]


def _n_operand(name, kind, direction='input', role=None, **extra):
    return dict(name=name, symbol=name, type=kind, direction=direction,
                role=role or ('result' if direction == 'output' else 'source'), **extra)


_N_BEHAVIOR_STARTS = frozenset({125, 128, 130, 132, 134, 136, 138, 140, 142, 146, 148, 151, 153,
    155, 157, 159, 161, 163, 165, 168, 170, 172, 174, 176, 178, 180, 182, 184, 186, 188, 192,
    194, 197, 200, 203, 207, 209, 210, 211, 213, 214, 215, 217})


def _prepare_n_behavior_sources(source, sections):
    """Bind all continuation cells, not only the first argument panel."""
    selected = [section for section in sections.values() if section.manual == 'sh080784engn'
                and section.has_instruction_chunk and section.pages
                and min(section.pages) in _N_BEHAVIOR_STARTS]
    by_first = {min(section.pages): section for section in selected}
    references = {207: 128, 209: 130, 210: 132, 211: 134, 213: 136, 214: 138, 215: 142, 217: 155}
    for section in selected:
        first, last = min(section.pages), max(end for _, end in section.page_pairs)
        section._n_behavior_tables, section._n_execution_rows = [], []
        for page in _fetch(source, 'SELECT pdf_page,word_geometry_json,visual_json FROM page_artifacts '
                                  'WHERE manual_id=? AND pdf_page>=? AND pdf_page<=? ORDER BY pdf_page',
                           (section.manual, first, last)):
            number = int(page['pdf_page'])
            owner = next((row for row in section.rows if int(row['pdf_page']) <= number <= int(
                row['pdf_page_end'] or row['pdf_page'])), None)
            if owner is None:
                continue
            words = json.loads(page['word_geometry_json'] or '[]')
            lines = _parameter_word_lines(words)
            section._geometry_flat.append((owner['id'], number, re.sub(r'\s+', ' ', '\n'.join(lines))))
            for table in json.loads(page['visual_json'] or '{}').get('behavior_tables', ()):
                actual = dict(table, page=number)
                section._n_behavior_tables.append(actual)
                cells = actual.get('cells', [])
                section._geometry_flat.extend((owner['id'], number,
                    re.sub(r'\s+', ' ', cell['text']).strip()) for cell in cells if cell.get('text'))
                headers = {cell['text']: cell for cell in cells if cell['text'] in {'EN', 'ENO'}}
                if set(headers) != {'EN', 'ENO'} or abs(headers['EN']['bbox'][1] - headers['ENO']['bbox'][1]) > 2:
                    continue  # an argument table is not an execution table
                for eno in cells:
                    if abs(eno['bbox'][0] - headers['ENO']['bbox'][0]) > 1 or eno['bbox'][1] < headers['ENO']['bbox'][3] - 1:
                        continue
                    if not eno['text'].startswith(('TRUE', 'FALSE')):
                        continue
                    row = [cell for cell in cells if abs(cell['bbox'][1] - eno['bbox'][1]) < 1
                           and abs(cell['bbox'][3] - eno['bbox'][3]) < 1]
                    row.sort(key=lambda cell: cell['bbox'][0])
                    if len(row) != 3 or not row[0]['text'].startswith(('TRUE', 'FALSE')):
                        continue
                    witness = ' '.join(re.sub(r'\s+', ' ', cell['text']) for cell in row)
                    section._geometry_flat.append((owner['id'], number, witness))
                    section._n_execution_rows.append({'EN': row[0]['text'].startswith('TRUE'),
                        'ENO': eno['text'].startswith('TRUE'), 'output': row[2]['text'],
                        'witness': witness, 'page': number})
        section._n_reference = by_first.get(references.get(first))
    # The audit flag follows bound, authored coverage. A geometry failure leaves
    # a source-present gap and never turns a section into a completed review.
    for section in selected:
        section._n_behavior_detail_cache = {symbol: _build_n_behavior_detail_specs(section, symbol)
                                            for symbol in section.symbols}
        section._n_whole_behavior_reviewed = not section._n_behavior_review['source_present_not_extracted']


def _n_behavior_detail_specs(section, symbol):
    if section.manual != 'sh080784engn':
        return []
    return copy.deepcopy(getattr(section, '_n_behavior_detail_cache', {}).get(symbol, []))


def _build_n_behavior_detail_specs(section, symbol):
    """Additional cases checked against every clause in N PDF125--217."""
    enabled = symbol.endswith('_E')
    base = symbol[:-2] if enabled else symbol
    operator_section = min(section.pages) >= 207
    reference = getattr(section, '_n_reference', None)
    evidence = reference or section
    text = ' '.join(evidence.text.split())
    semantics = {'+': 'ADD', '*': 'MUL', '-': 'SUB', '/': 'DIV', '**': 'EXPT', '&': 'AND',
                 '>': 'GT', '>=': 'GE', '=': 'EQ', '<=': 'LE', '<': 'LT', '<>': 'NE'}.get(base, base)
    languages = (('fbd',) if operator_section and base in {
        'ADD', 'MUL', 'SUB', 'DIV', 'AND', 'OR', 'XOR', 'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'}
        else ('st',) if operator_section else ('st', 'fbd'))
    report = section.__dict__.setdefault('_n_behavior_review', {
        'manual': section.manual, 'section': section.title, 'chapter': section.chapter,
        'pages': [min(section.pages), max(end for _, end in section.page_pairs)],
        'source_clauses': {}, 'source_present_not_extracted': [], 'source_conflict': [],
        'source_not_stated': [], 'native_pending': 'source_review_only_no_native_execution'})
    specs = []

    def add(key, dimension, witnesses, value, *, conflict=False, model_filter=None):
        witnesses = list(witnesses)
        owners = [section, evidence] if reference else [section]
        missing = [witness for witness in witnesses if not any(owner.bind(witness) for owner in owners)]
        if missing:
            row = {'form': symbol, 'rule': key, 'source_clauses': missing, 'reason': 'authored_clause_not_bound'}
            if row not in report['source_present_not_extracted']:
                report['source_present_not_extracted'].append(row)
            return
        if reference:
            witnesses = ['For details of the operation processing, refer to the following.', *witnesses]
        report['source_clauses'][symbol + '/' + key] = witnesses
        item = _reviewed_spec('n_review_' + key, dimension, witnesses, value, languages)
        item['evidence_sections'] = owners
        item['model_filter'] = model_filter
        if conflict:
            item['source_conflict'] = True
            row = {'forms': [symbol], 'rule': key, 'value': value}
            if row not in report['source_conflict']:
                report['source_conflict'].append(row)
        specs.append(item)
        if 'fbd' in languages:
            block = copy.deepcopy(item)
            block.update(key=item['key'] + '_structured_block', languages=('ladder',))
            block['conditions'] = [{'context': 'project_mode', 'equals': 'structured_ladder_block'}]
            block['value']['required_context'] = [{'field': 'project_mode', 'equals': 'structured_ladder_block'}]
            specs.append(block)

    def qualify(key, witness, detail):
        add(key, 'constraints.source_qualifications', [witness], {
            'subjects': detail.get('operand_names', ['source_values']),
            'qualifications': [dict(detail, parameter_binding='unknown',
                                    source_disposition='source_present_binding_unresolved')]})

    if enabled:
        rows = section._n_execution_rows
        if not rows:
            report['source_present_not_extracted'].append({'form': symbol, 'rule': 'execution_rows',
                'reason': 'actual_EN_ENO_result_table_not_bound'})
        else:
            output_tokens = {'Operation output value': 'computed_result', 'Previous output value': 'retain_previous',
                             'Undefined value': 'undefined_do_not_use'}
            disabled = next((row for row in rows if row['EN'] is False), None)
            cases = [{'EN': row['EN'], 'ENO': row['ENO'], 'result': output_tokens.get(row['output'], 'unknown')}
                     for row in rows]
            add('execution_results', 'execution.outcome', [row['witness'] for row in rows], {'cases': cases})
            if disabled:
                add('disabled_output', 'execution.disabled', [disabled['witness']], {
                    'when_disabled': output_tokens.get(disabled['output'], 'unknown'),
                    'disabled_ENO': False,
                    'outputs': ['d1', 'd2', 'd3'] if semantics == 'CTUD' else ['d1', 'd2'] if semantics in {
                        'CTU', 'CTD', 'TP', 'TON', 'TOF', 'TP_HIGH', 'TON_HIGH', 'TOF_HIGH'} else ['d']})
    if 'Rounding error may occur when specifying single-precision real or double-precision real type data' in text:
        add('input_rounding', 'effects.rounding', ['Rounding error may occur when specifying'], {
            'stage': 'programming_tool_input_specification', 'input_types': ['REAL', 'LREAL'],
            'rounding_error_possible': True})
    if semantics == 'ABS':
        add('signed_minimum', 'effects.boundary_case', ['input value is -32768, -32768 is output',
            'input value is -2147483648, -2147483648 is output'], {
            'cases': [{'type': 'INT', 'input': -32768, 'output': -32768},
                      {'type': 'DINT', 'input': -2147483648, 'output': -2147483648}],
            'operation_error': False, **({'ENO': True} if enabled else {})})
    if semantics in {'ABS', 'ADD', 'MUL', 'SUB', 'DIV', 'MOD', 'MOVE', 'AND', 'OR', 'XOR', 'NOT',
                     'SEL', 'MAXIMUM', 'MINIMUM', 'LIMITATION', 'MUX'}:
        # Generic type labels in the call table are overload domains, not
        # permission to mix different types inside one documented call.
        witness = _witness(text, (r'in the same data type as that of[^.]{0,140}',
                                  r'Only the same data type can be specified for[^.]{0,140}'))
        if witness:
            names = (['s', 'd'] if semantics in {'ABS', 'MOVE'} else ['s1', 'd'] if semantics == 'NOT'
                     else ['s2', 's3', 'd'] if semantics == 'SEL'
                     else ['s1', 's2', 's3', 'd'] if semantics == 'LIMITATION'
                     else ['s1', 's2', 'd'] if semantics in {'SUB', 'DIV', 'MOD'}
                     else ['s1..last_s_pin', 'd'])
            add('same_type', 'constraints.operand_type_relations', [witness], {
                'relation': 'same_runtime_type', 'operand_names': names,
                'binding': 'literal_named_operands', 'evaluation': 'requires_resolved_type_context'})
    if semantics in {'ADD', 'SUB', 'MUL', 'ADD_TIME', 'SUB_TIME', 'MUL_TIME'}:
        marker = 'No operation error occurs even if an underflow/overflow occurs.'
        overflow = {'operation_error': False, 'integer_result': 'retain_low_bits_twos_complement',
                    'result_bits': 32 if semantics.endswith('_TIME') else [16, 32]}
        if enabled:
            overflow['ENO'] = True
        if semantics in {'MUL', 'MUL_TIME'}:
            overflow['discarded_bits'] = 'upper_half_of_full_width_product'
        add('overflow', 'effects.overflow', [marker], overflow)
        if semantics == 'MUL':
            add('widening', 'effects.numeric_widening', ['INT_TO_DINT function', 'DINT_TO_REAL function'], {
                'before_operation': [{'source_type': 'INT', 'conversion': 'INT_TO_DINT', 'target_type': 'DINT'},
                                     {'source_type': 'DINT', 'conversion': 'DINT_TO_REAL', 'target_type': 'REAL'}]})
    if semantics in {'DIV', 'MOD', 'DIV_TIME'}:
        add('division_zero', 'effects.error_response', ['The value to be input to (s2) is 0.'], {
            'operation_error': 'documented', 'error_codes': [4100], 'when': 's2=0',
            'result': 'undefined', **({'ENO': False} if enabled else {})})
        qualify('nonzero_divisor', 'must be other than 0', {'operand_names': ['s2'],
            'subject': 's2', 'relation': 'not_equal', 'excluded_value': 0})
        if semantics == 'DIV_TIME':
            add('time_quotient', 'effects.remainder', ['Remainder is rounded down.'], {
                'quotient_result': 'd', 'remainder': 'discarded', 'unit': 'ms'})
            add('time_division_example_disagreement', 'effects.time_example_source_conflict',
                ['T#1d2h33m44s55ms', 'T#13h16m63s27ms'], {
                'subject': 'division_example_quotient', 'input_milliseconds': 95624055, 'divisor': 2,
                'printed_quotient_TIME_fields': {'hours': 13, 'minutes': 16, 'seconds': 63, 'milliseconds': 27},
                'printed_quotient_milliseconds': 47823027,
                'arithmetic_quotient_milliseconds': 47812027, 'arithmetic_remainder_milliseconds': 1,
                'comparison_basis': 'exact_integer_millisecond_arithmetic', 'resolution': 'unknown'}, conflict=True)
    if semantics == 'EXPT':
        add('error_instruction_selection', 'constraints.error_rule_references',
            ['These functions consist of the following common instructions depending on the data type',
             'MELSEC-Q/L Structured Programming Manual (Common Instructions)'], {
            'source_manual': 'SH080783ENG', 'selection': [
                {'s1_type': 'REAL', 's2_type': 'INT', 'instructions': ['LOG', 'FLT']},
                {'s1_type': 'REAL', 's2_type': 'DINT', 'instructions': ['LOG', 'DFLT']},
                {'s1_type': 'REAL', 's2_type': 'REAL', 'instructions': ['LOG']},
                {'s1_type': 'REAL', 's2_type': 'LREAL', 'instructions': ['LOGD', 'DFLTD']},
                {'s1_type': 'LREAL', 's2_type': 'INT', 'instructions': ['LOGD']},
                {'s1_type': 'LREAL', 's2_type': 'DINT', 'instructions': ['LOGD', 'FLTD']},
                {'s1_type': 'LREAL', 's2_type': 'REAL', 'instructions': ['LOGD', 'DFLTD']},
                {'s1_type': 'LREAL', 's2_type': 'LREAL', 'instructions': ['LOGD']}],
            'rule': 'error_rules_of_selected_common_instructions_apply'})
    if semantics in {'MAXIMUM', 'MINIMUM', 'LIMITATION', 'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'}:
        add('string_comparison', 'effects.string_comparison',
            ['Conditions for comparing the STRING data type values are as follows:'], {
            'equal': 'all_characters_equal', 'different_characters': 'compare_character_codes',
            'different_lengths': 'longer_is_greater', 'shorter_is_smaller': True})
        if semantics in {'MAXIMUM', 'MINIMUM', 'LIMITATION'}:
            add('unsigned_compile_warning', 'effects.compile_diagnostic', ['warning C9026 occurs'], {
                'when_output_type_in': ['WORD', 'DWORD'], 'code': 'C9026', 'severity': 'warning'})
    if semantics == 'LIMITATION':
        add('within_limits', 'effects.selection', ['outputs the input value of (s2) from (d).'], {
            'cases': [{'when': 's2>s3', 'result': 's3'}, {'when': 's2<s1', 'result': 's1'},
                      {'when': 's1<=s2<=s3', 'result': 's2'}]})
        qualify('ordered_limits', 'the input value of (s1) < the input value of (s3)',
                {'operand_names': ['s1', 's3'], 'relation': 's1<s3'})
    if semantics == 'MUX':
        qualify('mux_actual_pin_range', 'within the range of the number of pins', {
            'operand_names': ['n'], 'subject': 'selector_n', 'minimum': 1, 'maximum': 28,
            'additional_maximum': 'actual_number_of_s_input_pins', 'out_of_range_error': False,
            'out_of_range_output': 'undefined', **({'out_of_range_ENO': False} if enabled else {})})
    if semantics in {'MID', 'CONCAT', 'INSERT', 'DELETE', 'REPLACE'}:
        qualify('string_capacity', 'string type data within the range from 0 to 255 bytes.', {
            'operand_names': ['s'] if semantics in {'MID', 'DELETE'} else ['s1..s28'] if semantics == 'CONCAT' else ['s1', 's2'],
            'subject': 'input_string_length', 'minimum': 0, 'maximum': 255, 'unit': 'byte'})
        if semantics == 'INSERT':
            add('insertion_position', 'effects.string_position', ["'ABC123456DE'"], {
                'source': 's1', 'insertion': 's2', 'insert_before_character': 'n', 'index_base': 1,
                'example': {'s1': 'ABCDE', 's2': '123456', 'n': 4, 'd': 'ABC123456DE'},
                'embedded_insertion_terminator': 'ignored', 'maximum_result_bytes': 255,
                'overflow': 'keep_first_255_bytes'})
        elif semantics == 'CONCAT':
            add('concatenation_terminator', 'effects.string_termination', ["ignoring '00H'"], {
                'intermediate_terminators': 'ignored', 'maximum_result_bytes': 255,
                'overflow': 'keep_first_255_bytes', 'input_order': 's1_through_last_pin'})
        elif semantics == 'REPLACE':
            add('replacement_position', 'effects.string_position', ["'ABCD1234523'"], {
                'source': 's1', 'replacement': 's2', 'length': 'n1', 'start': 'n2', 'index_base': 1,
                'example': {'s1': 'ABCDEFGH123', 's2': '123456', 'n1': 5, 'n2': 5, 'd': 'ABCD1234523'},
                'replacement_segment': 'first_n1_characters_of_s2'})
        if semantics != 'CONCAT':
            qualify('string_position_length', 'must not exceed the number of characters', {
                'operand_names': ['n'] if semantics == 'INSERT' else ['n1', 'n2'],
                'subject': 'position_and_length', 'unit': 'character', 'bounds': [
                    {'name': 'n', 'minimum': 1, 'maximum': 255, 'maximum_length_of': 's1'}]
                    if semantics == 'INSERT' else [
                    {'name': 'n1', 'minimum': 0, 'maximum': 255, 'maximum_length_of': 's' if semantics in {'MID', 'DELETE'} else 's1'},
                    {'name': 'n2', 'minimum': 1, 'maximum': 255, 'maximum_length_of': 's' if semantics in {'MID', 'DELETE'} else 's1'}]})
        if semantics == 'MID':
            add('mid_errors', 'effects.error_response', ['MID(_E): MIDR', 'error flag (SM0) is turned ON'], {
                'operation_error': 'documented', 'error_flags': ['SM0'], 'error_code_registers': ['SD0'],
                'error_codes': [4101], 'any_of': ['n2>length(s)', 'destination_n1_characters_exceed_device_range',
                                              'n2=0', 'source_has_no_00H_before_device_end'],
                'cpu_groups': ['QnH', 'QnPH', 'QnPRH', 'QnU', 'LCPU']},
                model_filter=lambda model: model not in _BASIC_Q)
    if semantics.startswith(('TP', 'TON', 'TOF')) or semantics.startswith('TIMER_'):
        add('timeout_scan', 'execution.same_scan_repeat', ['After the time is up, input to (s) is ignored within one scan.'], {
            'after_timeout': 'ignore_input_within_that_scan', 'next_input_change': 'next_scan_or_later'})
    if semantics in {'TP', 'TP_HIGH', 'TON', 'TON_HIGH', 'TOF', 'TOF_HIGH'}:
        high = semantics.endswith('_HIGH')
        qualify('timer_preset', 'Valid setting range for n is', {'operand_names': ['n'], 'subject': 'preset_time',
            'minimum': 0, 'maximum': 327670 if high else 3276700, 'unit': 'ms'})
        qualify('timer_unit', 'The unit is set in Timer limit setting', {'operand_names': ['n'],
            'subject': 'PLC_parameter_timer_unit', 'unit': 'ms', 'maximum': 100 if high else 1000,
            **({'minimum_by_CPU_group': {'basic_high_performance_process_redundant_Q': 0.1,
                                        'universal_Q_and_L': 0.01}} if high else {'minimum': 1}),
            'setting_location': 'PLC_parameter_PLC_system_Timer_limit_setting'})
    if semantics.startswith('TIMER_'):
        if semantics in {'TIMER_10_FB_M', 'TIMER_100_FB_M'}:
            add('fixed_timer_warning', 'effects.compile_diagnostic', ['warning C9047 occurs in compilation.'], {
                'code': 'C9047', 'severity': 'warning', 'when': 'matching_PLC_timer_unit_differs_from_default',
                'fixed_unit_ms': 10 if semantics == 'TIMER_10_FB_M' else 100})
        add('timer_restart', 'execution.operation_condition', ['the initial value is 0.'], {
            'initial_value': 's3', 'negative_initial_value': 0,
            'when_s1_off': 'retain_current_and_status' if semantics in {'TIMER_CONT_FB_M', 'TIMER_CONTHFB_M'}
                            else 'current_equals_effective_initial_value_and_status_false',
            'on_again': 'resume_retained_time' if semantics in {'TIMER_CONT_FB_M', 'TIMER_CONTHFB_M'}
                        else 'start_from_effective_initial_value',
            **({'reset': 'RST_instance_Coil'} if semantics in {'TIMER_CONT_FB_M', 'TIMER_CONTHFB_M'} else {})})
        if semantics == 'TIMER_CONTHFB_M':
            add('retentive_unit_disagreement', 'effects.timer_unit_source_conflict',
                ['Starts measuring from the value input to (s3)',
                 'The unit of measurement (time period) for retentive timer is same as the low-speed timer',
                 '0.01ms to 100ms'], {
                'subject': 'high_speed_retentive_timer_measurement_unit',
                'shared_retentive_paragraph': {'minimum': 1, 'maximum': 1000, 'unit': 'ms'},
                'explicit_high_speed_reference': {'maximum': 100, 'unit': 'ms',
                    'minimum_by_CPU_group': {'basic_high_performance_process_redundant_Q': 0.1,
                                             'universal_Q_and_L': 0.01}},
                'resolution': 'unknown'}, conflict=True)
        if semantics == 'TIMER_LOW_FB_M':
            add('low_timer_example_disagreement', 'effects.timer_example_source_conflict',
                ['TIMER_LOW_FB_M_Inst', '900ms', 'The program which starts measuring from (s3)'], {
                'subject': 'example_initial_and_preset_time_scales', 'initial_count': 1, 'preset_count': 10,
                'caption_initial_scale_ms': 10, 'caption_preset_scale_ms': 100,
                'diagram_duration_ms': 900, 'caption_arithmetic_duration_ms': 990,
                'resolution': 'unknown'}, conflict=True)
    if semantics == 'CTUD':
        add('counter_edges', 'execution.edge', ['is turned from OFF to ON.'], {
            's1': {'edge': 'rising', 'action': 'd3_plus_1'},
            's2': {'edge': 'rising', 'action': 'd3_minus_1'},
            's3': {'edge': 'rising', 'action': 'd3_equals_0'},
            's4': {'edge': 'rising', 'action': 'd3_equals_n'}})
    if semantics == 'CTD':
        add('count_below_zero', 'effects.counter_boundary', ['Counts down at the falling of',
            'Counts down when EN = ON and the falling of'], {
            'source': 'rendered_count_diagram', 'count_continues_below_zero': True,
            'diagram_negative_counts': [-1, -2, -3], 'd1_while_negative': True,
            'count_edge': 'source_conflict_see_execution_edge_facts'})
    if semantics == 'COUNTER_FB_M':
        add('counter_hold', 'execution.operation_condition', ['It is not counted when (s1) stays ON.',
            'When resetting the current value of the counter, reset (s1).'], {
            'count_event': 's1_rising', 's1_stays_on': 'no_additional_count',
            'initial_value': 's3', 'negative_initial_value': 0,
            'reset_command': 'RST_instance_Coil', 'initial_assignment_is_not_a_reset_command': True})
    if semantics in {'CTU', 'CTD', 'CTUD', 'COUNTER_FB_M'} or semantics.startswith('TIMER_'):
        if semantics in {'CTU', 'CTD', 'CTUD'}:
            qualify('counter_preset', 'Valid setting range for n is -32768 to 32767.', {
                'operand_names': ['n'], 'subject': 'n', 'minimum': -32768, 'maximum': 32767,
                'nonpositive_effect': 'd1_and_d2_on' if semantics == 'CTUD' else 'd1_on'})
        else:
            qualify('initial_and_preset', 'Valid setting range for (s2) is 0 to 32767.', {
                'operand_names': ['s2', 's3'], 'subject': 'initial_and_preset',
                'bounds': [{'name': 's2', 'minimum': 0, 'maximum': 32767},
                           {'name': 's3', 'minimum': -32768, 'maximum': 32767, 'negative_effective_value': 0}]})
    # A no-extra-case operator still gains an explicit link to the reviewed
    # processing section. It is not changed into a function call.
    if reference:
        add('processing_reference', 'effects.processing_reference',
            ['For details of the operation processing, refer to the following.'], {
            'operation': semantics.lower(), 'referenced_manual': reference.manual_number,
            'referenced_revision': reference.revision, 'referenced_definition_page': min(reference.pages),
            'representation': 'FBD_named_operator' if languages == ('fbd',) else 'ST_expression'})
    return specs


def _reviewed_n_specs(section, symbol):
    """N-revision operations, typed pins and calls, authored from their own pages.

    Ordinary functions, EN functions, FB instances, and symbolic ST operators
    have separate call contracts. No Core execution capability is inferred.
    """
    if section.manual != 'sh080784engn':
        return []
    base = symbol[:-2] if symbol.endswith('_E') else symbol
    enabled = symbol.endswith('_E')
    family = family_of(section)
    text = section.text
    value, needles, inputs, outputs = None, [], [], []
    output_type, function_block = 'ANY_NUM', False
    operation_map = {'ADD': ('addition', 'add'), 'MUL': ('multiplication', 'multiply'),
                     'SUB': ('subtraction', 'subtract'), 'DIV': ('division', 'divide'),
                     'MOD': ('division', 'remainder'), 'EXPT': ('exponentiation', 'power')}
    conversion = N_TYPE_PAIR.fullmatch(symbol)
    if base in {'GET_BOOL_ADDR', 'GET_INT_ADDR', 'GET_WORD_ADDR'} and family == 'n_conversion':
        needles = ['Outputs data type of (s) as data type of (d)']
        output_type = {'GET_BOOL_ADDR': 'BOOL', 'GET_INT_ADDR': 'INT', 'GET_WORD_ADDR': 'WORD'}[base]
        inputs = [_n_operand('s', 'ANY')]
        value = {'operation': 'reinterpret_storage_type', 'input': 's', 'result': 'd',
                 'target_type': output_type, 'numeric_conversion': False}
    elif base in {'TIMER_10_FB_M', 'TIMER_100_FB_M', 'TIMER_HIGH_FB_M', 'TIMER_LOW_FB_M',
                  'TIMER_CONT_FB_M', 'TIMER_CONTHFB_M'} and family == 'n_fb_timer':
        needles = ['Starts measuring the current value when the executing condition of (s1) turns ON']
        function_block = True
        inputs = [_n_operand('s1', 'BOOL', role='coil'), _n_operand('s2', 'INT', role='preset'),
                  _n_operand('s3', 'INT', role='initial_value')]
        outputs = [_n_operand('d1', 'ANY16', 'output', 'current_value'), _n_operand('d2', 'BOOL', 'output', 'preset_reached')]
        retentive = base in {'TIMER_CONT_FB_M', 'TIMER_CONTHFB_M'}
        high = base in {'TIMER_10_FB_M', 'TIMER_HIGH_FB_M', 'TIMER_CONTHFB_M'}
        unit = ({'fixed': 10 if high else 100} if base in {'TIMER_10_FB_M', 'TIMER_100_FB_M'} else
                {'default': 10 if high else 100, 'maximum': 100 if high else 1000,
                 'minimum': 0.1 if high else 1})
        if high and 'fixed' not in unit:
            unit.pop('minimum', None)
            unit['minimum_by_CPU_group'] = {'basic_high_performance_process_redundant_Q': 0.1,
                                           'universal_Q_and_L': 0.01}
        value = {'operation': 'timer_counter_function_block', 'coil': 's1', 'preset': 's2',
                 'initial_value': 's3', 'negative_initial_value': 0, 'current_value': 'd1',
                 'preset_reached': 'd2', 'time_unit_ms': unit, 'retentive': retentive,
                 'when_coil_off': 'retain_value_and_status' if retentive else 'restore_initial_value_and_clear_status'}
        if retentive:
            value['reset'] = 'RST_s1_coil'
    elif conversion and family == 'n_conversion':
        source_type, destination_type = conversion.group(1), conversion.group(2)
        call = ('Function with EN/ENO (%s)' if enabled else 'Function without EN/ENO (%s)') % symbol
        if not section.bind(call):
            call = _witness(text, (r'\b' + re.escape(symbol) + r'\s*\(',))
            if not call:
                # Grouped conversion tables share a call shape across the
                # explicitly declared scalar types, but do not repeat each
                # representative call drawing (for example DINT_TO_BCD_E).
                forms = [form for form in section.call_forms()
                         if form['opcode'] in section.symbols and N_TYPE_PAIR.fullmatch(form['opcode'])
                         and bool(form['enable']) == enabled]
                if not forms:
                    return []
                call = forms[0].get('witness') or forms[0]['documented_form']
        type_names = {'STR': 'STRING'}
        input_type = type_names.get(source_type, source_type)
        output_type = type_names.get(destination_type, destination_type)
        if source_type == 'BCD':
            input_type = 'DWORD' if destination_type == 'DINT' else 'WORD'
        if destination_type == 'BCD':
            output_type = 'DWORD' if source_type == 'DINT' else 'WORD'
        inputs = [_n_operand('s', input_type)]
        needles = [call]
        value = {'operation': 'convert_type', 'input': 's', 'result': 'd',
                 'from_type': source_type, 'to_type': destination_type,
                 'result_channel': 'output_parameter' if enabled else 'function_return_value'}
    elif base in operation_map and family in {'n_arithmetic', 'n_operator'}:
        word, operation = operation_map[base]
        witness = _witness(text, (r'[Pp]erforms ' + word + r'[^\n]{0,260}',))
        if not witness:
            return []
        needles = [witness]
        numeric_type = 'ANY_INT' if base == 'MOD' else 'ANY_NUM'
        count = 28 if base in {'ADD', 'MUL'} else 2
        inputs = [_n_operand('s' + str(i), numeric_type) for i in range(1, count + 1)]
        if count == 28:
            for pin in inputs[2:]:
                pin['optional'] = True
        if base == 'EXPT':
            inputs[0]['type'] = 'ANY_REAL'
            output_type = 'ANY_REAL'
        else:
            output_type = numeric_type
        value = {'operation': operation, 'inputs': ['s1..s28' if count == 28 else 's1',
                                                  's2'] if count != 28 else ['s1..s28'],
                 'result': 'd', 'result_type': 'same_as_input',
                 'evaluation': 'left_to_right' if count == 28 else 'binary'}
        if base in {'DIV', 'MOD'}:
            value['zero_divisor'] = {'operand': 's2', 'error_code': 4100}
        if base == 'DIV':
            value['result_part'] = 'quotient'
        if base == 'MOD':
            value['result_part'] = 'remainder'
        if count == 28:
            value['input_count'] = {'minimum': 2, 'maximum': 28}
    elif base == 'ABS' and family == 'n_numeric':
        needles = [_witness(text, (r'outputs the absolute value[^\n]{0,250}',))]
        value = {'operation': 'absolute_value', 'input': 's', 'result': 'd',
                 'result_type': 'same_as_input'}
        inputs, output_type = [_n_operand('s', 'ANY_NUM')], 'ANY_NUM'
    elif base == 'MOVE' and family == 'n_arithmetic':
        needles = ['Moves the data input for (s) from (d) in the same data type as that of (s)']
        value = {'operation': 'copy_value', 'input': 's', 'result': 'd', 'type_preserved': True}
        inputs, output_type = [_n_operand('s', 'ANY')], 'ANY'
    elif base in {'AND', 'OR', 'XOR', 'NOT'} and family == 'n_bitwise':
        word = {'AND': 'AND', 'OR': 'OR', 'XOR': 'exclusive OR', 'NOT': 'NOT'}[base]
        needles = ['Performs Boolean ' + word]
        count = 1 if base == 'NOT' else 28
        inputs = [_n_operand('s' + str(i), 'ANY_BIT') for i in range(1, count + 1)]
        for pin in inputs[2:]:
            pin['optional'] = True
        output_type = 'ANY_BIT'
        value = {'operation': 'bitwise_' + base.lower(), 'inputs': [p['name'] for p in inputs],
                 'result': 'd', 'result_type': 'same_as_input'}
        if count > 1:
            value['input_count'] = {'minimum': 2, 'maximum': 28}
        if base == 'XOR':
            value['evaluation'] = 'left_to_right'
    elif base in {'SEL', 'LIMITATION', 'MUX', 'MAXIMUM', 'MINIMUM'} and family == 'n_selection':
        if base == 'SEL':
            needles = ['When the input value of (s1) is FALSE', 'When the input value of (s1) is TRUE']
            inputs = [_n_operand('s1', 'BOOL', role='selector'), _n_operand('s2', 'ANY'), _n_operand('s3', 'ANY')]
            value = {'operation': 'select_value', 'selector': 's1', 'when_false': 's2', 'when_true': 's3', 'result': 'd'}
            output_type = 'ANY'
        elif base == 'LIMITATION':
            needles = ['When the input value of (s2) > the input value of (s3)',
                       'When the input value of (s2) < the input value of (s1)']
            inputs = [_n_operand('s1', 'ANY_SIMPLE', role='lower_limit'),
                      _n_operand('s2', 'ANY_SIMPLE'), _n_operand('s3', 'ANY_SIMPLE', role='upper_limit')]
            value = {'operation': 'clamp_value', 'input': 's2', 'lower_limit': 's1', 'upper_limit': 's3',
                     'result': 'd', 'premise': 's1<s3'}
            output_type = 'ANY_SIMPLE'
        else:
            count = [_n_operand('s' + str(i), 'ANY' if base == 'MUX' else 'ANY_SIMPLE') for i in range(1, 29)]
            for pin in count[2:]:
                pin['optional'] = True
            inputs = ([_n_operand('n', 'INT', role='selector')] if base == 'MUX' else []) + count
            output_type = 'ANY' if base == 'MUX' else 'ANY_SIMPLE'
            if base == 'MUX':
                needles = ['When the input value of n is 1', 'outside the range of number of pins']
                value = {'operation': 'select_indexed_value', 'index': 'n', 'index_base': 1, 'input_count': {'minimum': 2, 'maximum': 28},
                         'result': 'd', 'out_of_range': {'result': 'undefined', 'operation_error': False, 'eno': False}}
            else:
                needles = ['Selects the ' + ('maximum' if base == 'MAXIMUM' else 'minimum') + ' value']
                value = {'operation': 'select_' + base.lower(), 'inputs': ['s1..s28'], 'result': 'd',
                         'input_count': {'minimum': 2, 'maximum': 28}}
    elif base in {'GT', 'GE', 'EQ', 'LE', 'LT', 'NE'} and family in {'n_comparison', 'n_operator'}:
        needles = ['Performs comparison' if family == 'n_comparison' else 'performs comparison operation']
        count = 2 if base == 'NE' else 28
        inputs = [_n_operand('s' + str(i), 'ANY_SIMPLE') for i in range(1, count + 1)]
        for pin in inputs[2:]:
            pin['optional'] = True
        output_type = 'BOOL'
        value = {'operation': 'compare_values', 'comparison': base.lower(), 'result': 'd',
                 'inputs': ['s1', 's2'] if count == 2 else ['s1..s28'],
                 'aggregation': 'pair' if count == 2 else 'all_adjacent_pairs',
                 'input_count': {'minimum': 2, 'maximum': count}}
    elif base in {'MID', 'CONCAT', 'INSERT', 'DELETE', 'REPLACE'} and family == 'n_string':
        definitions = {
            'MID': ('Extracts the specified number of characters', 'substring', ['s', 'n1', 'n2'],
                    {'length': 'n1', 'start': 'n2', 'index_base': 1}),
            'CONCAT': ('Concatenates the character string', 'concatenate_strings', ['s' + str(i) for i in range(1, 29)],
                       {'input_count': {'minimum': 2, 'maximum': 28}, 'maximum_result_bytes': 255, 'overflow': 'truncate_right'}),
            'INSERT': ('Inserts the character string', 'insert_string', ['s1', 's2', 'n'],
                       {'source': 's1', 'insertion': 's2', 'before_character': 'n', 'index_base': 1,
                        'maximum_result_bytes': 255, 'overflow': 'truncate_right'}),
            'DELETE': ('Deletes the specified number of characters', 'delete_substring', ['s', 'n1', 'n2'],
                       {'length': 'n1', 'start': 'n2', 'index_base': 1}),
            'REPLACE': ('Replaces the specified number of characters', 'replace_substring', ['s1', 's2', 'n1', 'n2'],
                        {'source': 's1', 'replacement': 's2', 'length': 'n1', 'start': 'n2', 'index_base': 1}),
        }
        witness, operation, names, detail = definitions[base]
        needles = [witness]
        inputs = [_n_operand(name, 'INT' if name.startswith('n') else 'STRING',
                             role='position_or_length' if name.startswith('n') else 'source') for name in names]
        if base == 'CONCAT':
            for pin in inputs[2:]:
                pin['optional'] = True
        output_type = 'STRING'
        value = {'operation': operation, 'result': 'd', **detail}
    elif base in {'ADD_TIME', 'SUB_TIME', 'MUL_TIME', 'DIV_TIME'} and family == 'n_time':
        word = {'ADD_TIME': 'addition', 'SUB_TIME': 'subtraction', 'MUL_TIME': 'multiplication', 'DIV_TIME': 'division'}[base]
        needles = ['Performs ' + word]
        inputs = [_n_operand('s1', 'TIME'), _n_operand('s2', 'TIME' if base in {'ADD_TIME', 'SUB_TIME'} else 'ANY_NUM')]
        output_type = 'TIME'
        value = {'operation': base.lower(), 'inputs': ['s1', 's2'], 'result': 'd'}
        if base == 'DIV_TIME':
            value.update(remainder='discarded', zero_divisor={'operand': 's2', 'error_code': 4100})
    elif base in {'SR', 'RS'} and family == 'n_fb_bistable':
        needles = ['Sets (d) when (s1) is turned ON']
        function_block, output_type = True, 'BOOL'
        inputs = [_n_operand('s1', 'BOOL', role='set'), _n_operand('s2', 'BOOL', role='reset')]
        value = {'operation': 'bistable_latch', 'set': 's1', 'reset': 's2', 'result': 'd',
                 'priority': 'set' if base == 'SR' else 'reset', 'otherwise': 'retain_output'}
    elif base in {'R_TRIG', 'F_TRIG'} and family == 'n_fb_edge':
        needles = ['Turns ON (d) for one scan when (s) is turned ' + ('ON' if base == 'R_TRIG' else 'OFF')]
        function_block, output_type = True, 'BOOL'
        inputs = [_n_operand('s', 'BOOL', role='clock')]
        value = {'operation': 'detect_edge', 'input': 's', 'result': 'd',
                 'edge': 'rising' if base == 'R_TRIG' else 'falling', 'pulse_scans': 1}
    elif base in {'CTU', 'CTD', 'CTUD', 'COUNTER_FB_M'} and family == 'n_fb_counter':
        function_block = True
        if base == 'COUNTER_FB_M':
            needles = ['Counts the detected rising edge', 'The current value is stored in']
            inputs = [_n_operand('s1', 'BOOL', role='count'), _n_operand('s2', 'INT', role='preset'), _n_operand('s3', 'INT', role='initial_value')]
            outputs = [_n_operand('d1', 'ANY16', 'output', 'current_count'), _n_operand('d2', 'BOOL', 'output', 'preset_reached')]
            value = {'operation': 'count_up', 'edge': 'rising', 'count_input': 's1', 'preset': 's2', 'initial_value': 's3',
                     'negative_initial_value': 0, 'count': 'd1', 'preset_reached': 'd2', 'reset': 'reset_s1_coil'}
        elif base == 'CTUD':
            needles = ['Counts up (+1) (D3)', 'Resets (D3) when (s3) turns ON', 'The value of n is set to (D3)']
            inputs = [_n_operand('s1', 'BOOL', role='count_up'), _n_operand('s2', 'BOOL', role='count_down'),
                      _n_operand('s3', 'BOOL', role='reset'), _n_operand('s4', 'BOOL', role='load'), _n_operand('n', 'INT', role='preset')]
            outputs = [_n_operand('d1', 'BOOL', 'output', 'upper_reached'), _n_operand('d2', 'BOOL', 'output', 'zero_reached'), _n_operand('d3', 'INT', 'output', 'current_count')]
            value = {'operation': 'count_up_down', 'up': 's1', 'down': 's2', 'count': 'd3', 'reset': 's3', 'load': 's4',
                     'preset': 'n', 'upper_reached': 'd1', 'zero_reached': 'd2', 'nonpositive_preset': 'd1_and_d2_on'}
        else:
            needles = ['Counts (d2) when (s1)' if base == 'CTU' else 'Counts down (-1) (d2) when (s1)']
            inputs = [_n_operand('s1', 'BOOL', role='count'), _n_operand('s2', 'BOOL', role='reset' if base == 'CTU' else 'load'), _n_operand('n', 'INT', role='preset')]
            outputs = [_n_operand('d1', 'BOOL', 'output', 'preset_or_zero_reached'), _n_operand('d2', 'INT', 'output', 'current_count')]
            value = {'operation': 'count_up' if base == 'CTU' else 'count_down', 'count_input': 's1', 'count': 'd2',
                     'preset': 'n', 'reset_or_load': 's2', 'reset_value': 0 if base == 'CTU' else 'n',
                     'result': 'd1', 'output_on': 'count_reaches_n' if base == 'CTU' else 'count_reaches_zero',
                     'nonpositive_preset': 'd1_on'}
    elif re.fullmatch(r'(?:TP|TON|TOF)(?:_HIGH)?', base) and family == 'n_fb_timer':
        function_block = True
        mode = base.split('_')[0]
        needles = [{'TP': 'Turns ON (d1) for the duration set to n', 'TON': 'Turns ON (d1) when (s) is turned ON after',
                    'TOF': 'Turns OFF (d1) when (s) is turned from ON to OFF after'}[mode]]
        inputs = [_n_operand('s', 'BOOL', role='timer_input'), _n_operand('n', 'TIME', role='preset_time')]
        outputs = [_n_operand('d1', 'BOOL', 'output', 'timer_output'), _n_operand('d2', 'TIME', 'output', 'elapsed_time')]
        value = {'operation': {'TP': 'pulse_timer', 'TON': 'on_delay_timer', 'TOF': 'off_delay_timer'}[mode],
                 'input': 's', 'preset_time': 'n', 'output': 'd1', 'elapsed_time': 'd2',
                 'timer_class': 'high_speed' if '_HIGH' in base else 'low_speed',
                 'maximum_preset_ms': 327670 if '_HIGH' in base else 3276700}
        if mode == 'TP':
            value.update(output_on='input_rising_starts_fixed_duration',
                         input_off_during_pulse='does_not_cancel',
                         elapsed_reset='after_output_off_and_input_off', elapsed_retained_after_timeout=True)
        elif mode == 'TON':
            value.update(output_on='input_on_and_preset_elapsed', reset='input_off_resets_output_and_elapsed')
        else:
            value.update(output_on='input_on', output_off='preset_elapsed_after_input_falling',
                         reset='input_on_resets_elapsed')
    if value is None or not needles or any(not needle or not section.bind(needle) for needle in needles):
        return []
    languages = ('fbd',) if family == 'n_operator' else ('st', 'fbd')
    specs = [_reviewed_spec('authored_operation', 'effects.operation', needles, value, languages)]
    if family == 'n_fb_timer' and 'when_coil_off' in value:
        specs.append(_reviewed_spec('timer_coil', 'execution.operation_condition', needles,
                                   {'enabled_when': 's1_true', 'coil': 's1',
                                    'when_disabled': value['when_coil_off'],
                                    'initial_value': 's3'}, languages))
    if not outputs:
        outputs = [_n_operand('d', output_type, 'output')]
    pins = ([_n_operand('EN', 'BOOL', role='enable')] if enabled else []) + inputs + outputs
    if enabled:
        pins += [_n_operand('ENO', 'BOOL', 'output', 'execution_status')]
    specs.append(_reviewed_spec('typed_operands', 'parameters.typed_operands', needles,
                               {'operands': pins}, languages))
    specs.append(_reviewed_spec('fbd_signature', 'parameters.fbd_signature', needles,
                               {'name': symbol, 'pins': pins, 'instance_required': function_block,
                                'available': True}, ('fbd',)))
    if family == 'n_operator' or base in {'MOD', 'NOT'} and not enabled:
        specs.append(_reviewed_spec('st_unavailable', 'parameters.st_signature', needles,
                                   {'available': False, 'reason': 'symbolic_ST_operator_required'}, ('st',)))
    else:
        # The signature is grounded in the section's declaration/call table;
        # function blocks require an instance and expose their output pins.
        call_pins = pins if function_block else (pins[:-1] if enabled else inputs)
        specs.append(_reviewed_spec('st', 'parameters.st_signature', needles,
                                   {'name': symbol, 'parameters': call_pins,
                                    'returns': 'VOID' if function_block else 'BOOL' if enabled else output_type,
                                    'enable': enabled, 'available': True,
                                    'call_kind': 'function_block_instance' if function_block else 'function',
                                    'instance_required': function_block}, ('st',)))
    if enabled and 'When EN = OFF, retains the previous scan output result' in text:
        specs.append(_reviewed_spec('enable_retention', 'execution.disabled',
                                   ['When EN = OFF, retains the previous scan output result'],
                                   {'when_disabled': 'retain_previous_scan_output', 'output': 'd'}, languages))
    elif enabled and 'FALSE (Operation stop) FALSE' in text:
        specs.append(_reviewed_spec('enable_gate', 'execution.operation_condition',
                                   ['FALSE (Operation stop) FALSE'],
                                   {'enabled_when': 'EN_true', 'when_disabled': 'operation_stopped',
                                    'disabled_eno': False}, languages))
    if base in {'R_TRIG', 'F_TRIG'}:
        specs.append(_reviewed_spec('edge_execution', 'execution.edge', needles,
                                   {'edge': value['edge'], 'input': 's', 'pulse_scans': 1}, languages))
    if base in {'CTU', 'CTD', 'CTUD'}:
        rising = _witness(text, (r'counts the number of times[^\n]{0,110}OFF to ON',))
        if rising:
            claim = _reviewed_spec('count_edge', 'execution.edge', [rising],
                                   {'edge': 'rising', 'input': 's1'}, languages)
            claim['shared_claim'] = True
            specs.append(claim)
        if base == 'CTD':
            falling = _witness(text, (r'Counts down at the falling of', r'Counts down when EN = ON and the falling of'))
            if falling:
                # Same identity and scope: the shared merger preserves this real
                # source disagreement instead of choosing an edge arbitrarily.
                claim = _reviewed_spec('count_edge', 'execution.edge', [falling],
                                       {'edge': 'falling', 'input': 's1'}, languages)
                claim['shared_claim'] = True
                specs.append(claim)
    return specs


_I_CONTROL_PAGES = {
    'S.PID': (93, 100), 'S.2PID': (101, 108), 'S.PIDP': (109, 117),
    'S.SPI': (118, 124), 'S.IPD': (125, 132),
}


def _i_control_memory_specs(section, symbol, add, gap):
    """The literal control-field layout, separate from scalar call operands."""
    base = symbol.replace('_', '.')
    # name, argument block, first word, word count, format, unit, standard
    # value, setter code. These rows were read in the actual specified-position
    # cells; no address is obtained by filling an empty neighbouring cell.
    fields = [
        ('E1', 'S1', 0, 2, 'REAL', 'none' if base == 'S.PID' else 'percent', 'not_given', 'U'),
        ('BW', 'D1', 0, 2, 'REAL', 'percent' if base in {'S.2PID', 'S.SPI', 'S.IPD'} else 'none', 'not_given', 'S'),
        ('BB', 'D1', 2, 1, 'BIN16', 'none', 'not_given', 'S'),
        ('DVLS', 'S2', 0 if base == 'S.SPI' else 2, 2, 'REAL', 'percent', 2.0, 'U'),
        ('PN', 'S2', 2 if base == 'S.SPI' else 4, 1, 'BIN16', 'none', 0, 'U'),
        ('TRK', 'S2', 3 if base == 'S.SPI' else 5, 1, 'BIN16', 'none', 0, 'U'),
        ('SVPTN', 'S2', 4 if base == 'S.SPI' else 6, 1, 'BIN16', 'none', 3, 'U'),
        ('MODE', 'D2', 1, 1, 'BIN16', 'none', 8, 'S/U'),
        ('ALM', 'D2', 3, 1, 'BIN16', 'none', 16384, 'S/U'),
        ('INH', 'D2', 4, 1, 'BIN16', 'none', 0, 'S/U'),
        ('SV', 'D2', 14, 2, 'REAL', 'none', 0.0, 'U'),
        ('DV', 'D2', 16, 2, 'REAL', 'percent', 0.0, 'S'),
        ('RH', 'D2', 22, 2, 'REAL', 'none', 100.0, 'U'),
        ('RL', 'D2', 24, 2, 'REAL', 'none', 0.0, 'U'),
        ('ST' if base == 'S.SPI' else 'CT', 'D2', 46, 2, 'REAL', 's', 0.0 if base == 'S.SPI' else 1.0, 'U'),
        ('DVL', 'D2', 50, 2, 'REAL', 'percent', 100.0, 'U'),
        ('P', 'D2', 52, 2, 'REAL', 'none', 1.0, 'U'),
        ('I', 'D2', 54, 2, 'REAL', 's', 10.0, 'U'),
        ('STHT' if base == 'S.SPI' else 'D', 'D2', 56, 2, 'REAL', 's', 0.0, 'U'),
        ('GW', 'D2', 58, 2, 'REAL', 'percent', 0.0, 'U'),
        ('GG', 'D2', 60, 2, 'REAL', 'none', 1.0, 'U'),
        ('E2', 'S3', 0, 2, 'REAL', 'percent', 0.0, 'U'),
    ]
    if base != 'S.SPI':
        fields.append(('MTD', 'S2', 0, 2, 'REAL', 'none', 8.0, 'U'))
    if base == 'S.PIDP':
        fields += [('NMAX', 'S2', 7, 2, 'REAL', 'none', 100.0, 'U'),
                   ('NMIN', 'S2', 9, 2, 'REAL', 'none', 0.0, 'U'),
                   ('MV', 'D2', 12, 2, 'REAL', 'percent', 0.0, 'S/U'),
                   ('MH', 'D2', 18, 2, 'REAL', 'percent', 100.0, 'U'),
                   ('ML', 'D2', 20, 2, 'REAL', 'percent', 0.0, 'U'),
                   ('DML', 'D2', 48, 2, 'REAL', 'percent', 100.0, 'U')]
    else:
        fields.append(('MVP', 'D2', 62, 2, 'REAL', 'percent', 0.0, 'S'))
    if base == 'S.2PID':
        fields += [('alpha', 'D2', 64, 2, 'REAL', 'none', 0.0, 'U'),
                   ('beta', 'D2', 66, 2, 'REAL', 'none', 1.0, 'U')]
    domains = {'E1': (-999999, 999999), 'BW': (-999999, 999999),
               'DVLS': (0, 100), 'PN': (0, 1), 'TRK': (0, 1), 'SVPTN': (0, 3),
               'MODE': (0, 65535), 'ALM': (0, 65535), 'INH': (0, 65535),
               'DV': (-110, 110), 'RH': (-999999, 999999), 'RL': (-999999, 999999),
               'CT': (0, 999999), 'ST': (0, 999999), 'STHT': (0, 999999),
               'DVL': (0, 100), 'P': (0, 999999), 'I': (0, 999999), 'D': (0, 999999),
               'GW': (0, 100), 'GG': (0, 999999), 'MTD': (0, 999999), 'MVP': (-999999, 999999),
               'E2': (-10, 110), 'NMAX': (-999999, 999999), 'NMIN': (-999999, 999999),
               'MV': (-10, 110), 'MH': (-10, 110), 'ML': (-10, 110), 'DML': (0, 100),
               'alpha': (0, 1), 'beta': (0, 1)}
    values = []
    for name, block, offset, count, data_format, unit, standard, setter in fields:
        value = {'name': name, 'argument_block': block, 'first_word_offset': offset,
                 'word_count': count, 'format': data_format, 'unit': unit,
                 'standard_value': standard, 'setter_code': setter}
        if name in domains:
            minimum, maximum = domains[name]
            value['recommended_domain'] = {'minimum': minimum, 'maximum': maximum}
        elif name == 'SV':
            value['recommended_domain'] = {'minimum_field': 'RL', 'maximum_field': 'RH'}
        if name in {'BW', 'DV', 'MVP'}:
            value.update(user_setting='prohibited_system_written', parenthesized_domain=True)
        values.append(value)
    add('memory_fields', 'memory.control_fields', ['Specified position', 'Recommended range',
        'The loop tag memory and loop tag past value memory occupy a total of 128 words.'],
        {'fields': values, 'range_semantics': 'source_recommendation_not_scalar_operand_constraint',
         'loop_tag_total_words': 128,
         'PN_values': {'0': 'reverse', '1': 'forward'}, 'TRK_values': {'0': 'disabled', '1': 'enabled'},
         'SVPTN': {'bit0': {'0': 'E2_used', '1': 'E2_unused'},
                   'bit1': {'0': 'E2_is_upper_loop_MV', '1': 'E2_not_upper_loop_MV'}},
         'MODE_bits': {'LCM': 0, 'LCA': 1, 'LCC': 2, 'MAN': 3, 'AUT': 4, 'CAS': 5,
                       'CMB': 6, 'CAB': 7, 'CCB': 8, 'CMV': 9, 'CSV': 10},
         'ALM_bits': {'SPA': 14, 'DVLA': 2, 'MHA': 1, 'MLA': 0,
                      **({'DMLA': 11} if base in {'S.2PID', 'S.PIDP'} else {})},
         'INH_bits': {'ERRI': 15, 'TRKF': 13, 'DVLI': 2, 'MHI': 1, 'MLI': 0,
                      **({'DMLI': 11} if base in {'S.2PID', 'S.PIDP'} else {})},
         'BB_bits': {**{'BB1': 0}, **({name: index for index, name in enumerate(
                        ['BB1', 'BB2', 'BB3', 'BB4', 'BB5'])} if base == 'S.PIDP' else {})}})
    past = [('initial_preset_flag', 96, 1), ('sample_counter' if base == 'S.SPI' else 'control_cycle_counter', 97, 1)]
    if base == 'S.SPI':
        past += [('operation_counter', 98, 1), ('hold_counter', 99, 1), ('DVn_previous', 100, 2)]
    else:
        past += [('Bn_previous', 102, 2), ('PVn', 104, 2), ('PVn_previous', 106, 2)]
        if base != 'S.PIDP':
            past.append(('PVn_before_previous', 108, 2))
        if base in {'S.PID', 'S.2PID'}:
            past.append(('DVn_previous', 110, 2))
        if base == 'S.2PID':
            past += [('DVn_before_previous', 112, 2), ('Dn_previous', 114, 2)]
        if base == 'S.PIDP':
            past.append(('In_previous', 100, 2))
    past.append(('ALM2', 116, 1))
    add('past_memory', 'memory.past_values', ['Control cycle counter initial preset flag',
        'Alarm detection 2 (ALM2)', 'the data must be cleared with the sequence program.'], {
        'argument_block': 'D2', 'reserved_interval': {'first_word_offset': 96, 'last_word_offset': 116},
        'fields': [{'name': name, 'first_word_offset': offset, 'word_count': count}
                   for name, offset, count in past],
        'ALM2_bits': {'MHA2': 1, 'MLA2': 2},
        'initial_restart': 'clear_past_memory_with_sequence_program',
        'field_types': 'not_stated_in_past_value_table'})
    add('cycle_register', 'resources.execution_cycle', ['Set the execution cycle in SD1500 and SD1501 as real numbers.'],
        {'registers': ['SD1500', 'SD1501'], 'format': 'REAL', 'symbol': 'delta_T', 'unit': 's'})
    periods = ['ST', 'STHT'] if base == 'S.SPI' else ['CT']
    add('counter_capacity', 'constraints.source_qualifications', ['32767'], {
        'subjects': periods, 'qualifications': [
            {'subject': name, 'unit': 's', 'relative_to': 'delta_T',
             'ratio_maximum': 32767, 'expression': name + '/delta_T<=32767',
             'parameter_binding': 'unknown', 'subject_kind': 'control_block_field',
             'source_disposition': 'source_present_binding_unresolved'} for name in periods]})
    return []  # add() appends the individually bound specifications.


def _i_control_specs(section, symbol):
    """Authored cases read from all pages of the five PID-family sections.

    These are descriptions, not executable semantics. In particular a revision
    selects an algorithm; it does not change the instruction's availability.
    The private receipt retains the actual cells, including three incomplete
    source labels. A missing premise never selects the newer algorithm.
    """
    base = symbol.replace('_', '.')
    if base not in _I_CONTROL_PAGES:
        return []
    languages = _section_languages(section, symbol)
    tables = getattr(section, '_i_behavior_tables', ())
    cells = [cell['text'] for table in tables for cell in table.get('cells', ()) if cell.get('text')]
    report = section.__dict__.setdefault('_i_behavior_review', {
        'manual': section.manual, 'section': section.title, 'pages': list(_I_CONTROL_PAGES[base]),
        'source_clauses': {}, 'source_present_not_extracted': [], 'source_not_stated': [],
        'native_pending': 'source_review_only_no_native_execution',
    })
    specs = []

    def add(key, dimension, witnesses, value, *, model_filter=None, conditions=()):
        witnesses = list(witnesses)
        missing = [witness for witness in witnesses if not section.bind(witness)]
        if missing:
            row = {'rule': key, 'source_clauses': missing, 'reason': 'authored_clause_not_bound'}
            if row not in report['source_present_not_extracted']:
                report['source_present_not_extracted'].append(row)
            return None
        report['source_clauses'][key] = witnesses
        item = _reviewed_spec('i_control_' + key, dimension, witnesses, value, languages)
        item['model_filter'], item['conditions'] = model_filter, list(conditions)
        specs.append(item)
        if '_' in symbol and getattr(section, '_has_structured_block', False):
            block = copy.deepcopy(item)
            block['key'] += '_structured_block'
            block['languages'] = ('ladder',)
            block['conditions'].append({'context': 'project_mode', 'equals': 'structured_ladder_block'})
            block['value']['required_context'] = [*block['value'].get('required_context', []),
                {'field': 'project_mode', 'equals': 'structured_ladder_block'}]
            specs.append(block)
        return item

    def cell_containing(*fragments):
        return next((text for text in cells if all(fragment in re.sub(r'\s+', ' ', text)
                                                    for fragment in fragments)), '')

    def gap(key, value, witnesses, model_filter=None):
        value = dict(value, source_disposition='source_not_stated', gap_kind='source_not_stated')
        item = add(key, 'effects.source_qualification', witnesses, value, model_filter=model_filter)
        row = {'rule': key, **value}
        if row not in report['source_not_stated']:
            report['source_not_stated'].append(row)
        return item

    operation = {'S.PID': 'velocity_PID_process_value_incomplete_derivative',
                 'S.2PID': 'two_degree_of_freedom_PID', 'S.PIDP': 'position_PID',
                 'S.SPI': 'sample_PI', 'S.IPD': 'I_PD'}[base]
    marker = {'S.PID': 'Performs PID operation when the specified control cycle is reached.',
              'S.2PID': 'Performs 2-degree-of-freedom PID operation when the specified control cycle is reached.',
              'S.PIDP': 'Performs position type PID operation when the specified control cycle is reached.',
              'S.SPI': 'Performs normal PI operation during operating time (ST).',
              'S.IPD': 'Performs I-PD control when the specified control cycle is reached.'}[base]
    phases = ['SV_setting', 'tracking', 'gain_Kp', operation, 'deviation_check']
    if base == 'S.PIDP':
        phases += ['mode_judgment', 'rate_and_upper_lower_limits_or_alarm_clear', 'output_conversion']
    add('operation', 'effects.operation', [marker],
        {'operation': operation, 'result': 'BW', 'ordered_phases': phases,
         'update_gate': 'operating_time_ST' if base == 'S.SPI' else 'control_cycle_CT_reached'})
    add('setpoint', 'effects.setpoint', ['engineering value conversion is performed',
        'The set value (SV) is converted reversely from the engineering value',
        'When not using E2 as the set value, make sure to specify a dummy device.'], {
        'forward_conversion': {'modes': ['CAS', 'CCB', 'CSV'], 'requires_E2_used': True,
                               'expression': 'SVn=E2*(RH-RL)/100+RL',
                               'when_E2_not_used': 'skip_conversion_then_tracking'},
        'other_modes': ['MAN', 'AUT', 'CMV', 'CMB', 'CAB', 'LCM', 'LCA', 'LCC'],
        'other_modes_action': 'tracking_without_forward_conversion',
        'inverse_conversion': 'SVn_prime=100*(SVn-RL)/(RH-RL)',
        'E2_unused_argument': 'dummy_device', 'documented_dummy_device': 'SD1506',
        'upper_loop_E2_reference': {'base': 'upper_loop_tag', 'word_offset': 12, 'field': 'MV'}})
    add('tracking', 'effects.tracking', ['The tracking bit (TRK) of the operation constant is 1.',
        'The set value (E2) is used.',
        'the tracking flag (TRKF) of the alarm detection inhibition (INH) in the upper loop turns to 1.'], {
        'all_of': [{'field': 'TRK', 'equals': 1}, {'field': 'E2_used', 'equals': True},
                   {'field': 'MODE', 'in': ['MAN', 'AUT', 'CMV', 'CMB', 'CAB', 'LCM', 'LCA', 'LCC']}],
        'assignment': 'E2=SVn_prime',
        'when_E2_is_upper_loop_MV': {'upper_loop_INH_TRKF': 1}})
    add('gain', 'effects.gain', ['The deviation (DV) is calculated under the following condition.',
        'The output gain (K) is calculated under the following condition.'], {
        'deviation': [{'PN': 1, 'expression': 'DV=E1-SVn_prime'},
                      {'PN': 0, 'expression': 'DV=SVn_prime-E1'}],
        'gap_gain': [{'when': 'abs(DV)<=GW', 'expression': 'K=GG'},
                     {'when': 'abs(DV)>GW', 'expression': 'K=1-(1-GG)*GW/abs(DV)'}],
        'effective_proportional_gain': 'Kp=K*P'})
    alarm_pin = 'BB2' if base == 'S.PIDP' else 'BB1'
    add('deviation_alarm', 'effects.alarm', ['A deviation check is made under the following condition',
        'alarm detection inhibition (INH)'], {
        'outputs': ['ALM.DVLA', alarm_pin],
        'cases': [{'when': 'abs(DV)>DVL', 'value': 1},
                  {'when': 'DVL-DVLS<abs(DV)<=DVL', 'value': 'retain_previous'},
                  {'when': 'abs(DV)<=DVL-DVLS', 'value': 0}],
        'inhibit_any_of': ['INH.DVLI=1', 'INH.ERRI=1'], 'inhibited_value': 0})
    stop_clears = ['ALM.DVLA', 'BB1']
    if base == 'S.PIDP':
        stop_clears = ['ALM.DVLA', 'ALM.MHA', 'ALM.MLA', 'ALM.DMLA',
                       'ALM2.MHA2', 'ALM2.MLA2', 'BB1', 'BB2', 'BB3', 'BB4', 'BB5']
    add('loop_stop', 'execution.operation_condition', ['A loop stop performs the following processing',
        'The operation mode (MODE) is changed to MAN.'], {
        'selector': 'ALM.SPA', 'stop_value': 1, 'run_value': 0,
        'when_stopped': {'BW': 'retain_previous' if base == 'S.PIDP' else 0,
                         'clear': stop_clears, 'MODE': 'MAN', 'return': 'terminate_instruction'},
        'when_running': 'operating_or_hold_time_judgment' if base == 'S.SPI' else 'control_cycle_judgment'})
    if base != 'S.SPI':
        not_due = ({'temporary_assignment': 'T=MV', 'continue_at': 'mode_judgment'}
                   if base == 'S.PIDP' else {'BW': 0, 'return': 'terminate_instruction'})
        add('cycle', 'execution.sampling', ['When the specified control cycle is reached,',
            'control cycle is not reached'], {
            'period': 'CT', 'when_due': 'SV_setting_then_processing_chain',
            'when_not_due': not_due, 'counter_rounding': 'nearest_whole_number'})

    # Formula tables were read in the rendered pages. No formula is inferred
    # from an instruction name: the two-degree-of-freedom BW cell only says s.
    formula_marker = {'S.PID': 'PID operation is performed with the following operation expression.',
        'S.2PID': '2-degree-of-freedom PID operation is performed with the following operation expression.',
        'S.PIDP': 'PID operation is performed with the following operation expression.',
        'S.SPI': 'SPI operation is performed with the following operation expression.',
        'S.IPD': 'I-PD operation is performed with the following operation expression.'}[base]
    difference = 'PVn-PVn_previous' if base == 'S.PIDP' else 'PVn-2*PVn_previous+PVn_before_previous'
    derivative = 'Bn_previous+MD*TD/(MD*CT+TD)*(%s-CT*Bn_previous/TD)'
    formulas = {'definitions': {'Kp': 'K*P', 'TI': 'I'}, 'cases': []}
    if base != 'S.SPI':
        formulas['definitions'].update(MD='MTD', TD='D')
        formulas['cases'] = [
            {'PN': 1, 'expression': 'Bn=' + derivative % difference},
            {'PN': 0, 'expression': 'Bn=' + derivative % ('-(' + difference + ')')}]
    if base == 'S.PID':
        formulas['result'] = 'BW=Kp*(DVn-DVn_previous+CT/TI*DVn+Bn)'
    elif base == 'S.IPD':
        formulas['result_cases'] = [
            {'PN': 1, 'expression': 'BW=Kp*(CT/TI*DVn+PVn-PVn_previous+Bn)'},
            {'PN': 0, 'expression': 'BW=Kp*(CT/TI*DVn-(PVn-PVn_previous)+Bn)'}]
    elif base == 'S.PIDP':
        formulas.update(integral='In=In_previous+CT/TI*DVn', result='T=Kp*(DVn+In+Bn)')
    elif base == 'S.SPI':
        formulas['definitions']['BT'] = 'execution_cycle_delta_T'
        formulas.update(result='BW=Kp*(DVn-DVn_previous+BT/TI*DVn)',
                        active_period='ST', hold_period='STHT-ST',
                        hold_result={'BW': 0, 'past_value_memory_updated': False})
    else:
        formulas['cases'] = [
            {'expression': 'Bn=' + derivative % 'DVn-2*DVn_previous+DVn_before_previous'},
            {'PN': 1, 'expression': 'Cn=PVn-PVn_previous'},
            {'PN': 0, 'expression': 'Cn=-(PVn-PVn_previous)'},
            {'PN': 1, 'expression': 'Dn=Dn_previous+MD*TD/(MD*CT+TD)*(PVn-2*PVn_previous+PVn_before_previous-CT*Dn_previous/TD)'},
            {'PN': 0, 'expression': 'Dn=Dn_previous+MD*TD/(MD*CT+TD)*(-(PVn-2*PVn_previous+PVn_before_previous)-CT*Dn_previous/TD)'}]
        gap('two_degree_result', {'subject': 'BW', 'source_result_symbol': 's',
            'result_expression_binding': 'unknown', 'reason': 'rendered_BW_cell_has_undefined_symbol_s'},
            [formula_marker])
        add('two_degree_parameters', 'effects.tuning_parameters',
            ['manipulated value variation relative to the set value change.',
             'the effect of derivative on the set value change.'], {
            'alpha': {'offset': 64, 'increasing': 'less_MV_variation_slower_stabilization',
                      'decreasing': 'more_compensation_possible_hunting'},
            'beta': {'offset': 66, 'increasing': 'less_derivative_effect_on_setpoint_change',
                     'decreasing': 'more_derivative_effect_on_setpoint_change'}})
    add('formulas', 'effects.control_formula', [formula_marker], formulas)

    old_header = cell_containing('07031')
    new_header = cell_containing('07032', 'or later')
    derivative_marker = cell_containing('Derivative constant', 'MAN, LCM and CMV')
    is_legacy_cpu = lambda model: bool(re.fullmatch(r'Q\d+(?:PH|PRH)', model))
    is_process_cpu = lambda model: bool(re.fullmatch(r'Q\d+PH', model))
    is_redundant_cpu = lambda model: bool(re.fullmatch(r'Q\d+PRH', model))
    is_udpv = lambda model: 'UDPV' in model
    version_cpus = is_process_cpu if base == 'S.PIDP' else is_legacy_cpu
    version_families = ['QnPHCPU'] if base == 'S.PIDP' else ['QnPHCPU', 'QnPRHCPU']
    if base != 'S.SPI':
        add('derivative_disabled', 'effects.derivative', [derivative_marker], {
            'any_of': ['TD=0', 'MODE_in(MAN,LCM,CMV)'],
            'zero_terms': ['Bn', 'Dn'] if base == 'S.2PID' else ['Bn'],
            'past_value_memory_updated': True},
            model_filter=(lambda model: not is_redundant_cpu(model)) if base == 'S.PIDP' else None)
    interval = 'BT' if base == 'S.SPI' else 'CT'
    integral_term = interval + '/TI*DVn'
    for newer, header in ((False, old_header), (True, new_header)):
        alarms = ['MHA2', 'MLA2'] if newer else ['MHA', 'MLA']
        integral_cell = cell_containing('Integral constant', alarms[0], alarms[1])
        if base == 'S.PIDP':
            guards = ['TI=0', alarms[0] + '=1_and_' + integral_term + '>0',
                      alarms[1] + '=1_and_' + integral_term + '<0']
            if newer:
                guards.append('MODE_in(MAN,LCM,CMV)')
        else:
            either_alarm = 'any(' + alarms[0] + '=1,' + alarms[1] + '=1)'
            guards = ['TI=0', either_alarm + '_and_MVP>MH_and_' + integral_term + '>0',
                      either_alarm + '_and_MVP<ML_and_' + integral_term + '<0']
        value = {'operation': 'suppress_integral_increment', 'integral_term': integral_term,
                 'any_of': guards, 'replacement': 0, 'alarm_fields': alarms,
                 'cpu_families': version_families,
                 'algorithm_revision': 'revised_alarm_fields' if newer else 'legacy_alarm_fields'}
        witnesses = [header, integral_cell]
        if newer or 'earlier' in old_header:
            comparison, boundary = ('ge', 7032) if newer else ('le', 7031)
            required = [{'field': 'cpu_serial_first_five', 'type': 'UINT32', 'digits': 5,
                         'meaning': 'first_five_CPU_serial_digits_as_decimal'}]
            condition = {'kind': 'cpu_revision_condition',
                         'predicate': _context_test('cpu_serial_first_five', comparison, boundary),
                         'required_context': required}
            value.update(serial_first_five={comparison: boundary}, required_context=required)
            add('integral_new' if newer else 'integral_old', 'effects.integral', witnesses, value,
                model_filter=version_cpus, conditions=[condition])
        else:
            # Revision I physically truncates the IPD old-column heading after
            # "07031 or". The visible new-column lower bound is not a licence
            # to fabricate a missing old-column comparison.
            value.update(serial_label_number=7031, serial_comparison='unknown',
                         source_disposition='source_not_stated', gap_kind='source_not_stated')
            add('integral_old', 'effects.integral', witnesses, value, model_filter=version_cpus,
                conditions=[{'kind': 'documented_condition', 'value': 'unknown'}])
            gap('old_header_truncated', {'subject': 'legacy_integral_CPU_serial_comparison',
                'visible_serial': 7031, 'serial_comparison': 'unknown',
                'cpu_families': version_families}, [header], model_filter=version_cpus)
        if newer:
            udpv_value = {key: copy.deepcopy(item) for key, item in value.items()
                          if key not in {'serial_first_five', 'required_context'}}
            udpv_value['cpu_families'] = ['QnUDPVCPU']
            udpv_value['CPU_serial_restriction'] = 'none_in_table'
            add('integral_UDPV', 'effects.integral', witnesses, udpv_value, model_filter=is_udpv)
    if base == 'S.PIDP':
        gap('PRH_revision_scope', {'subject': 'revision_dependent_integral_and_derivative_cases',
            'cpu_families': ['QnPRHCPU'], 'branch_applicability': 'unknown',
            'reason': 'revision_table_CPU_header_omits_QnPRHCPU'},
            [old_header, new_header], model_filter=is_redundant_cpu)
        correction = cell_containing('SD1508', 'TRKF')
        corrected = cell_containing('In-1', 'MV', 'TRKF=0')
        for suffix, matcher, conditions in (
                ('PH', is_process_cpu, [{'kind': 'cpu_revision_condition',
                    'predicate': _context_test('cpu_serial_first_five', 'ge', 7032),
                    'required_context': [{'field': 'cpu_serial_first_five', 'type': 'UINT32', 'digits': 5}]}]),
                ('UDPV', is_udpv, [])):
            add('tracking_integral_correction_' + suffix, 'effects.tracking_integral',
                [new_header, correction, corrected], {
                'all_of': ['SD1508.b0=1', 'INH.TRKF=1', 'MODE_not_in(MAN,LCM,CMV)'],
                'assignments': ['In_previous=MV/Kp-(DVn+Bn)', 'INH.TRKF=0'],
                'cpu_families': ['QnPHCPU'] if suffix == 'PH' else ['QnUDPVCPU'],
                **({'required_context': conditions[0]['required_context']} if conditions else {})},
                model_filter=matcher, conditions=conditions)
        add('mode_and_limit', 'effects.limiting', ['(6) Mode judgment',
            'The change rate limiter performs the following operation',
            'The upper/lower limiter performs the following operation',
            'In the output conversion, the output value is calculated'], {
            'manual_modes': ['MAN', 'CMB', 'CMV', 'LCM'],
            'manual_actions': {'clear': ['ALM.MHA', 'ALM.MLA', 'ALM.DMLA', 'ALM2.MLA2', 'BB3', 'BB4', 'BB5'],
                               'assignment': 'BB1=BB2', 'next': 'output_conversion_then_return'},
            'limit_modes': ['AUT', 'CAB', 'CAS', 'CCB', 'CSV', 'LCA', 'LCC'],
            'rate_limit': [{'when': 'abs(T-MV)<=DML', 'T1': 'T', 'alarm': 0},
                           {'when': 'T-MV>DML', 'T1': 'MV+DML', 'alarm': 1},
                           {'when': 'T-MV<-DML', 'T1': 'MV-DML', 'alarm': 1}],
            'rate_alarm_outputs': ['BB5', 'ALM.DMLA'],
            'rate_alarm_inhibit_any_of': ['INH.DMLI=1', 'INH.ERRI=1'],
            'upper_lower_limit': [
                {'when': 'T1>MH', 'MV': 'MH', 'upper_alarm': 1, 'lower_alarm': 0},
                {'when': 'T1<ML', 'MV': 'ML', 'upper_alarm': 0, 'lower_alarm': 1},
                {'when': 'ML<=T1<=MH', 'MV': 'T1', 'upper_alarm': 0, 'lower_alarm': 0}],
            'upper_alarm_outputs': ['BB3', 'ALM.MHA', 'ALM2.MHA2'],
            'lower_alarm_outputs': ['BB4', 'ALM.MLA', 'ALM2.MLA2'],
            'upper_alarm_inhibition': {'any_of': ['INH.MHI=1', 'INH.ERRI=1'],
                                       'suppresses': ['BB3', 'ALM.MHA'], 'does_not_suppress': 'ALM2.MHA2'},
            'lower_alarm_inhibition': {'any_of': ['INH.MLI=1', 'INH.ERRI=1'],
                                       'suppresses': ['BB4', 'ALM.MLA'], 'does_not_suppress': 'ALM2.MLA2'},
            'output_conversion': 'BW=(NMAX-NMIN)/100*MV+NMIN'})
        gap('mode_clear_MAH2', {'subject': 'mode_judgment_ALM2_clear_field',
            'source_field_name': 'MAH2', 'field_binding': 'unknown',
            'reason': 'mode_clear_spelling_differs_from_ALM2_declared_MHA2'},
            ['MAH2 and MLA2 of the alarm detection 2 (ALM2) are turned to 0.'])
    if base == 'S.SPI':
        add('sample_timing', 'execution.sampling', ['(7) Operating time/hold time check judgment',
            'When the integer part of', 'no processing is performed.',
            'The sample counter and operation counter round off the data to the nearest whole number.'], {
            'operating_period': 'ST', 'hold_period': 'STHT-ST',
            'operating_phases': phases, 'hold_phases': ['tracking', 'zero_BW_without_past_memory_update', 'deviation_check'],
            'continuous_PI_when': 'STHT/delta_T<=ST/delta_T', 'continuous_PI_hold_period': 0,
            'no_operation_when': 'integer_part(STHT/delta_T)=0',
            'no_operation_result': 'delta_MV_unchanged',
            'sample_and_operation_counter_rounding': 'nearest_whole_number'})
    error_conditions = (['execution_cycle_delta_T<0', 'ST<0', 'sample_counter<0']
                        if base == 'S.SPI' else ['CT<0', 'SD1500<0'])
    error_witness = 'the error flag (SM0) turns ON and the error code is stored'
    for newer, matcher in ((False, is_legacy_cpu), (True, is_udpv)):
        add('errors_UDPV' if newer else 'errors_PH_PRH', 'effects.error_response',
            [error_witness, 'When an operation error occurs', 'non-normalized number'], {
            'operation_error': 'documented', 'error_flags': ['SM0'], 'error_code_registers': ['SD0'],
            'cpu_families': ['QnUDPVCPU'] if newer else ['QnPHCPU', 'QnPRHCPU'],
            'cases': [{'error_code': 4100, 'any_of': error_conditions},
                      {'error_code': 4140 if newer else 4100,
                       'when': 'S1_S2_D2_value_non_numeric_or_non_normalized'},
                      {'error_code': 4141 if newer else 4100, 'when': 'arithmetic_operation_error'}]},
            model_filter=matcher)
    specs.extend(_i_control_memory_specs(section, symbol, add, gap))
    return specs


def _reviewed_i_specs(section, symbol):
    if section.manual != 'sh080316ei' or 'List of Instructions' in section.chapter:
        return []
    base = symbol.replace('_', '.')
    if base in _I_CONTROL_PAGES:
        return _i_control_specs(section, symbol)
    table = {
        'S.ADD': ('The input value (El to En) data is added by attaching a coefficient',
                  {'operation': 'weighted_sum', 'inputs': 'E1..En', 'coefficients': 'K1..Kn', 'bias': 'B',
                   'result': 'BW', 'expression': 'SUM(K[i]*E[i])+B', 'zero_inputs_result': 'B'}),
        'S.SUB': ('The input value (E1 to En) data is subtracted by attaching a coefficient',
                  {'operation': 'weighted_difference', 'inputs': 'E1..En', 'coefficients': 'K1..Kn', 'bias': 'B',
                   'result': 'BW', 'expression': 'K1*E1-SUM(K[2..n]*E[2..n])+B', 'zero_inputs_result': 'B'}),
        'S.MUL': ('The input value (E1 to En) data is multiplied by attaching a coefficient',
                  {'operation': 'weighted_product', 'inputs': 'E1..En', 'coefficients': 'K1..Kn', 'bias': 'B',
                   'result': 'BW', 'expression': 'PRODUCT(K[i]*E[i])+B', 'zero_inputs_result': 'B'}),
        'S.DIV': ('Divides the input value 1 (E1) by the input value 2 (E2)',
                  {'operation': 'adjusted_division', 'numerator_input': 'E1', 'denominator_input': 'E2',
                   'result': 'BW', 'zero_denominator_result': 'B3', 'coefficient_bias_parameters': ['A', 'K1', 'K2', 'B1', 'B2']}),
        'S.SQR': ('When the input value is negative, 0 is output',
                  {'operation': 'square_root_with_low_cut', 'input': 'E1', 'result': 'BW',
                   'negative_input_result': 0, 'coefficient': 'K', 'low_cut': 'OLC'}),
        'S.ABS': ('The input value (E1) absolute value is output',
                  {'operation': 'absolute_value', 'input': 'E1', 'result': 'BW',
                   'sign_outputs': {'positive': [1, 0], 'negative': [0, 1], 'zero': [0, 0]},
                   'sign_output_order': ['BB1', 'BB2']}),
        'S.LIMT': ('The upper and lower limit limiter is applied to the output value by adding a hysteresis',
                   {'operation': 'hysteretic_limit', 'input': 'E1', 'result': 'BW', 'upper_limit': 'HILMT',
                    'lower_limit': 'LOLMT', 'upper_hysteresis': 'HS1', 'lower_hysteresis': 'HS2',
                    'upper_alarm': 'BB1', 'lower_alarm': 'BB2'}),
        'S.PGS': ('Provides a control output according to the SV and MV pattern',
                  {'operation': 'program_setpoint_pattern', 'inputs': ['SV_pattern', 'MV_pattern'],
                   'result': 'control_output', 'end_modes': {'hold': 'hold_SV10', 'return': 'SV_zero_MV_last',
                                                         'cyclic': 'restart_SV1_after_SV10'}}),
        'S.BUMP': ('Brings the output value (BW) closer to the output set value (E1)',
                   {'operation': 'bumpless_transfer', 'manual_output': 'E2', 'automatic_target': 'E1', 'result': 'BW',
                    'manual_to_auto': 'approach_at_fixed_rate', 'inside_delay_zone': 'first_order_lag'}),
        'S.AMR': ('Increases or decreases the output value at the fixed rate',
                  {'operation': 'analog_memory_ramp', 'result': 'BW', 'manual_mode_value': 'E3',
                   'addition_signal': 'e2', 'subtraction_signal': 'e3', 'addition_rate': 'E1', 'subtraction_rate': 'E2'}),
    }
    row = table.get(base)
    if row is None or not section.bind(row[0]):
        return []
    return [_reviewed_spec('loop_operation', 'effects.operation', [row[0]], row[1],
                           _section_languages(section, symbol))]


# Each row is an authored operation, its object and its result. The final field
# is a literal phrase that must be present in this instruction's source section.
_M_OPERATIONS = {
    'CPRTCL': ('execute_predefined_protocol', 'protocols_in_module_flash_ROM', 'serial_protocol_execution', 'executes the protocols and functional protocols written to the flash ROM'),
    'RIFR': ('read_auto_refresh_buffer', 'specified_station_auto_refresh_buffer', 'host_devices', 'reads data from the auto-refresh buffer of the specified station'),
    'RITO': ('write_auto_refresh_buffer', 'host_devices', 'specified_station_auto_refresh_buffer', 'writes the data to the auto-refresh buffer of the specified station'),
    'RLPASET': ('set_network_parameters_and_start_link', 'network_parameter_data', 'master_station_data_link_started', 'sets the network parameters to the master station and starts up the data link'),
    'REQ': ('request_remote_CPU_operation', 'request_data_s2_and_control_data_s1', 'response_data_d1', 'Remotely runs or stops a programmable controller on another station'),
    'ICSMPRD': ('read_sampling_counter', 'specified_channel_sampling_count', 'd', 'stores a sampling count value of the specified CH'),
    'ICFCNT': ('measure_frequency', 'specified_channel_with_configured_measurement_unit_time', 'd', 'measures a frequency of the specified CH'),
    'ICRCNT': ('measure_rotation_speed', 'specified_channel_with_configured_measurement_unit_time', 'd', 'measures a rotation speed of the specified CH'),
    'ICPLSRD': ('read_measured_pulse_value', 'specified_channel_measured_pulse', 'd', 'stores a measured pulse value of the specified CH'),
    'OFFGAN': ('select_analog_mode', 'analog_module', 'normal_or_offset_gain_mode', 'converts the mode of analog modules'),
    'OGLOAD': ('read_offset_gain_settings', 'analog_module_user_range', 'cpu_devices', 'reads the user range settings offset/gain values'),
    'OGSTOR': ('restore_offset_gain_settings', 'cpu_stored_user_range', 'analog_module', 'restores the user range settings offset/gain values'),
    'ABRST': ('restore_absolute_position', 'specified_axis', 'axis_absolute_position', 'restores the absolute position of the specified axis'),
    'PSTRT': ('start_positioning', 'specified_axis', 'positioning_started', 'starts positioning of the specified axis'),
    'TEACH': ('teach_position', 'specified_axis', 'positioning_data', 'performs teaching for the specified axis'),
    'PFWRT': ('write_positioning_flash', 'parameters_positioning_data_block_start_data', 'module_flash_ROM', 'writes the positioning module parameters, positioning data, and block start data'),
    'PINIT': ('initialize_positioning_settings', 'positioning_module', 'initialized_settings', 'initializes the positioning module setting data'),
    'ONDEMAND': ('send_MC_on_demand', 'user_data', 'remote_recipient', 'sends data using the on-demand function of MC protocol'),
    'OUTPUT': ('send_nonprocedural', 'user_message', 'serial_port', 'sends data in the message format specified by the user using the nonprocedural protocol'),
    'INPUT': ('receive_nonprocedural', 'serial_port', 'user_message', 'receives data in the message format specified by the user using the nonprocedural protocol'),
    'BIDOUT': ('send_bidirectional_protocol', 'user_data', 'serial_port', 'sends data using the bidirectional protocol'),
    'BIDIN': ('receive_bidirectional_protocol', 'serial_port', 'user_data', 'receives data using the bidirectional protocol'),
    'SPBUSY': ('read_communication_status', 'serial_transmission_and_reception', 'status_data', 'reads the data transmission/reception status'),
    'PRR': ('send_user_frame', 'transmission_user_frame_area', 'serial_port', 'sends data by user frame according to the specification'),
    'PUTE': ('register_user_frame', 'user_frame_data', 'serial_module_frame_store', 'registers a user frame'),
    'GETE': ('read_user_frame', 'serial_module_frame_store', 'user_frame_data', 'reads a user frame'),
    'RIRD': ('read_station_words', 'station_buffer_memory_or_cpu_devices', 'host_devices', 'reads data for the specified number of points from the buffer memory'),
    'RIWT': ('write_station_words', 'host_devices', 'station_buffer_memory_or_cpu_devices', 'writes the data for the specified number of points to the buffer memory'),
    'RIRCV': ('receive_handshaked_buffer', 'intelligent_device_station_buffer', 'host_devices', 'automatically performs handshaking with an intelligent device station and reads data'),
    'RISEND': ('send_handshaked_buffer', 'host_devices', 'intelligent_device_station_buffer', 'automatically performs handshaking with an intelligent device station and writes data'),
    'READ': ('read_other_station_words', 'other_station_word_device', 'host_words', 'reads data from a word device of another station'),
    'SREAD': ('read_other_station_words_with_completion_notice', 'other_station_word_device', 'host_words', 'reads data from a word device of another station'),
    'WRITE': ('write_other_station_words', 'host_words', 'other_station_word_device', 'writes data to a word device of another station'),
    'SWRITE': ('write_other_station_words_with_completion_notice', 'host_words', 'other_station_word_device', 'writes data to a word device of another station'),
    'SEND': ('send_station_message', 'host_message_data', 'other_station', 'sends data to another station'),
    'RECV': ('read_station_message', 'received_message', 'host_devices', 'reads received data (for main program)'),
    'RECVS': ('read_station_message_in_interrupt', 'received_message', 'host_devices', 'reads received data (for interrupt program)'),
    'ZNRD': ('read_other_station_words', 'other_station_cpu_devices', 'host_devices', 'reads data from devices of a programmable controller CPU on another station'),
    'ZNWR': ('write_other_station_words', 'host_devices', 'other_station_cpu_devices', 'writes data to devices of a programmable controller CPU on another station'),
    'RRUN': ('remote_run', 'other_station_cpu', 'RUN', 'remotely switches a CPU module on another station to RUN'),
    'RSTOP': ('remote_stop', 'other_station_cpu', 'STOP', 'remotely switches a CPU module on another station to STOP'),
    'RTMRD': ('read_remote_clock', 'other_station_cpu_clock', 'host_devices', 'reads clock data from a CPU module on another station'),
    'RTMWR': ('write_remote_clock', 'host_clock_data', 'other_station_cpu_clock', 'writes clock data to a CPU module on another station'),
    'REMFR': ('read_remote_module_buffer', 'remote_IO_station_module_buffer', 'host_d1_words', 'reads data from the buffer memory of an intelligent function module'),
    'REMTO': ('write_remote_module_buffer', 'host_words', 'remote_IO_station_module_buffer', 'writes data to the buffer memory of an intelligent function module'),
    'CCPASET': ('set_network_parameters', 'parameter_data', 'master_local_module_master_station', 'sets parameters for master/local module (master station)'),
    'OPEN': ('open_module_connection', 'external_device_connection', 'open', 'establishes (opens) a connection with external device'),
    'CLOSE': ('close_module_connection', 'external_device_connection', 'closed', 'shuts off (closes) a connection with external device'),
    'BUFRCV': ('read_fixed_buffer', 'ethernet_receive_buffer', 'host_devices', 'reads receive data from external device in fixed buffer communication'),
    'BUFSND': ('send_fixed_buffer', 'host_devices', 'external_device', 'sends data to external device in fixed buffer communication'),
    'ERRCLR': ('clear_ethernet_errors', 'ethernet_module_errors', 'LED_off_and_error_buffer_cleared', 'turns OFF the LED on Ethernet module and clears error information'),
    'ERRRD': ('read_ethernet_errors', 'ethernet_error_buffer', 'host_devices', 'reads the error information stored in the buffer memory of the Ethernet module'),
    'MRECV': ('read_email', 'received_email', 'host_devices', 'reads received e-mail'),
    'MSEND': ('send_email', 'host_email', 'email_recipient', 'sends an e-mail'),
    'PIDINIT': ('register_PID_loops', 's_control_data', 'cpu_PID_registration', 'registering the PID control data for the number of loops'),
    'PIDCONT': ('execute_PID', 'set_value_and_process_value', 'automatic_manipulated_value_area', 'performs PID operation based on the set value (SV) and process value (PV)'),
    'PIDSTOP': ('stop_PID_loop', 'loop_n', 'PID_stopped', 'stops the PID operation for the loop number'),
    'PIDRUN': ('start_PID_loop', 'loop_n', 'PID_started', 'starts the PID operation for the loop number'),
    'PIDPRMW': ('change_PID_parameters', 's_control_data', 'loop_n_parameters', 'changes the operation parameter of the loop number'),
    'SOCOPEN': ('open_socket_connection', 'connection_s1', 'open', 'establishes a connection'),
    'SOCCLOSE': ('close_socket_connection', 'connection_s1', 'closed', 'shuts off a specified connection'),
    'SOCRCV': ('read_socket_buffer', 'connection_receive_area', 'd1_received_data', 'reads receive data of a specified connection from the socket communication receive data area at the end process'),
    'SOCRCVS': ('read_socket_buffer_synchronously', 'connection_receive_area', 'd_received_data', 'reads receive data of a specified connection from the socket communication receive data area'),
    'SOCSND': ('send_socket_data', 's3_data', 'external_device_of_connection_s1', 'sends data to the external device of a specified connection'),
    'SOCCINF': ('read_socket_connection_information', 'connection_s1', 'd_information', 'reads connection information of a specified connection'),
    'SOCCSET': ('change_socket_destination', 'destination_IP_and_port', 'connection_destination', 'changes the IP address and port number'),
    'SOCRMODE': ('change_socket_receive_mode', 'TCP_receive_mode_and_size', 'connection_receive_settings', 'changes the TCP receive mode'),
    'SOCRDATA': ('read_socket_words', 'connection_receive_area', 'destination_words', 'reads data for the specified number of words from the socket communication receive data area'),
    'SFCSCOMR': ('read_active_SFC_step_comments', 'block_n1_active_steps', 'd1_comments', 'reads step comments being activated in the SFC block'),
    'SFCTCOMR': ('read_active_SFC_transition_comments', 'block_n1_active_step_transition_1', 'd1_comments', 'reads comments of the transition condition 1 associated with steps activated'),
    'IPPSTRT': ('start_builtin_positioning', 'axis_positioning_data_n', 'axis_started', 'starts the specified axis'),
    'IPDSTRT': ('start_builtin_positioning', 'axis_positioning_data_n', 'axis_started', 'starts the specified axis'),
    'IPSIMUL': ('start_two_axes_simultaneously', 'axis1_data_n1_and_axis2_data_n2', 'both_axes_started', 'simultaneously starts the positioning of the axis 1'),
    'IPOPR': ('start_origin_return', 'specified_axis', 'OPR_started', 'starts the OPR of which type is specified by (s)'),
    'IPJOG': ('start_jog', 'specified_axis', 'JOG_started', 'starts the JOG operation of the specified axis'),
    'IPABRST': ('restore_absolute_position', 'servo_input_s_and_output_d', 'axis_absolute_position', 'executes the absolute position restoration of the specified axis'),
    'IPSTOP': ('stop_positioning', 'specified_axis', 'axis_stopped', 'stops the positioning of the specified axis'),
    'IPSPCHG': ('change_axis_speed', 's_speed_and_acceleration_deceleration_data', 'axis_speed', 'changes the speed of the specified axis'),
    'IPTPCHG': ('change_axis_target_position', 's_target_position', 'axis_target', 'changes the position of the specified axis'),
    'ICCNTRD': ('refresh_counter_current_value', 'channel_counter_at_instruction_execution', 'channel_current_value_special_register', 'stores a value at the time of instruction execution to the current value'),
    'ICRNGWR': ('set_ring_counter_limits', 'channel_lower_and_upper_limits', 'ring_counter_configuration', 'sets the ring counter lower limit value and the ring counter upper limit value'),
    'ICPREWR': ('set_counter_preset', 'channel_preset_value', 'counter_preset', 'sets a preset value of the specified CH'),
    'ICLTHRD': ('read_latched_counter', 'channel_latch_n', 'd', 'stores a latch count value n of the specified CH'),
    'ICCOVWR': ('write_counter_coincidence_point', 'channel_output_number_n', 'coincidence_output_point', 'stores a coincidence output No. n point of the specified CH'),
    'ICPWM': ('output_PWM', 'specified_channel', 'PWM_waveform', 'outputs a PWM waveform of the specified CH'),
    'LOGTRG': ('trigger_data_logging', 'logging_configuration_n', 'trigger_on_then_latched_records', 'LOGTRG instruction generates a trigger'),
    'LOGTRGR': ('reset_data_logging_trigger', 'logging_configuration_n', 'trigger_reset', 'LOGTRGR instruction'),
}


def _reviewed_m_specs(section, symbol):
    if section.manual != 'sh080785engm':
        return []
    base = re.sub(r'^(?:[GJSZ]P?|SP)_', '', symbol)
    base = re.sub(r'_[JU]$', '', base)
    base = re.sub(r'([1-4])P?$', '', base)
    if base not in _M_OPERATIONS and base.endswith('P') and base[:-1] in _M_OPERATIONS:
        base = base[:-1]
    row = _M_OPERATIONS.get(base)
    if base == 'BUFRCVS':
        row = (('receive_serial_in_interrupt', 'serial_nonprocedural_or_bidirectional_data', 'host_devices',
                'receives data with an interrupt program') if family_of(section) == 'm_serial' else
               ('read_fixed_buffer_in_interrupt', 'ethernet_receive_buffer', 'host_devices',
                'reads receive data from external device in fixed buffer communication'))
    if base == 'UINI':
        row = (('change_serial_mode', 'mode_transmission_specification_and_station_number', 'serial_module_settings',
                'switches the mode, transmission specification, and host station number')
               if family_of(section) == 'm_serial' else
               ('reinitialize_ethernet_module', 'ethernet_module', 'reinitialized_module', 'reinitializes the Ethernet module'))
    if base == 'CSET':
        row = {'Receive data clear': ('clear_serial_receive_data', 'serial_receive_buffer', 'empty', 'clears the receive data'),
               'Initial setting': ('set_serial_protocol', 'transmission_reception_settings', 'serial_module_settings', 'changes the setting values for sending/receiving data'),
               'CSET instruction (programmable controller CPU monitor)': ('configure_CPU_monitor', 'CPU_monitor_registration', 'registered_or_cancelled', 'registers and cancels the programmable controller CPU monitoring')}.get(section.title)
    if row is None or not section.bind(row[3]):
        return []
    operation, object_name, result, witness = row
    value = {'operation': operation, 'object': object_name, 'result': result}
    axis = re.search(r'([1-4])P?$', symbol)
    if axis and base in {'ABRST', 'PSTRT', 'TEACH', 'IPPSTRT', 'IPDSTRT', 'IPOPR', 'IPJOG',
                         'IPABRST', 'IPSTOP', 'IPSPCHG', 'IPTPCHG'}:
        value['axis'] = int(axis.group(1))
    if axis and base.startswith('IC'):
        value['channel'] = int(axis.group(1))
    if base in {'PIDINIT', 'PIDCONT', 'PIDPRMW', 'PIDSTOP', 'PIDRUN'}:
        value['differential'] = 'inexact' if 'Inexact' in section.chapter else 'exact'
    if base == 'OFFGAN':
        value['mode_values'] = {'0': 'normal', '1': 'offset_gain_setting'}
    if base == 'SOCRCV':
        value['read_timing'] = 'END_processing_after_instruction'
    if base == 'SOCCSET':
        value['protocol'] = 'UDP'
    if base == 'SOCRMODE':
        value['protocol'] = 'TCP'
    if base == 'REQ':
        value['request_operations'] = ['remote_run', 'remote_stop', 'read_clock', 'write_clock']
    if base in {'SFCSCOMR', 'SFCTCOMR'}:
        value.update(total_comments='n2', comments_per_scan='n3', completion='d2_one_scan')
    specs = [_reviewed_spec('module_' + base.lower(), 'effects.operation', [witness], value, ('st', 'fbd'))]
    requirement = getattr(section, 'module_requirement', None)
    if base == 'UINI' and family_of(section) == 'm_network' and requirement and requirement.get('bound'):
        def case_condition(module_family):
            names = requirement['models'][module_family]
            fields = ['selected_module_model_' + re.sub(r'[^A-Za-z0-9_]', '_', name) for name in names]
            return {'kind': 'module_condition',
                    'predicate': _join_predicates('or', [_context_test(field) for field in fields]),
                    'required_context': [{'any_of': fields, 'models': names, 'type': 'BOOL'}]}
        specs[0]['conditions'] = [case_condition('ethernet')]
        if symbol.startswith('ZP_'):
            station = _witness(section.text, (r'this instruction sets the station number of the CC-Link IE',))
            if station:
                change = _reviewed_spec('module_uini_controller_station', 'effects.operation', [station],
                    {'operation': 'set_CC_IE_controller_station_number', 'station': 'host_normal_station',
                     'CPU_group': 'Universal_model_QCPU'}, ('st', 'fbd'))
                change['conditions'] = [case_condition('cc_ie_controller')]
                specs.append(change)
    if requirement:
        source = requirement['source']
        marker = 'Function/module for explaining an instruction' if requirement.get('bound') else {
            'analog': 'Analog module', 'positioning': 'Positioning module',
            'serial_communication': 'Serial communication module', 'network': 'CC-Link system master/local module'}[requirement['family']]
        requirement_value = ({'target_module_compatibility': 'specified_model_or_family'} if requirement.get('bound')
                             else {'module_family': requirement['family'],
                                   'instruction_specific_module_compatibility': 'required'})
        required = _reviewed_spec('module_required', 'constraints.hardware_requirements', [marker],
                                 requirement_value, ('st', 'fbd'))
        required['evidence_sections'] = [source]
        specs.append(required)
    if base == 'PIDCONT':
        marker = 'first execution of the PIDCONT instruction after the set sampling cycle time has elapsed'
        if section.bind(marker):
            specs.append(_reviewed_spec('pid_sampling', 'execution.sampling', [marker],
                                       {'update_timing': 'first_instruction_execution_after_sampling_interval',
                                        'clock_check': 'instruction_execution'}, ('st', 'fbd')))
    return specs


_XP_OPERATIONS = {
    'STOP': ('stop_cpu', 'CPU_and_output_Y', 'CPU_STOP_and_Y_reset', r'Resets the output \(Y\) and stops the CPU'),
    'BMOV': ('copy_word_block', 'S_n_words', 'D_n_words', r'Transfers in batch 16-bit data of n points|Transfers 16-bit data of n points'),
    'RBMOV': ('copy_file_register_block', 'S_n_words', 'D_n_words', r'Transfers in batch 16-bit data of n points'),
    'FMOV': ('fill_word_block', 'S_word', 'D_n_words', r'Transfers 16-bit data at the device'),
    'DFMOV': ('fill_double_word_block', 'S_double_word', 'D_n_double_words', r'transfers 32-bit data of the device'),
    'XCH': ('exchange_values', 'D1_and_D2', 'exchanged_16_bit_values', r'Conducts 16-bit data exchange'),
    'DXCH': ('exchange_values', 'D1_and_D2', 'exchanged_32_bit_values', r'Conducts 32-bit data exchange'),
    'SWAP': ('exchange_word_bytes', 'D_word', 'D_bytes_swapped', r'[Ee]xchanges?[^.]{0,100}(?:upper|lower)[^.]{0,100}(?:byte|8.bit)'),
    'GOEND': ('jump_to_end', 'current_program', 'FEND_or_END', r'Jumps to the FEND or END instruction'),
    'IRET': ('return_from_interrupt', 'interrupt_program', 'interrupted_program_continues', r'[Rr]eturns?[^.]{0,100}(?:interrupt|program which was executing)'),
    'RFS': ('refresh_IO_mid_scan', 'specified_X_or_Y_range', 'physical_input_read_or_output_written', r'Refreshes only the device being scanned during a scan'),
    'UDCNT1': ('up_down_counter_single_phase', 'S_count_edge_and_S+1_direction', 'D_counter_current_value', r'When the input designated at \(S\) goes from OFF to ON'),
    'UDCNT2': ('up_down_counter_two_phase', 'S_phase_A_and_S+1_phase_B', 'D_counter_current_value', r'present value of the counter[^.]{0,160}A phase pulse'),
    'TTMR': ('measure_command_on_time', 'execution_command', 'D_measured_time', r'[Mm]easures?[^.]{0,160}(?:ON|time)'),
    'STMR': ('four_mode_timer', 'S_timer_and_command_edges', 'D_four_bit_outputs', r'STMR instruction uses the 4 points'),
    'ROTC': ('shortest_rotary_direction', 'station_S+1_and_item_S+2', 'shortest_rotation_direction', r'shortest direction control of the rotary table'),
    'SPD': ('count_edges_over_interval', 'S_input_rising_edges_over_n_time', 'D_edge_count', r'count results are stored in the device'),
    'PLSY': ('output_pulse_train', 'frequency_n1_and_count_n2', 'Y_output_D', r'Outputs a pulse at a frequency designated by n1'),
    'PWM': ('output_PWM', 'on_time_n1_and_cycle_n2', 'output_D', r'Outputs the pulse of the cycle set by n2'),
    'MTR': ('scan_input_matrix', 'S_16_inputs_by_n_rows', 'D2_matrix_values', r'reads the input from 16 points'),
    'BKRST': ('reset_bit_block', 'D_n_bit_devices', 'all_zero', r'Resets bit device n-points'),
    'CRC': ('compute_CRC16', 'S_n_bytes', 'D_CRC', r'Generates the CRC value of n points'),
    'RET': ('return_from_subroutine', 'current_subroutine', 'instruction_after_caller', r'Indicates end of subroutine program|returns to the step following the CALL'),
    'FCALL': ('execute_subroutine_output_off_processing', 'pointer_Pn', 'subroutine_nonexecution_state', r'non-execution processing of the subroutine program'),
    'EFCALL': ('execute_external_subroutine_output_off_processing', 'program_and_pointer_Pn', 'subroutine_nonexecution_state', r'non-execution processing of the subroutine program'),
    'FIFW': ('append_fifo_word', 'S_word', 'D_table_tail', r'Stores the 16-bit data designated by \(S\) in the data table'),
    'FIFR': ('read_oldest_fifo_word', 'S_table_oldest_word', 'D_word', r'(?:[Rr]eads|[Ss]tores|[Tt]ransfers)[^.]{0,160}(?:oldest|first)'),
    'FPOP': ('read_newest_table_word', 'S_table_newest_word', 'D_word', r'(?:[Rr]eads|[Ss]tores|[Tt]ransfers)[^.]{0,160}(?:newest|last)'),
    'PRC': ('output_device_comment', 'S_device_comment_ASCII', 'D_output_module', r'Outputs comment \(ASCII code\)'),
    'LEDR': ('reset_error_or_annunciator_display', 'self_diagnosis_error_or_annunciator', 'one_item_reset_per_execution', r'Resets the self-diagnosis error display'),
    'CHKCIR': ('define_check_ladder', 'ladder_until_CHKEND', 'CHK_failure_check_pattern', r'check ladder pattern[^.]{0,90}updated to any format'),
    'CHKEND': ('end_check_ladder_definition', 'CHKCIR_check_ladder', 'CHK_failure_check_pattern_end', r'ladder pattern described between the CHKCIR and CHKEND'),
    'ASC': ('encode_hexadecimal_ASCII', 'S_BIN16_data', 'D_n_ASCII_characters', r'Converts the BIN 16-bit data[^.]{0,140}ASCII'),
    'HEX': ('decode_hexadecimal_ASCII', 'S_ASCII_characters', 'D_BIN16_data', r'[Cc]onverts[^.]{0,130}ASCII[^.]{0,130}(?:BIN|hexadecimal)'),
    'STRDEL': ('delete_string_segment', 'D_string', 'D_string_without_n2_characters_from_n1', r'deletes n2 characters data'),
    'QCDSET': ('select_comment_file', 'S_file_name', 'active_comment_data_file', r'After the file name change, comment data'),
    'RSET': ('select_file_register_block', 'specified_block_number', 'active_file_register_block', r'[Cc]hanges?[^.]{0,100}(?:block|file register)|[Ss]witches[^.]{0,100}block'),
    'QDRSET': ('select_file_register_file', 'S_file_name', 'active_file_register_file', r'[Cc]hanges? the file register file name'),
    'DATEWR': ('write_CPU_clock', 'S_BIN_clock_fields', 'CPU_clock', r'Writes clock data stored in the device number'),
    'PSTOP': ('put_program_in_standby', 'S_program_file', 'standby_at_END_processing', r'Places the file name program[^.]{0,140}stand-by'),
    'POFF': ('program_output_off_then_standby', 'S_program_file', 'output_off_processing_then_standby', r'OUT instruction: Forced OFF'),
    'PSCAN': ('register_scan_program', 'S_program_file', 'scan_execution_at_END_processing', r'Sets the program[^.]{0,150}scan execution type'),
    'PLOW': ('register_low_speed_program', 'S_program_file', 'low_speed_execution_at_END_processing', r'Sets the program[^.]{0,150}low-speed execution type'),
    'LDPCHK': ('check_program_execution', 'named_program', 'boolean_execution_status', r'Checks whether the program of the specified file name'),
    'ANDPCHK': ('and_program_execution_status', 'named_program_and_current_result', 'boolean_execution_status', r'Checks whether the program of the specified file name'),
    'ORPCHK': ('or_program_execution_status', 'named_program_and_current_result', 'boolean_execution_status', r'Checks whether the program of the specified file name'),
    'PID': ('sampled_PID_control', 'S1_setpoint_S2_process_value_S3_parameters', 'D_manipulated_value', r'manipulated value \(MV\) is stored in the output value'),
    'WDT': ('reset_watchdog_timer', 'watchdog_elapsed_time', 'watchdog_reset', r'Resets watchdog timer during the execution'),
    'ZRRDB': ('read_file_register_byte', 'serial_byte_number_n', 'D_low_byte_upper_byte_zero', r'Reads the serial byte number designated by n'),
    'ZRWRB': ('write_file_register_byte', 'S_low_byte', 'serial_byte_number_n', r'Writes the lower 8 bits of data stored'),
    'ZPUSH': ('push_index_registers', 'CPU_index_registers', 'D_stack_and_increment_save_count', r'Saves the contents of the following index registers'),
    'ZPOP': ('pop_index_registers', 'D_saved_stack', 'CPU_index_registers_and_decrement_save_count', r'Recovers the contents saved in the area'),
    'UNIRD': ('read_installed_module_information', 'module_n1_information_count_n2', 'D_information', r'Reads the module information as much as designated by n2'),
    'TYPERD': ('read_module_type', 'module_n1', 'D_type_information', r'[Rr]eads[^.]{0,100}(?:module type|type of|model)'),
    'TRACE': ('start_trace', 'trace_configuration', 'trace_started', r'[Ss]tarts?[^.]{0,100}trace|[Ee]nables?[^.]{0,100}trace'),
    'TRACER': ('stop_trace', 'trace_configuration', 'trace_stopped', r'[Ss]tops?[^.]{0,100}trace|[Cc]ancels?[^.]{0,100}trace'),
    'PLOADP': ('load_program', 'memory_card_or_standard_ROM_program', 'program_memory_drive_0', r'program stored in the memory card or standard ROM is transferred'),
    'PUNLOADP': ('unload_standby_program', 'standby_program_in_drive_0', 'program_deleted_from_memory', r'standby program stored in the program memory[^.]{0,90}deleted'),
    'PSWAPP': ('swap_standby_programs', 'S1_old_program_and_S2_loaded_program', 'replacement_program_in_standby', r'standby type program stored in the program memory'),
}


def _reviewed_xp_specs(section, symbol):
    if section.manual not in {'sh080809engx', 'sh080783engp'}:
        return []
    contact = _reviewed_contact_specs(section, symbol)
    if contact:
        return contact
    residual = _reviewed_xp_residual(section, symbol)
    if residual:
        return residual
    base = symbol
    if base.endswith('P') and base[:-1] in _XP_OPERATIONS:
        base = base[:-1]
    row = _XP_OPERATIONS.get(base)
    value, witnesses = None, []
    if row:
        witness = _witness(section.text, (row[3],))
        if witness:
            value = {'operation': row[0], 'object': row[1], 'result': row[2]}
            witnesses = [witness]
    if re.fullmatch(r'(?:D|E|ED)?(?:CMP|ZCP)P?', symbol):
        band = 'ZCP' in symbol
        witness = _witness(section.text, (r'One of \(D\), \(D\)\+1, and \(D\)\+2 turns on',))
        if witness:
            witnesses = [witness]
            value = {'operation': 'compare_band' if band else 'compare_values',
                     'inputs': ['S1_lower', 'S2_upper', 'S3_value'] if band else ['S1', 'S2'],
                     'result': 'one_hot_D_to_D+2', 'outcomes': ['below', 'within', 'above'] if band else ['less', 'equal', 'greater']}
    shift = re.fullmatch(r'(SFTB[RL]|SFTW[RL]|SFT[RL]|WSF[RL])P?', symbol)
    if shift:
        name = shift.group(1)
        direction = 'right' if name.endswith('R') else 'left'
        word = name.startswith(('SFTW', 'WSF'))
        witness = _witness(section.text, (r'shifts[^.]{0,140}to the ' + direction,))
        if witness:
            witnesses = [witness]
            value = {'operation': 'shift_range', 'unit': 'word' if word else 'bit',
                     'range_start': 'D', 'range_count': 'n1', 'shift_count': 'n2', 'direction': direction}
            if name in {'SFTR', 'SFTL', 'WSFR', 'WSFL'}:
                value['fill_source'] = 'S_n2_units'
                value['fill_destination'] = 'D+n1-n2' if direction == 'right' else 'D'
            elif 'filled with 0s' in section.text:
                value['fill_value'] = 0
    clock = re.fullmatch(r'(S[._]|SP[._])?DATERDP?', symbol)
    if clock:
        witness = _witness(section.text, (r'Reads "year, month, day, hour, minute, second[^\n]+',))
        if witness:
            witnesses = [witness]
            value = {'operation': 'read_CPU_clock', 'object': 'CPU_clock', 'result': 'D_BIN_fields',
                     'fields': ['year', 'month', 'day', 'hour', 'minute', 'second', 'weekday'] +
                     (['millisecond'] if clock.group(1) else []), 'weekday_range': [0, 6]}
    refresh = re.fullmatch(r'SP?[._]REFDV(WR|RD)(B|W)', symbol)
    if refresh:
        write = refresh.group(1) == 'WR'
        witness = _witness(section.text, (r'Number of points specified in n2 is ' + ('written' if write else 'read'),))
        if witness:
            witnesses = [witness]
            value = {'operation': 'write_refresh_device' if write else 'read_refresh_device',
                     'unit': 'bit' if refresh.group(2) == 'B' else 'word', 'count': 'n2',
                     'station': 'n1_and_S1+0', 'device_selector': 'S1+1', 'offset': 'S1+2',
                     'source': 'S2' if write else 'remote_refresh_device',
                     'destination': 'remote_refresh_device' if write else 'D1',
                     'reflection_timing': 'automatic_refresh'}
    if symbol in {'COM', 'CCOM', 'CCOMP'}:
        witness = _witness(section.text, (r'perform I/O refresh at any timing', r'execute the select refresh'))
        if witness:
            witnesses = [witness]
            value = {'operation': 'selected_refresh', 'object': 'configured_refresh_targets', 'result': 'refreshed_during_program'}
    if symbol in {'IX', 'IXEND', 'IXDEV', 'IXSET'}:
        witness = _witness(section.text, (r'Performs index modification on all devices in the ladder',
                                         r'used to configure an index modification table'))
        if witness:
            witnesses = [witness]
            value = {'operation': {'IX': 'begin_ladder_index_modification', 'IXEND': 'end_ladder_index_modification',
                                   'IXDEV': 'set_index_modification_devices', 'IXSET': 'set_index_modification_offsets'}[symbol],
                     'object': 'index_modification_table', 'scope': 'ladder_between_IX_and_IXEND'}
    if symbol in {'DI', 'EI', 'IMASK'}:
        patterns = {'DI': r'Disables the execution of an interrupt program',
                    'EI': r'[Ee]nables the execution of an interrupt program',
                    'IMASK': r'[Ss]pecif[^.]{0,140}(?:mask|interrupt)|[Mm]asks?[^.]{0,140}interrupt'}
        witness = _witness(section.text, (patterns[symbol],))
        if witness:
            witnesses = [witness]
            value = {'operation': {'DI': 'disable_interrupts', 'EI': 'enable_interrupts', 'IMASK': 'set_interrupt_mask'}[symbol],
                     'object': 'interrupt_program_execution', 'result': 'interrupt_acceptance_state'}
    if symbol in {'OUT_F', 'SET_F', 'RST_F'} or section.title in {'Annunciator output', 'Setting and resetting annunciators'}:
        if symbol in {'OUT_F', 'OUT'}:
            witness = _witness(section.text, (r'Operation results up to the OUT instruction are output',))
            operation = 'write_annunciator_coil'
        elif symbol in {'SET_F', 'SET'}:
            witness = _witness(section.text, (r'annunciator designated by \(D\) is turned ON',))
            operation = 'set_annunciator'
        else:
            witness = _witness(section.text, (r'annunciator designated by \(D\) is turned OFF',))
            operation = 'reset_annunciator'
        if witness:
            witnesses = [witness]
            value = {'operation': operation, 'object': 'D_annunciator', 'result': 'annunciator_status'}
    if value is None:
        return []
    specs = [_reviewed_spec('authored_operation', 'effects.operation', witnesses, value,
                           _section_languages(section, symbol))]
    if base == 'CRC':
        specs[0]['value']['generator_polynomial'] = 'x16+x15+x2+1'
        specs[0]['value']['modes'] = ['16_bit_conversion', '8_bit_conversion']
    if base in {'PLSY', 'MTR', 'WDT', 'POFF'}:
        detail = {'PLSY': {'frequency_hz': {'minimum': 1, 'maximum': 100}, 'frequency_outside_range': 'not_executed'},
                  'MTR': {'rows_per_scan': 1, 'points_per_row': 16},
                  'WDT': {'scan_time_registers': 'not_cleared'},
                  'POFF': {'OUT': 'forced_off', 'SET_RST': 'retained', 'nonretentive_timer': 'reset',
                           'retentive_timer_counter': 'retained'}}[base]
        # Every detail is separately bound; a sibling manual may omit it.
        markers = {'PLSY': 'Frequencies between 1 to 100Hz', 'MTR': 'One row (16 points) can be fetched in 1 scan',
                   'WDT': 'Scan time values stored at the special register will not be cleared', 'POFF': 'Current value of retentive timer'}
        if section.bind(markers[base]):
            specs.append(_reviewed_spec('operation_detail', 'effects.operation_detail', [markers[base]],
                                       {'operation': value['operation'], **detail}, _section_languages(section, symbol)))
    return specs


def _reviewed_contact_specs(section, symbol):
    ordinary = {'LD', 'LDI', 'AND', 'ANI', 'OR', 'ORI'}
    rising = {'LDP', 'ANDP', 'ORP', 'LDPI', 'ANDPI', 'ORPI'}
    falling = {'LDF', 'ANDF', 'ORF', 'LDFI', 'ANDFI', 'ORFI'}
    if family_of(section) != 'contact' or symbol not in ordinary | rising | falling:
        return []
    witness = _witness(section.text, (r'\b' + re.escape(symbol)
        + r' is (?:the |a |an )?[^.]{0,150}?instruction',))
    if not witness:
        return []
    composition = 'assign' if symbol.startswith('LD') else 'and' if symbol.startswith(('AND', 'ANI')) else 'or'
    if symbol in ordinary:
        negated = symbol in {'LDI', 'ANI', 'ORI'}
        predicate = 'NOT(S)' if negated else 'S'
    else:
        negated = symbol.endswith('I')
        predicate = ('rising_edge' if symbol in rising else 'falling_edge') + '(S)'
        if negated:
            predicate = 'NOT(' + predicate + ')'
    value = {'operation': 'compose_contact_result', 'input': 'S',
             'predicate': predicate, 'composition': composition, 'result_type': 'BOOL'}
    if 'serial connection' in witness and symbol.startswith('OR'):
        value.update(connection='unknown', source_wording='serial_connection',
                     source_disagreement='OR_group_and_serial_wording')
    return [_reviewed_spec('contact_evaluation', 'effects.operation', [witness], value,
                           _section_languages(section, symbol))]


# Per-form restatements for the X/P prose that cannot be safely inferred from
# the title. The pattern always binds in this section, including P's own labels.
_XP_RESIDUAL_OPERATIONS = {
    'OUT': (r'Operation results up to the OUT instruction are output to the (?:designated|specified) device',
            {'operation': 'write_bus_state', 'destination': 'D', 'inactive_value': False}),
    'OUT_C': (r'When the operation results up to the OUT_C instruction change from OFF to ON, 1 is added',
              {'operation': 'count_rising_edges', 'counter': 'D', 'increment': 1,
               'after_count_up': 'retain_count_and_contacts_until_RST'}),
    'SET': (r'Devices turned ON remain ON even when the execution command is turned OFF',
            {'operation': 'set_device', 'destination': 'D', 'disabled_state': 'retained', 'cleared_by': 'RST'}),
    'SFT': (r'Shifts to a device (?:specified for \(d\)|designated by \(D\)) the ON/OFF status of the device immediately prior',
            {'operation': 'shift_single_bit', 'source': 'D-1', 'destination': 'D', 'source_after_shift': False}),
    'BMOV': (r'Batch transfers the 16-bit data n points from the device specified for \(s\)',
             {'operation': 'copy_word_block', 'source': 'S', 'destination': 'D', 'count': 'n', 'unit': 'words'}),
    'RBMOV': (r'Batch transfers the 16-bit data n points from the device specified for \(s\)',
              {'operation': 'copy_file_register_block', 'source': 'S', 'destination': 'D', 'count': 'n', 'unit': 'words'}),
    'GOEND': (r'Jumps to the FEND instruction or END processing in the same program file',
              {'operation': 'jump_to_end', 'target': 'FEND_or_END', 'program_scope': 'same_program_file'}),
    'IRET': (r'Used to end the interrupt program processing forcedly',
             {'operation': 'return_from_interrupt', 'source': 'interrupt_program', 'destination': 'interrupted_program'}),
    'UDCNT1': (r'When the input specified for \(s\)\[0\] turns from OFF to ON, the current value of the counter',
               {'operation': 'up_down_counter_single_phase', 'count_edge': 'rising_S+0',
                'direction_input': 'S+1', 'up_when_direction': False, 'down_when_direction': True,
                'counter': 'D', 'preset': 'n'}),
    'UDCNT2': (r'The current value of the counter specified for \(d\) is updated depending on the status',
               {'operation': 'up_down_counter_two_phase', 'phase_A': 'S+0', 'phase_B': 'S+1',
                'counter': 'D', 'preset': 'n', 'counter_type': 'signed_16_bit_ring'}),
    'RAMP': (r'Shifts from the value specified (?:by|for) n1 to the value specified (?:by|for) n2',
             {'operation': 'scan_linear_ramp', 'initial': 'n1', 'final': 'n2', 'scan_count': 'n3',
              'current_value': 'D1+0', 'execution_count': 'D1+1', 'completion': 'D2+0'}),
    'SPD': (r'The number of turning from OFF to ON input from the device specified for \(s\)',
            {'operation': 'measure_pulse_density', 'source': 'S_rising_edges', 'interval': 'n',
             'result': 'D', 'repeat': 'restart_from_zero_after_each_interval'}),
    'PLSY': (r'Outputs a pulse at a frequency specified for n1 in the number of times specified for n2',
             {'operation': 'output_pulse_train', 'frequency': 'n1', 'count': 'n2', 'destination': 'D',
              'zero_count': 'continuous_output'}),
    'PWM': (r'Outputs the ON-time specified for n1 and the pulse of the cycle specified for n2',
            {'operation': 'output_PWM', 'on_time': 'n1', 'cycle_time': 'n2', 'destination': 'D'}),
    'BKRST': (r'Resets bit devices n points from the bit device specified for \(s\)',
              {'operation': 'reset_bit_block', 'range_start': 'S', 'count': 'n', 'value': False}),
    'DECO': (r'Turns ON the bit position of \([dD]\), which corresponds to the binary value (?:specified|designated) by the lower n bits',
             {'operation': 'decode_binary_to_one_hot', 'source': 'S_low_n_bits', 'destination': 'D_2_power_n_bits',
              'zero_n': 'destination_unchanged'}),
    'ENCO': (r'Stores the binary value corresponding to the bits (?:set to 1 in|which are "1" included in) the 2 n-bit data',
             {'operation': 'encode_highest_set_bit', 'source': 'S_2_power_n_bits', 'destination': 'D_low_n_bits',
              'multiple_bits': 'highest_set_bit_selected', 'zero_n': 'destination_unchanged'}),
    'UNI': (r'Connects lower 4 bits of 16-bit data n points from device specified for \(s\)',
            {'operation': 'pack_low_nibbles', 'source': 'S_n_words', 'destination': 'D_word',
             'unused_upper_nibbles': 0}),
    'FOR': (r'Executes the processing of the next step of the NEXT instruction, when the process of the FOR to NEXT',
            {'operation': 'begin_bounded_repeat', 'body': 'FOR_to_NEXT', 'repetitions': 'n',
             'nonpositive_n': 'one_iteration'}),
    'NEXT': (r'Executes the processing of the next step of the NEXT instruction, when the process of the FOR to NEXT',
             {'operation': 'end_bounded_repeat', 'body': 'FOR_to_NEXT', 'repetitions': 'n',
              'after_last_iteration': 'instruction_after_NEXT'}),
    'BREAK': (r'The remaining number of the FOR to NEXT instruction loops is stored to \(d\)',
              {'operation': 'exit_FOR_NEXT_loop', 'destination': 'D',
               'stored_value': 'remaining_iterations_including_current_iteration'}),
    'RET': (r'Indicates the end of the subroutine program',
            {'operation': 'return_from_subroutine', 'destination': 'instruction_after_calling_CALL'}),
    'FIFW': (r'Stores the 16-bit data specified for \(s\) to the data table specified for \(d\)',
             {'operation': 'append_fifo_word', 'source': 'S', 'table': 'D',
              'count_storage': 'D', 'data_start': 'D+1'}),
    'LEDR': (r'When the LEDR instruction is executed, the "ERR\." LED is reset',
             {'operation': 'reset_error_or_annunciator_display', 'error_LED': 'reset',
              'SM0_SM1_SD0': 'not_automatically_reset', 'selection': 'configured_error_priority'}),
    'CHKST': (r'The CHKST instruction is the instruction that starts the CHK instruction',
              {'operation': 'gate_CHK_diagnostics', 'on': 'execute_CHK', 'off': 'skip_CHK'}),
    'COMRD': (r'Reads the comment at the device number designated by \(S\), and stores it as ASCII code',
              {'operation': 'read_device_comment', 'source': 'S_device_comment', 'destination': 'D_ASCII'}),
    'ESTR': (r'Converts the 32-bit floating-point real number data specified for \(s1\) to a character string',
             {'operation': 'format_real_as_string', 'source': 'S1_REAL', 'format': 'S2', 'destination': 'D_STRING'}),
    'EVAL': (r'Converts character string data stored in the device specified for \(s\).*?to 32-bit floating-point real',
             {'operation': 'parse_string_as_real', 'source': 'S_STRING', 'destination': 'D_REAL',
              'accepted_forms': ['decimal', 'exponential']}),
    'ASC': (r'Converts the 16-bit BIN data stored in the device specified for \(s\).*?to ASCII data',
            {'operation': 'encode_hexadecimal_ASCII', 'source': 'S_BIN_words', 'destination': 'D_ASCII', 'count': 'n_characters'}),
    'STRDEL': (r'Deletes n2 characters from n1th character',
               {'operation': 'delete_string_segment', 'string': 'D', 'start': 'n1', 'count': 'n2',
                'following_data': 'shift_to_fill_gap', 'terminator': 'NULL'}),
    'QDRSET': (r'Changes the file name of the file register used in the program',
               {'operation': 'select_file_register_file', 'file_name': 'S', 'result': 'active_file_register_file'}),
    'DATEWR': (r'Writes clock data stored in \(s\) and the following devices to the clock element',
               {'operation': 'write_CPU_clock', 'source': 'S_BIN_clock_fields', 'destination': 'CPU_clock'}),
    'PSTOP': (r'Places the program whose file name is stored in the device specified for \(s\) in a standby type',
              {'operation': 'put_program_in_standby', 'program_name': 'S', 'effective_at': 'END_processing'}),
    'WDT': (r'Resets a watchdog timer during the execution of a sequence program',
            {'operation': 'reset_watchdog_timer', 'target': 'watchdog_elapsed_time'}),
    'DUTY': (r'Turns ON the user timing clock .*?specified (?:by|for) \([Dd]\)|Turns the user timing clock .*?designated by \(D\), ON',
             {'operation': 'generate_scan_timing_pulse', 'output': 'D_user_timing_clock',
              'on_scans': 'n1', 'off_scans': 'n2', 'update_at': 'END_processing'}),
    'ZRRDB': (r'Reads the serial byte number specified for n that does not signify a block number',
              {'operation': 'read_file_register_byte', 'byte_index': 'n', 'destination': 'D_low_byte', 'D_high_byte': 0}),
    'ZRWRB': (r'Writes the lower bits of data stored in the device specified for \(s\)',
              {'operation': 'write_file_register_byte', 'source': 'S_low_byte', 'byte_index': 'n', 'S_high_byte': 'ignored'}),
    'ADRSET': (r'Stores the indirect address of the device (?:specified for \(s\) in \(d\)|designated by \(S\) at \(D\))',
               {'operation': 'read_device_indirect_address', 'source': 'S', 'destination': 'D'}),
    'UNIRD': (r'Reads the module information whose amount is specified for n2',
              {'operation': 'read_installed_module_information', 'module': 'n1', 'count': 'n2',
               'destination': 'D', 'identity_from': 'actually_mounted_module'}),
    'TYPERD': (r'This instruction reads the module information stored in the area starting from the I/O number',
               {'operation': 'read_module_model_information', 'module_IO_number': 'n', 'destination': 'D'}),
    'PUNLOADP': (r'The standby type program stored in the program memory \(drive 0\) is deleted',
                 {'operation': 'unload_standby_program', 'source': 'program_memory_drive_0',
                  'result': 'program_deleted_and_program_number_empty'}),
    'S.RTREAD': (r'Reads data from transfer destination network number.*?using routing information set by the routing parameters',
                 {'operation': 'read_routing_information', 'network_number': 'n', 'destination': 'D',
                  'missing_routing_entry': 'all_zero'}),
    'S.RTWRITE': (r'Registers (?:the )?routing (?:data|information) .*?transfer destination network number',
                  {'operation': 'register_routing_information', 'network_number': 'n', 'source': 'S',
                   'existing_entry': 'replaced'}),
    'D.DDWR': (r'Writes data stored in the device \(d1\).*?host CPU.*?to the device \(s2\)',
               {'operation': 'write_other_CPU_devices', 'source': 'host_CPU_D1', 'destination': 'CPU_n_S2',
                'count': 'S1+1', 'completion': 'D2+0', 'completion_status': 'D2+1'}),
    'D.DDRD': (r'Stores data read from the device \(s2\).*?to the device \(d1\)',
               {'operation': 'read_other_CPU_devices', 'source': 'CPU_n_S2', 'destination': 'host_CPU_D1',
                'count': 'S1+1', 'completion': 'D2+0', 'completion_status': 'D2+1'}),
    'BSET': (r'Sets (?:\(sets "1" at\) the nth bit in the word device designated at \(D\)|\(to 1\) the nth bit of the word device specified for \(d\))',
             {'operation': 'set_word_bit', 'destination': 'D_word', 'bit_index': 'n_modulo_16', 'value': True}),
    'BRST': (r'Resets (?:the nth bit of a word device designated by \(D\) to 0|\(to 0\) the nth bit of the word device specified for \(d\))',
             {'operation': 'reset_word_bit', 'destination': 'D_word', 'bit_index': 'n_modulo_16', 'value': False}),
    'TEST': (r'Fetches bit data (?:at the location designated by \(S2\) within the word device designated by \(S1\)|from the location specified for \(s2\) within the word device specified for \(s1\))',
             {'operation': 'read_word_bit', 'source': 'S1_word', 'bit_index': 'S2_modulo_16', 'destination': 'D_bit'}),
    'DTEST': (r'Fetches bit data (?:at the location designated by \(S2\) within th e 2-word device designated by \(S1\)|from the location specified for \(s2\) within the 2-word device specified for \(s1\))',
              {'operation': 'read_double_word_bit', 'source': 'S1_double_word', 'bit_index': 'S2_modulo_32', 'destination': 'D_bit'}),
    'NDIS': (r'Dissociates data stored in device numbers starting from that designated at \(S1\)|Separates each bit of data stored in the device number specified for \(s1\)',
             {'operation': 'unpack_bit_groups', 'source': 'S1', 'group_lengths': 'S2', 'destination': 'D',
              'group_list_terminator': 0}),
    'FDEL': (r'Deletes the nth block of data from the data table (?:designated by \(D\)|specified for \(d\))',
             {'operation': 'delete_table_word', 'table': 'D', 'index': 'n', 'deleted_value_destination': 'S',
              'following_data': 'compact_forward_one_word'}),
    'FINS': (r'Inserts the 16-bit data designated by \(S\) at the nth block of the data table designated by \(D\)',
             {'operation': 'insert_table_word', 'table': 'D', 'index': 'n', 'source': 'S',
              'following_data': 'shift_back_one_word'}),
    'FROM': (r'Reads the data in n3 words from the buffer memory address (?:designated by|specified for) n2',
             {'operation': 'read_module_buffer_memory', 'module_IO_number': 'n1', 'buffer_address': 'n2',
              'count': 'n3', 'word_count_multiplier': 1, 'destination': 'D'}),
    'DFRO': (r'Reads the data in \(n3 \[GLYPH-(?:F0B4|F0C8)\] 2\) words from the buffer memory address',
             {'operation': 'read_module_buffer_memory', 'module_IO_number': 'n1', 'buffer_address': 'n2',
              'count': 'n3', 'word_count_multiplier': 2, 'destination': 'D'}),
    'TO': (r'Writes the data stored in n3 (?:points starting from the device designated by \(S\)|words following the device specified for \(s\))',
           {'operation': 'write_module_buffer_memory', 'module_IO_number': 'n1', 'buffer_address': 'n2',
            'count': 'n3', 'word_count_multiplier': 1, 'source': 'S'}),
    'DTO': (r'Writes the data stored in (?:n3 \[GLYPH-F0B4\] 2 points starting from the device designated by \(S\)|\(n3 \[GLYPH-F0C8\] 2\) words following the device specified for \(s\))',
            {'operation': 'write_module_buffer_memory', 'module_IO_number': 'n1', 'buffer_address': 'n2',
             'count': 'n3', 'word_count_multiplier': 2, 'source': 'S'}),
    'ZONE': (r'Adds bias value designated by \(S1\) or \(S2\) to input value designated by \(S3\)',
             {'operation': 'add_sign_dependent_bias', 'input': 'S3', 'destination': 'D', 'width_bits': 16,
              'when_negative': 'S3+S1', 'when_zero': 0, 'when_positive': 'S3+S2', 'overflow': 'wrap_signed_16_bit'}),
    'DZONE': (r'Adds bias value designated by \(\(S1\), \(S1\) \+1\)',
              {'operation': 'add_sign_dependent_bias', 'input': 'S3_double_word', 'destination': 'D_double_word', 'width_bits': 32,
               'when_negative': 'S3+S1', 'when_zero': 0, 'when_positive': 'S3+S2', 'overflow': 'wrap_signed_32_bit'}),
    '$MOV': (r'Transfers the character string data specified for \(s\) to the device specified for \(d\)',
             {'operation': 'copy_NULL_terminated_string', 'source': 'S', 'destination': 'D', 'overlap': 'supported'}),
    'WTOB': (r'Separates n bytes of the 16-bit data stored in the device specified for \(s\)',
             {'operation': 'unpack_word_bytes', 'source': 'S_words', 'destination': 'D_low_byte_words',
              'count': 'n_bytes', 'destination_upper_bytes': 0}),
    'BTOW': (r'(?:Connects|Combines) (?:n bytes|the lower 8 bits of the 16-bit data in n words).*?device specified for \(s\)',
             {'operation': 'pack_low_bytes_as_words', 'source': 'S_low_byte_words', 'destination': 'D_words',
              'count': 'n_bytes', 'source_upper_bytes': 'ignored', 'odd_count_final_upper_byte': 0}),
    'ZPUSH': (r'Saves the content of the following index registers to \(d\)',
              {'operation': 'push_index_registers', 'registers': 'CPU_model_index_register_set',
               'destination': 'D_stack', 'save_count': 'D+0_incremented'}),
    'ZPOP': (r'Recovers the contents saved in \(d\) and the following devices to the index register',
             {'operation': 'pop_index_registers', 'source': 'D_stack', 'registers': 'CPU_model_index_register_set',
              'save_count': 'D+0_decremented'}),
}


def _reviewed_xp_residual(section, symbol):
    declared_base = symbol
    for token in section.symbol_tokens:
        forms = expand_symbols([token])
        if symbol in forms:
            declared_base = forms[0]
            break
    canonical = re.sub(r'^(S|SP|D|DP)_', r'\1.', declared_base)
    for prefix in ('SP.', 'DP.'):
        if canonical.startswith(prefix):
            canonical = prefix[0] + '.' + canonical[len(prefix):]
            break
    base = (canonical[:-1] if canonical.endswith('P')
            and canonical[:-1] in _XP_RESIDUAL_OPERATIONS else canonical)
    text, title = ' '.join(section.text.split()), section.title
    pattern, value = _XP_RESIDUAL_OPERATIONS.get(base, ('', None))
    witnesses, specs = [], []
    if pattern:
        witness = _witness(text, (pattern,))
        if witness:
            value, witnesses = dict(value), [witness]
        else:
            value = None
    if base in {'CJ', 'SCJ', 'JMP'} and title == 'Pointer branch':
        patterns = {'CJ': r'Executes the program (?:specified by|of) the (?:specified )?pointer number[^.]*when the execution command is ON',
                    'SCJ': r'Executes the program (?:specified by|of) the (?:specified )?pointer number[^.]*'
                           r'(?:starting with the scan immediately after|from the next scan after)',
                    'JMP': r'Unconditionally executes (?:the )?program (?:of designated|of the specified) pointer number'}
        witness = _witness(' '.join(section.statements(symbol)), (patterns[base],))
        if witness:
            value = {'operation': 'jump_to_pointer_or_block_label', 'destination': 'specified_pointer',
                     'condition': 'unconditional' if base == 'JMP' else 'execution_command_on',
                     'effective_scan': 'scan_after_rising_command' if base == 'SCJ' else 'current_scan'}
            return [_reviewed_spec('pointer_jump', 'execution.jump', [witness], value,
                                   _section_languages(section, symbol))]
    # Same opcode can address a module or another CPU. The source section, not
    # its spelling alone, chooses the documented object and argument layout.
    if 'shared memory' in title.lower():
        write = 'Writing' in title
        pattern = (r'Writes (?:device data of words n3 to n4|\(s4\) words of device data|n3 words of device data)'
                   if write else r'Reads (?:n3 words of data|the data of n3 words) from the CPU shared memory address')
        witness = _witness(text, (pattern,))
        if witness:
            structured = section.kind == 'structured'
            value = {'operation': 'write_CPU_shared_memory' if write else 'read_CPU_shared_memory',
                     'CPU_selector': 'S1' if structured and canonical == 'S.TO' else 'n1',
                     'shared_memory_address': 'S2' if structured and canonical == 'S.TO' else 'n2',
                     'data': 'S3' if structured and canonical == 'S.TO' else
                             'n3' if not structured and canonical == 'S.TO' else 'S' if write else 'D',
                     'count': 'S4' if structured and canonical == 'S.TO' else
                              'n4' if not structured and canonical == 'S.TO' else 'n3',
                     'word_count_multiplier': 2 if canonical in {'DTO', 'DFRO'} else 1}
            if canonical == 'S.TO':
                value['completion'] = 'D_one_scan_on_completion'
            witnesses = [witness]
    elif base in {'S.DEVST', 'S.DEVLD'}:
        write = base == 'S.DEVST'
        pattern = r'Writes (?:device data for the number of points|n points of device data)' if write else r'Reads (?:device data for the number of points|n points of device data)'
        witness = _witness(text, (pattern,))
        if witness:
            structured = section.kind == 'structured'
            value = {'operation': 'write_standard_ROM_device_file' if write else 'read_standard_ROM_device_file',
                     'offset': 'S1' if structured and write else 'S' if structured else 'n1',
                     'count': 'n' if structured else 'n2', 'offset_unit': 'words',
                     'data': 'S2' if structured and write else 'S' if write else 'D'}
            witnesses = [witness]
    elif base in {'S.FWRITE', 'S.FREAD'}:
        write = base == 'S.FWRITE'
        pattern = (r'The (?:designated|specified) number of data is written to the (?:designated|specified) file'
                   if write else r'Data are read from the specified file|The (?:designated|specified) number of data is read from the (?:designated|specified) file')
        witness = _witness(text, (pattern,))
        if witness:
            value = {'operation': 'write_file_data' if write else 'read_file_data',
                     'file_name': 'S1', 'control_data': 'D0', 'data': 'S2' if write else 'D2',
                     'completion': 'D1', 'error_completion': 'D1+1',
                     'formats': ['binary', 'CSV_conversion']}
            witnesses = [witness]
    elif base == 'COM' or base == 'CCOM':
        if 'select' in title.lower():
            witness = _witness(text, (r'(?i)(?:select|selection).*?refresh',))
            if witness:
                value = {'operation': 'selected_refresh', 'object': 'configured_refresh_targets',
                         'result': 'refresh_during_program_execution'}
                witnesses = [witness]
        else:
            witness = _witness(text, (r'temporarily stops the processing of the sequence program',
                                      r'temporarily stops execution of the sequence program'))
            if witness:
                value = {'operation': 'communicate_and_auto_refresh', 'timing': 'at_COM_execution',
                         'SM775_off': 'communication_and_auto_refresh', 'SM775_on': 'communication_only',
                         'excluded': 'low_speed_cyclic_refresh'}
                witnesses = [witness]
    elif base in {'DATERD', 'S.DATERD'}:
        witness = _witness(text, (r'Reads ["\']?year, month, day, (?:hour, )?minute, second',))
        if witness:
            value = {'operation': 'read_CPU_clock', 'source': 'CPU_clock', 'destination': 'D_BIN_fields',
                     'field_order': ['year', 'month', 'day', 'hour', 'minute', 'second', 'weekday']
                                    + (['millisecond'] if base.startswith('S.') else [])}
            witnesses = [witness]
    elif base in {'S.DATE+', 'S.DATE-'}:
        add = base.endswith('+')
        witness = _witness(text, (r'Adds the clock data specified for \(s2\)',) if add else
                            (r'Subtracts the clock data specified for \(s2\)',))
        if witness:
            value = {'operation': 'add_extended_clock' if add else 'subtract_extended_clock',
                     'left': 'S1', 'right': 'S2', 'destination': 'D_extended_clock_data'}
            witnesses = [witness]
    elif canonical in {'DBK+', 'DBK+P', 'DBK-', 'DBK-P'}:
        subtract = '-' in canonical
        witness = _witness(' '.join(section.statements(symbol)),
                           (r'This instruction subtracts BIN 32-bit data',) if subtract else
                           (r'This instruction adds BIN 32-bit data',))
        if witness:
            value = {'operation': 'block_subtract' if subtract else 'block_add',
                     'left': 'S1', 'right': 'S2', 'destination': 'D', 'count': 'n', 'width_bits': 32}
            witnesses = [witness]
    elif base in {'DROR', 'DROL'}:
        direction = 'right' if base.endswith('R') else 'left'
        witness = _witness(text, (r'The 32-bit data .*?not including the carry flag, is rotated n-bits to the ' + direction,))
        if witness:
            value = {'operation': 'rotate_bits', 'destination': 'D_double_word', 'width_bits': 32,
                     'direction': direction, 'count': 'n_modulo_32', 'carry_in_rotation': False}
            witnesses = [witness]
    elif base in {'NUNI', 'NUNIP'}:
        witness = _witness(text, (r'Links individual bits of data stored into the area',
                                  r'Connects individual bits of data stored', r'Connects each bit of data stored'))
        if witness:
            value = {'operation': 'pack_bit_groups', 'source': 'S1', 'group_lengths': 'S2', 'destination': 'D'}
            witnesses = [witness]
    elif base in {'MEAN', 'MEANP'}:
        witness = _witness(text, (r'This instruction calculates the mean of 16-bit BIN data',))
        if witness:
            value = {'operation': 'mean_integer_block', 'source': 'S_n_words', 'destination': 'D',
                     'fraction': 'discarded', 'zero_n': 'not_processed'}
            witnesses = [witness]
    elif base in {'DSCL', 'DSCLP'}:
        witness = _witness(text, (r'This instruction executes scaling for the scaling conversion data \(32-bit data units\)',))
        if witness:
            value = {'operation': 'scale_by_coordinate_points', 'input': 'S1', 'coordinates': 'S2',
                     'destination': 'D', 'width_bits': 32}
            witnesses = [witness]
    elif base in {'TCMP', 'TZCP'}:
        witness = _witness(text, (r'The comparison target time .*?is compared',))
        if witness:
            band = base == 'TZCP'
            value = {'operation': 'compare_time_band' if band else 'compare_time',
                     'input': 'S3_clock_triplet' if band else 'S4_clock_triplet',
                     'reference': 'S1_lower_and_S2_upper_triplets' if band else 'S1_hour_S2_minute_S3_second',
                     'outputs': 'D_to_D+2', 'outcomes': ['below', 'within', 'above'] if band else ['less', 'equal', 'greater']}
            witnesses = [witness]
    elif base == 'EI':
        witness = _witness(text, (r'The EI instruction is used to clear the interrupt disable state',
                                  r'Releases the disable interrupt status of the DI instruction'))
        if witness:
            value = {'operation': 'enable_interrupt_programs', 'clears': 'DI_state', 'accepted_interrupts': 'IMASK_enabled_pointers'}
            witnesses = [witness]
    elif base in {'SET', 'RST', 'SET_F', 'RST_F'} and 'annunciator' in title.lower():
        on = base.startswith('SET')
        witness = _witness(text, (r'(?:The )?annunciator (?:specified for \(d\)|designated by \(D\)) is turned ' + ('ON' if on else 'OFF'),))
        if witness:
            value = {'operation': 'set_annunciator' if on else 'reset_annunciator',
                     'destination': 'D_annunciator', 'SD63': 'increment_if_registered' if on else 'decrement_if_removed',
                     'SD64_to_SD79': 'register_annunciator_number' if on else 'remove_and_compact',
                     'registered_number_capacity': 16}
            witnesses = [witness]
    elif base == 'TIMCHK':
        statement = _witness(text, (r'Measures the ON time .*?turns ON the device specified (?:by|for) \([sS]2\).*?device specified (?:by|for) \([dD]\)',))
        legend = _witness(text, (r'\(D\): Device to be turned ON at time-out',
                                 r'd Device to be turned ON at the end of the process'))
        if statement and legend:
            common = {'operation': 'check_ON_duration', 'current_value': 'S1', 'time_unit_ms': 100}
            for needle, preset, output in ((statement, 'D', 'S2'), (legend, 'S2', 'D')):
                item = _reviewed_spec('timchk_operand_roles', 'effects.operation', [needle],
                                      dict(common, preset=preset, timeout_output=output),
                                      _section_languages(section, symbol))
                # The prose swaps S2/D; the operand legend and program use the
                # opposite roles. Both source claims survive as a real conflict.
                item['shared_claim'] = True
                specs.append(item)
            return specs
    elif base == 'ORF' and section.manual == 'sh080809engx':
        witness = _witness(text, (r'ORF is a falling edge pulse serial connection instruction',))
        if witness:
            value = {'operation': 'compose_falling_edge_contact', 'input': 'S',
                     'connection': 'unknown', 'source_wording': 'serial_connection',
                     'declared_group': 'ORP_ORF_parallel_connection',
                     'source_disagreement': 'connection_wording_requires_resolution'}
            witnesses = [witness]
    elif base == 'S.CONTSW':
        witness = _witness(text, (r'Switches between the control system and standby system at the END processing',))
        if witness:
            value = {'operation': 'switch_redundant_system', 'timing': 'END_processing_of_execution_scan',
                     'argument': 'S', 'argument_storage': ['SD6', 'SD1602']}
            witnesses = [witness]
            prerequisite = _witness(text, (r'manual switching enable flag \(SM1592\).*?turned ON',))
            if prerequisite:
                specs.append(_reviewed_spec('switch_enable_required', 'constraints.context', [prerequisite],
                    {'context': 'redundant_system',
                     'requirements': [{'path': 'manual_switching_enabled', 'equals': True}],
                     'rule': 'SM1592_must_be_enabled_before_manual_system_switch'},
                    _section_languages(section, symbol)))
    elif re.fullmatch(r'S\.ZCOM_[JU]', base):
        witness = _witness(text, (r'conducts refresh processing of the network modules specified by Jn/Un',))
        if witness:
            value = {'operation': 'refresh_selected_module',
                     'selector': 'Jn_network_number' if base.endswith('_J') else 'Un_module_IO_number',
                     'timing': 'during_instruction_execution', 'result': 'module_refresh_completed'}
            witnesses = [witness]
    if value is not None and witnesses:
        specs.append(_reviewed_spec('residual_operation', 'effects.operation', witnesses, value,
                                   _section_languages(section, symbol)))
    return specs
