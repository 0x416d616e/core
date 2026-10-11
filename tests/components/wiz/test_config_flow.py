"""Test the WiZ Platform config flow."""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pywizlight import BulbType
from pywizlight.discovery import DiscoveredBulb
from pywizlight.exceptions import WizLightConnectionError, WizLightTimeOutError

from homeassistant import config_entries
from homeassistant.components.wiz.const import (
    CONF_LOCAL_INTEGRATION_FILE,
    CONF_REMOVE_SIGNING_KEY,
    CONF_SIGNING_KEY,
    DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from . import (
    FAKE_DIMMABLE_BULB,
    FAKE_EXTENDED_WHITE_RANGE,
    FAKE_IP,
    FAKE_MAC,
    FAKE_RGBW_BULB,
    FAKE_RGBWW_BULB,
    FAKE_SIGNING_KEY,
    FAKE_SOCKET,
    TEST_CONNECTION,
    TEST_SYSTEM_INFO,
    _mocked_wizlight,
    _patch_discovery,
    _patch_wizlight,
    async_setup_integration,
)

from tests.common import (
    MockConfigEntry,
    async_load_fixture,
    async_load_json_object_fixture,
)

DHCP_DISCOVERY = DhcpServiceInfo(
    hostname="wiz_abcabc",
    ip=FAKE_IP,
    macaddress=FAKE_MAC,
)


INTEGRATION_DISCOVERY = {
    "ip_address": FAKE_IP,
    "mac_address": FAKE_MAC,
}

NEW_IP = "1.1.1.2"
NEW_MAC = "a8bb50000001"
OTHER_IP = "1.1.1.3"
OTHER_MAC = "a8bb50000002"
LOCAL_INTEGRATION_FILE_ID = "6a5f6e2c-3f5e-4c51-8d1e-9a0f3c2b1d4e"
UNLISTED_IP = "1.1.1.4"
UNLISTED_MAC = "a8bb50000003"
LOCAL_INTEGRATION_FIXTURE = "local_integration.json"


async def _async_init_manual_flow(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a user flow and pick the manual setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )


async def test_form(hass: HomeAssistant) -> None:
    """Test we get the form."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)

    result = await _async_init_manual_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    # Patch functions
    with (
        _patch_wizlight(device=bulb),
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TEST_CONNECTION,
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "WiZ Dimmable White ABCABC"
    assert result2["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result2["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1
    bulb.async_close.assert_awaited_once()


async def test_user_flow_enters_dns_name(hass: HomeAssistant) -> None:
    """Test we reject dns names and want ips."""
    result = await _async_init_manual_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "ip.only"},
    )
    await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "no_ip"}

    with (
        _patch_wizlight(),
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            TEST_CONNECTION,
        )
        await hass.async_block_till_done()

    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["title"] == "WiZ Dimmable White ABCABC"
    assert result3["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("side_effect", "error_base"),
    [
        (WizLightTimeOutError, "bulb_time_out"),
        (WizLightConnectionError, "no_wiz_light"),
        (Exception, "unknown"),
        (ConnectionRefusedError, "cannot_connect"),
    ],
)
async def test_user_form_exceptions(
    hass: HomeAssistant, side_effect: type[Exception], error_base: str
) -> None:
    """Test all user exceptions in the flow."""
    result = await _async_init_manual_flow(hass)

    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.get_bulbtype = AsyncMock(side_effect=side_effect)

    with _patch_wizlight(device=bulb):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TEST_CONNECTION,
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": error_base}
    bulb.async_close.assert_awaited_once()

    with (
        _patch_wizlight(),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            TEST_CONNECTION,
        )
        await hass.async_block_till_done()

    assert result3["type"] is FlowResultType.CREATE_ENTRY


async def test_form_updates_unique_id(hass: HomeAssistant) -> None:
    """Test a duplicate id aborts and updates existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_SYSTEM_INFO["id"],
        data={CONF_HOST: "dummy"},
    )
    entry.add_to_hass(hass)

    result = await _async_init_manual_flow(hass)
    with _patch_discovery(), _patch_wizlight():
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            TEST_CONNECTION,
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == FAKE_IP
    assert entry.state is ConfigEntryState.LOADED


