"""Core manager for the Shopping Budget integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
import asyncio
import functools
import logging
from typing import Any
from urllib.parse import quote_plus

import voluptuous as vol

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.components.todo.const import DATA_COMPONENT
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .catalog import (
    Catalog,
    normalize,
    ocr_match_score,
    parse_package,
    product_display_name,
    product_payload,
    seed_products,
)
from .const import (
    ABC_DEFAULT_STORE_ID,
    ASSAI_DEFAULT_JSON_URL,
    ASSAI_DEFAULT_PAGE_URL,
    ASSAI_STORE_EID,
    ASSAI_STORE_LOJA_ID,
    ASSAI_STORE_NID,
    BAHAMAS_DEFAULT_FLYERS_URL,
    BAHAMAS_DEFAULT_PAGE_URL,
    DEFAULT_FLYER_TTL_HOURS,
    DEFAULT_OCR_LANGUAGE,
    DEFAULT_OCR_MAX_IMAGES,
    DEFAULT_OCR_MAX_PAGES,
    DEFAULT_OCR_MIN_CONFIDENCE,
    DEFAULT_OCR_PSM,
    DEFAULT_PRICE_TTL_HOURS,
    DEFAULT_TODO_ENTITY,
    DOMAIN,
    OCR_AUTO_SCORE,
    OCR_SUGGEST_SCORE,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .ocr import available_packages, ocr_available
from .providers.abc import ABCError, ABCProvider
from .providers.atacadao import AtacadaoError, AtacadaoProvider
from .providers.assai import AssaiError, AssaiProvider
from .providers.bahamas import BahamasError, BahamasProvider

_LOGGER = logging.getLogger(__name__)

ONLINE_AUTO_SCORE = 0.60


def _utcnow() -> str:
    """Return an ISO timestamp for storage."""
    return dt_util.utcnow().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from storage."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_stale(value: str | None, hours: float) -> bool:
    """Return True when an ISO timestamp is older than the given hours."""
    parsed = _parse_iso(value)
    if parsed is None:
        return True
    return (dt_util.utcnow() - parsed) > timedelta(hours=hours)


def _default_data() -> dict[str, Any]:
    """Return an empty storage schema."""
    return {
        "version": STORAGE_VERSION,
        "catalog": {},
        "items": {},
        "prices": {},
        "offers": {},
        "meta": {},
    }


def _default_markets() -> dict[str, dict[str, Any]]:
    """Built-in market definitions used when the YAML omits markets."""
    return {
        "assai": {
            "name": "Assaí",
            "type": "assai",
            "url": ASSAI_DEFAULT_PAGE_URL,
            "flyer_url": ASSAI_DEFAULT_JSON_URL,
            "store_nid": ASSAI_STORE_NID,
            "store_eid": ASSAI_STORE_EID,
            "store_loja_id": ASSAI_STORE_LOJA_ID,
        },
        "bahamas": {
            "name": "Bahamas",
            "type": "bahamas",
            "url": BAHAMAS_DEFAULT_PAGE_URL,
            "flyers_url": BAHAMAS_DEFAULT_FLYERS_URL,
        },
        "abc": {
            "name": "ABC",
            "type": "abc",
            "url": "https://superabconline.com.br/",
            "store_id": ABC_DEFAULT_STORE_ID,
            "auto_store": True,
        },
    }


class ShoppingBudgetManager:
    """Coordinate catalog, prices, OCR offers and the Google Keep list."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self.todo_entity_id = config.get("todo_entity") or DEFAULT_TODO_ENTITY
        self.market_configs = self._build_markets(config.get("markets"))
        self._markets_by_id = {market["id"]: market for market in self.market_configs}
        requested_default = config.get("default_market")
        if requested_default in self._markets_by_id:
            self.default_market_id = str(requested_default)
        else:
            self.default_market_id = next(
                (
                    market["id"]
                    for market in self.market_configs
                    if market.get("type") == "abc"
                ),
                self.market_configs[0]["id"] if self.market_configs else "",
            )
        self.price_ttl_hours = float(
            config.get("price_ttl_hours", DEFAULT_PRICE_TTL_HOURS)
        )
        self.flyer_ttl_hours = float(
            config.get("flyer_ttl_hours", DEFAULT_FLYER_TTL_HOURS)
        )
        ocr_config = config.get("ocr") or {}
        self.ocr_enabled = bool(ocr_config.get("enabled", True))
        self.ocr_language = ocr_config.get("language") or DEFAULT_OCR_LANGUAGE
        self.ocr_psm = int(ocr_config.get("psm", DEFAULT_OCR_PSM))
        self.ocr_max_pages = int(
            ocr_config.get("max_pages", DEFAULT_OCR_MAX_PAGES)
        )
        self.ocr_max_images = int(
            ocr_config.get("max_images", DEFAULT_OCR_MAX_IMAGES)
        )
        self.ocr_min_confidence = float(
            ocr_config.get("min_confidence", DEFAULT_OCR_MIN_CONFIDENCE)
        )

        self.store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True
        )
        self.data: dict[str, Any] = _default_data()
        self.catalog = Catalog()

        self._entity: TodoListEntity | None = None
        self._unsub_items: CALLBACK_TYPE | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_stop: CALLBACK_TYPE | None = None
        self._unsub_initial: CALLBACK_TYPE | None = None
        self._unsub_interval: CALLBACK_TYPE | None = None
        self._processing_task: asyncio.Task | None = None
        self._pending_items: list[TodoItem] | None = None
        self._process_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._lookup_lock = asyncio.Lock()
        self._shutdown = False
        self._ocr_score_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}

        latitude = getattr(hass.config, "latitude", 0.0) or 0.0
        longitude = getattr(hass.config, "longitude", 0.0) or 0.0
        self._abc: ABCProvider | None = None
        self._atacadao: AtacadaoProvider | None = None
        self._assai: AssaiProvider | None = None
        self._bahamas: BahamasProvider | None = None
        for market in self.market_configs:
            market_type = market.get("type")
            if market_type == "abc" and self._abc is None:
                self._abc = ABCProvider(
                    market, float(latitude), float(longitude)
                )
            elif market_type == "atacadao" and self._atacadao is None:
                self._atacadao = AtacadaoProvider(market)
            elif market_type == "assai" and self._assai is None:
                self._assai = AssaiProvider(market)
            elif market_type == "bahamas" and self._bahamas is None:
                self._bahamas = BahamasProvider(market)

    # ------------------------------------------------------------------
    # Setup / storage
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load storage, seed the catalog and register services."""
        raw = await self.store.async_load()
        self.data = self._normalize_data(raw)
        self.catalog = Catalog(self.data["catalog"])
        if self._seed_catalog():
            self._save()
        self._register_services()
        self._unsub_stop = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._async_stop
        )
        async_at_started(self.hass, self._async_start)

    @staticmethod
    def _normalize_data(raw: Any) -> dict[str, Any]:
        base = _default_data()
        if not isinstance(raw, dict):
            return base
        for key, default in base.items():
            value = raw.get(key)
            if isinstance(default, dict):
                base[key] = value if isinstance(value, dict) else default
            else:
                base[key] = value if value is not None else default
        base["version"] = STORAGE_VERSION
        return base

    def _seed_catalog(self) -> bool:
        changed = False
        for product in seed_products():
            product_id = product["id"]
            if product_id not in self.data["catalog"]:
                self.data["catalog"][product_id] = product
                changed = True
        if self.data["meta"].get("seed_version") != 1:
            self.data["meta"]["seed_version"] = 1
            changed = True
        if changed:
            self.catalog.load(self.data["catalog"])
        return changed

    def _build_markets(
        self, raw_markets: dict[str, dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        for market_id, market_config in (raw_markets or _default_markets()).items():
            if not isinstance(market_config, dict):
                continue
            if not market_config.get("enabled", True):
                continue
            market = {"id": market_id, **market_config}
            market.setdefault("name", market_id)
            markets.append(market)
        return markets

    @property
    def _markets(self) -> list[dict[str, Any]]:
        return self.market_configs

    def _market(self, market_id: str) -> dict[str, Any] | None:
        return self._markets_by_id.get(market_id)

    @property
    def _online_markets(self) -> list[dict[str, Any]]:
        return [
            market
            for market in self._markets
            if market.get("type") in ("abc", "atacadao")
        ]

    def _provider_for(self, market: dict[str, Any]) -> Any | None:
        market_type = market.get("type")
        if market_type == "abc":
            return self._abc
        if market_type == "atacadao":
            return self._atacadao
        return None

    def _save(self) -> None:
        if self._shutdown:
            return
        self.store.async_delay_save(lambda: self.data, 1)

    def _fire_event(self) -> None:
        self.hass.bus.async_fire(
            f"{DOMAIN}_updated",
            {"entity_id": self.todo_entity_id, "updated_at": _utcnow()},
        )

    # ------------------------------------------------------------------
    # Startup / Google Keep subscription
    # ------------------------------------------------------------------
    async def _async_start(self, _hass: HomeAssistant) -> None:
        if self._shutdown:
            return
        entity = self._get_todo_entity(self.todo_entity_id)
        if entity is not None:
            self._subscribe_entity(entity)
        else:
            self._unsub_state = self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._handle_state_event
            )
        self._setup_periodic()

    @callback
    def _handle_state_event(self, event: Event) -> None:
        if event.data.get("entity_id") != self.todo_entity_id:
            return
        entity = self._get_todo_entity(self.todo_entity_id)
        if entity is not None and entity is not self._entity:
            self._subscribe_entity(entity)

    def _subscribe_entity(self, entity: TodoListEntity) -> None:
        if self._entity is entity and self._unsub_items is not None:
            return
        if self._unsub_items is not None:
            self._unsub_items()
        self._entity = entity
        self._unsub_items = entity.async_subscribe_updates(self._handle_items)
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        self._handle_items(entity.todo_items)

    @callback
    def _handle_items(self, items: list[TodoItem] | None) -> None:
        if self._shutdown:
            return
        self._pending_items = items
        if self._processing_task and not self._processing_task.done():
            return
        self._processing_task = self.hass.async_create_task(
            self._async_process_pending()
        )

    async def _async_process_pending(self) -> None:
        while self._pending_items is not None and not self._shutdown:
            items = self._pending_items
            self._pending_items = None
            await asyncio.sleep(0.15)
            await self._async_process_items(items)

    def _get_todo_entity(self, entity_id: str) -> TodoListEntity | None:
        component = self.hass.data.get(DATA_COMPONENT)
        if component is None:
            return None
        entity = component.get_entity(entity_id)
        return entity if isinstance(entity, TodoListEntity) else None

    def _setup_periodic(self) -> None:
        if self._unsub_initial is not None or self._shutdown:
            return
        interval_hours = max(1.0, min(self.price_ttl_hours, self.flyer_ttl_hours))
        self._unsub_initial = async_call_later(
            self.hass, 300, self._async_auto_refresh
        )
        self._unsub_interval = async_track_time_interval(
            self.hass,
            self._async_auto_refresh,
            timedelta(hours=interval_hours),
        )

    async def _async_auto_refresh(self, _now: datetime | None = None) -> None:
        if self._shutdown or self._refresh_lock.locked():
            return
        try:
            await self.async_refresh_prices(force=False, source="auto")
        except Exception:  # noqa: BLE001 - background refresh must not crash startup
            _LOGGER.exception("Automatic Shopping Budget refresh failed")

    # ------------------------------------------------------------------
    # Item state / purchase snapshots
    # ------------------------------------------------------------------
    async def _async_process_items(self, items: list[TodoItem] | None) -> None:
        async with self._process_lock:
            if self._shutdown:
                return
            current_items = items or []
            now = _utcnow()
            current_uids: set[str] = set()
            changed = False

            for item in current_items:
                uid = item.uid
                if not uid:
                    continue
                current_uids.add(uid)
                summary = (item.summary or "").strip()
                status = (
                    "completed"
                    if item.status == TodoItemStatus.COMPLETED
                    else "needs_action"
                )
                item_data = self.data["items"].get(uid)
                if item_data is None:
                    item_data = {
                        "uid": uid,
                        "quantity": 1,
                        "product_id": None,
                        "link_source": None,
                        "linked_text_norm": None,
                        "purchased": None,
                        "seen": False,
                        "first_seen": now,
                        "last_seen": now,
                        "summary": summary,
                        "status": None,
                    }
                    self.data["items"][uid] = item_data
                    changed = True

                previous_status = item_data.get("status")
                was_seen = bool(item_data.get("seen"))
                if item_data.get("summary") != summary:
                    item_data["summary"] = summary
                    changed = True
                if item_data.get("status") != status:
                    item_data["status"] = status
                    changed = True
                item_data["last_seen"] = now
                changed = True

                if not was_seen:
                    item_data["seen"] = True
                    changed = True
                    self._apply_pending_add(item_data, summary)
                    if status == "completed" and not item_data.get("purchased"):
                        if self._snapshot_item_prices(
                            item_data, estimated=True
                        ):
                            changed = True
                    elif status != "completed":
                        product, _suggestions, _score = self._resolve_product(
                            item_data, summary
                        )
                        self._schedule_default_price(product)
                elif previous_status != status:
                    if status == "completed":
                        if self._snapshot_item_prices(
                            item_data, estimated=False
                        ):
                            changed = True
                    elif item_data.get("purchased") is not None:
                        item_data["purchased"] = None
                        changed = True
                elif status == "completed" and not item_data.get("purchased"):
                    if self._snapshot_item_prices(item_data, estimated=True):
                        changed = True

            cutoff = dt_util.utcnow() - timedelta(days=45)
            for uid, item_data in list(self.data["items"].items()):
                if uid in current_uids:
                    continue
                last_seen = _parse_iso(item_data.get("last_seen"))
                if last_seen is not None and last_seen < cutoff:
                    del self.data["items"][uid]
                    changed = True

            if changed:
                self._save()
                self._fire_event()

    def _apply_pending_add(self, item_data: dict[str, Any], summary: str) -> None:
        pending = self.data["meta"].get("pending_adds")
        if not isinstance(pending, dict):
            return
        entry = pending.pop(normalize(summary), None)
        if not entry:
            return
        item_data["product_id"] = entry.get("product_id")
        item_data["link_source"] = "manual"
        item_data["linked_text_norm"] = normalize(summary)
        item_data["quantity"] = entry.get("quantity", 1)
        item_data["status"] = item_data.get("status") or "needs_action"

    def _snapshot_item_prices(
        self, item_data: dict[str, Any], *, estimated: bool
    ) -> bool:
        summary = item_data.get("summary") or ""
        product, _suggestions, _score = self._resolve_product(item_data, summary)
        if not product:
            return False
        current = self._current_prices(product)
        snapshot: dict[str, dict[str, Any]] = {}
        now = _utcnow()
        for market_id, price in current.items():
            if not price or price.get("price") is None:
                continue
            copied = dict(price)
            copied["frozen"] = True
            copied["snapshot_at"] = now
            copied["estimated"] = bool(
                estimated or price.get("source") not in ("manual", "abc")
            )
            snapshot[market_id] = copied
        item_data["purchased"] = {
            "at": now,
            "prices": snapshot,
            "quantity": float(item_data.get("quantity", 1) or 1),
        }
        return True

    # ------------------------------------------------------------------
    # Catalog resolution
    # ------------------------------------------------------------------
    def _resolve_product(
        self, item_data: dict[str, Any], summary: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
        summary = (summary or "").strip()
        product_id = item_data.get("product_id")
        product = self.catalog.get(product_id) if product_id else None
        link_source = item_data.get("link_source")

        if product and link_source != "auto":
            return product, [], 1.0
        if (
            product
            and link_source == "auto"
            and item_data.get("linked_text_norm") == normalize(summary)
        ):
            return product, [], 1.0

        if not summary:
            if product and link_source != "auto":
                return product, [], 1.0
            return None, [], 0.0

        resolved, suggestions, score = self.catalog.resolve(summary)
        if resolved:
            item_data["product_id"] = resolved["id"]
            item_data["link_source"] = "auto"
            item_data["linked_text_norm"] = normalize(summary)
            return resolved, suggestions, score

        if product_id:
            item_data["product_id"] = None
            item_data["link_source"] = None
            item_data["linked_text_norm"] = None
        return None, suggestions, score

    def _scored_ocr_candidates(
        self, product: dict[str, Any], offer: dict[str, Any]
    ) -> list[dict[str, Any]]:
        cache_key = (
            str(product.get("id") or product_display_name(product)),
            str(offer.get("fetched_at") or id(offer)),
        )
        cached = self._ocr_score_cache.get(cache_key)
        if cached is not None:
            return cached
        scored: list[dict[str, Any]] = []
        for candidate in offer.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            name = candidate.get("name") or ""
            try:
                confidence = float(candidate.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            score = ocr_match_score(product, name, confidence)
            if score < OCR_SUGGEST_SCORE:
                continue
            payload = dict(candidate)
            payload["score"] = round(score, 4)
            scored.append(payload)
        scored.sort(
            key=lambda item: (
                float(item.get("score", 0)),
                float(item.get("confidence", 0)),
            ),
            reverse=True,
        )
        # Keep only distinct names/prices to avoid visual noise.
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, float]] = set()
        for candidate in scored:
            key = (
                normalize(candidate.get("name")),
                round(float(candidate.get("price", 0)), 2),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        self._ocr_score_cache[cache_key] = unique
        return unique

    def _best_ocr_match(
        self, product: dict[str, Any], offer: dict[str, Any]
    ) -> dict[str, Any] | None:
        scored = self._scored_ocr_candidates(product, offer)
        if scored and scored[0]["score"] >= OCR_AUTO_SCORE:
            return scored[0]
        return None

    # ------------------------------------------------------------------
    # Price enrichment
    # ------------------------------------------------------------------
    def _current_prices(self, product: dict[str, Any]) -> dict[str, dict[str, Any]]:
        prices: dict[str, dict[str, Any]] = {}
        product_id = product["id"]
        stored_for_product = self.data["prices"].get(product_id) or {}

        for market in self._markets:
            market_id = market["id"]
            stored = stored_for_product.get(market_id)
            if isinstance(stored, dict) and stored.get("source") in (
                "manual",
                "abc",
                "atacadao",
            ):
                price = dict(stored)
                price["market_id"] = market_id
                price["stale"] = (
                    price.get("source") in ("abc", "atacadao")
                    and _is_stale(price.get("updated_at"), self.price_ttl_hours)
                )
                price["estimated"] = bool(price.get("estimated", False))
                price["usable"] = float(price.get("price") or 0) > 0
                if price["usable"]:
                    prices[market_id] = price
                continue

            market_type = market.get("type")
            if market_type not in ("assai", "bahamas"):
                continue
            offer = self.data["offers"].get(market_id)
            if not isinstance(offer, dict):
                continue
            match = self._best_ocr_match(product, offer)
            if not match:
                continue
            valid_to = offer.get("valid_to")
            expired = False
            if valid_to:
                try:
                    expired = date.fromisoformat(str(valid_to)) < date.today()
                except ValueError:
                    expired = False
            score = float(match.get("score", 0))
            prices[market_id] = {
                **match,
                "market_id": market_id,
                "source": "ocr",
                "updated_at": offer.get("fetched_at"),
                "stale": _is_stale(
                    offer.get("fetched_at"), self.flyer_ttl_hours
                )
                or expired,
                "estimated": score < 0.70,
                "usable": float(match.get("price") or 0) > 0,
                "offer_name": match.get("name"),
                "valid_to": valid_to,
                "url": offer.get("source_url")
                or (offer.get("images") or [None])[0],
            }
        return prices

    def _ocr_suggestions(
        self, product: dict[str, Any], offer: dict[str, Any], limit: int = 3
    ) -> list[dict[str, Any]]:
        return self._scored_ocr_candidates(product, offer)[:limit]

    def _prices_for_item(
        self,
        item_data: dict[str, Any],
        product: dict[str, Any] | None,
        completed: bool,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, list[dict[str, Any]]],
        list[str],
        str | None,
    ]:
        if not product:
            return {}, {}, [market["id"] for market in self._markets], None
        current = self._current_prices(product)
        purchased = item_data.get("purchased") or {}
        purchased_prices = purchased.get("prices") or {}
        prices: dict[str, dict[str, Any]] = {}
        ocr_suggestions: dict[str, list[dict[str, Any]]] = {}
        missing: list[str] = []

        for market in self._markets:
            market_id = market["id"]
            price = current.get(market_id)
            if completed and market_id in purchased_prices:
                snapshot = dict(purchased_prices[market_id])
                snapshot["market_id"] = market_id
                snapshot["frozen"] = True
                snapshot["stale"] = False
                prices[market_id] = snapshot
            elif price:
                entry = dict(price)
                if completed:
                    entry["estimated"] = True
                    entry["note"] = (
                        "Sem preço no momento da compra; usando o preço atual."
                    )
                prices[market_id] = entry
            else:
                missing.append(market_id)

            if market.get("type") in ("assai", "bahamas"):
                offer = self.data["offers"].get(market_id)
                if isinstance(offer, dict):
                    ocr_suggestions[market_id] = self._ocr_suggestions(
                        product, offer
                    )

        priced = [
            (market_id, price)
            for market_id, price in prices.items()
            if price.get("price") is not None
        ]
        best_market = None
        if priced:
            best_market = min(
                priced, key=lambda item: float(item[1]["price"])
            )[0]
        return prices, ocr_suggestions, missing, best_market

    # ------------------------------------------------------------------
    # Budget response
    # ------------------------------------------------------------------
    async def async_get_budget(
        self, entity_id: str | None = None
    ) -> dict[str, Any]:
        entity_id = entity_id or self.todo_entity_id
        entity = self._get_todo_entity(entity_id)
        if entity is None:
            raise HomeAssistantError(
                f"A lista de tarefas {entity_id} não foi encontrada."
            )
        items = entity.todo_items or []
        await self._async_process_items(items)
        self._ocr_score_cache.clear()
        now = _utcnow()
        changed = False
        views: list[dict[str, Any]] = []

        for item in items:
            uid = item.uid
            if not uid:
                continue
            summary = (item.summary or "").strip()
            if not summary:
                continue
            item_data = self.data["items"].get(uid)
            if item_data is None:
                continue
            before_product = item_data.get("product_id")
            before_source = item_data.get("link_source")
            product, suggestions, _score = self._resolve_product(
                item_data, summary
            )
            if (
                before_product != item_data.get("product_id")
                or before_source != item_data.get("link_source")
            ):
                changed = True
            views.append(
                self._build_item_view(item_data, item, product, suggestions)
            )

        market_views = [
            self._build_market_view(market, views) for market in self._markets
        ]
        overall = self._build_overall(market_views, views)
        meta = self._build_meta(views, market_views)
        if changed:
            self._save()
        return {
            "entity_id": entity_id,
            "updated_at": now,
            "markets": market_views,
            "items": views,
            "overall": overall,
            "meta": meta,
        }

    def _build_item_view(
        self,
        item_data: dict[str, Any],
        item: TodoItem,
        product: dict[str, Any] | None,
        suggestions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        summary = (item.summary or "").strip()
        completed = item.status == TodoItemStatus.COMPLETED
        quantity = item_data.get("quantity", 1)
        try:
            quantity = max(0.01, float(quantity))
        except (TypeError, ValueError):
            quantity = 1.0
        prices, ocr_suggestions, missing, best_market = self._prices_for_item(
            item_data, product, completed
        )
        best_price = None
        if best_market and best_market in prices:
            best_price = prices[best_market].get("price")
        abc_suggestions: dict[str, dict[str, Any]] = {}
        atacadao_suggestions: dict[str, dict[str, Any]] = {}
        if product:
            product_prices = self.data["prices"].get(product["id"]) or {}
            for market in self._online_markets:
                market_id = market["id"]
                suggestion = product_prices.get(f"{market_id}_suggestion")
                if not (
                    isinstance(suggestion, dict) and suggestion.get("price")
                ):
                    continue
                if prices.get(market_id):
                    continue
                if market.get("type") == "abc":
                    abc_suggestions[market_id] = suggestion
                elif market.get("type") == "atacadao":
                    atacadao_suggestions[market_id] = suggestion
        google_urls = {
            market["id"]: self._google_url(
                product, market["name"], summary
            )
            for market in self._markets
        }
        return {
            "uid": item_data.get("uid") or item.uid,
            "summary": summary,
            "status": "completed" if completed else "needs_action",
            "completed": completed,
            "quantity": quantity,
            "product": product,
            "brand": (product or {}).get("brand") or "",
            "grams": (product or {}).get("grams"),
            "package": (product or {}).get("package") or "",
            "link_source": item_data.get("link_source"),
            "manual_link": item_data.get("link_source") == "manual",
            "suggestions": suggestions if product is None else [],
            "prices": prices,
            "ocr_suggestions": ocr_suggestions,
            "abc_suggestions": abc_suggestions,
            "atacadao_suggestions": atacadao_suggestions,
            "missing_markets": missing,
            "best_market": best_market,
            "best_price": best_price,
            "google_urls": google_urls,
            "purchased": self._public_purchased(item_data.get("purchased")),
        }

    @staticmethod
    def _public_purchased(purchased: Any) -> dict[str, Any] | None:
        if not isinstance(purchased, dict):
            return None
        return {
            "at": purchased.get("at"),
            "quantity": purchased.get("quantity"),
            "prices": purchased.get("prices") or {},
        }

    def _build_market_view(
        self, market: dict[str, Any], views: list[dict[str, Any]]
    ) -> dict[str, Any]:
        market_id = market["id"]
        total = 0.0
        purchased_total = 0.0
        remaining_total = 0.0
        estimated_total = 0.0
        priced_items = 0
        estimated_items = 0
        ocr_items = 0
        frozen_items = 0
        missing: list[str] = []
        last_refresh: str | None = None

        for view in views:
            price = (view.get("prices") or {}).get(market_id)
            if not price or price.get("price") is None:
                missing.append(view.get("summary") or "Item")
                continue
            quantity = float(view.get("quantity") or 1)
            value = float(price.get("price") or 0) * quantity
            total += value
            priced_items += 1
            if view.get("completed"):
                purchased_total += value
            else:
                remaining_total += value
            if price.get("estimated"):
                estimated_total += value
                estimated_items += 1
            if price.get("source") == "ocr":
                ocr_items += 1
            if price.get("frozen"):
                frozen_items += 1
            updated = price.get("updated_at") or price.get("snapshot_at")
            if updated and (last_refresh is None or updated > last_refresh):
                last_refresh = updated

        offer = self.data["offers"].get(market_id)
        if isinstance(offer, dict) and offer.get("fetched_at"):
            fetched = offer["fetched_at"]
            if last_refresh is None or fetched > last_refresh:
                last_refresh = fetched
        errors: list[str] = []
        if isinstance(offer, dict):
            errors.extend(offer.get("errors") or [])
        errors.extend(
            self.data["meta"].get("market_errors", {}).get(market_id, [])
        )
        errors = list(dict.fromkeys(str(error) for error in errors))[:5]
        items_total = len(views)
        coverage = priced_items / items_total if items_total else 0.0
        flyer_url = market.get("flyer_url")
        valid_from = None
        valid_to = None
        if isinstance(offer, dict):
            flyer_url = (
                offer.get("pdf_url")
                or offer.get("source_url")
                or (offer.get("images") or [None])[0]
                or flyer_url
            )
            valid_from = offer.get("valid_from")
            valid_to = offer.get("valid_to")
        store = None
        if market.get("type") == "abc":
            store = self.data["meta"].get("abc_store")
        elif market.get("type") == "atacadao":
            store = self.data["meta"].get("atacadao_store")
        return {
            "id": market_id,
            "name": market.get("name") or market_id,
            "type": market.get("type"),
            "url": market.get("url"),
            "flyer_url": flyer_url,
            "store": store,
            "total": round(total, 2),
            "purchased": round(purchased_total, 2),
            "remaining": round(remaining_total, 2),
            "estimated_total": round(estimated_total, 2),
            "items_total": items_total,
            "priced_items": priced_items,
            "estimated_items": estimated_items,
            "ocr_items": ocr_items,
            "frozen_items": frozen_items,
            "coverage": round(coverage, 4),
            "average": round(total / priced_items, 2) if priced_items else 0.0,
            "missing": missing[:30],
            "missing_count": len(missing),
            "last_refresh": last_refresh,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "errors": errors,
        }

    def _build_overall(
        self, market_views: list[dict[str, Any]], views: list[dict[str, Any]]
    ) -> dict[str, Any]:
        priced_markets = [
            market for market in market_views if market.get("priced_items", 0) > 0
        ]
        if priced_markets:
            total = sum(market["total"] for market in priced_markets) / len(
                priced_markets
            )
            purchased = sum(
                market["purchased"] for market in priced_markets
            ) / len(priced_markets)
            remaining = sum(
                market["remaining"] for market in priced_markets
            ) / len(priced_markets)
            coverage = sum(
                market["coverage"] for market in priced_markets
            ) / len(priced_markets)
        else:
            total = purchased = remaining = coverage = 0.0

        fully_priced = [
            market
            for market in market_views
            if market.get("items_total")
            and market.get("priced_items") == market["items_total"]
        ]
        if fully_priced:
            best = min(fully_priced, key=lambda market: market["total"])
        elif market_views:
            best = max(
                market_views,
                key=lambda market: (
                    market.get("coverage", 0),
                    -market.get("total", 0),
                ),
            )
        else:
            best = None

        return {
            "total": round(total, 2),
            "purchased": round(purchased, 2),
            "remaining": round(remaining, 2),
            "coverage": round(coverage, 4),
            "items_total": len(views),
            "markets_with_prices": len(priced_markets),
            "best_market": (
                {
                    "id": best["id"],
                    "name": best["name"],
                    "total": best["total"],
                }
                if best
                else None
            ),
        }

    def _build_meta(
        self, views: list[dict[str, Any]], market_views: list[dict[str, Any]]
    ) -> dict[str, Any]:
        needs_refresh = False
        for view in views:
            product = view.get("product")
            if not product:
                continue
            for market in self._markets:
                market_id = market["id"]
                if market.get("type") in ("abc", "atacadao"):
                    source = market.get("type")
                    stored = (
                        self.data["prices"]
                        .get(product["id"], {})
                        .get(market_id)
                    )
                    if isinstance(stored, dict) and stored.get("source") == "manual":
                        continue
                    if (
                        not isinstance(stored, dict)
                        or stored.get("source") != source
                        or _is_stale(
                            stored.get("updated_at"), self.price_ttl_hours
                        )
                    ):
                        needs_refresh = True
                elif market.get("type") in ("assai", "bahamas"):
                    offer = self.data["offers"].get(market_id)
                    if not isinstance(offer, dict) or _is_stale(
                        offer.get("fetched_at"), self.flyer_ttl_hours
                    ):
                        needs_refresh = True
        packages = available_packages()
        available = ocr_available()
        return {
            "todo_entity": self.todo_entity_id,
            "ocr_enabled": self.ocr_enabled,
            "ocr_available": available,
            "ocr_packages": packages,
            "ocr_language": self.ocr_language,
            "default_market": self.default_market_id,
            "default_market_name": (
                self._market(self.default_market_id) or {}
            ).get("name")
            or self.default_market_id,
            "needs_refresh": needs_refresh,
            "refreshing": self._refresh_lock.locked(),
            "last_refresh": self.data["meta"].get("last_refresh"),
            "abc_store": self.data["meta"].get("abc_store"),
            "atacadao_store": self.data["meta"].get("atacadao_store"),
            "catalog_size": len(self.data["catalog"]),
            "errors": (self.data["meta"].get("errors") or [])[-10:],
            "market_errors": self.data["meta"].get("market_errors") or {},
            "price_ttl_hours": self.price_ttl_hours,
            "flyer_ttl_hours": self.flyer_ttl_hours,
            "markets_total": len(market_views),
        }

    @staticmethod
    def _google_url(
        product: dict[str, Any] | None,
        market_name: str,
        summary: str | None = None,
    ) -> str:
        name = (
            product_display_name(product)
            if product
            else (summary or "produto")
        )
        query = quote_plus(f"{name} {market_name} Uberlândia preço")
        return f"https://www.google.com/search?q={query}"

    # ------------------------------------------------------------------
    # Refresh operations
    # ------------------------------------------------------------------
    async def _async_current_products(self) -> list[dict[str, Any]]:
        entity = self._get_todo_entity(self.todo_entity_id)
        if entity is None:
            return []
        items = entity.todo_items or []
        await self._async_process_items(items)
        products: dict[str, dict[str, Any]] = {}
        for item in items:
            uid = item.uid
            if not uid:
                continue
            summary = (item.summary or "").strip()
            if not summary:
                continue
            item_data = self.data["items"].get(uid)
            if item_data is None:
                continue
            product, _suggestions, _score = self._resolve_product(
                item_data, summary
            )
            if product:
                products[product["id"]] = product
        return list(products.values())

    async def async_refresh_prices(
        self,
        market_id: str | None = None,
        force: bool = False,
        source: str = "manual",
    ) -> dict[str, Any]:
        if self._refresh_lock.locked():
            raise HomeAssistantError(
                "Já existe uma atualização de preços em andamento."
            )
        async with self._refresh_lock:
            entity = self._get_todo_entity(self.todo_entity_id)
            if entity is not None:
                await self._async_process_items(entity.todo_items or [])
            selected = [
                market
                for market in self._markets
                if market_id is None or market["id"] == market_id
            ]
            if not selected:
                raise HomeAssistantError(f"Mercado {market_id} não configurado.")
            results: dict[str, Any] = {}
            errors: list[str] = []
            for market in selected:
                mid = market["id"]
                market_type = market.get("type")
                if market_type in ("abc", "atacadao"):
                    result = await self._async_refresh_online_market(
                        mid, force
                    )
                elif market_type in ("assai", "bahamas"):
                    result = await self._async_refresh_flyer_market(mid, force)
                else:
                    result = {"status": "skipped", "reason": "manual"}
                results[mid] = result
                errors.extend(str(error) for error in result.get("errors") or [])
            now = _utcnow()
            self.data["meta"]["last_refresh"] = now
            if errors:
                existing = self.data["meta"].get("errors") or []
                self.data["meta"]["errors"] = (existing + errors)[-10:]
            self._save()
            self._fire_event()
            return {
                "status": "ok",
                "source": source,
                "updated_at": now,
                "markets": results,
                "errors": errors,
            }

    def _online_record(
        self,
        market: dict[str, Any],
        product: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the stored price record for one online search result."""
        provider = self._provider_for(market)
        term = result.get("searched_term") or product_display_name(product)
        return {
            "price": result["price"],
            "source": market.get("type"),
            "updated_at": _utcnow(),
            "product_name": result.get("product_name"),
            "product_id_ext": result.get("product_id"),
            "brand": result.get("brand"),
            "package": result.get("package"),
            "grams": result.get("grams"),
            "unit": result.get("unit"),
            "image": result.get("image"),
            "confidence": result.get("score"),
            "store_id": result.get("store_id"),
            "store_name": result.get("store_name"),
            "searched_term": result.get("searched_term"),
            "url": provider.product_search_url(term) if provider else None,
        }

    def _schedule_default_price(self, product: dict[str, Any] | None) -> None:
        """Look up online prices without blocking the item action."""
        if self._shutdown or not product or not self._online_markets:
            return
        self.hass.async_create_task(self._async_lookup_default_price(product))

    async def _async_lookup_default_price(
        self, product: dict[str, Any], *, force: bool = False
    ) -> None:
        """Fetch and apply real prices for a newly added/linked product."""
        if self._shutdown or not product:
            return
        product_id = product.get("id")
        if not product_id or not self._online_markets:
            return
        if self._refresh_lock.locked():
            return

        changed = False
        async with self._lookup_lock:
            for market in self._online_markets:
                market_id = market["id"]
                provider = self._provider_for(market)
                if provider is None:
                    continue
                stored = self.data["prices"].setdefault(product_id, {}).get(
                    market_id
                )
                if isinstance(stored, dict) and stored.get("source") == "manual":
                    continue
                source = market.get("type")
                if (
                    not force
                    and isinstance(stored, dict)
                    and stored.get("source") == source
                    and not _is_stale(
                        stored.get("updated_at"), self.price_ttl_hours
                    )
                ):
                    continue
                suggestion_key = f"{market_id}_suggestion"
                suggestion = self.data["prices"].setdefault(
                    product_id, {}
                ).get(suggestion_key)
                if (
                    not force
                    and isinstance(suggestion, dict)
                    and not _is_stale(
                        suggestion.get("updated_at"), self.price_ttl_hours
                    )
                ):
                    continue

                try:
                    result, info = await self.hass.async_add_executor_job(
                        provider.search_best, product
                    )
                except Exception as err:  # noqa: BLE001 - lookup failure is non-fatal
                    errors = [f"{product_display_name(product)}: {err}"]
                    self.data["meta"].setdefault("market_errors", {})[
                        market_id
                    ] = errors
                    changed = True
                    continue

                if not result:
                    errors = [
                        str(error) for error in info.get("errors") or []
                    ]
                    if errors:
                        self.data["meta"].setdefault("market_errors", {})[
                            market_id
                        ] = errors[-5:]
                        changed = True
                    continue

                record = self._online_record(market, product, result)
                prices = self.data["prices"].setdefault(product_id, {})
                if float(result.get("score") or 0) >= ONLINE_AUTO_SCORE:
                    prices[market_id] = record
                    prices.pop(suggestion_key, None)
                else:
                    prices[suggestion_key] = record
                self.data["meta"].setdefault("market_errors", {}).pop(
                    market_id, None
                )
                changed = True

        if changed:
            self._save()
            self._fire_event()

    async def _async_refresh_online_market(
        self, market_id: str, force: bool
    ) -> dict[str, Any]:
        market = self._market(market_id)
        provider = self._provider_for(market) if market else None
        if not market or provider is None:
            return {"status": "error", "errors": ["Mercado online não configurado"]}
        products = await self._async_current_products()
        if not products:
            return {
                "status": "skipped",
                "reason": "sem itens vinculados",
                "updated": 0,
                "errors": [],
            }
        store: dict[str, Any] | None = None
        if market.get("type") == "abc":
            try:
                abc_store = await self.hass.async_add_executor_job(
                    provider.choose_store
                )
            except ABCError as err:
                return {"status": "error", "updated": 0, "errors": [str(err)]}
            store = {
                "id": abc_store.get("id"),
                "name": abc_store.get("apelido")
                or abc_store.get("nomeFantasia"),
                "number": abc_store.get("numeroLoja"),
                "distance_km": abc_store.get("_distance_km"),
            }
            self.data["meta"]["abc_store"] = store
        elif market.get("type") == "atacadao":
            store = {
                "name": provider.store_name,
                "postal_code": provider.postal_code,
            }
            self.data["meta"]["atacadao_store"] = store

        source = market.get("type")
        suggestion_key = f"{market_id}_suggestion"
        updated = 0
        skipped = 0
        suggestions = 0
        errors: list[str] = []
        consecutive_errors = 0
        for index, product in enumerate(products):
            product_id = product["id"]
            stored = self.data["prices"].setdefault(product_id, {}).get(market_id)
            if isinstance(stored, dict) and stored.get("source") == "manual":
                skipped += 1
                continue
            if (
                not force
                and isinstance(stored, dict)
                and stored.get("source") == source
                and not _is_stale(stored.get("updated_at"), self.price_ttl_hours)
            ):
                skipped += 1
                continue
            if index:
                await asyncio.sleep(0.25)
            try:
                result, info = await self.hass.async_add_executor_job(
                    provider.search_best, product
                )
            except (ABCError, AtacadaoError) as err:
                errors.append(f"{product_display_name(product)}: {err}")
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    break
                continue
            except Exception as err:  # noqa: BLE001 - provider failure is isolated
                errors.append(f"{product_display_name(product)}: {err}")
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    break
                continue
            consecutive_errors = 0
            if not result:
                suggestions += 1
                errors.extend(info.get("errors") or [])
                continue
            record = self._online_record(market, product, result)
            if float(result.get("score") or 0) >= ONLINE_AUTO_SCORE:
                self.data["prices"].setdefault(product_id, {})[market_id] = record
                updated += 1
            else:
                self.data["prices"].setdefault(product_id, {})[
                    suggestion_key
                ] = record
                suggestions += 1
        if errors:
            self.data["meta"].setdefault("market_errors", {})[market_id] = errors[-5:]
        else:
            self.data["meta"].setdefault("market_errors", {}).pop(market_id, None)
        self._save()
        return {
            "status": "ok",
            "updated": updated,
            "skipped": skipped,
            "suggestions": suggestions,
            "errors": errors[-5:],
            "store": store,
        }

    async def _async_refresh_flyer_market(
        self, market_id: str, force: bool
    ) -> dict[str, Any]:
        market = self._market(market_id)
        if not market:
            return {"status": "error", "errors": ["Mercado não configurado"]}
        if not self.ocr_enabled:
            return {
                "status": "skipped",
                "reason": "OCR desativado",
                "errors": [],
            }
        offer = self.data["offers"].get(market_id)
        if (
            not force
            and isinstance(offer, dict)
            and not _is_stale(offer.get("fetched_at"), self.flyer_ttl_hours)
        ):
            valid_to = offer.get("valid_to")
            if not valid_to or str(valid_to) >= date.today().isoformat():
                return {
                    "status": "skipped",
                    "reason": "emcache",
                    "candidates": len(offer.get("candidates") or []),
                    "errors": [],
                }
        errors: list[str] = []
        try:
            if market.get("type") == "assai":
                if self._assai is None:
                    raise AssaiError("Assaí não configurado")
                result = await self.hass.async_add_executor_job(
                    functools.partial(
                        self._assai.fetch_offers,
                        max_images=self.ocr_max_images,
                        language=self.ocr_language,
                        psm=self.ocr_psm,
                    )
                )
            elif market.get("type") == "bahamas":
                if self._bahamas is None:
                    raise BahamasError("Bahamas não configurado")
                result = await self.hass.async_add_executor_job(
                    functools.partial(
                        self._bahamas.fetch_offers,
                        max_pages=self.ocr_max_pages,
                        language=self.ocr_language,
                        psm=self.ocr_psm,
                    )
                )
            else:
                return {"status": "skipped", "reason": "manual", "errors": []}
        except (AssaiError, BahamasError) as err:
            errors.append(str(err))
        except Exception as err:  # noqa: BLE001 - OCR failure falls back to manual
            errors.append(f"Falha ao atualizar encarte: {err}")
        if errors:
            self.data["meta"].setdefault("market_errors", {})[market_id] = errors
            self._save()
            return {"status": "error", "errors": errors}

        self.data["offers"][market_id] = result
        result_errors = [str(error) for error in result.get("errors") or []]
        if result_errors:
            self.data["meta"].setdefault("market_errors", {})[
                market_id
            ] = result_errors[-5:]
        else:
            self.data["meta"].setdefault("market_errors", {}).pop(market_id, None)
        self._ocr_score_cache.clear()
        self._save()
        return {
            "status": "ok",
            "candidates": len(result.get("candidates") or []),
            "ocr_pages": result.get("ocr_pages"),
            "valid_from": result.get("valid_from"),
            "valid_to": result.get("valid_to"),
            "flyer_url": result.get("pdf_url")
            or (result.get("images") or [None])[0],
            "errors": result_errors[-5:],
        }

    async def async_refresh_flyers(
        self, market_id: str | None = None, force: bool = False
    ) -> dict[str, Any]:
        if self._refresh_lock.locked():
            raise HomeAssistantError(
                "Já existe uma atualização de preços em andamento."
            )
        async with self._refresh_lock:
            selected = [
                market
                for market in self._markets
                if market.get("type") in ("assai", "bahamas")
                and (market_id is None or market["id"] == market_id)
            ]
            if not selected:
                raise HomeAssistantError(
                    "Nenhum mercado com encarte foi configurado para atualizar."
                )
            results: dict[str, Any] = {}
            errors: list[str] = []
            for market in selected:
                result = await self._async_refresh_flyer_market(
                    market["id"], force
                )
                results[market["id"]] = result
                errors.extend(str(error) for error in result.get("errors") or [])
            now = _utcnow()
            self.data["meta"]["last_refresh"] = now
            if errors:
                existing = self.data["meta"].get("errors") or []
                self.data["meta"]["errors"] = (existing + errors)[-10:]
            self._save()
            self._fire_event()
            return {
                "status": "ok",
                "updated_at": now,
                "markets": results,
                "errors": errors,
            }

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------
    def _register_services(self) -> None:
        aliases_schema = vol.All(cv.ensure_list, [cv.string])
        self.hass.services.async_register(
            DOMAIN,
            "set_price",
            self._service_set_price,
            schema=vol.Schema(
                {
                    vol.Required("uid"): cv.string,
                    vol.Required("market"): cv.string,
                    vol.Required("price"): vol.All(
                        vol.Coerce(float), vol.Range(min=0.01)
                    ),
                    vol.Optional("source", default="manual"): vol.In(
                        ["manual", "ocr"]
                    ),
                    vol.Optional("note", default=""): cv.string,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "clear_price",
            self._service_clear_price,
            schema=vol.Schema(
                {
                    vol.Required("uid"): cv.string,
                    vol.Required("market"): cv.string,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "link_item",
            self._service_link_item,
            schema=vol.Schema(
                {
                    vol.Required("uid"): cv.string,
                    vol.Optional("product_id"): cv.string,
                    vol.Optional("name"): cv.string,
                    vol.Optional("brand", default=""): cv.string,
                    vol.Optional("package", default=""): cv.string,
                    vol.Optional("grams"): vol.Coerce(float),
                    vol.Optional("unit", default=""): cv.string,
                    vol.Optional("aliases", default=[]): aliases_schema,
                    vol.Optional("quantity"): vol.All(
                        vol.Coerce(float), vol.Range(min=0.01)
                    ),
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "add_catalog_product",
            self._service_add_catalog_product,
            schema=vol.Schema(
                {
                    vol.Required("name"): cv.string,
                    vol.Optional("brand", default=""): cv.string,
                    vol.Optional("package", default=""): cv.string,
                    vol.Optional("grams"): vol.Coerce(float),
                    vol.Optional("unit", default=""): cv.string,
                    vol.Optional("aliases", default=[]): aliases_schema,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "add_item",
            self._service_add_item,
            schema=vol.Schema(
                {
                    vol.Optional(
                        "entity_id", default=self.todo_entity_id
                    ): cv.entity_id,
                    vol.Optional("summary"): cv.string,
                    vol.Optional("product_id"): cv.string,
                    vol.Optional("quantity", default=1): vol.All(
                        vol.Coerce(float), vol.Range(min=0.01)
                    ),
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "set_quantity",
            self._service_set_quantity,
            schema=vol.Schema(
                {
                    vol.Required("uid"): cv.string,
                    vol.Required("quantity"): vol.All(
                        vol.Coerce(float), vol.Range(min=0.01)
                    ),
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "refresh_prices",
            self._service_refresh_prices,
            schema=vol.Schema(
                {
                    vol.Optional("market"): cv.string,
                    vol.Optional("force", default=False): cv.boolean,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )
        self.hass.services.async_register(
            DOMAIN,
            "refresh_flyers",
            self._service_refresh_flyers,
            schema=vol.Schema(
                {
                    vol.Optional("market"): cv.string,
                    vol.Optional("force", default=False): cv.boolean,
                }
            ),
            supports_response=SupportsResponse.OPTIONAL,
        )

    async def _service_set_price(self, call: ServiceCall) -> dict[str, Any]:
        uid = call.data["uid"]
        item_data = self.data["items"].get(uid)
        if item_data is None:
            raise ServiceValidationError("Item não encontrado na lista.")
        market_id = call.data["market"]
        if self._market(market_id) is None:
            raise ServiceValidationError(f"Mercado {market_id} não configurado.")
        product, _suggestions, _score = self._resolve_product(
            item_data, item_data.get("summary") or ""
        )
        if not product:
            raise ServiceValidationError(
                "Vincule o item a um produto antes de definir o preço."
            )
        price = round(float(call.data["price"]), 2)
        source = call.data.get("source", "manual")
        record = {
            "price": price,
            "source": source,
            "updated_at": _utcnow(),
            "note": call.data.get("note") or "",
            "estimated": source != "manual",
        }
        self.data["prices"].setdefault(product["id"], {})[market_id] = record
        if item_data.get("status") == "completed":
            purchased = item_data.setdefault(
                "purchased",
                {
                    "at": _utcnow(),
                    "prices": {},
                    "quantity": float(item_data.get("quantity", 1) or 1),
                },
            )
            snapshot = dict(record)
            snapshot["frozen"] = True
            snapshot["estimated"] = source != "manual"
            purchased.setdefault("prices", {})[market_id] = snapshot
        self._save()
        self._fire_event()
        return {"product": product, "price": record}

    async def _service_clear_price(self, call: ServiceCall) -> dict[str, Any]:
        uid = call.data["uid"]
        item_data = self.data["items"].get(uid)
        if item_data is None:
            raise ServiceValidationError("Item não encontrado na lista.")
        product, _suggestions, _score = self._resolve_product(
            item_data, item_data.get("summary") or ""
        )
        if not product:
            raise ServiceValidationError("O item não está vinculado a um produto.")
        market_id = call.data["market"]
        product_prices = self.data["prices"].get(product["id"], {})
        product_prices.pop(market_id, None)
        product_prices.pop(f"{market_id}_suggestion", None)
        purchased = item_data.get("purchased")
        if isinstance(purchased, dict):
            (purchased.get("prices") or {}).pop(market_id, None)
        self._save()
        self._fire_event()
        return {"status": "ok"}

    async def _service_link_item(self, call: ServiceCall) -> dict[str, Any]:
        uid = call.data["uid"]
        item_data = self.data["items"].get(uid)
        if item_data is None:
            raise ServiceValidationError("Item não encontrado na lista.")
        product_id = call.data.get("product_id")
        if product_id:
            product = self.catalog.get(product_id)
            if not product:
                raise ServiceValidationError("Produto não encontrado no catálogo.")
        else:
            name = (call.data.get("name") or item_data.get("summary") or "").strip()
            if not name:
                raise ServiceValidationError(
                    "Informe o nome do produto para vincular."
                )
            product = self._ensure_product(
                name=name,
                brand=call.data.get("brand") or "",
                package=call.data.get("package") or "",
                grams=call.data.get("grams"),
                unit=call.data.get("unit") or "",
                aliases=call.data.get("aliases") or [],
            )
        item_data["product_id"] = product["id"]
        item_data["link_source"] = "manual"
        item_data["linked_text_norm"] = normalize(item_data.get("summary") or "")
        if call.data.get("quantity") is not None:
            item_data["quantity"] = float(call.data["quantity"])
        if item_data.get("status") == "completed" and not item_data.get("purchased"):
            self._snapshot_item_prices(item_data, estimated=True)
        self._save()
        self._schedule_default_price(product)
        self._fire_event()
        return {"product": product}

    async def _service_add_catalog_product(
        self, call: ServiceCall
    ) -> dict[str, Any]:
        product = self._ensure_product(
            name=call.data["name"],
            brand=call.data.get("brand") or "",
            package=call.data.get("package") or "",
            grams=call.data.get("grams"),
            unit=call.data.get("unit") or "",
            aliases=call.data.get("aliases") or [],
        )
        return {"product": product}

    async def _service_add_item(self, call: ServiceCall) -> dict[str, Any]:
        entity_id = call.data.get("entity_id") or self.todo_entity_id
        entity = self._get_todo_entity(entity_id)
        if entity is None:
            raise ServiceValidationError(f"Lista {entity_id} não encontrada.")
        if not (
            entity.supported_features & TodoListEntityFeature.CREATE_TODO_ITEM
        ):
            raise ServiceValidationError("A lista não permite criar itens.")
        product = None
        product_id = call.data.get("product_id")
        if product_id:
            product = self.catalog.get(product_id)
            if not product:
                raise ServiceValidationError("Produto não encontrado no catálogo.")
        summary = (call.data.get("summary") or "").strip()
        if not summary and product:
            summary = product_display_name(product)
        if not summary:
            raise ServiceValidationError("Informe o nome do item a adicionar.")
        if product is None:
            product, _suggestions, _score = self.catalog.resolve(summary)
        quantity = float(call.data.get("quantity", 1) or 1)
        before_uids = {
            item.uid for item in (entity.todo_items or []) if item.uid is not None
        }
        await entity.async_create_todo_item(
            TodoItem(summary=summary, status=TodoItemStatus.NEEDS_ACTION)
        )
        new_uid: str | None = None
        for _ in range(20):
            current = entity.todo_items or []
            for item in current:
                if (
                    item.uid
                    and item.uid not in before_uids
                    and normalize(item.summary) == normalize(summary)
                ):
                    new_uid = item.uid
                    break
            if new_uid:
                break
            await asyncio.sleep(0.25)
        if new_uid:
            item_data = self.data["items"].setdefault(
                new_uid,
                {
                    "uid": new_uid,
                    "quantity": quantity,
                    "product_id": None,
                    "link_source": None,
                    "linked_text_norm": None,
                    "purchased": None,
                    "seen": True,
                    "first_seen": _utcnow(),
                    "last_seen": _utcnow(),
                    "summary": summary,
                    "status": "needs_action",
                },
            )
            item_data.update(
                {
                    "summary": summary,
                    "status": "needs_action",
                    "quantity": quantity,
                    "seen": True,
                    "last_seen": _utcnow(),
                }
            )
            if product:
                item_data["product_id"] = product["id"]
                item_data["link_source"] = "manual"
                item_data["linked_text_norm"] = normalize(summary)
        elif product:
            self.data["meta"].setdefault("pending_adds", {})[
                normalize(summary)
            ] = {"product_id": product["id"], "quantity": quantity}
        self._save()
        self._schedule_default_price(product)
        self._fire_event()
        return {
            "uid": new_uid,
            "summary": summary,
            "quantity": quantity,
            "product": product,
        }

    async def _service_set_quantity(self, call: ServiceCall) -> dict[str, Any]:
        uid = call.data["uid"]
        item_data = self.data["items"].get(uid)
        if item_data is None:
            raise ServiceValidationError("Item não encontrado na lista.")
        quantity = float(call.data["quantity"])
        item_data["quantity"] = quantity
        purchased = item_data.get("purchased")
        if isinstance(purchased, dict):
            purchased["quantity"] = quantity
        self._save()
        self._fire_event()
        return {"status": "ok", "quantity": quantity}

    async def _service_refresh_prices(self, call: ServiceCall) -> dict[str, Any]:
        return await self.async_refresh_prices(
            call.data.get("market"),
            bool(call.data.get("force", False)),
            source="service",
        )

    async def _service_refresh_flyers(self, call: ServiceCall) -> dict[str, Any]:
        return await self.async_refresh_flyers(
            call.data.get("market"), bool(call.data.get("force", False))
        )

    def _ensure_product(
        self,
        *,
        name: str,
        brand: str,
        package: str,
        grams: float | None,
        unit: str,
        aliases: list[str],
    ) -> dict[str, Any]:
        name = (name or "").strip()
        brand = (brand or "").strip()
        package = (package or "").strip()
        unit = (unit or "").strip()
        if not name:
            raise ServiceValidationError("Informe o nome do produto.")
        parsed_grams = grams
        parsed_unit = None
        if parsed_grams is None and package:
            parsed_grams, parsed_unit, _label = parse_package(package)
        if not package and parsed_grams:
            if unit == "ml" or (unit or parsed_unit) == "ml":
                package = (
                    f"{parsed_grams / 1000:g} L"
                    if parsed_grams >= 1000
                    else f"{parsed_grams:g} ml"
                )
            else:
                package = (
                    f"{parsed_grams / 1000:g} kg"
                    if parsed_grams >= 1000
                    else f"{parsed_grams:g} g"
                )
        unit = unit or parsed_unit or ("g" if parsed_grams else "un")
        id_parts = [name, brand]
        if package:
            id_parts.append(package)
        elif parsed_grams:
            id_parts.append(f"{float(parsed_grams):g}{unit}")
        product_id = slugify(" ".join(id_parts))
        existing = self.catalog.get(product_id)
        if existing:
            return existing

        clean_aliases = [
            str(alias).strip()
            for alias in aliases or []
            if str(alias or "").strip()
        ]
        product = {
            "id": product_id,
            "name": name,
            "brand": brand,
            "package": package,
            "grams": parsed_grams,
            "unit": unit,
            "aliases": clean_aliases,
            "created_at": _utcnow(),
            "seed": False,
        }
        product = self.catalog.add(product)
        self.data["catalog"][product_id] = dict(product)
        self._save()
        return product

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    async def _async_stop(self, _event: Event) -> None:
        self._shutdown = True
        for unsubscribe in (
            self._unsub_items,
            self._unsub_state,
            self._unsub_initial,
            self._unsub_interval,
        ):
            if unsubscribe is not None:
                unsubscribe()
        if self._processing_task and not self._processing_task.done():
            self._processing_task.cancel()
        for provider in (
            self._abc,
            self._atacadao,
            self._assai,
            self._bahamas,
        ):
            session = getattr(provider, "session", None)
            if session is not None:
                try:
                    session.close()
                except Exception:  # noqa: BLE001 - best effort shutdown
                    pass
