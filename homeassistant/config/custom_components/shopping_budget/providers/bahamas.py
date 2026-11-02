"""Bahamas flyer OCR provider."""

from __future__ import annotations

from datetime import date, datetime
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import requests

from ..const import (
    BAHAMAS_DEFAULT_FLYERS_URL,
    BAHAMAS_DEFAULT_PAGE_URL,
    BROWSER_USER_AGENT,
)
from ..ocr import OCRPage, OCRUnavailableError, ocr_pdf, parse_candidates

_LOGGER = logging.getLogger(__name__)


class BahamasError(RuntimeError):
    """Raised when Bahamas flyers cannot be loaded."""


def _flyer_dates(url: str) -> tuple[str | None, str | None]:
    """Try to infer the validity range from the flyer filename.

    The common pattern is ``...-02-A-08-11.pdf`` (02 to 08 November).
    """
    filename = os.path.basename(url.split("?")[0])
    match = re.search(
        r"(\d{2})[-_aA]+(\d{2})[-_aA]+(\d{2})",
        filename,
    )
    if not match:
        return None, None
    start_day, end_day, month = (int(part) for part in match.groups())
    today = date.today()
    year = today.year
    try:
        start = date(year, month, start_day)
        end = date(year, month, end_day)
    except ValueError:
        return None, None
    # A flyer from December viewed in January belongs to the previous year.
    if start > today.replace(month=12, day=31):
        start = start.replace(year=year - 1)
        end = end.replace(year=year - 1)
    return start.isoformat(), end.isoformat()


class BahamasProvider:
    """Fetch the current Bahamas flyer and OCR its product grid."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.page_url = (config.get("url") or BAHAMAS_DEFAULT_PAGE_URL).rstrip("/")
        self.flyers_url = (
            config.get("flyers_url") or BAHAMAS_DEFAULT_FLYERS_URL
        ).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": BROWSER_USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def _choose_pdf(self, links: list[str]) -> str:
        unique: list[str] = []
        for link in links:
            if link not in unique:
                unique.append(link)
        if not unique:
            raise BahamasError("Nenhum encarte em PDF foi encontrado na Bahamas.")

        main = [
            link
            for link in unique
            if "SEMANAL" in link.upper()
            and "MIX" in link.upper()
            and "ESPECIAL" not in link.upper()
            and "BOX" not in link.upper()
        ]
        if main:
            return main[0]
        weekly = [
            link
            for link in unique
            if "SEMANAL" in link.upper()
            and "ESPECIAL" not in link.upper()
            and "BOX" not in link.upper()
        ]
        if weekly:
            return weekly[0]
        return unique[0]

    def fetch_offers(
        self, *, max_pages: int = 4, language: str = "por", psm: int = 6
    ) -> dict[str, Any]:
        """Fetch the current PDF flyer and OCR it."""
        try:
            response = self.session.get(self.flyers_url, timeout=40)
        except requests.RequestException as err:
            raise BahamasError(
                f"Não foi possível abrir a página de encartes da Bahamas: {err}"
            ) from err
        if response.status_code != 200:
            raise BahamasError(
                f"A página de encartes da Bahamas respondeu HTTP {response.status_code}."
            )
        links = re.findall(
            r"https?://[^\"'<> ]+\.pdf(?:\?[^\"'<> ]*)?",
            response.text,
            flags=re.IGNORECASE,
        )
        pdf_url = self._choose_pdf(links)
        errors: list[str] = []
        pages: list[OCRPage] = []
        with tempfile.TemporaryDirectory(prefix="shopping_budget_bahamas_") as temp_dir:
            pdf_path = Path(temp_dir) / "bahamas.pdf"
            try:
                pdf_response = self.session.get(pdf_url, timeout=90)
                pdf_response.raise_for_status()
                pdf_path.write_bytes(pdf_response.content)
            except (requests.RequestException, OSError) as err:
                raise BahamasError(f"Falha ao baixar o encarte da Bahamas: {err}") from err

            try:
                pages = ocr_pdf(
                    pdf_path,
                    language=language,
                    psm=psm,
                    max_pages=max_pages,
                )
                for page in pages:
                    page.text = f"[{pdf_url}]\n{page.text}"
            except (OCRUnavailableError, OSError) as err:
                errors.append(f"OCR do encarte: {err}")

        candidates = parse_candidates(pages) if pages else []
        raw_text = "\n\n".join(page.text for page in pages)
        if len(raw_text) > 250_000:
            raw_text = raw_text[:250_000]
        valid_from, valid_to = _flyer_dates(pdf_url)
        return {
            "fetched_at": datetime.now().astimezone().isoformat(),
            "source_url": self.flyers_url,
            "pdf_url": pdf_url,
            "images": [],
            "offers": [],
            "valid_from": valid_from,
            "valid_to": valid_to,
            "candidates": candidates,
            "raw_text": raw_text,
            "errors": errors,
            "ocr_pages": len(pages),
        }