async def test_manual_reloads_entry_retrying_setup(hass: HomeAssistant) -> None:
    """Test entering a device that failed to set up retries it right away."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.getMac = AsyncMock(side_effect=WizLightTimeOutError)
    _, entry = await async_setup_integration(hass, wizlight=bulb)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    bulb.getMac = AsyncMock(return_value=FAKE_MAC)

    result = await _async_init_manual_flow(hass)
    with _patch_discovery(), _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("source", "data"),
    [
        (config_entries.SOURCE_DHCP, DHCP_DISCOVERY),
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, INTEGRATION_DISCOVERY),
    ],
)
async def test_discovered_by_dhcp_connection_fails(
    hass: HomeAssistant, source, data
) -> None:
    """Test we abort on connection failure."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.get_bulbtype = AsyncMock(side_effect=WizLightTimeOutError)

    with _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data=data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    bulb.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    ("source", "data", "bulb_type", "extended_white_range", "name"),
    [
        (
            config_entries.SOURCE_DHCP,
            DHCP_DISCOVERY,
            FAKE_DIMMABLE_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ Dimmable White ABCABC",
        ),
        (
            config_entries.SOURCE_INTEGRATION_DISCOVERY,
            INTEGRATION_DISCOVERY,
            FAKE_DIMMABLE_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ Dimmable White ABCABC",
        ),
        (
            config_entries.SOURCE_DHCP,
            DHCP_DISCOVERY,
            FAKE_RGBW_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ RGBW Tunable ABCABC",
        ),
        (
            config_entries.SOURCE_INTEGRATION_DISCOVERY,
            INTEGRATION_DISCOVERY,
            FAKE_RGBW_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ RGBW Tunable ABCABC",
        ),
        (
            config_entries.SOURCE_DHCP,
            DHCP_DISCOVERY,
            FAKE_RGBWW_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ RGBWW Tunable ABCABC",
        ),
        (
            config_entries.SOURCE_INTEGRATION_DISCOVERY,
            INTEGRATION_DISCOVERY,
            FAKE_RGBWW_BULB,
            FAKE_EXTENDED_WHITE_RANGE,
            "WiZ RGBWW Tunable ABCABC",
        ),
        (
            config_entries.SOURCE_DHCP,
            DHCP_DISCOVERY,
            FAKE_SOCKET,
            None,
            "WiZ Socket ABCABC",
        ),
        (
            config_entries.SOURCE_INTEGRATION_DISCOVERY,
            INTEGRATION_DISCOVERY,
            FAKE_SOCKET,
            None,
            "WiZ Socket ABCABC",
        ),
    ],
)
async def test_discovered_by_dhcp_or_integration_discovery(
    hass: HomeAssistant, source, data, bulb_type, extended_white_range, name
) -> None:
    """Test we can configure when discovered from dhcp or discovery."""
    bulb = _mocked_wizlight(None, extended_white_range, bulb_type)

    with _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data=data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    bulb.async_close.assert_awaited_once()

    bulb.async_close.reset_mock()

    with (
        _patch_wizlight(device=bulb),
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == name
    assert result2["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result2["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1
    bulb.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    ("source", "data"),
    [
        (config_entries.SOURCE_DHCP, DHCP_DISCOVERY),
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, INTEGRATION_DISCOVERY),
    ],
)
async def test_discovered_by_dhcp_or_integration_discovery_updates_host(
    hass: HomeAssistant, source, data
) -> None:
    """Test dhcp or discovery updates existing host."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_SYSTEM_INFO["id"],
        data={CONF_HOST: "dummy"},
    )
    entry.add_to_hass(hass)

    with _patch_wizlight():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data=data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == FAKE_IP


@pytest.mark.parametrize(
    ("source", "data"),
    [
        (config_entries.SOURCE_DHCP, DHCP_DISCOVERY),
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, INTEGRATION_DISCOVERY),
    ],
)
async def test_discovered_by_dhcp_or_integration_discovery_avoid_waiting_for_retry(
    hass: HomeAssistant, source, data
) -> None:
    """Test dhcp or discovery kicks off setup when in retry."""
    bulb = _mocked_wizlight(None, None, FAKE_SOCKET)
    bulb.getMac = AsyncMock(side_effect=OSError)
    _, entry = await async_setup_integration(hass, wizlight=bulb)
    assert entry.data[CONF_HOST] == FAKE_IP
    assert entry.state is ConfigEntryState.SETUP_RETRY
    bulb.getMac = AsyncMock(return_value=FAKE_MAC)

    with _patch_wizlight():
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data=data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.state is ConfigEntryState.LOADED


async def test_setup_via_discovery(hass: HomeAssistant) -> None:
    """Test setting up via discovery."""
    result = await _async_init_manual_flow(hass)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]

    with _patch_discovery():
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "pick_device"
    assert not result2["errors"]

    # test we can try again
    result = await _async_init_manual_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]

    with _patch_discovery():
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "pick_device"
    assert not result2["errors"]

    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)

    with (
        _patch_wizlight(device=bulb),
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
        patch(
            "homeassistant.components.wiz.async_setup_entry", return_value=True
        ) as mock_setup_entry,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: FAKE_MAC},
        )
        await hass.async_block_till_done()

    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["title"] == "WiZ Dimmable White ABCABC"
    assert result3["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result3["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1
    bulb.async_close.assert_awaited_once()

    # ignore configured devices
    result = await _async_init_manual_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]

    with _patch_discovery():
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "no_devices_found"


async def test_setup_via_discovery_cannot_connect(hass: HomeAssistant) -> None:
    """Test setting up via discovery and we fail to connect to the discovered device."""
    result = await _async_init_manual_flow(hass)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]

    with _patch_discovery():
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "pick_device"
    assert not result2["errors"]

    with (
        patch(
            "homeassistant.components.wiz.wizlight.getBulbConfig",
            side_effect=WizLightTimeOutError,
        ),
        _patch_discovery(),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_DEVICE: FAKE_MAC},
        )
        await hass.async_block_till_done()

    assert result3["type"] is FlowResultType.ABORT
    assert result3["reason"] == "cannot_connect"


async def test_setup_via_discovery_exception_finds_nothing(hass: HomeAssistant) -> None:
    """Test we do not find anything if discovery throws."""
    result = await _async_init_manual_flow(hass)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]

    with patch(
        "homeassistant.components.wiz.discovery.find_wizlights",
        side_effect=OSError,
    ):
        result2 = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "no_devices_found"


async def test_discovery_with_firmware_update(hass: HomeAssistant) -> None:
    """Test we recheck the device between discovery and entry creation."""
    with _patch_wizlight(
        device=None,
        extended_white_range=FAKE_EXTENDED_WHITE_RANGE,
        bulb_type=FAKE_RGBW_BULB,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data=INTEGRATION_DISCOVERY,
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"

    # In between discovery and when the user clicks to set it up the firmware
    # updates and we now can see its really RGBWW not RGBW since the older
    # firmwares did not tell us how many white channels exist

    with (
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
        _patch_wizlight(
            device=None,
            extended_white_range=FAKE_EXTENDED_WHITE_RANGE,
            bulb_type=FAKE_RGBWW_BULB,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {},
        )
        await hass.async_block_till_done()

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["title"] == "WiZ RGBWW Tunable ABCABC"
    assert result2["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result2["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.parametrize(
    ("source", "data"),
    [
        (config_entries.SOURCE_DHCP, DHCP_DISCOVERY),
        (config_entries.SOURCE_INTEGRATION_DISCOVERY, INTEGRATION_DISCOVERY),
    ],
)
async def test_discovered_during_onboarding(hass: HomeAssistant, source, data) -> None:
    """Test dhcp or discovery during onboarding creates the config entry."""
    with (
        _patch_wizlight(),
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup", return_value=True
        ) as mock_setup,
        patch(
            "homeassistant.components.onboarding.async_is_onboarded", return_value=False
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data=data
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WiZ Dimmable White ABCABC"
    assert result["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1


async def test_flow_replace_ignored_device(hass: HomeAssistant) -> None:
    """Test we can replace an ignored device via discovery."""
    # Add ignored entry to simulate previously ignored device
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FAKE_MAC,
        source=config_entries.SOURCE_IGNORE,
    )
    entry.add_to_hass(hass)
    # Patch discovery to find the same ignored device
    with _patch_discovery(), _patch_wizlight():
        result = await _async_init_manual_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"
    # Proceed with selecting the device — previously ignored
    with (
        _patch_wizlight(),
        patch(
            "homeassistant.components.wiz.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
        patch(
            "homeassistant.components.wiz.async_setup",
            return_value=True,
        ) as mock_setup,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_DEVICE: FAKE_MAC}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WiZ Dimmable White ABCABC"
    assert result["data"] == {
        CONF_HOST: "1.1.1.1",
    }
    assert result["result"].unique_id == FAKE_MAC
    assert len(mock_setup.mock_calls) == 1
    assert len(mock_setup_entry.mock_calls) == 1


def _patch_local_integration_file(tmp_path: Path, contents: str) -> Any:
    """Patch reading the uploaded local integration file."""
    file_path = tmp_path / "wiz.json"
    file_path.write_text(contents, encoding="utf-8")
    ctx_mock = MagicMock()
    ctx_mock.__enter__.return_value = file_path
    return patch(
        "homeassistant.components.wiz.config_flow.process_uploaded_file",
        return_value=ctx_mock,
    )


def _patch_discovered_devices(devices: list[DiscoveredBulb]) -> Any:
    """Patch the devices found by a network scan."""
    return patch(
        "homeassistant.components.wiz.discovery.find_wizlights", return_value=devices
    )


async def test_manual_with_signing_key(hass: HomeAssistant) -> None:
    """Test a device is set up with the signing key entered by the user."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    result = await _async_init_manual_flow(hass)

    with (
        patch(
            "homeassistant.components.wiz.config_flow.wizlight", return_value=bulb
        ) as mock_wizlight,
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: f" {FAKE_SIGNING_KEY.upper()} "},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == FAKE_MAC
    mock_wizlight.assert_called_once_with(FAKE_IP, signing_key=FAKE_SIGNING_KEY)


