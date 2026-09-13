from collections import Counter

from tools.device_entity_cleanup import sanitize_device_like_entities


def test_lowercase_n_constraints_do_not_become_devices():
    text = 'N: Number of store points plus "1"*1 2 n 512 [16-bit binary]'
    entities = Counter({("N512", "device"): 1})
    cleaned = sanitize_device_like_entities(text, "instruction", entities)
    assert ("N512", "device") not in cleaned
    assert cleaned[("N512", "operand_placeholder")] == 1


def test_uppercase_n_nesting_levels_are_preserved_as_nesting_syntax():
    text = "MC N 4 M0\nMCR N4\nAvailable nesting levels are N0 to N7."
    entities = Counter({("N4", "device"): 2})
    cleaned = sanitize_device_like_entities(text, "instruction", entities)
    assert ("N4", "device") not in cleaned
    assert cleaned[("N4", "nesting_level")] >= 2


def test_instruction_step_count_is_not_data_register():
    text = "FNC 62\nABSD\n9 steps ABSD\nD 17 steps DABSD Continuous"
    entities = Counter({("D17", "device"): 1, ("ABSD", "instruction"): 1})
    cleaned = sanitize_device_like_entities(text, "instruction", entities)
    assert ("D17", "device") not in cleaned
    assert cleaned[("ABSD", "instruction")] == 1


def test_real_device_examples_are_not_removed():
    text = (
        "The display data is stored in D300 to D307.\n"
        "M500 to M599 are reset at one time.\n"
        "CJ P10\nCALL P11\nCALL P12"
    )
    entities = Counter(
        {
            ("D307", "device"): 1,
            ("M599", "device"): 1,
            ("P10", "device"): 1,
            ("P11", "device"): 1,
            ("P12", "device"): 1,
        }
    )
    cleaned = sanitize_device_like_entities(text, "instruction", entities)
    for token in ("D307", "M599", "P10", "P11", "P12"):
        assert cleaned[(token, "device")] == 1


def test_layout_only_n_fragment_is_dropped_not_promoted():
    text = "external fault\nN\n36 | END | is reset (set to OFF) in turn."
    entities = Counter({("N36", "device"): 1})
    cleaned = sanitize_device_like_entities(text, "instruction", entities)
    assert ("N36", "device") not in cleaned
    assert not any(entity == "N36" for entity, _kind in cleaned)
