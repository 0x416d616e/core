"""Test WiZ diagnostics."""

from syrupy.assertion import SnapshotAssertion

from homeassistant.components.wiz.const import CONF_SIGNING_KEY
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import FAKE_IP, FAKE_SIGNING_KEY, async_setup_integration

from tests.components.diagnostics import get_diagnostics_for_config_entry
from tests.typing import ClientSessionGenerator


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    snapshot: SnapshotAssertion,
) -> None:
    """Test generating diagnostics for a config entry."""
    _, entry = await async_setup_integration(
        hass, data={CONF_HOST: FAKE_IP, CONF_SIGNING_KEY: FAKE_SIGNING_KEY}
    )

    assert await get_diagnostics_for_config_entry(hass, hass_client, entry) == snapshot
