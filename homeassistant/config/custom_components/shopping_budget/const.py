"""Constants for the Shopping Budget integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "shopping_budget"
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = "shopping_budget"
DEFAULT_TODO_ENTITY: Final = "todo.google_keep_lista_de_compras"

DEFAULT_PRICE_TTL_HOURS: Final = 12
DEFAULT_FLYER_TTL_HOURS: Final = 24
DEFAULT_OCR_LANGUAGE: Final = "por"
DEFAULT_OCR_PSM: Final = 6
DEFAULT_OCR_MAX_PAGES: Final = 4
DEFAULT_OCR_MAX_IMAGES: Final = 6
DEFAULT_OCR_MIN_CONFIDENCE: Final = 35
OCR_AUTO_SCORE: Final = 0.58
OCR_SUGGEST_SCORE: Final = 0.30

ABC_DEFAULT_API_URL: Final = "https://apiecommerce.regexsolutions.com.br"
ABC_DEFAULT_SITE_URL: Final = "https://superabconline.com.br"
ABC_DEFAULT_STORE_ID: Final = 909
ABC_DEFAULT_EVERSION: Final = "20261027153817"
ABC_DEFAULT_UID: Final = "379dc9d6-abde-4f12-ae9b-9637177f7e5c"

ASSAI_DEFAULT_PAGE_URL: Final = (
    "https://www.assai.com.br/ofertas/minas-gerais/assai-uberlandia"
)
ASSAI_DEFAULT_JSON_URL: Final = (
    "https://www.assai.com.br/sites/default/files/static/ofertas_assai.json"
)
ASSAI_STORE_NID: Final = 3098
ASSAI_STORE_EID: Final = 12
ASSAI_STORE_LOJA_ID: Final = 158

BAHAMAS_DEFAULT_PAGE_URL: Final = "https://bahamasmix.com.br/regiao/uberlandia/"
BAHAMAS_DEFAULT_FLYERS_URL: Final = "https://bahamasmix.com.br/encartes/"

BROWSER_USER_AGENT: Final = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
