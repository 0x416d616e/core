"""Config flow for WiZ Platform."""

from dataclasses import asdict
import logging
from typing import Any, override

import probatio
from pywizlight import wizlight
from pywizlight.discovery import DiscoveredBulb
from pywizlight.exceptions import WizLightConnectionError, WizLightTimeOutError

from homeassistant.components import onboarding
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_DEVICE, CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.selector import (
    FileSelector,
    FileSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.util.json import json_loads_object
from homeassistant.util.network import is_ip_address

from .const import (
    CONF_LOCAL_INTEGRATION_FILE,
    CONF_REMOVE_SIGNING_KEY,
    CONF_SIGNING_KEY,
    DEFAULT_NAME,
    DISCOVER_SCAN_TIMEOUT,
    DOMAIN,
    WIZ_CONNECT_EXCEPTIONS,
)
from .coordinator import WizConfigEntry
from .discovery import async_discover_devices
from .utils import (
    LocalIntegrationExport,
    _short_mac,
    name_from_bulb_type_and_mac,
    normalize_signing_key,
    parse_local_integration_export,
)

_LOGGER = logging.getLogger(__name__)

SIGNING_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class WizConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WiZ."""

    VERSION = 1

    _discovered_device: DiscoveredBulb
    _name: str

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_devices: dict[str, DiscoveredBulb] = {}
        self._signing_key: str | None = None
        self._device_name: str | None = None

    def _entry_data(self, host: str) -> dict[str, str]:
        """Return the config entry data for a device at host."""
        if self._signing_key is None:
            return {CONF_HOST: host}
        return {CONF_HOST: host, CONF_SIGNING_KEY: self._signing_key}

    @override
    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Handle discovery via dhcp."""
        self._discovered_device = DiscoveredBulb(
            discovery_info.ip, discovery_info.macaddress
        )
        return await self._async_handle_discovery()

    @override
    async def async_step_integration_discovery(
        self, discovery_info: dict[str, str]
    ) -> ConfigFlowResult:
        """Handle integration discovery."""
        self._discovered_device = DiscoveredBulb(
            discovery_info["ip_address"], discovery_info["mac_address"]
        )
        self._signing_key = discovery_info.get(CONF_SIGNING_KEY)
        self._device_name = discovery_info.get(CONF_NAME)
        return await self._async_handle_discovery()

    async def _async_handle_discovery(self) -> ConfigFlowResult:
        """Handle any discovery."""
        device = self._discovered_device
        _LOGGER.debug("Discovered device: %s", device)
        ip_address = device.ip_address
        mac = device.mac_address
        await self.async_set_unique_id(mac)
        # Loaded entries are reloaded by their update listener
        self._abort_if_unique_id_configured(
            updates=self._entry_data(ip_address), reload_on_update=False
        )
        await self._async_connect_discovered_or_abort()
        return await self.async_step_discovery_confirm()

    async def _async_connect_discovered_or_abort(self) -> None:
        """Connect to the device and verify its responding."""
        device = self._discovered_device
        bulb = wizlight(device.ip_address, signing_key=self._signing_key)
        try:
            bulbtype = await bulb.get_bulbtype()
        except WIZ_CONNECT_EXCEPTIONS as ex:
            _LOGGER.debug(
                "Failed to connect to %s during discovery: %s",
                device.ip_address,
                ex,
                exc_info=True,
            )
            raise AbortFlow("cannot_connect") from ex
        finally:
            await bulb.async_close()
        self._name = self._device_name or name_from_bulb_type_and_mac(
            bulbtype, device.mac_address
        )

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        ip_address = self._discovered_device.ip_address
        if user_input is not None or not onboarding.async_is_onboarded(self.hass):
            # Make sure the device is still there and
            # update the name if the firmware has auto
            # updated since discovery
            await self._async_connect_discovered_or_abort()
            return self.async_create_entry(
                title=self._name,
                data=self._entry_data(ip_address),
            )

        self._set_confirm_only()
        placeholders = {"name": self._name, "host": ip_address}
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders=placeholders,
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the step to pick discovered device."""
        if user_input is not None:
            device = self._discovered_devices[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(device.mac_address, raise_on_progress=False)
            bulb = wizlight(device.ip_address, signing_key=self._signing_key)
            try:
                bulbtype = await bulb.get_bulbtype()
            except WIZ_CONNECT_EXCEPTIONS:
                return self.async_abort(reason="cannot_connect")
            finally:
                await bulb.async_close()

            return self.async_create_entry(
                title=name_from_bulb_type_and_mac(bulbtype, device.mac_address),
                data=self._entry_data(device.ip_address),
            )

        current_unique_ids = self._async_current_ids(include_ignore=False)
        current_hosts = {
            entry.data[CONF_HOST]
            for entry in self._async_current_entries(include_ignore=False)
        }
        discovered_devices = await async_discover_devices(
            self.hass, DISCOVER_SCAN_TIMEOUT
        )
        self._discovered_devices = {
            device.mac_address: device for device in discovered_devices
        }
        devices_name = {
            mac: f"{DEFAULT_NAME} {_short_mac(mac)} ({device.ip_address})"
            for mac, device in self._discovered_devices.items()
            if mac not in current_unique_ids and device.ip_address not in current_hosts
        }
        # Check if there is at least one device
        if not devices_name:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="pick_device",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_DEVICE): probatio.In(devices_name)}
            ),
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        return self.async_show_menu(
            step_id="user", menu_options=["manual", "local_integration"]
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setting up a device by IP address or network scan."""
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            raw_signing_key = user_input.get(CONF_SIGNING_KEY)
            signing_key = (
                normalize_signing_key(raw_signing_key) if raw_signing_key else None
            )
            if raw_signing_key and signing_key is None:
                errors[CONF_SIGNING_KEY] = "invalid_signing_key"
            elif not host:
                self._signing_key = signing_key
                return await self.async_step_pick_device()
            elif not is_ip_address(host):
                errors["base"] = "no_ip"
            else:
                self._signing_key = signing_key
                bulb = wizlight(host, signing_key=signing_key)
                try:
                    bulbtype = await bulb.get_bulbtype()
                    mac = await bulb.getMac()
                except WizLightTimeOutError:
                    errors["base"] = "bulb_time_out"
                except ConnectionRefusedError:
                    errors["base"] = "cannot_connect"
                except WizLightConnectionError:
                    errors["base"] = "no_wiz_light"
                except Exception:
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"
                else:
                    if (
                        entry := await self.async_set_unique_id(
                            mac, raise_on_progress=False
                        )
                    ) and entry.source != SOURCE_IGNORE:
                        self._async_update_entry(
                            entry, {**entry.data, **self._entry_data(host)}
                        )
                        return self.async_abort(reason="already_configured")
                    name = name_from_bulb_type_and_mac(bulbtype, mac)
                    return self.async_create_entry(
                        title=name,
                        data=self._entry_data(host),
                    )
                finally:
                    await bulb.async_close()

        return self.async_show_form(
            step_id="manual",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(CONF_HOST, default=""): str,
                    probatio.Optional(
                        probatio.Secret(CONF_SIGNING_KEY)
                    ): SIGNING_KEY_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of a WiZ device."""
        errors = {}
        reconfigure_entry = self._get_reconfigure_entry()
        host = reconfigure_entry.data[CONF_HOST]
        if user_input is not None:
            host = user_input[CONF_HOST]
            raw_signing_key = user_input.get(CONF_SIGNING_KEY)
            remove_signing_key = user_input.get(CONF_REMOVE_SIGNING_KEY, False)
            if remove_signing_key:
                signing_key = None
            elif raw_signing_key:
                signing_key = normalize_signing_key(raw_signing_key)
            else:
                signing_key = reconfigure_entry.data.get(CONF_SIGNING_KEY)
            if raw_signing_key and remove_signing_key:
                errors[CONF_SIGNING_KEY] = "signing_key_with_remove"
            elif raw_signing_key and signing_key is None:
                errors[CONF_SIGNING_KEY] = "invalid_signing_key"
            elif not is_ip_address(host):
                errors[CONF_HOST] = "no_ip"
            else:
                bulb = wizlight(host, signing_key=signing_key)
                try:
                    mac = await bulb.getMac()
                except WIZ_CONNECT_EXCEPTIONS:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(mac, raise_on_progress=False)
                    self._abort_if_unique_id_mismatch(reason="different_device")
                    self._signing_key = signing_key
                    self._async_update_entry(reconfigure_entry, self._entry_data(host))
                    return self.async_abort(reason="reconfigure_successful")
                finally:
                    await bulb.async_close()

        schema: dict[Any, Any] = {
            probatio.Required(CONF_HOST, default=host): str,
            probatio.Optional(probatio.Secret(CONF_SIGNING_KEY)): SIGNING_KEY_SELECTOR,
        }
        if CONF_SIGNING_KEY in reconfigure_entry.data:
            schema[probatio.Optional(CONF_REMOVE_SIGNING_KEY, default=False)] = bool
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=probatio.Schema(schema),
            errors=errors,
            description_placeholders={"name": reconfigure_entry.title},
        )

    async def async_step_local_integration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle importing a WiZ app local integration export."""
        errors = {}
        if user_input is not None:
            try:
                export = await self.hass.async_add_executor_job(
                    self._read_local_integration_file,
                    user_input[CONF_LOCAL_INTEGRATION_FILE],
                )
            except (ValueError, probatio.Invalid) as err:
                _LOGGER.debug("Invalid local integration file: %s", err)
                errors["base"] = "invalid_local_integration_file"
            else:
                return await self._async_import_local_integration(export)

        return self.async_show_form(
            step_id="local_integration",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_LOCAL_INTEGRATION_FILE): FileSelector(
                        FileSelectorConfig(accept=".json,application/json")
                    )
                }
            ),
            errors=errors,
        )

    def _read_local_integration_file(self, file_id: str) -> LocalIntegrationExport:
        """Read and parse an uploaded local integration export."""
        with process_uploaded_file(self.hass, file_id) as file_path:
            contents = file_path.read_text(encoding="utf-8")
        return parse_local_integration_export(json_loads_object(contents))

    async def _async_import_local_integration(
        self, export: LocalIntegrationExport
    ) -> ConfigFlowResult:
        """Add the signing key to the devices of the exported home."""
        updated = 0
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id not in export.devices:
                continue
            updated += 1
            if entry.data.get(CONF_SIGNING_KEY) != export.signing_key:
                self._async_update_entry(
                    entry, {**entry.data, CONF_SIGNING_KEY: export.signing_key}
                )

        current_unique_ids = self._async_current_ids()
        new_devices = {
            device.mac_address: device
            for device in await async_discover_devices(self.hass, DISCOVER_SCAN_TIMEOUT)
            if device.mac_address in export.devices
            and device.mac_address not in current_unique_ids
        }
        # Replace discovery flows that were started without the signing key,
        # including those of devices the scan missed
        for flow in self._async_in_progress(include_uninitialized=True):
            mac = flow["context"].get("unique_id")
            if mac not in export.devices or mac in current_unique_ids:
                continue
            if mac not in new_devices:
                if not (placeholders := flow["context"].get("title_placeholders")):
                    continue
                new_devices[mac] = DiscoveredBulb(placeholders["host"], mac)
            self.hass.config_entries.flow.async_abort(flow["flow_id"])
        for device in new_devices.values():
            discovery_flow.async_create_flow(
                self.hass,
                DOMAIN,
                context={"source": SOURCE_INTEGRATION_DISCOVERY},
                data={
                    **asdict(device),
                    CONF_NAME: export.devices[device.mac_address],
                    CONF_SIGNING_KEY: export.signing_key,
                },
            )

        if not updated and not new_devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_abort(
            reason="local_integration_successful",
            description_placeholders={
                "updated": str(updated),
                "discovered": str(len(new_devices)),
            },
        )

    @callback
    def _async_update_entry(self, entry: WizConfigEntry, data: dict[str, Any]) -> None:
        """Update the data of an entry and reload it."""
        self.hass.config_entries.async_update_entry(entry, data=data)
        # The update listener only reloads entries that are loaded
        if entry.state is not ConfigEntryState.LOADED:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
