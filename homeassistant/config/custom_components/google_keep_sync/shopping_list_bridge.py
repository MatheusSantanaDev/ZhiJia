"""Bridge the native shopping list interface to a Google Keep list."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, ClassVar

import voluptuous as vol
from homeassistant.components.todo import (
    DATA_COMPONENT,
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.const import EVENT_COMPONENT_LOADED, Platform
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN, get_default_shopping_list_id

_LOGGER = logging.getLogger(__name__)

SHOPPING_LIST_DOMAIN = "shopping_list"
INTENT_DOMAIN = "intent"
BRIDGE_DATA_KEY = f"{DOMAIN}_shopping_list_bridge"

INTENT_ADD_ITEM = "HassShoppingListAddItem"
INTENT_COMPLETE_ITEM = "HassShoppingListCompleteItem"
INTENT_LAST_ITEMS = "HassShoppingListLastItems"

SERVICE_ADD_ITEM = "add_item"
SERVICE_REMOVE_ITEM = "remove_item"
SERVICE_COMPLETE_ITEM = "complete_item"
SERVICE_INCOMPLETE_ITEM = "incomplete_item"
SERVICE_COMPLETE_ALL = "complete_all"
SERVICE_INCOMPLETE_ALL = "incomplete_all"
SERVICE_CLEAR_COMPLETED_ITEMS = "clear_completed_items"
SERVICE_SORT = "sort"

SERVICE_ITEM_SCHEMA = vol.Schema({vol.Required("name"): cv.string})
SERVICE_LIST_SCHEMA = vol.Schema({})
SERVICE_SORT_SCHEMA = vol.Schema({vol.Optional("reverse", default=False): cv.boolean})


def _get_bridge(hass: HomeAssistant) -> ShoppingListBridge | None:
    """Return the active shopping list bridge, if any."""
    bridge = hass.data.get(BRIDGE_DATA_KEY)
    return bridge if isinstance(bridge, ShoppingListBridge) else None


def _get_entity_id(hass: HomeAssistant, list_id: str) -> str | None:
    """Return the HA entity ID for a Google Keep list ID."""
    return er.async_get(hass).async_get_entity_id(
        Platform.TODO, DOMAIN, f"{DOMAIN}.list.{list_id}"
    )


def _get_entity(hass: HomeAssistant, entity_id: str) -> TodoListEntity:
    """Return a loaded todo entity or raise a useful error."""
    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component is not None else None
    if not isinstance(entity, TodoListEntity):
        raise HomeAssistantError(
            f"The default Google Keep shopping list {entity_id} is not available"
        )
    return entity


class ShoppingListBridge:
    """Expose a Google Keep list through the native shopping list interface."""

    def __init__(self, hass: HomeAssistant, entry_id: str, list_id: str) -> None:
        """Initialize the bridge."""
        self.hass = hass
        self.entry_id = entry_id
        self.list_id = list_id
        self._registered = False
        self._unloaded = False
        self._start_unsub: CALLBACK_TYPE | None = None
        self._component_unsub: CALLBACK_TYPE | None = None
        self._register_retries = 0

    @property
    def entity_id(self) -> str | None:
        """Return the current entity ID for the bridged list."""
        return _get_entity_id(self.hass, self.list_id)

    @property
    def items(self) -> list[TodoItem]:
        """Return the current items of the bridged list."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        return list(_get_entity(self.hass, entity_id).todo_items or [])

    @callback
    def schedule(self) -> None:
        """Register once Home Assistant has finished starting."""
        if self._unloaded:
            return
        if self._start_unsub is None:
            self._start_unsub = async_at_started(
                self.hass, self._async_schedule_register
            )

    @callback
    def _async_schedule_register(self, _: HomeAssistant) -> None:
        """Wait one loop tick so the new entity is in the registry."""
        if self._unloaded:
            return
        self._start_unsub = async_call_later(self.hass, 0, self._async_register)

    @callback
    def _async_register(self, _now: Any = None) -> None:
        """Register the bridge services and intents."""
        self._start_unsub = None
        if self._registered or self._unloaded:
            return
        if self.entity_id is None:
            if self._register_retries < 10:
                self._register_retries += 1
                self._start_unsub = async_call_later(
                    self.hass, 0.5, self._async_register
                )
                return
            _LOGGER.warning(
                "Default shopping list %s has no Home Assistant entity; "
                "the shopping list bridge will not be registered",
                self.list_id,
            )
            return
        self._register_retries = 0

        self._register_services()
        self._register_intents()
        self._component_unsub = self.hass.bus.async_listen(
            EVENT_COMPONENT_LOADED, self._handle_component_loaded
        )
        self._registered = True
        _LOGGER.info(
            "Google Keep list %s (%s) is now the default shopping list",
            self.list_id,
            self.entity_id,
        )

    @callback
    def _handle_component_loaded(self, event: Event) -> None:
        """Re-register after a component that owns the native handlers loads."""
        if self._unloaded or not self._registered:
            return
        if event.data.get("component") in (SHOPPING_LIST_DOMAIN, INTENT_DOMAIN):
            async_call_later(self.hass, 0, self._async_reapply)

    @callback
    def _async_reapply(self, _now: Any = None) -> None:
        """Apply the bridge after the integration platform has loaded."""
        if self._unloaded or not self._registered:
            return
        self._register_services()
        self._register_intents()

    @callback
    def _register_services(self) -> None:
        """Register the native shopping list services against the Keep list."""
        services = self.hass.services
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_ADD_ITEM,
            self._async_add_item,
            schema=SERVICE_ITEM_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_REMOVE_ITEM,
            self._async_remove_item,
            schema=SERVICE_ITEM_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_COMPLETE_ITEM,
            self._async_complete_item,
            schema=SERVICE_ITEM_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_INCOMPLETE_ITEM,
            self._async_incomplete_item,
            schema=SERVICE_ITEM_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_COMPLETE_ALL,
            self._async_complete_all,
            schema=SERVICE_LIST_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_INCOMPLETE_ALL,
            self._async_incomplete_all,
            schema=SERVICE_LIST_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_CLEAR_COMPLETED_ITEMS,
            self._async_clear_completed_items,
            schema=SERVICE_LIST_SCHEMA,
        )
        services.async_register(
            SHOPPING_LIST_DOMAIN,
            SERVICE_SORT,
            self._async_sort,
            schema=SERVICE_SORT_SCHEMA,
        )

    @callback
    def _register_intents(self) -> None:
        """Register the native shopping list intents against the Keep list."""
        for intent_type in (INTENT_ADD_ITEM, INTENT_COMPLETE_ITEM, INTENT_LAST_ITEMS):
            intent.async_remove(self.hass, intent_type)
        intent.async_register(self.hass, KeepAddItemIntent())
        intent.async_register(self.hass, KeepCompleteItemIntent())
        intent.async_register(self.hass, KeepLastItemsIntent())

    async def async_unregister(self) -> None:
        """Remove the bridge and restore native handlers when available."""
        self._unloaded = True
        if self._start_unsub is not None:
            self._start_unsub()
            self._start_unsub = None
        if self._component_unsub is not None:
            self._component_unsub()
            self._component_unsub = None

        if not self._registered:
            return
        self._registered = False

        for service in (
            SERVICE_ADD_ITEM,
            SERVICE_REMOVE_ITEM,
            SERVICE_COMPLETE_ITEM,
            SERVICE_INCOMPLETE_ITEM,
            SERVICE_COMPLETE_ALL,
            SERVICE_INCOMPLETE_ALL,
            SERVICE_CLEAR_COMPLETED_ITEMS,
            SERVICE_SORT,
        ):
            self.hass.services.async_remove(SHOPPING_LIST_DOMAIN, service)

        for intent_type in (INTENT_ADD_ITEM, INTENT_COMPLETE_ITEM, INTENT_LAST_ITEMS):
            intent.async_remove(self.hass, intent_type)

        if self.hass.config_entries.async_loaded_entries(SHOPPING_LIST_DOMAIN):
            from homeassistant.components.shopping_list.intent import (
                async_setup_intents as async_setup_native_intents,
            )
            from homeassistant.components.shopping_list.services import (
                async_setup_services as async_setup_native_services,
            )

            await async_setup_native_intents(self.hass)
            async_setup_native_services(self.hass)
            _LOGGER.info("Restored the native shopping list handlers")
        else:
            _LOGGER.info("Removed the Google Keep shopping list bridge")

    async def async_add_item_by_name(self, name: str) -> None:
        """Add an item, matching the native shopping list behavior."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        normalized_name = name.casefold()
        completed_match: TodoItem | None = None

        for item in entity.todo_items or []:
            if (item.summary or "").casefold() != normalized_name:
                continue
            if item.status != TodoItemStatus.COMPLETED:
                return
            if completed_match is None:
                completed_match = item

        if completed_match is None:
            await entity.async_create_todo_item(
                TodoItem(summary=name, status=TodoItemStatus.NEEDS_ACTION)
            )
            return

        await entity.async_update_todo_item(
            replace(completed_match, status=TodoItemStatus.NEEDS_ACTION)
        )

    async def async_complete_item_by_name(self, name: str) -> list[str]:
        """Mark incomplete items with the given name as completed."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        matches = [
            item
            for item in entity.todo_items or []
            if item.summary == name and item.status != TodoItemStatus.COMPLETED
        ]
        for item in matches:
            await entity.async_update_todo_item(
                replace(item, status=TodoItemStatus.COMPLETED)
            )
        return [item.summary or "" for item in matches]

    async def async_incomplete_item_by_name(self, name: str) -> bool:
        """Mark the first matching item as incomplete."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        match = next(
            (item for item in entity.todo_items or [] if item.summary == name),
            None,
        )
        if match is None:
            return False
        await entity.async_update_todo_item(
            replace(match, status=TodoItemStatus.NEEDS_ACTION)
        )
        return True

    async def async_remove_item_by_name(self, name: str) -> bool:
        """Remove the first matching item."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        match = next(
            (item for item in entity.todo_items or [] if item.summary == name),
            None,
        )
        if match is None or match.uid is None:
            return False
        await entity.async_delete_todo_items([match.uid])
        return True

    async def async_update_all(self, status: TodoItemStatus) -> None:
        """Set all items to the given status."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        for item in entity.todo_items or []:
            if item.status != status:
                await entity.async_update_todo_item(replace(item, status=status))

    async def async_clear_completed(self) -> None:
        """Delete all completed items."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        uids = [
            item.uid
            for item in entity.todo_items or []
            if item.status == TodoItemStatus.COMPLETED and item.uid is not None
        ]
        if uids:
            await entity.async_delete_todo_items(uids)

    async def async_sort(self, reverse: bool = False) -> None:
        """Sort the Keep list when the entity supports manual reordering."""
        entity_id = self.entity_id
        if entity_id is None:
            raise HomeAssistantError(
                "The default Google Keep shopping list is not available"
            )
        entity = _get_entity(self.hass, entity_id)
        if not (
            (entity.supported_features or 0) & TodoListEntityFeature.MOVE_TODO_ITEM
        ):
            _LOGGER.warning(
                "Cannot sort Google Keep list %s because item reordering is "
                "disabled by the integration's auto-sort option",
                self.list_id,
            )
            return

        previous_uid: str | None = None
        for item in sorted(
            entity.todo_items or [],
            key=lambda item: (item.summary or "").casefold(),
            reverse=reverse,
        ):
            if item.uid is None:
                continue
            await entity.async_move_todo_item(item.uid, previous_uid)
            previous_uid = item.uid

    async def _async_add_item(self, call: ServiceCall) -> None:
        """Handle shopping_list.add_item."""
        await self.async_add_item_by_name(call.data["name"].strip())

    async def _async_remove_item(self, call: ServiceCall) -> None:
        """Handle shopping_list.remove_item."""
        name = call.data["name"].strip()
        if not await self.async_remove_item_by_name(name):
            _LOGGER.error("Removing of item failed: %s cannot be found", name)

    async def _async_complete_item(self, call: ServiceCall) -> None:
        """Handle shopping_list.complete_item."""
        name = call.data["name"].strip()
        if not await self.async_complete_item_by_name(name):
            _LOGGER.error("Completing of item failed: %s cannot be found", name)

    async def _async_incomplete_item(self, call: ServiceCall) -> None:
        """Handle shopping_list.incomplete_item."""
        name = call.data["name"].strip()
        if not await self.async_incomplete_item_by_name(name):
            _LOGGER.error("Restoring of item failed: %s cannot be found", name)

    async def _async_complete_all(self, _: ServiceCall) -> None:
        """Handle shopping_list.complete_all."""
        await self.async_update_all(TodoItemStatus.COMPLETED)

    async def _async_incomplete_all(self, _: ServiceCall) -> None:
        """Handle shopping_list.incomplete_all."""
        await self.async_update_all(TodoItemStatus.NEEDS_ACTION)

    async def _async_clear_completed_items(self, _: ServiceCall) -> None:
        """Handle shopping_list.clear_completed_items."""
        await self.async_clear_completed()

    async def _async_sort(self, call: ServiceCall) -> None:
        """Handle shopping_list.sort."""
        await self.async_sort(call.data.get("reverse", False))


