"""Route exact-source pending reviews without changing default semantics.

Each specialist owns disjoint source identities and preserves its own source,
operand, form and context guards. Ambiguous ownership is a build error, never
an order-dependent choice of technical meaning.
"""
from tools.fact_fx5_file_backlog_review import review_auxiliary as file_review
from tools.fact_fx5_network_backlog_review import review_network_backlog_clause
from tools.fact_fx5_numeric_backlog_review import review_numeric_backlog_clause
from tools.fact_fx5_lifecycle_backlog_review import review_lifecycle_clause


def review_backlog_clause(kind, clause, *, section, block, value, declared):
    matches = []
    for reviewer in (file_review, review_network_backlog_clause,
                     review_numeric_backlog_clause, review_lifecycle_clause):
        review = reviewer(kind, clause, section=section, block=block,
                          value=value, declared=declared)
        if review is not None:
            matches.append(review)
    if len(matches) > 1:
        raise ValueError('Overlapping FX5 authored backlog review identities')
    return matches[0] if matches else None
