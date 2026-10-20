"""Model-specific device limits and reserved/read-only special devices."""
import json


def _source_device_profiles():
    from shared.paths import resource_path
    models = json.loads(resource_path("plc_models.json").read_text(encoding="utf-8"))
    return {cpu: model["validation"] for cpu, model in models.items()
            if isinstance(model, dict) and model.get("validation", {}).get("status") == "source_subset"}


SOURCE_DEVICE_PROFILES = _source_device_profiles()
SUPPORTED_PLC_MODELS = {"FX3U", "FX5U", *SOURCE_DEVICE_PROFILES}

# These are software limits, not a promise that every address is physically
# installed.  X/Y on FX5U are configuration-dependent, so only their decimal
# syntax is enforced here.  SM/SD 8000-series compatibility devices are valid
# on FX5U even though the native special-device range is also exposed at 0-2047.
DEVICE_LIMITS = {
    "FX3U": {
        "X": 0o367,
        "Y": 0o367,
        "M": 8511,
        "D": 8511,
        "T": 511,
        "C": 255,
        "S": 4095,
        # FX3U index registers. V0-V7 are 16-bit index registers; Z0-Z7
        # are accepted as index registers and may also be used as ordinary
        # word operands. Indexed operands such as D100Z0 are validated below.
        "V": 7,
        "Z": 7,
    },
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
