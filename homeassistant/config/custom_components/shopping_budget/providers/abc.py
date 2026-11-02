"""ABC online-store price provider.

The ABC e-commerce frontend encrypts some API responses and needs a matching
SSR session. This module reproduces the small part of that protocol needed to
search the nearest Uberlândia store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import re
import time
from typing import Any
from urllib.parse import quote
import uuid

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import lzstring
import requests

from ..catalog import (
    normalize,
    ocr_match_score,
    parse_package,
    product_display_name,
)
from ..const import (
    ABC_DEFAULT_API_URL,
    ABC_DEFAULT_EVERSION,
    ABC_DEFAULT_SITE_URL,
    ABC_DEFAULT_STORE_ID,
    ABC_DEFAULT_UID,
    BROWSER_USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)
_LZ = lzstring.LZString()


class ABCError(RuntimeError):
    """Raised when the ABC API cannot be used."""


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in kilometres."""
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _js_encode(value: str) -> str:
    """Equivalent of JavaScript encodeURIComponent for JSON headers."""
    return quote(value, safe="-_.!~*'()")


class ABCProvider:
    """Search products/prices in the ABC online store."""

    def __init__(self, config: dict[str, Any], latitude: float, longitude: float):
        self.config = config
        self.latitude = latitude
        self.longitude = longitude
        self.site_url = (config.get("url") or ABC_DEFAULT_SITE_URL).rstrip("/")
        self.api_url = (config.get("api_url") or ABC_DEFAULT_API_URL).rstrip("/")
        self.eversion = config.get("eversion") or ABC_DEFAULT_EVERSION
        self.app_uid = config.get("uid") or ABC_DEFAULT_UID
        self.configured_store_id = config.get("store_id") or ABC_DEFAULT_STORE_ID
        self.auto_store = bool(config.get("auto_store", True))
        self.session: requests.Session | None = None
        self.api_headers: dict[str, str] = {}
        self.passphrase: str | None = None
        self.session_created_at = 0.0
        self.stores: list[dict[str, Any]] = []
        self.store: dict[str, Any] | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # Session / encryption
    # ------------------------------------------------------------------
    def ensure_session(self, force: bool = False) -> None:
        """Create an ABC SSR session and extract the API secret."""
        if (
            not force
            and self.session
            and self.api_headers
            and time.time() - self.session_created_at < 30 * 60
        ):
            return

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        try:
            response = session.get(self.site_url + "/", timeout=40)
        except requests.RequestException as err:
            raise ABCError(f"Não foi possível abrir a loja ABC: {err}") from err
        if response.status_code != 200:
            raise ABCError(f"A loja ABC respondeu HTTP {response.status_code}.")
        html = response.text

        state_match = re.search(
            r'<script id="ng-state" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not state_match:
            raise ABCError("Não encontrei o estado SSR da loja ABC.")
        try:
            state = json.loads(state_match.group(1))
        except json.JSONDecodeError as err:
            raise ABCError("O estado SSR da loja ABC não é um JSON válido.") from err

        uid_sessao = ""
        bootstrap: dict[str, Any] | None = None
        passphrase: str | None = None
        for raw_key, value in state.items():
            key = raw_key
            try:
                decoded_key = base64.b64decode(raw_key).decode("utf-8")
                if decoded_key.startswith(("init-app-", "/ecommerce/")):
                    key = decoded_key
            except Exception:  # noqa: BLE001 - base64 decoding is best effort
                pass

            if key.startswith("init-app-uid-sessao"):
                if isinstance(value, dict) and isinstance(value.get("data"), str):
                    try:
                        uid_sessao = json.loads(
                            _LZ.decompressFromBase64(value["data"])
                        )["uidSessao"]
                    except Exception:  # noqa: BLE001
                        pass
            elif key.startswith(
                "/ecommerce/usuarioFidelidade/ecommerce/bootstrapSite"
            ):
                if isinstance(value, dict) and isinstance(value.get("data"), str):
                    try:
                        bootstrap = json.loads(
                            _LZ.decompressFromBase64(value["data"])
                        )
                    except Exception as err:  # noqa: BLE001
                        raise ABCError(
                            "Não consegui descompactar o bootstrap da ABC."
                        ) from err
            elif raw_key == "CYUwzgxg9gdgLlIA" or key == "CYUwzgxg9gdgLlIA":
                if isinstance(value, str):
                    try:
                        passphrase = _LZ.decompressFromBase64(value)
                    except Exception:  # noqa: BLE001
                        pass

        if not bootstrap:
            raise ABCError("A ABC não retornou os dados de bootstrap.")
        sessao = bootstrap.get("sessao")
        usuario = bootstrap.get("usuario") or {}
        token = usuario.get("token")
        if not sessao or not token:
            raise ABCError("A sessão anônima da ABC não foi encontrada.")
        if not passphrase:
            raise ABCError("A chave de descriptografia da ABC não foi encontrada.")

        uid_match = re.search(
            r"/production/ecommerce/([0-9a-f]{8}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/",
            html,
        )
        if uid_match:
            self.app_uid = uid_match.group(1)

        session.cookies.set(
            "sessaoEcommerce",
            sessao,
            domain="superabconline.com.br",
            path="/",
        )
        self.session = session
        self.passphrase = passphrase
        self.session_created_at = time.time()
        self.api_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": BROWSER_USER_AGENT,
            "Accept-Language": "pt-BR,pt;q=0.9",
            "uid": self.app_uid,
            "aud": "lojaonlinte",
            "origem": "site",
            "eversion": self.eversion,
            "Fingerprint": str(uuid.uuid4()),
            "Sessao": sessao,
            "sessao-ssr": self.eversion,
            "uidSecao": uid_sessao,
            "Authorization": token,
            "Origin": self.site_url,
            "Referer": self.site_url + "/",
        }
        self.store = None
        self.stores = []
        self.last_error = None

    def _decrypt(self, payload: Any) -> Any:
        """Decrypt an ABC response if needed."""
        if not isinstance(payload, dict) or "encrypted" not in payload:
            return payload
        if not self.passphrase:
            raise ABCError("Chave de descriptografia ausente.")
        try:
            first = base64.b64decode(payload["encrypted"])
            raw = base64.b64decode(first)
            if raw[:8] != b"Salted__":
                raise ValueError("envelope sem Salted__")
            salt = raw[8:16]
            ciphertext = raw[16:]
            key, iv = self._evp_bytes(self.passphrase.encode(), salt)
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            plain = unpadder.update(padded) + unpadder.finalize()
            return json.loads(plain.decode("utf-8"))
        except Exception as err:  # noqa: BLE001
            raise ABCError(f"Falha ao descriptografar resposta da ABC: {err}") from err

    @staticmethod
    def _evp_bytes(
        passphrase: bytes, salt: bytes, key_len: int = 32, iv_len: int = 16
    ) -> tuple[bytes, bytes]:
        """OpenSSL EVP_BytesToKey with MD5 (same as CryptoJS)."""
        data = b""
        previous = b""
        while len(data) < key_len + iv_len:
            previous = hashlib.md5(previous + passphrase + salt).digest()
            data += previous
        return data[:key_len], data[key_len : key_len + iv_len]

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        retry: bool = True,
    ) -> Any:
        self.ensure_session()
        assert self.session is not None
        headers = dict(self.api_headers)
        if extra_headers:
            headers.update(extra_headers)
        try:
            response = self.session.request(
                method,
                self.api_url + path,
                headers=headers,
                params=params,
                json=body,
                timeout=45,
            )
        except requests.RequestException as err:
            raise ABCError(f"Falha de rede na API da ABC: {err}") from err

        if response.status_code in (401, 403) and retry:
            _LOGGER.debug("ABC returned %s, refreshing session", response.status_code)
            self.ensure_session(force=True)
            return self._request(
                method,
                path,
                params=params,
                body=body,
                extra_headers=extra_headers,
                retry=False,
            )
        if response.status_code != 200:
            snippet = re.sub(r"\s+", " ", response.text)[:300]
            self.last_error = f"HTTP {response.status_code}: {snippet}"
            raise ABCError(
                f"A API da ABC respondeu HTTP {response.status_code}: {snippet}"
            )
        try:
            payload = response.json()
        except ValueError as err:
            raise ABCError("A API da ABC não retornou JSON.") from err
        return self._decrypt(payload)

    def get_stores(self) -> list[dict[str, Any]]:
        """Fetch store list and keep the nearest Uberlândia stores."""
        data = self._request(
            "GET",
            "/ecommerce/filial/lojasDelivery",
            params={
                "tipoFilial": 1,
                "localizacao": "true",
                "lat": self.latitude,
                "lng": self.longitude,
            },
        )
        stores = data if isinstance(data, list) else data.get("lojas", [])
        result: list[dict[str, Any]] = []
        for store in stores:
            if not isinstance(store, dict):
                continue
            lat = store.get("lat")
            lng = store.get("lng")
            if lat is not None and lng is not None:
                try:
                    store["_distance_km"] = round(
                        _distance_km(
                            self.latitude,
                            self.longitude,
                            float(lat),
                            float(lng),
                        ),
                        2,
                    )
                except (TypeError, ValueError):
                    store["_distance_km"] = None
            result.append(store)
        self.stores = result
        return result

    def choose_store(self) -> dict[str, Any]:
        """Pick the configured store, or the nearest Uberlândia store."""
        if self.store:
            return self.store
        stores = self.get_stores()
        if not stores:
            raise ABCError("A ABC não retornou lojas.")

        if not self.auto_store:
            for store in stores:
                if store.get("id") == self.configured_store_id:
                    self.store = store
                    return store

        candidates = [
            store
            for store in stores
            if store.get("uf") == "MG"
            and (
                str(store.get("cidadeId")) == "3170206"
                or "uberl" in normalize(store.get("cidade"))
                or "uberl" in normalize(store.get("apelido"))
            )
        ]
        if not candidates:
            candidates = [
                store
                for store in stores
                if "uberl" in normalize(store.get("apelido"))
                or "uberl" in normalize(store.get("endereco"))
            ]
        if not candidates:
            candidates = stores

        def rank(store: dict[str, Any]) -> tuple[int, float]:
            active = 1 if store.get("deliveryAtivo") and store.get("cargaEcommerce") else 0
            distance = store.get("_distance_km")
            return (active, -(distance if distance is not None else 9999))

        candidates.sort(key=rank, reverse=True)
        selected = candidates[0]
        configured = next(
            (store for store in stores if store.get("id") == self.configured_store_id),
            None,
        )
        # If the configured store is in Uberlândia and usable, prefer it. This
        # keeps prices stable while still allowing auto-selection on first run.
        if configured and configured in candidates:
            selected = configured
        self.store = selected
        return selected

    def search(self, term: str, store_id: int, per_page: int = 20) -> list[dict[str, Any]]:
        """Search products in one store."""
        body = {
            "filialId": store_id,
            "page": 0,
            "perPage": per_page,
            "ordenacao": 1,
            "busca": term,
            "sugestao": False,
            "tokenPaginaAtual": "",
        }
        data = self._request(
            "POST",
            "/ecommerce/produto/lista",
            body=body,
            extra_headers={
                "x-payload": _js_encode(
                    json.dumps(body, ensure_ascii=False, separators=(",", ":"))
                )
            },
        )
        if not isinstance(data, dict):
            return []
        products = data.get("produtos") or []
        return [product for product in products if isinstance(product, dict)]

    @staticmethod
    def _candidate_text(product: dict[str, Any]) -> str:
        desc = (
            product.get("descricaoApp")
            or product.get("descricao")
            or product.get("nome")
            or ""
        )
        brand = product.get("marca") or ""
        return f"{desc} {brand}".strip()

    def _score_candidate(
        self, catalog_product: dict[str, Any], candidate: dict[str, Any]
    ) -> float:
        text = self._candidate_text(candidate)
        if not text:
            return 0.0
        score = ocr_match_score(catalog_product, text, 0.85)
        brand = (catalog_product.get("brand") or "").strip()
        candidate_brand = (candidate.get("marca") or "").strip()
        if brand and candidate_brand:
            brand_norm = normalize(brand)
            candidate_brand_norm = normalize(candidate_brand)
            if (
                brand_norm not in candidate_brand_norm
                and candidate_brand_norm not in brand_norm
            ):
                score -= 0.12
        if candidate.get("disponivel") in (0, False):
            score -= 0.12
        if not candidate.get("precoVenda"):
            score -= 1.0
        return max(0.0, min(1.0, score))

    def search_best(
        self, catalog_product: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Find the best ABC product/price for a catalog product."""
        store = self.choose_store()
        store_id = int(store.get("id") or self.configured_store_id)
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
        best: tuple[float, dict[str, Any], str] | None = None
        errors: list[str] = []
        for query in queries:
            query_norm = normalize(query)
            if not query_norm or query_norm in seen_queries:
                continue
            seen_queries.add(query_norm)
            try:
                candidates = self.search(query, store_id)
            except ABCError as err:
                errors.append(str(err))
                _LOGGER.warning("ABC search '%s' failed: %s", query, err)
                continue

            for candidate in candidates:
                score = self._score_candidate(catalog_product, candidate)
                if score <= 0:
                    continue
                price = candidate.get("precoVenda")
                try:
                    price_float = float(price)
                except (TypeError, ValueError):
                    continue
                if price_float <= 0:
                    continue
                if best is None or score > best[0]:
                    best = (score, candidate, query)

            if best and best[0] >= 0.80:
                break

        info = {
            "store_id": store_id,
            "store_name": store.get("apelido") or store.get("nomeFantasia"),
            "store_number": store.get("numeroLoja"),
            "store_distance_km": store.get("_distance_km"),
            "searched_terms": list(seen_queries),
            "errors": errors,
        }
        if not best:
            return None, info

        score, candidate, query = best
        text = self._candidate_text(candidate)
        grams, unit, package = parse_package(text)
        if not package:
            package = candidate.get("gramatura") or candidate.get("quantidade")
        return (
            {
                "price": round(float(candidate["precoVenda"]), 2),
                "product_name": text,
                "product_id": candidate.get("produtoId"),
                "brand": candidate.get("marca") or "",
                "package": package or "",
                "grams": grams,
                "unit": unit,
                "image": candidate.get("imagem"),
                "score": round(score, 4),
                "searched_term": query,
                "store_id": store_id,
                "store_name": store.get("apelido") or store.get("nomeFantasia"),
                "store_number": store.get("numeroLoja"),
                "store_distance_km": store.get("_distance_km"),
            },
            info,
        )

    def product_search_url(self, term: str) -> str:
        """Public search URL for a product term."""
        return f"{self.site_url}/p/busca/{quote(term, safe='')}"
