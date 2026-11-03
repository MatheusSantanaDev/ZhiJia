"""Atacadão online-store price provider.

The Atacadão storefront is a VTEX store. Its public search API returns the
catalog, but the price shown there is not tied to the customer's address. For
an Uberlândia price we simulate delivery with the configured postal code and
use the resulting item price from the local seller.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from ..catalog import normalize, ocr_match_score, parse_package, product_display_name
from ..const import BROWSER_USER_AGENT

_LOGGER = logging.getLogger(__name__)

ATACADAO_DEFAULT_SITE_URL = "https://www.atacadao.com.br"
ATACADAO_SEARCH_PATH = "/api/io/_v/api/intelligent-search/product_search/"
ATACADAO_SIMULATION_PATH = "/api/checkout/pub/orderForms/simulation"
ATACADAO_MAX_SIMULATIONS = 3


class AtacadaoError(RuntimeError):
    """Raised when the Atacadão API cannot be used."""


class AtacadaoProvider:
    """Search products and resolve local prices in the Atacadão store."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.site_url = (config.get("url") or ATACADAO_DEFAULT_SITE_URL).rstrip("/")
        self.api_url = (config.get("api_url") or ATACADAO_DEFAULT_SITE_URL).rstrip("/")
        self.postal_code = re.sub(r"\D", "", str(config.get("postal_code") or ""))
        self.store_name = config.get("store_name") or "Atacadão Uberlândia"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Origin": self.site_url,
                "Referer": self.site_url + "/",
            }
        )
        self.last_error: str | None = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: int = 35,
    ) -> Any:
        try:
            response = self.session.request(
                method,
                self.api_url + path,
                params=params,
                json=body,
                timeout=timeout,
            )
        except requests.RequestException as err:
            raise AtacadaoError(f"Falha de rede no Atacadão: {err}") from err
        if response.status_code != 200:
            snippet = re.sub(r"\s+", " ", response.text)[:240]
            self.last_error = f"HTTP {response.status_code}: {snippet}"
            raise AtacadaoError(
                f"A API do Atacadão respondeu HTTP {response.status_code}: {snippet}"
            )
        try:
            return response.json()
        except ValueError as err:
            raise AtacadaoError("A API do Atacadão não retornou JSON.") from err

    def search(self, term: str, per_page: int = 20) -> list[dict[str, Any]]:
        """Return catalog candidates for a search term."""
        payload = self._request(
            "GET",
            ATACADAO_SEARCH_PATH,
            params={
                "query": term,
                "locale": "pt-BR",
                "count": max(1, min(50, per_page)),
            },
        )
        if not isinstance(payload, dict):
            return []
        products = payload.get("products") or []
        return [product for product in products if isinstance(product, dict)]

    @staticmethod
    def _candidate_text(candidate: dict[str, Any]) -> str:
        return (
            f"{candidate.get('product_name', '')} {candidate.get('brand', '')}".strip()
        )

    @staticmethod
    def _candidates_from_product(
        product: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in product.get("items") or []:
            if not isinstance(item, dict):
                continue
            sellers = item.get("sellers") or []
            if not sellers or not isinstance(sellers[0], dict):
                continue
            seller = sellers[0]
            offer = seller.get("commertialOffer") or {}
            if not offer.get("Price"):
                continue
            images = item.get("images") or []
            image = ""
            if images and isinstance(images[0], dict):
                image = images[0].get("imageUrl") or ""
            candidates.append(
                {
                    "product_name": product.get("productName") or "",
                    "brand": product.get("brand") or "",
                    "product_id": item.get("itemId"),
                    "seller_id": seller.get("sellerId"),
                    "search_price": offer.get("Price"),
                    "image": image,
                    "link": product.get("link") or "",
                }
            )
        return candidates

    def _score_candidate(
        self, catalog_product: dict[str, Any], candidate: dict[str, Any]
    ) -> float:
        text = self._candidate_text(candidate)
        if not text:
            return 0.0
        score = ocr_match_score(catalog_product, text, 0.85)
        brand = (catalog_product.get("brand") or "").strip()
        candidate_brand = (candidate.get("brand") or "").strip()
        if brand and candidate_brand:
            product_brand = normalize(brand)
            offer_brand = normalize(candidate_brand)
            if product_brand not in offer_brand and offer_brand not in product_brand:
                score -= 0.12
        if not candidate.get("search_price"):
            score -= 1.0
        return max(0.0, min(1.0, score))

    def _simulate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        """Return the local item/price for a candidate, if deliverable."""
        if not self.postal_code:
            raise AtacadaoError(
                "Configure postal_code do Atacadão para consultar o preço local."
            )
        item_id = candidate.get("product_id")
        seller_id = candidate.get("seller_id")
        if not item_id or not seller_id:
            return None
        payload = self._request(
            "POST",
            ATACADAO_SIMULATION_PATH,
            params={"sc": 1},
            body={
                "items": [
                    {
                        "id": str(item_id),
                        "quantity": 1,
                        "seller": str(seller_id),
                    }
                ],
                "postalCode": self.postal_code,
                "country": "BRA",
            },
        )
        if not isinstance(payload, dict):
            return None
        items = payload.get("items") or []
        if not items or not isinstance(items[0], dict):
            return None
        local = items[0]
        raw_price = local.get("price")
        if raw_price is None:
            raw_price = local.get("sellingPrice")
        try:
            price = float(raw_price) / 100.0
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        return {
            "price": price,
            "seller": local.get("seller") or seller_id,
            "logistics": payload.get("logisticsInfo") or [],
        }

    def search_best(
        self, catalog_product: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Find the best Atacadão product and its local delivery price."""
        display = product_display_name(catalog_product)
        brand = (catalog_product.get("brand") or "").strip()
        aliases = [
            alias
            for alias in catalog_product.get("aliases") or []
            if alias and normalize(alias) not in normalize(display)
        ][:2]

        queries: list[str] = [display]
        if catalog_product.get("name"):
            queries.append(str(catalog_product["name"]))
        if brand:
            queries.append(brand)
        queries.extend(aliases)

        seen_queries: set[str] = set()
        candidates: dict[str, tuple[float, dict[str, Any], str]] = {}
        errors: list[str] = []
        for query in queries:
            query_norm = normalize(query)
            if not query_norm or query_norm in seen_queries:
                continue
            seen_queries.add(query_norm)
            try:
                products = self.search(query)
            except AtacadaoError as err:
                errors.append(str(err))
                _LOGGER.warning("Atacadão search '%s' failed: %s", query, err)
                continue
            for product in products:
                for candidate in self._candidates_from_product(product):
                    key = str(candidate.get("product_id") or "")
                    if not key:
                        continue
                    score = self._score_candidate(catalog_product, candidate)
                    if score <= 0:
                        continue
                    previous = candidates.get(key)
                    if previous is None or score > previous[0]:
                        candidates[key] = (score, candidate, query)
            if candidates and max(entry[0] for entry in candidates.values()) >= 0.80:
                break

        ordered = sorted(candidates.values(), key=lambda entry: entry[0], reverse=True)
        best: tuple[float, dict[str, Any], dict[str, Any], str] | None = None
        for score, candidate, query in ordered[:ATACADAO_MAX_SIMULATIONS]:
            try:
                local = self._simulate(candidate)
            except AtacadaoError as err:
                errors.append(str(err))
                _LOGGER.warning(
                    "Atacadão simulation for '%s' failed: %s",
                    candidate.get("product_name"),
                    err,
                )
                continue
            if not local:
                continue
            if best is None or score > best[0]:
                best = (score, candidate, local, query)
                if score >= 0.80:
                    break

        info = {
            "store_name": self.store_name,
            "postal_code": self.postal_code,
            "searched_terms": list(seen_queries),
            "errors": errors,
        }
        if not best:
            return None, info

        score, candidate, local, query = best
        text = self._candidate_text(candidate)
        grams, unit, package = parse_package(text)
        return (
            {
                "price": round(float(local["price"]), 2),
                "product_name": text,
                "product_id": candidate.get("product_id"),
                "brand": candidate.get("brand") or "",
                "package": package or "",
                "grams": grams,
                "unit": unit,
                "image": candidate.get("image") or "",
                "score": round(score, 4),
                "searched_term": query,
                "store_id": local.get("seller"),
                "store_name": self.store_name,
                "store_number": None,
                "store_distance_km": None,
            },
            info,
        )

    def product_search_url(self, term: str) -> str:
        """Public search URL for a product term."""
        return f"{self.site_url}/s?q={quote(term, safe='')}"
