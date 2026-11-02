"""Integration for synchronization with Google Keep."""

from __future__ import annotations

import logging
import traceback
from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import as_timestamp, utcnow

from .api import GoogleKeepAPI
from .const import DOMAIN
from .coordinator import GoogleKeepSyncCoordinator
from .shopping_list_bridge import (
    async_setup_shopping_list_bridge,
    async_unload_shopping_list_bridge,
)

PLATFORMS: list[Platform] = [Platform.TODO]

_LOGGER = logging.getLogger(__name__)

SERVICE_REQUEST_SYNC_SCHEMA = vol.Schema(
    {vol.Optional("force", default=False): cv.boolean}
)

SERVICE_DELETE_LIST_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})


def _filter_unknown_node_type_warning(record: logging.LogRecord) -> bool:
    """Drop gkeepapi's noisy 'Unknown node type' warning; re-route to our DEBUG."""
    if not record.getMessage().startswith("Unknown node type:"):
        return True
    if _LOGGER.getEffectiveLevel() > logging.DEBUG:
        return False
    stack = "".join(traceback.format_stack()[:-1])
    record.name = _LOGGER.name
    record.levelno = logging.DEBUG
    record.levelname = "DEBUG"
    record.msg = (
        f"suppressed gkeepapi '{record.getMessage()}' from "
        f"{record.pathname}:{record.lineno}\nStack:\n{stack}"
    )
    record.args = None
    return True


async def async_service_request_sync(coordinator: GoogleKeepSyncCoordinator, call):
    """Handle the request_sync call."""
    force = bool(call.data.get("force", False))
    sync_threshold = 55
    last_update_timestamp = as_timestamp(coordinator.last_update_success_time)
    seconds_since_update = as_timestamp(utcnow()) - last_update_timestamp

    if force or seconds_since_update > sync_threshold:
        _LOGGER.info("Requesting manual sync%s.", " (forced)" if force else "")
        await coordinator.async_refresh()
    else:
        time_to_next_allowed_update = round(sync_threshold - seconds_since_update)
        _LOGGER.warning(
            "Requesting sync too soon after last update."
            f" Try again in {time_to_next_allowed_update} seconds."
        )


async def async_service_delete_list(hass: HomeAssistant, call: ServiceCall) -> None:
    """Move selected Google Keep lists to trash and stop syncing them."""
    entity_ids = call.data["entity_id"]
    registry = er.async_get(hass)

    entries_to_reload: dict[str, ConfigEntry] = {}
    removed_by_entry: dict[str, list[str]] = {}

    for entity_id in entity_ids:
        entity_entry = registry.async_get(entity_id)
        if entity_entry is None or entity_entry.platform != DOMAIN:
            _LOGGER.warning("Entity %s is not managed by %s", entity_id, DOMAIN)
            continue

        unique_id = entity_entry.unique_id or ""
        prefix = f"{DOMAIN}.list."
        if not unique_id.startswith(prefix):
            _LOGGER.warning(
                "Entity %s has unexpected unique_id: %s", entity_id, unique_id
            )
            continue

        config_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
        if config_entry is None:
            continue

        coordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
        if coordinator is None:
            _LOGGER.warning("No coordinator found for entity %s", entity_id)
            continue

        list_id = unique_id[len(prefix) :]
        try:
            await coordinator.api.async_trash_list(list_id)
        except Exception:
            _LOGGER.exception("Failed to move list %s to Google Keep trash", list_id)
            continue

        registry.async_remove(entity_id)
        removed_by_entry.setdefault(config_entry.entry_id, []).append(list_id)
        entries_to_reload[config_entry.entry_id] = config_entry
        _LOGGER.info("Removed Google Keep list %s from sync", list_id)

    for entry_id, list_ids in removed_by_entry.items():
        config_entry = entries_to_reload[entry_id]
        new_data = {
            **config_entry.data,
            "lists_to_sync": [
                list_id
                for list_id in config_entry.data.get("lists_to_sync", [])
                if list_id not in list_ids
            ],
        }
        hass.config_entries.async_update_entry(config_entry, data=new_data)

    for entry_id in entries_to_reload:
        await hass.config_entries.async_reload(entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Google Keep Sync from a config entry."""
    gkeepapi_logger = logging.getLogger("gkeepapi.node")
    if _filter_unknown_node_type_warning not in gkeepapi_logger.filters:
        gkeepapi_logger.addFilter(_filter_unknown_node_type_warning)

    # Create API instance
    api = GoogleKeepAPI(
        hass,
        entry.data["username"],
        entry.data.get("token"),
    )

    # Authenticate with the API
    if not await api.authenticate():
        _LOGGER.error("Failed to authenticate Google Keep API")
        return False  # Exit early if authentication fails

    # Create the coordinator
    coordinator = GoogleKeepSyncCoordinator(hass, api, entry)

    # Start the data update coordinator
    await coordinator.async_config_entry_first_refresh()

    # Store the coordinator object in hass.data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register the request_sync service
    hass.services.async_register(
        DOMAIN,
        "request_sync",
        partial(async_service_request_sync, coordinator),
        schema=SERVICE_REQUEST_SYNC_SCHEMA,
    )

    # Register the delete_list service
    hass.services.async_register(
        DOMAIN,
        "delete_list",
        async_service_delete_list,
        schema=SERVICE_DELETE_LIST_SCHEMA,
    )

    # Forward the setup to the todo platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Expose the selected Keep list through the native shopping list interface
    await async_setup_shopping_list_bridge(
        hass, entry.entry_id, dict(entry.data), coordinator.data
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await async_unload_shopping_list_bridge(hass, entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry to new format."""
    version = entry.version
    data = dict(entry.data)
    unique_id = entry.unique_id
    updated = False

    # Migration 1: unique_id format
    if version == 1:
        if unique_id and not unique_id.startswith(f"{DOMAIN}."):
            new_unique_id = f"{DOMAIN}.{data['username'].lower()}"
            hass.config_entries.async_update_entry(entry, unique_id=new_unique_id)
            updated = True
        version = 2

    # Migration 2: Remove password, enforce token
    migration_version_token_required = 2
    migration_version_latest = 3
    if version == migration_version_token_required:
        if "password" in data:
            data.pop("password")
            updated = True
        token = data.get("token")
        if not token:
            # No token present, migration cannot continue
            _LOGGER.error(
                "Google Keep Sync config entry migration failed: token is required. "
                "Please update your integration to use a token."
            )
            return False
        version = migration_version_latest

    if updated or entry.version != version:
        hass.config_entries.async_update_entry(entry, data=data, version=version)
        _LOGGER.info("Migrated Google Keep Sync config entry to version %d", version)
    return True