@pytest.mark.parametrize(
    "signing_key",
    [
        pytest.param("0011", id="too_short"),
        pytest.param("00" * 17, id="too_long"),
        pytest.param("zz" * 16, id="not_hex"),
    ],
)
async def test_manual_invalid_signing_key(
    hass: HomeAssistant, signing_key: str
) -> None:
    """Test an invalid signing key is rejected and the flow recovers."""
    result = await _async_init_manual_flow(hass)

    with _patch_wizlight():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: signing_key}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SIGNING_KEY: "invalid_signing_key"}

    with (
        _patch_wizlight(),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == FAKE_MAC


async def test_manual_pick_device_with_signing_key(hass: HomeAssistant) -> None:
    """Test a picked device is set up with the signing key entered by the user."""
    result = await _async_init_manual_flow(hass)

    with _patch_discovery():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick_device"

    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    with (
        patch(
            "homeassistant.components.wiz.config_flow.wizlight", return_value=bulb
        ) as mock_wizlight,
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: FAKE_MAC}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == FAKE_MAC
    mock_wizlight.assert_called_once_with(FAKE_IP, signing_key=FAKE_SIGNING_KEY)


async def test_manual_adds_signing_key_to_existing_entry(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test setting up a configured device again adds the signing key."""
    bulb, entry = await async_setup_integration(hass)
    result = await _async_init_manual_flow(hass)

    with _patch_discovery(), _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert entry.state is ConfigEntryState.LOADED
    assert "update listener" not in caplog.text


async def test_integration_discovery_with_signing_key(hass: HomeAssistant) -> None:
    """Test a device discovered with a signing key is set up with it."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    with patch(
        "homeassistant.components.wiz.config_flow.wizlight", return_value=bulb
    ) as mock_wizlight:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={**INTEGRATION_DISCOVERY, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    mock_wizlight.assert_called_once_with(FAKE_IP, signing_key=FAKE_SIGNING_KEY)

    with (
        _patch_wizlight(device=bulb),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == FAKE_MAC


async def test_integration_discovery_adds_signing_key(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test discovery with a signing key adds it to the configured device."""
    bulb, entry = await async_setup_integration(hass)

    with _patch_discovery(), _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={**INTEGRATION_DISCOVERY, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert entry.state is ConfigEntryState.LOADED
    assert "update listener" not in caplog.text


@pytest.mark.parametrize(
    ("entry_data", "user_input", "expected_data"),
    [
        pytest.param(
            TEST_CONNECTION,
            {CONF_HOST: NEW_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY.upper()},
            {CONF_HOST: NEW_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
            id="add_key",
        ),
        pytest.param(
            {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
            {CONF_HOST: NEW_IP},
            {CONF_HOST: NEW_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
            id="keep_key",
        ),
        pytest.param(
            {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
            {CONF_HOST: FAKE_IP, CONF_REMOVE_SIGNING_KEY: True},
            {CONF_HOST: FAKE_IP},
            id="remove_key",
        ),
    ],
)
async def test_reconfigure(
    hass: HomeAssistant,
    entry_data: dict[str, str],
    user_input: dict[str, Any],
    expected_data: dict[str, str],
) -> None:
    """Test reconfiguring the host and signing key of a loaded device."""
    bulb, entry = await async_setup_integration(hass, data=entry_data)
    assert entry.state is ConfigEntryState.LOADED

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with (
        _patch_discovery(),
        patch(
            "homeassistant.components.wiz.config_flow.wizlight", return_value=bulb
        ) as mock_flow_wizlight,
        patch("homeassistant.components.wiz.wizlight", return_value=bulb),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == expected_data
    assert entry.state is ConfigEntryState.LOADED
    mock_flow_wizlight.assert_called_once_with(
        expected_data[CONF_HOST], signing_key=expected_data.get(CONF_SIGNING_KEY)
    )


@pytest.mark.parametrize(
    ("entry_data", "offers_remove"),
    [
        pytest.param(TEST_CONNECTION, False, id="without_key"),
        pytest.param(
            {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
            True,
            id="with_key",
        ),
    ],
)
async def test_reconfigure_offers_removing_signing_key(
    hass: HomeAssistant, entry_data: dict[str, str], offers_remove: bool
) -> None:
    """Test removing the signing key is only offered when there is one."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=FAKE_MAC, data=entry_data)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert (CONF_REMOVE_SIGNING_KEY in result["data_schema"].schema) is offers_remove


async def test_reconfigure_remove_signing_key_with_new_key(
    hass: HomeAssistant,
) -> None:
    """Test entering a key while removing it is rejected and the flow recovers."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=FAKE_MAC,
        data={CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: FAKE_IP,
            CONF_SIGNING_KEY: "ff" * 16,
            CONF_REMOVE_SIGNING_KEY: True,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SIGNING_KEY: "signing_key_with_remove"}

    with _patch_discovery(), _patch_wizlight():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_REMOVE_SIGNING_KEY: True}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_HOST: FAKE_IP}


async def test_reconfigure_reloads_entry_not_loaded(hass: HomeAssistant) -> None:
    """Test reconfiguring a device that failed to set up reloads it."""
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.getMac = AsyncMock(side_effect=WizLightTimeOutError)
    _, entry = await async_setup_integration(hass, wizlight=bulb)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    bulb.getMac = AsyncMock(return_value=FAKE_MAC)

    result = await entry.start_reconfigure_flow(hass)
    with _patch_discovery(), _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("user_input", "side_effect", "errors"),
    [
        pytest.param(
            {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: "0011"},
            None,
            {CONF_SIGNING_KEY: "invalid_signing_key"},
            id="invalid_key",
        ),
        pytest.param(
            {CONF_HOST: "this is no IP input"},
            None,
            {CONF_HOST: "no_ip"},
            id="invalid_host",
        ),
        pytest.param(
            {CONF_HOST: FAKE_IP},
            WizLightTimeOutError,
            {"base": "cannot_connect"},
            id="cannot_connect",
        ),
    ],
)
async def test_reconfigure_errors(
    hass: HomeAssistant,
    user_input: dict[str, str],
    side_effect: type[Exception] | None,
    errors: dict[str, str],
) -> None:
    """Test reconfigure errors and recovery."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=FAKE_MAC, data=TEST_CONNECTION)
    entry.add_to_hass(hass)
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.getMac = AsyncMock(side_effect=side_effect, return_value=FAKE_MAC)

    result = await entry.start_reconfigure_flow(hass)
    with _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    bulb.getMac = AsyncMock(return_value=FAKE_MAC)
    with _patch_discovery(), _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}


async def test_reconfigure_different_device(hass: HomeAssistant) -> None:
    """Test reconfiguring to the address of another device aborts."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=FAKE_MAC, data=TEST_CONNECTION)
    entry.add_to_hass(hass)
    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.getMac = AsyncMock(return_value=NEW_MAC)

    result = await entry.start_reconfigure_flow(hass)
    with _patch_wizlight(device=bulb):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: NEW_IP}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "different_device"
    assert entry.data == TEST_CONNECTION
    bulb.async_close.assert_awaited_once()


async def test_local_integration(hass: HomeAssistant, tmp_path: Path) -> None:
    """Test importing a local integration export."""
    bulb, entry = await async_setup_integration(hass)
    ignored_entry = MockConfigEntry(
        domain=DOMAIN, unique_id=OTHER_MAC, source=config_entries.SOURCE_IGNORE
    )
    ignored_entry.add_to_hass(hass)
    other_home_entry = MockConfigEntry(
        domain=DOMAIN, unique_id="a8bb50000004", data={CONF_HOST: "1.1.1.5"}
    )
    other_home_entry.add_to_hass(hass)
    with _patch_wizlight(device=bulb):
        stale_flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={"ip_address": NEW_IP, "mac_address": NEW_MAC},
        )
        unlisted_flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_INTEGRATION_DISCOVERY},
            data={"ip_address": UNLISTED_IP, "mac_address": UNLISTED_MAC},
        )
    assert stale_flow["step_id"] == "discovery_confirm"
    assert unlisted_flow["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_integration"

    export = await async_load_fixture(hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN)
    with (
        _patch_local_integration_file(tmp_path, export),
        _patch_discovered_devices(
            [
                DiscoveredBulb(FAKE_IP, FAKE_MAC),
                DiscoveredBulb(NEW_IP, NEW_MAC),
                DiscoveredBulb(OTHER_IP, OTHER_MAC),
                DiscoveredBulb(UNLISTED_IP, UNLISTED_MAC),
            ]
        ),
        _patch_wizlight(device=bulb),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "local_integration_successful"
    assert result["description_placeholders"] == {"updated": "1", "discovered": "1"}
    assert entry.data == {CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert entry.state is ConfigEntryState.LOADED
    assert other_home_entry.data == {CONF_HOST: "1.1.1.5"}

    flows = {
        flow["context"]["unique_id"]: flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    }
    assert flows.keys() == {NEW_MAC, UNLISTED_MAC}
    assert flows[UNLISTED_MAC]["flow_id"] == unlisted_flow["flow_id"]
    assert flows[NEW_MAC]["flow_id"] != stale_flow["flow_id"]
    assert flows[NEW_MAC]["context"]["title_placeholders"] == {
        "name": "Desk lamp",
        "host": NEW_IP,
    }

    with (
        _patch_wizlight(device=bulb),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            flows[NEW_MAC]["flow_id"], {}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Desk lamp"
    assert result["data"] == {CONF_HOST: NEW_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == NEW_MAC


async def test_local_integration_replaces_flow_missed_by_scan(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test a pending discovery of a listed device the scan missed gets the key."""
    with _patch_wizlight():
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_DHCP},
            data=DhcpServiceInfo(
                hostname="wiz_000001", ip="10.0.0.5", macaddress=NEW_MAC
            ),
        )
    assert result["step_id"] == "discovery_confirm"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )
    export = await async_load_fixture(hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN)
    with (
        _patch_local_integration_file(tmp_path, export),
        _patch_discovered_devices([]),
        _patch_wizlight(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "local_integration_successful"
    assert result["description_placeholders"] == {"updated": "0", "discovered": "1"}
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_INTEGRATION_DISCOVERY

    with (
        _patch_wizlight(),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "10.0.0.5", CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == NEW_MAC


async def test_local_integration_skips_flow_still_connecting(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test a discovery that has not reached the device yet is left alone."""
    connected = asyncio.Event()

    async def _get_bulbtype() -> BulbType:
        await connected.wait()
        return FAKE_DIMMABLE_BULB

    bulb = _mocked_wizlight(None, None, FAKE_DIMMABLE_BULB)
    bulb.get_bulbtype = AsyncMock(side_effect=_get_bulbtype)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )
    export = await async_load_fixture(hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN)

    with (
        _patch_wizlight(device=bulb),
        _patch_local_integration_file(tmp_path, export),
        _patch_discovered_devices([]),
    ):
        init_task = hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_DHCP},
                data=DhcpServiceInfo(
                    hostname="wiz_000001", ip=NEW_IP, macaddress=NEW_MAC
                ),
            )
        )
        await asyncio.sleep(0)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        connected.set()
        discovery_result = await init_task

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
    assert discovery_result["type"] is FlowResultType.FORM
    assert discovery_result["step_id"] == "discovery_confirm"


async def test_local_integration_incomplete_devices(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test devices without a name get a generated one and without a MAC are skipped."""
    export = await async_load_json_object_fixture(
        hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN
    )
    for device in export["devices"]:
        device["name"] = None
        device["mac_address"] = device["mac_address"].upper()
    export["devices"].append({"device_id": 1, "type": "remote", "mac_address": None})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )

    with (
        _patch_local_integration_file(tmp_path, json.dumps(export)),
        _patch_discovered_devices([DiscoveredBulb(NEW_IP, NEW_MAC)]),
        _patch_wizlight(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "local_integration_successful"
    assert result["description_placeholders"] == {"updated": "0", "discovered": "1"}
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1

    with (
        _patch_wizlight(),
        patch("homeassistant.components.wiz.async_setup_entry", return_value=True),
        patch("homeassistant.components.wiz.async_setup", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WiZ Dimmable White 000001"
    assert result["data"] == {CONF_HOST: NEW_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    assert result["result"].unique_id == NEW_MAC


@pytest.mark.parametrize(
    "contents",
    [
        pytest.param("not json", id="not_json"),
        pytest.param("[]", id="not_an_object"),
        pytest.param(json.dumps({"devices": [{"mac_address": FAKE_MAC}]}), id="no_key"),
        pytest.param(
            json.dumps(
                {"udp_signing_key": "0011", "devices": [{"mac_address": FAKE_MAC}]}
            ),
            id="invalid_key",
        ),
        pytest.param(
            json.dumps({"udp_signing_key": FAKE_SIGNING_KEY}), id="no_devices"
        ),
        pytest.param(
            json.dumps({"udp_signing_key": FAKE_SIGNING_KEY, "devices": []}),
            id="empty_devices",
        ),
        pytest.param(
            json.dumps(
                {
                    "udp_signing_key": FAKE_SIGNING_KEY,
                    "devices": [{"mac_address": "ab:ca:bc:ab:ca:bc"}],
                }
            ),
            id="invalid_mac",
        ),
    ],
)
async def test_local_integration_invalid_file(
    hass: HomeAssistant, tmp_path: Path, contents: str
) -> None:
    """Test an invalid local integration export is rejected and the flow recovers."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )

    with _patch_local_integration_file(tmp_path, contents):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_local_integration_file"}

    export = await async_load_fixture(hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN)
    with (
        _patch_local_integration_file(tmp_path, export),
        _patch_discovered_devices([DiscoveredBulb(NEW_IP, NEW_MAC)]),
        _patch_wizlight(),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "local_integration_successful"
    assert result["description_placeholders"] == {"updated": "0", "discovered": "1"}


async def test_local_integration_no_devices(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Test importing an export without configured or discovered devices."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "local_integration"}
    )

    export = await async_load_fixture(hass, LOCAL_INTEGRATION_FIXTURE, DOMAIN)
    with (
        _patch_local_integration_file(tmp_path, export),
        _patch_discovered_devices([DiscoveredBulb(UNLISTED_IP, UNLISTED_MAC)]),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_LOCAL_INTEGRATION_FILE: LOCAL_INTEGRATION_FILE_ID},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
