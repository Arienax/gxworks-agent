# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

"""One recursive argument validator for all registered tools."""
from collections.abc import Mapping
import math
import re


def validate_schema(rule, value, path='arguments'):
    """Validate the shared tool JSON Schema subset, including nested operations."""
    if not rule:
        return
    if 'oneOf' in rule:
        matches = 0
        for option in rule['oneOf']:
            try:
                validate_schema(option, value, path)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(path + ' must match one operation or value structure')
    types = {'object': lambda x: isinstance(x, Mapping), 'array': lambda x: isinstance(x, list),
             'string': lambda x: isinstance(x, str), 'integer': lambda x: type(x) is int,
             'boolean': lambda x: type(x) is bool, 'null': lambda x: x is None,
             'number': lambda x: type(x) in (int, float) and math.isfinite(x)}
    expected = rule.get('type')
    if isinstance(expected, list):
        if not any(kind in types and types[kind](value) for kind in expected):
            raise ValueError(path + ' must be one of ' + ', '.join(expected))
    elif expected in types and not types[expected](value):
        raise ValueError(path + ' must be ' + expected)
    if 'enum' in rule and not any(value == item and isinstance(value, bool) == isinstance(item, bool) for item in rule['enum']):
        raise ValueError(path + ' is not an allowed value')
    if isinstance(value, Mapping):
        fields = rule.get('properties', {})
        if not set(rule.get('required', ())) <= value.keys():
            raise ValueError(path + ' is missing required fields')
        if not rule.get('minProperties', 0) <= len(value) <= rule.get('maxProperties', float('inf')):
            raise ValueError(path + ' has an invalid field count')
        for key, item in value.items():
            if 'propertyNames' in rule:
                validate_schema(rule['propertyNames'], key, path + '.key')
            additional = rule.get('additionalProperties', True)
            if key not in fields and additional is False:
                raise ValueError(path + ' has an unknown field: ' + str(key))
            child = fields.get(key, additional if isinstance(additional, dict) else {})
            validate_schema(child, item, path + '.' + str(key))
    elif isinstance(value, list):
        if not rule.get('minItems', 0) <= len(value) <= rule.get('maxItems', float('inf')):
            raise ValueError(path + ' has an invalid item count')
        if 'items' in rule:
            for index, item in enumerate(value):
                validate_schema(rule['items'], item, path + '[' + str(index) + ']')
    elif isinstance(value, str):
        if not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', float('inf')):
            raise ValueError(path + ' has an invalid text length')
        if 'pattern' in rule and not re.fullmatch(rule['pattern'], value):
            raise ValueError(path + ' has invalid text syntax')
    elif type(value) in (int, float) and not rule.get('minimum', -float('inf')) <= value <= rule.get('maximum', float('inf')):
        raise ValueError(path + ' has an out-of-range number')
