"""Model-specific device limits and reserved/read-only special devices."""
import json


def _source_device_profiles():
    from shared.paths import resource_path
    models = json.loads(resource_path("plc_models.json").read_text(encoding="utf-8"))
    return {cpu: model["validation"] for cpu, model in models.items()
            if isinstance(model, dict) and model.get("validation", {}).get("status") in {
                "source_subset", "source_verified"}}


SOURCE_DEVICE_PROFILES = _source_device_profiles()
SUPPORTED_PLC_MODELS = {"FX5U", *SOURCE_DEVICE_PROFILES}

# These are software limits, not a promise that every address is physically
# installed.  X/Y on FX5U are configuration-dependent, so only their decimal
# syntax is enforced here.  SM/SD 8000-series compatibility devices are valid
# on FX5U even though the native special-device range is also exposed at 0-2047.
DEVICE_LIMITS = {
    "FX5U": {
        # The physical allocation remains configuration-dependent. 1777 is
        # the decimal device-number ceiling used by the confirmed-spec and
        # C++ hardware contracts; accepting the namespace here is not a claim
        # that a particular installed rack exposes every point.
        "X": 1777,
        "Y": 1777,
        "M": 7679,
        "SM": 8999,
        "D": 7999,
        "SD": 8999,
        "T": 1023,
        "C": 1023,
        "S": 4095,
    },
}
DEVICE_LIMITS.update({cpu: p["device_limits"] for cpu, p in SOURCE_DEVICE_PROFILES.items()})


def program_pointer_domain(plc_model):
    """A sourced CPU P-number domain, separate from memory and allocation.

    The existing model validation resource owns every numeric value. A merged
    menu resolves through the identity catalogue and requires agreement from
    all members; a missing or wrongly bound source grants no numeric domain.
    """
    import copy
    from plc.target_identity import target_identity_state, target_family
    members, reason = target_identity_state(plc_model)
    if reason is not None or not members or target_family(plc_model) not in {'Q', 'L'}:
        return None
    domains = []
    for member in members:
        profile = SOURCE_DEVICE_PROFILES.get(member, {})
        domain = profile.get('program_pointer_domain')
        if (not isinstance(domain, dict) or domain.get('status') != 'source_verified'
                or domain.get('kind') != 'program_pointer_identity' or domain.get('prefix') != 'P'
                or type(domain.get('radix')) is not int or domain['radix'] != 10
                or type(domain.get('minimum_number')) is not int or domain['minimum_number'] != 0
                or type(domain.get('maximum_number')) is not int or domain['maximum_number'] < 0
                or domain.get('allocation_verified') is not False):
            return None
        sources = domain.get('sources')
        model_manuals = {(s.get('manual_id'), s.get('revision')) for s in profile.get('sources', [])
                         if s.get('target_model') == member and member in s.get('covered_models', [])}
        if (not isinstance(sources, list) or not sources or any(
                not isinstance(s, dict) or (s.get('manual_id'), s.get('revision')) not in model_manuals
                or s.get('target_model') != member or s.get('covered_models') != [member]
                or s.get('conditions') or s.get('applicability')
                or type(s.get('pdf_page')) is not int or s['pdf_page'] < 1 for s in sources)):
            return None
        domains.append(domain)
    if len({(d['minimum_number'], d['maximum_number']) for d in domains}) != 1:
        return None
    result = copy.deepcopy(domains[0])
    result['sources'] = [copy.deepcopy(s) for d in domains for s in d['sources']]
    return result


def native_word_value_layout(prefix, number, plc_model):
    """Width of one valid device's numeric value, separate from its contact."""
    from plc.target_identity import target_identity_state
    members, reason = target_identity_state(plc_model)
    if reason is not None or not members:
        return None
    layouts = []
    for member in members:
        profile = SOURCE_DEVICE_PROFILES.get(member)
        if profile is None:
            return None
        regions = profile.get('word_value_regions', {}).get(prefix, [])
        matches = [r for r in regions if r['minimum'] <= number <= r['maximum']]
        if len(matches) > 1:
            return None
        layouts.append({k: matches[0][k] for k in ('bits', 'minimum_operand_bits') if k in matches[0]}
                       if matches else {'bits': 16})
    return layouts[0] if all(layout == layouts[0] for layout in layouts) else None


def native_word_value_bits(prefix, number, plc_model):
    layout = native_word_value_layout(prefix, number, plc_model)
    return layout['bits'] if layout else None


