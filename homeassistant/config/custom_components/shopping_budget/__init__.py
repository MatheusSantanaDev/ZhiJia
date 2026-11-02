"""Shopping Budget integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DEFAULT_FLYER_TTL_HOURS,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_OCR_MAX_IMAGES,
    DEFAULT_OCR_MAX_PAGES,
    DEFAULT_OCR_MIN_CONFIDENCE,
    DEFAULT_OCR_PSM,
    DEFAULT_PRICE_TTL_HOURS,
    DEFAULT_TODO_ENTITY,
    DOMAIN,
)
from .manager import ShoppingBudgetManager
from .websocket_api import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

MARKET_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Required("type"): vol.In(["abc", "assai", "bahamas", "manual"]),
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("url"): cv.string,
        vol.Optional("flyer_url"): cv.string,
        vol.Optional("flyers_url"): cv.string,
        vol.Optional("api_url"): cv.string,
        vol.Optional("store_id"): vol.Coerce(int),
        vol.Optional("store_nid"): vol.Coerce(int),
        vol.Optional("store_eid"): vol.Coerce(int),
        vol.Optional("store_loja_id"): vol.Coerce(int),
        vol.Optional("auto_store", default=True): cv.boolean,
        vol.Optional("eversion"): cv.string,
        vol.Optional("uid"): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

OCR_SCHEMA = vol.Schema(
    {
        vol.Optional("enabled", default=True): cv.boolean,
        vol.Optional("language", default=DEFAULT_OCR_LANGUAGE): cv.string,
        vol.Optional("psm", default=DEFAULT_OCR_PSM): vol.All(
            vol.Coerce(int), vol.Range(min=3, max=13)
        ),
        vol.Optional("max_pages", default=DEFAULT_OCR_MAX_PAGES): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20)
        ),
        vol.Optional("max_images", default=DEFAULT_OCR_MAX_IMAGES): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20)
        ),
        vol.Optional(
            "min_confidence", default=DEFAULT_OCR_MIN_CONFIDENCE
        ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    },
    extra=vol.ALLOW_EXTRA,
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(
                    "todo_entity", default=DEFAULT_TODO_ENTITY
                ): cv.entity_id,
                vol.Optional("markets"): vol.Schema(
                    {cv.slug: MARKET_SCHEMA}
                ),
                vol.Optional("ocr"): OCR_SCHEMA,
                vol.Optional(
                    "price_ttl_hours", default=DEFAULT_PRICE_TTL_HOURS
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
                vol.Optional(
                    "flyer_ttl_hours", default=DEFAULT_FLYER_TTL_HOURS
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=336)),
            },
            extra=vol.ALLOW_EXTRA,
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Shopping Budget integration from YAML."""
    if DOMAIN not in config:
        return True
    conf: dict[str, Any] = dict(config[DOMAIN])
    manager = ShoppingBudgetManager(hass, conf)
    await manager.async_setup()
    hass.data[DOMAIN] = manager
    async_register_websocket_api(hass)
    _LOGGER.info(
        "Shopping Budget configured for %s with %d markets",
        manager.todo_entity_id,
        len(manager.market_configs),
    )
    return True
