"""Assaí flyer OCR provider."""

from __future__ import annotations

from datetime import date, datetime
import logging
import os
from pathlib import Path
import random
import re
import tempfile
import time
from typing import Any
import requests

from ..const import (
    ASSAI_DEFAULT_JSON_URL,
    ASSAI_DEFAULT_PAGE_URL,
    ASSAI_STORE_EID,
    ASSAI_STORE_LOJA_ID,
    ASSAI_STORE_NID,
    BROWSER_USER_AGENT,
)
from ..ocr import OCRPage, OCRUnavailableError, ocr_image, parse_candidates

_LOGGER = logging.getLogger(__name__)


class AssaiError(RuntimeError):
    """Raised when Assaí offers cannot be loaded."""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class AssaiProvider:
    """Fetch current Assaí flyers and OCR their product/price grid."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.page_url = (config.get("url") or ASSAI_DEFAULT_PAGE_URL).rstrip("/")
        self.json_url = config.get("flyer_url") or ASSAI_DEFAULT_JSON_URL
        self.store_nid = config.get("store_nid") or ASSAI_STORE_NID
        self.store_eid = config.get("store_eid") or ASSAI_STORE_EID
        self.store_loja_id = config.get("store_loja_id") or ASSAI_STORE_LOJA_ID
        self.session: requests.Session | None = None

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        return session

    def _fetch_json(self) -> dict[str, Any]:
        session = self._new_session()
        try:
            page = session.get(self.page_url, timeout=40)
        except requests.RequestException as err:
            raise AssaiError(f"Não foi possível abrir a página do Assaí: {err}") from err
        if page.status_code != 200:
            raise AssaiError(f"A página do Assaí respondeu HTTP {page.status_code}.")
        self.session = session

        json_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.page_url,
            "Origin": "https://www.assai.com.br",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Connection": "keep-alive",
        }
        last_error: Exception | None = None
        for attempt in range(2):
            cache_buster = f"{int(time.time() * 1000)}-{random.randint(0, 999999)}"
            url = f"{self.json_url}?{cache_buster}"
            try:
                response = session.get(url, headers=json_headers, timeout=40)
            except requests.RequestException as err:
                last_error = err
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as err:
                    last_error = err
                    continue
            last_error = AssaiError(
                f"Ofertas do Assaí responderam HTTP {response.status_code}"
            )
            # The anti-bot cookies may have expired. Refresh the page once and
            # retry with the same browser-like headers.
            if attempt == 0:
                try:
                    session.get(self.page_url, timeout=40)
                except requests.RequestException:
                    pass
        raise AssaiError(f"Não foi possível ler as ofertas do Assaí: {last_error}")

    def _store_matches(self, store: dict[str, Any]) -> bool:
        checks = []
        if self.store_nid:
            checks.append(str(store.get("nid")) == str(self.store_nid))
        if self.store_eid:
            checks.append(str(store.get("eid")) == str(self.store_eid))
        if self.store_loja_id:
            checks.append(str(store.get("loja_id")) == str(self.store_loja_id))
        return all(checks) if checks else True

    def fetch_offers(
        self, *, max_images: int = 6, language: str = "por", psm: int = 6
    ) -> dict[str, Any]:
        """Fetch current offers for the configured store and OCR the flyers."""
        payload = self._fetch_json()
        raw_offers = payload.get("ofertas") or []
        today = date.today()
        matching: list[dict[str, Any]] = []
        for offer in raw_offers:
            if not isinstance(offer, dict):
                continue
            stores = offer.get("lojas") or []
            if not any(
                isinstance(store, dict) and self._store_matches(store)
                for store in stores
            ):
                continue
            start = _parse_date(offer.get("start_date"))
            end = _parse_date(offer.get("end_date"))
            if start and end and not (start <= today <= end):
                continue
            matching.append(offer)

        if not matching:
            raise AssaiError(
                "Nenhum encarte atual foi encontrado para a loja Assaí Uberlândia."
            )

        matching.sort(
            key=lambda offer: (
                _parse_date(offer.get("end_date")) or date.min,
                _parse_date(offer.get("start_date")) or date.min,
                str(offer.get("id") or ""),
            ),
            reverse=True,
        )

        image_urls: list[str] = []
        selected_offers: list[dict[str, Any]] = []
        for offer in matching:
            images = [
                image.get("url")
                for image in offer.get("images") or []
                if isinstance(image, dict) and image.get("url")
            ]
            if not images:
                continue
            selected_offers.append(
                {
                    "id": offer.get("id"),
                    "title": offer.get("title") or "",
                    "start_date": offer.get("start_date"),
                    "end_date": offer.get("end_date"),
                    "custom_text": offer.get("custom_text") or "",
                    "images": images,
                }
            )
            for image_url in images:
                if image_url not in image_urls:
                    image_urls.append(image_url)
                if len(image_urls) >= max_images:
                    break
            if len(image_urls) >= max_images:
                break

        if not image_urls:
            raise AssaiError("Os encartes do Assaí não contêm imagens.")

        ocr_pages: list[OCRPage] = []
        errors: list[str] = []
        downloaded: list[tuple[str, Path]] = []
        with tempfile.TemporaryDirectory(prefix="shopping_budget_assai_") as temp_dir:
            for index, image_url in enumerate(image_urls, start=1):
                suffix = os.path.splitext(image_url.split("?")[0])[1] or ".jpg"
                path = Path(temp_dir) / f"assai_{index}{suffix}"
                try:
                    response = requests.get(
                        image_url,
                        headers={"User-Agent": BROWSER_USER_AGENT},
                        timeout=60,
                    )
                    response.raise_for_status()
                    path.write_bytes(response.content)
                    downloaded.append((image_url, path))
                except (requests.RequestException, OSError) as err:
                    errors.append(f"Falha ao baixar imagem {image_url}: {err}")
                    continue

            for index, (image_url, path) in enumerate(downloaded, start=1):
                try:
                    page = ocr_image(
                        path,
                        language=language,
                        psm=psm,
                        page_number=index,
                    )
                    # Keep the source URL available for debugging/preview.
                    page.text = f"[{image_url}]\n{page.text}"
                    ocr_pages.append(page)
                except (OCRUnavailableError, OSError) as err:
                    errors.append(f"OCR da imagem {index}: {err}")

        candidates = parse_candidates(ocr_pages) if ocr_pages else []
        raw_text = "\n\n".join(page.text for page in ocr_pages)
        if len(raw_text) > 250_000:
            raw_text = raw_text[:250_000]

        starts = [
            value
            for offer in selected_offers
            if (value := _parse_date(offer.get("start_date"))) is not None
        ]
        ends = [
            value
            for offer in selected_offers
            if (value := _parse_date(offer.get("end_date"))) is not None
        ]
        return {
            "fetched_at": datetime.now().astimezone().isoformat(),
            "source_url": self.page_url,
            "pdf_url": None,
            "images": image_urls,
            "offers": selected_offers,
            "valid_from": min(starts).isoformat() if starts else None,
            "valid_to": max(ends).isoformat() if ends else None,
            "candidates": candidates,
            "raw_text": raw_text,
            "errors": errors,
            "ocr_pages": len(ocr_pages),
        }