def device_address_rules(plc_model):
    """The same numbering rules for checking, retrieval and generation.

    Numbering limits, default allocations and native-operation limits are
    different fields. Only numbering facts belong in a generated program's
    language context; no native capability is inferred from them.
    """
    import copy
    from plc.target_support import normalize_target_cpu
    model = normalize_target_cpu(plc_model)
    profile = SOURCE_DEVICE_PROFILES.get(model, {})
    limits = DEVICE_LIMITS.get(model)
    pointer = program_pointer_domain(plc_model)
    pointer_rules = {'program_pointer_domain': pointer} if pointer is not None else {}
    if pointer is not None and profile.get('program_pointer_auto_assignment'):
        pointer_rules['program_pointer_auto_assignment'] = copy.deepcopy(profile['program_pointer_auto_assignment'])
    if limits is None:
        return {'cpu': model, 'status': 'unavailable', 'families': {}, **pointer_rules}
    bits = profile.get('bit_prefixes', ['X', 'Y', 'M', 'SM', 'S', 'T', 'C'])
    families = {}
    for prefix, maximum in limits.items():
        radix = profile.get('radices', {}).get(prefix, 8 if model == 'FX3U' and prefix in {'X', 'Y'} else 10)
        families[prefix] = dict(radix=radix, maximum_number=maximum,
            regions=copy.deepcopy(profile.get('device_regions', {}).get(prefix, [[0, maximum]])),
            value_type='bit' if prefix in bits else 'word',
            word_bit_view=prefix in profile.get('word_bit_prefixes', []),
            allocation=copy.deepcopy(profile.get('allocation_rules', {}).get(prefix, {'kind': 'fixed_numbering'})))
        if prefix in profile.get('word_value_regions', {}):
            families[prefix]['word_value_regions'] = copy.deepcopy(profile['word_value_regions'][prefix])
    return {'cpu': model, 'status': 'available', 'families': families, **pointer_rules,
            'default_allocations': copy.deepcopy(profile.get('default_allocations', {})),
            'special_regions': copy.deepcopy(profile.get('special_regions', {})),
            'special_access': copy.deepcopy(profile.get('special_access', {})),
            'allocation_constraints': copy.deepcopy(profile.get('allocation_constraints', {})),
            'model_conditions': copy.deepcopy(profile.get('model_conditions', [])),
            'source_conflicts': copy.deepcopy(profile.get('source_conflicts', [])),
            'sources': copy.deepcopy(profile.get('sources', [])),
            'meaning': 'Address numbering; physical I/O installation and project allocation are separate conditions.'}


# CPU-owned special relays may be read as contacts, but the user program must
# not drive them.  Keep the profiles model-specific: FX3U uses M8000-series
# devices, while FX5U uses native/compatible SM devices.  The FX3U entries
# cover the read-only flags documented by the bundled model reference; the
# additional positioning-axis entries inherit the documented Y0 meaning for
# Y1-Y3 (M835x-M837x).
READ_ONLY_SPECIAL_DEVICE_NUMBERS = {
    "FX3U": {
        "M": frozenset(
            {
                8018, 8019, 8020, 8021, 8022, 8029,
                8041, 8042, 8046, 8048,
                8060, 8061, 8063, 8064, 8065, 8066, 8067,
                8072, 8073,
                8105, 8107, 8109,
                # M8123 is deliberately excluded: the RS receive-complete
                # flag is cleared by the user program after processing.
                8121, 8124, 8126, 8127,
                8131, 8133, 8138, 8139,
                8304, 8306,
                8316, 8318, 8328, 8329,
                8340, 8348, 8350, 8358, 8360, 8368, 8370, 8378,
                # M8403/M8423 are likewise program-reset receive-complete
                # flags for RS2, not immutable status contacts.
                8401, 8404, 8405,
                8421, 8424, 8425, 8426, 8427,
            }
        ),
    },
    "FX5U": {
        "SM": frozenset(
            {
                # Native iQ-F status devices.
                0, 1, 51, 53, 56,
                400, 401, 402, 403,
                409, 410, 411, 412, 413, 414,
                # FX-compatible aliases documented by the model profile.
                8000, 8001, 8002, 8003,
                8011, 8012, 8013, 8014,
                8029, 8340, 8348,
            }
        ),
    },
}

READ_ONLY_SPECIAL_DEVICE_RANGES = {
    "FX3U": {
        "M": (
            (8000, 8009),
            (8011, 8014),
            (8151, 8154),
            (8156, 8159),
            (8183, 8191),
            (8246, 8255),
            (8330, 8334),
        ),
    },
    "FX5U": {},
}

# FX3U M8000-M8511 is a CPU-owned special-device area.  Numbers that the
# bundled Mitsubishi reference marks as reserved/unavailable are invalid for
# both reads and writes, not merely read-only.  Keep this aligned with the
# confirmed-spec validator so a draft cannot pass one layer and fail another.
RESERVED_SPECIAL_DEVICE_RANGES = {
    "FX3U": {
        "M": (
            (8010, 8010),
            (8023, 8023),
            (8080, 8089),
            (8100, 8103),
            (8112, 8120),
            (8140, 8149),
            (8256, 8259),
            (8300, 8303),
            (8305, 8305),
            (8307, 8315),
            (8317, 8317),
            (8319, 8327),
            (8335, 8335),
            (8337, 8337),
            (8339, 8339),
            (8396, 8397),
            (8399, 8400),
            (8406, 8408),
            (8410, 8420),
            (8430, 8437),
            (8439, 8448),
            (8450, 8459),
            (8468, 8511),
        ),
    },
    "FX5U": {},
}
