"""WiZ utils."""

from dataclasses import dataclass
import re
from typing import Any

import probatio
from pywizlight import BulbType
from pywizlight.bulblibrary import BulbClass

from .const import DEFAULT_NAME

SIGNING_KEY_RE = re.compile(r"[0-9a-f]{32}")
MAC_RE = re.compile(r"[0-9a-f]{12}")


LOCAL_INTEGRATION_EXPORT_SCHEMA = probatio.Schema(
    {
        probatio.Required("udp_signing_key"): probatio.All(
            str, probatio.Strip, probatio.Lower, probatio.Match(r"[0-9a-f]{32}\Z")
        ),
        probatio.Required("devices"): [dict],
    },
    extra=probatio.ALLOW_EXTRA,
)


@dataclass(frozen=True)
class LocalIntegrationExport:
    """Home exported by the local integrations page of the WiZ app."""

    signing_key: str
    # Device names by MAC address
    devices: dict[str, str | None]


def _short_mac(mac: str) -> str:
    """Get the short mac address from the full mac."""
    return mac.replace(":", "").upper()[-6:]


def name_from_bulb_type_and_mac(bulb_type: BulbType, mac: str) -> str:
    """Generate a name from bulb_type and mac."""
    if bulb_type.bulb_type == BulbClass.RGB:
        if bulb_type.white_channels == 2:
            description = "RGBWW Tunable"
        else:
            description = "RGBW Tunable"
    else:
        description = bulb_type.bulb_type.value
    return f"{DEFAULT_NAME} {description} {_short_mac(mac)}"


def normalize_signing_key(signing_key: str) -> str | None:
    """Return the signing key in lowercase, or None if it is not 32 hex digits."""
    signing_key = signing_key.strip().lower()
    if SIGNING_KEY_RE.fullmatch(signing_key) is None:
        return None
    return signing_key


def parse_local_integration_export(data: Any) -> LocalIntegrationExport:
    """Parse a home exported by the local integrations page of the WiZ app."""
    home = LOCAL_INTEGRATION_EXPORT_SCHEMA(data)
    devices: dict[str, str | None] = {}
    for device in home["devices"]:
        mac = device.get("mac_address")
        # Skip entries that cannot be matched to a device on the network
        if not isinstance(mac, str) or not MAC_RE.fullmatch(mac := mac.strip().lower()):
            continue
        name = device.get("name")
        devices[mac] = name if isinstance(name, str) and name else None
    if not devices:
        raise ValueError("No devices with a MAC address in the export")
    return LocalIntegrationExport(signing_key=home["udp_signing_key"], devices=devices)
