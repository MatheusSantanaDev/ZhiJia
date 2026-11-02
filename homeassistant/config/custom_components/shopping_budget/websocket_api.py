"""WebSocket API for Shopping Budget."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .manager import ShoppingBudgetManager


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register Shopping Budget WebSocket commands."""
    websocket_api.async_register_command(hass, websocket_budget)
    websocket_api.async_register_command(hass, websocket_catalog_search)
    websocket_api.async_register_command(hass, websocket_refresh_prices)
    websocket_api.async_register_command(hass, websocket_refresh_flyers)


def _manager(hass: HomeAssistant) -> ShoppingBudgetManager:
    manager = hass.data.get(DOMAIN)
    if not isinstance(manager, ShoppingBudgetManager):
        raise HomeAssistantError("Integração Shopping Budget não carregada.")
    return manager


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "shopping_budget/budget",
        probatio.Optional("entity_id"): str,
    }
)
@websocket_api.async_response
async def websocket_budget(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the current market budget for a list."""
    manager = _manager(hass)
    data = await manager.async_get_budget(msg.get("entity_id"))
    connection.send_result(msg["id"], data)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "shopping_budget/catalog/search",
        probatio.Required("query"): str,
        probatio.Optional("limit", default=10): int,
    }
)
@websocket_api.async_response
async def websocket_catalog_search(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Search the catalog by product name, brand or alias."""
    manager = _manager(hass)
    limit = max(1, min(50, int(msg.get("limit", 10))))
    products = manager.catalog.search(msg["query"], limit)
    connection.send_result(msg["id"], {"products": products})


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "shopping_budget/refresh_prices",
        probatio.Optional("market"): str,
        probatio.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def websocket_refresh_prices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Refresh ABC prices and stale flyers."""
    manager = _manager(hass)
    result = await manager.async_refresh_prices(
        msg.get("market"), bool(msg.get("force", False)), source="websocket"
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        probatio.Required("type"): "shopping_budget/refresh_flyers",
        probatio.Optional("market"): str,
        probatio.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def websocket_refresh_flyers(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Refresh and OCR Assaí/Bahamas flyers."""
    manager = _manager(hass)
    result = await manager.async_refresh_flyers(
        msg.get("market"), bool(msg.get("force", False))
    )
    connection.send_result(msg["id"], result)
