"""Case-derived vocabulary expands recall without assigning implementations."""
import json
import re
from functools import lru_cache

from shared.paths import resource_path


@lru_cache(maxsize=1)
def entries():
    return json.loads(resource_path('knowledge/retrieval_vocabulary.json').read_text(encoding='utf-8'))['entries']


def matching_entries(query):
    text = str(query)
    return [entry for entry in entries() if any(
        re.search(r'(?<![A-Za-z0-9_])' + re.escape(phrase) + r'(?![A-Za-z0-9_])', text, re.I)
        if phrase.isascii() else phrase in text for phrase in entry['phrases'])]


def expand_query(query):
    expansions = dict.fromkeys(entry['terms'] for entry in matching_entries(query))
    return ' '.join([str(query), *expansions])


def purpose_terms(function):
    return sorted({term for entry in entries() if entry['function'] == function
                   for term in entry['terms'].split()})


def resource_queries(function):
    return list(dict.fromkeys(query for entry in entries() if entry['function'] == function
                             for query in entry.get('resource_queries', ())))