class _KeepShoppingListIntent(intent.IntentHandler):
    """Base intent handler for the Google Keep shopping list."""

    platforms: ClassVar[set[str]] = {DOMAIN}

    def _bridge(self, intent_obj: intent.Intent) -> ShoppingListBridge:
        """Return the active bridge."""
        bridge = _get_bridge(intent_obj.hass)
        if bridge is None:
            raise intent.IntentHandleError(
                "The default Google Keep shopping list is not available"
            )
        return bridge


class KeepAddItemIntent(_KeepShoppingListIntent):
    """Handle adding an item to the Keep shopping list."""

    intent_type = INTENT_ADD_ITEM
    description = "Adds an item to the Google Keep shopping list"
    slot_schema: ClassVar[dict[str, Any]] = {"item": cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        slots = self.async_validate_slots(intent_obj.slots)
        item_name = slots["item"]["value"].strip()
        await self._bridge(intent_obj).async_add_item_by_name(item_name)
        return intent_obj.create_response()


class KeepCompleteItemIntent(_KeepShoppingListIntent):
    """Handle completing an item on the Keep shopping list."""

    intent_type = INTENT_COMPLETE_ITEM
    description = "Marks an item as completed on the Google Keep shopping list"
    slot_schema: ClassVar[dict[str, Any]] = {"item": cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        slots = self.async_validate_slots(intent_obj.slots)
        item_name = slots["item"]["value"].strip()
        completed_items = await self._bridge(intent_obj).async_complete_item_by_name(
            item_name
        )
        response = intent_obj.create_response()
        response.async_set_speech_slots({"completed_items": completed_items})
        return response


class KeepLastItemsIntent(_KeepShoppingListIntent):
    """Handle listing recent items on the Keep shopping list."""

    intent_type = INTENT_LAST_ITEMS
    description = "List the top five items on the Google Keep shopping list"
    slot_schema: ClassVar[dict[str, Any]] = {"item": cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Handle the intent."""
        items = self._bridge(intent_obj).items[-5:]
        response = intent_obj.create_response()

        if not items:
            response.async_set_speech("There are no items on your shopping list")
        else:
            items_list = ", ".join(str(item.summary or "") for item in reversed(items))
            response.async_set_speech(
                "These are the top"
                f" {len(items)} items on your"
                f" shopping list: {items_list}"
            )
        return response


async def async_setup_shopping_list_bridge(
    hass: HomeAssistant,
    entry_id: str,
    entry_data: dict[str, Any],
    lists: list[Any] | None,
) -> None:
    """Activate the shopping list bridge when a Keep list is selected."""
    list_id = get_default_shopping_list_id(entry_data, lists)
    if not list_id:
        return

    existing = _get_bridge(hass)
    if existing is not None:
        if existing.entry_id == entry_id and existing.list_id == list_id:
            return
        if existing.entry_id != entry_id:
            _LOGGER.warning(
                "The default shopping list is already provided by config entry %s",
                existing.entry_id,
            )
            return
        await existing.async_unregister()

    bridge = ShoppingListBridge(hass, entry_id, list_id)
    hass.data[BRIDGE_DATA_KEY] = bridge
    bridge.schedule()


async def async_unload_shopping_list_bridge(hass: HomeAssistant, entry_id: str) -> None:
    """Deactivate the shopping list bridge for a config entry."""
    bridge = _get_bridge(hass)
    if bridge is None or bridge.entry_id != entry_id:
        return
    await bridge.async_unregister()
    hass.data.pop(BRIDGE_DATA_KEY, None)
