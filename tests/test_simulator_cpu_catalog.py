# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

import pytest

from gxworks2.project_profiles import NATIVE_PROJECT_PROFILES
from plc.device_policy import DevicePolicyError, native_simulator_profile, simulator_cpu_is_running


@pytest.mark.parametrize(
    ("model", "menu", "cpu_type", "model_code", "run_device"),
    [
        ("FX0N", "FX0N", 0x0202, 0x8E, "M8000"),
        ("FX1S", "FX1S", 0x0206, 0xF2, "M8000"),
        ("FX1NC", "FX1N/FX1NC", 0x0207, 0x9E, "M8000"),
        ("FX2C", "FXU/FX2C", 0x0204, 0x8D, "M8000"),
        ("FX2NC", "FX2N/FX2NC", 0x0205, 0x9D, "M8000"),
        ("Q00J", "Q00J", 0x30, 0x0250, "SD203"),
        ("Q02H", "Q02/Q02H", 0x22, 0x0041, "SD203"),
        ("Q02PH", "Q02PH", 0x45, 0x0041, "SD203"),
        ("Q03UD", "Q03UD", 0x70, 0x0268, "SD203"),
        ("Q03UDE", "Q03UDE", 0x90, 0x0268, "SD203"),
        ("Q04UDV", "Q04UDV", 0xD2, 0x0367, "SD203"),
        ("Q04UDPV", "Q04UDPV", 0x47, 0x0367, "SD203"),
        ("Q25PRH", "Q25PRH", 0x44, 0x004C, "SD203"),
        ("Q100UDEH", "Q100UDEH", 0x9A, 0x026E, "SD203"),
        ("L02S-P", "L02S/L02S-P", 0xA3, 0x0543, "SD203"),
        ("L26-PBT", "L26-BT/L26-PBT", 0xA2, 0x0542, "SD203"),
    ],
)
def test_simulator_profile_uses_documented_setting_and_returned_code(
    model, menu, cpu_type, model_code, run_device
):
    profile = native_simulator_profile(model)
    assert profile["cpu_menu"] == menu
    assert profile["cpu_type"] == cpu_type
    assert profile["model_code"] == model_code
    assert profile["device"] == run_device


def test_simulator_catalog_covers_source_bound_programmable_menu_members():
    unmapped = {"FX0S/FX0", "FX1"}
    for project_profile in NATIVE_PROJECT_PROFILES:
        menu = project_profile.cpu
        if not project_profile.program_supported or menu in unmapped:
            with pytest.raises(DevicePolicyError):
                native_simulator_profile(menu)
            continue

        profile = native_simulator_profile(menu)
        assert profile["cpu_menu"] == menu
        assert profile["unit_type"] == 0x30
        assert profile["target_simulator"] == 0
        for member in menu.split("/"):
            assert native_simulator_profile(member) == profile


@pytest.mark.parametrize("model", ["FX0N", "FX1S", "FX1NC", "FX2C", "FX2NC"])
def test_legacy_fx_simulator_run_and_error_devices(model):
    profile = native_simulator_profile(model)
    assert profile["read_devices"] == {"M8000": "bit", "M8067": "bit", "D8067": "word"}
    assert simulator_cpu_is_running(1, model)
    assert not simulator_cpu_is_running(0, model)


@pytest.mark.parametrize("model", ["Q00J", "Q02PH", "Q25PRH", "Q100UDEH", "L26-PBT"])
def test_q_and_l_simulator_run_uses_cpu_status_nibble(model):
    profile = native_simulator_profile(model)
    assert profile["read_devices"] == {"SD203": "word", "SM0": "bit", "SD0": "word"}
    assert simulator_cpu_is_running(0, model)
    assert simulator_cpu_is_running(0x10, model)
    assert not simulator_cpu_is_running(2, model)
    assert not simulator_cpu_is_running(3, model)
