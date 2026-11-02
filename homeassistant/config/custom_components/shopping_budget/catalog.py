"""Product catalog, aliases and matching helpers for Shopping Budget."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any

_PACKAGE_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>kg|g|mg|ml|l|litros?|un|unid(?:ade|ades)?|"
    r"rolos?|folhas?|capsulas?|sach[eê]s?|pacotes?|caixas?)\b",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
    "tipo",
    "pacote",
    "unidade",
    "unidades",
    "cada",
    "apenas",
    "preco",
    "precos",
    "oferta",
    "promocao",
    "leve",
    "pague",
}

_MARKETING_RE = re.compile(
    r"\b(?:r\$|rs|r5|r8|cada|no app|meu assai|meu assa[ií]|"
    r"valido|v[aá]lido|ate|at[eé]|desconto|leve|pague|apenas|"
    r"a partir de|preco|precos|oferta|promocao|clube|app|"
    r"por|de|un|und)\b",
    re.IGNORECASE,
)

_ACCEPTED_UNITS = {
    "kg",
    "g",
    "mg",
    "ml",
    "l",
    "litro",
    "litros",
    "un",
    "unidade",
    "unidades",
    "rolo",
    "rolos",
    "folha",
    "folhas",
    "capsula",
    "capsulas",
    "sache",
    "saches",
    "pacote",
    "pacotes",
    "caixa",
    "caixas",
}


def strip_accents(text: str) -> str:
    """Return text without accents."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize(text: str | None) -> str:
    """Normalize text for comparisons."""
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str | None) -> set[str]:
    """Tokenize text, dropping very small/stop words but keeping units."""
    return {
        token
        for token in normalize(text).split()
        if token and token not in _STOPWORDS and (len(token) > 1 or token.isdigit())
    }


def slugify(text: str) -> str:
    """Create a stable ASCII identifier."""
    text = normalize(text)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "produto"


def parse_package(text: str | None) -> tuple[float | None, str | None, str | None]:
    """Extract package size from free text.

    Returns (grams_or_ml, unit, label). For counted packages (eggs, rolls)
    grams is None and unit is "un".
    """
    if not text:
        return None, None, None
    match = _PACKAGE_RE.search(text)
    if not match:
        return None, None, None
    try:
        number = float(match.group("num").replace(",", "."))
    except ValueError:
        return None, None, None
    unit = normalize(match.group("unit"))
    if unit == "kg":
        grams = number * 1000
        return grams, "g", f"{number:g} kg"
    if unit == "g":
        return number, "g", f"{number:g} g"
    if unit == "mg":
        return number / 1000, "g", f"{number:g} mg"
    if unit in ("l", "litro", "litros"):
        return number * 1000, "ml", f"{number:g} L"
    if unit == "ml":
        return number, "ml", f"{number:g} ml"
    if unit.startswith("unid"):
        return None, "un", f"{number:g} unidades"
    if unit in ("un", "rolo", "rolos", "folha", "folhas", "capsula", "capsulas",
                "sache", "saches", "pacote", "pacotes", "caixa", "caixas"):
        label_unit = unit
        if unit == "un":
            label_unit = "unidades"
        elif not unit.endswith("s") and unit not in ("sache", "saches"):
            label_unit = f"{unit}s"
        return None, "un", f"{number:g} {label_unit}"
    return None, None, None


def product_display_name(product: dict[str, Any]) -> str:
    """Return a human-readable product name, avoiding duplicate brand/size."""
    name = (product.get("name") or "").strip()
    brand = (product.get("brand") or "").strip()
    package = (product.get("package") or "").strip()
    if not name:
        return brand or package or "Produto"
    if brand and normalize(brand) not in normalize(name):
        name = f"{name} {brand}"
    if package and normalize(package) not in normalize(name):
        name = f"{name} {package}"
    return re.sub(r"\s+", " ", name).strip()


def product_payload(product: dict[str, Any]) -> dict[str, Any]:
    """Return a catalog product ready for the API/frontend."""
    payload = dict(product)
    payload["display_name"] = product_display_name(product)
    payload.setdefault("aliases", [])
    payload.setdefault("unit", "g" if payload.get("grams") else "un")
    if not payload.get("package") and payload.get("grams"):
        grams = float(payload["grams"])
        if payload.get("unit") == "ml":
            payload["package"] = f"{grams / 1000:g} L" if grams >= 1000 else f"{grams:g} ml"
        else:
            payload["package"] = (
                f"{grams / 1000:g} kg" if grams >= 1000 else f"{grams:g} g"
            )
    return payload


def _package_compatibility(
    product: dict[str, Any], candidate_text: str
) -> float:
    """Score whether candidate package matches the product package."""
    p_grams = product.get("grams")
    c_grams, c_unit, _ = parse_package(candidate_text)
    if not p_grams:
        # Counted products (eggs, rolls): do not reward/penalize grams.
        if c_grams:
            return -0.10
        return 0.02 if c_unit else 0.0
    if not c_grams:
        if c_unit == "un":
            return -0.20
        return -0.02
    try:
        p_grams = float(p_grams)
        c_grams = float(c_grams)
    except (TypeError, ValueError):
        return 0.0
    if p_grams <= 0 or c_grams <= 0:
        return 0.0
    ratio = min(p_grams, c_grams) / max(p_grams, c_grams)
    if ratio >= 0.97:
        return 0.12
    if ratio >= 0.85:
        return 0.04
    if ratio < 0.55:
        return -0.25
    return -0.12


def _score_query(
    query: str,
    product: dict[str, Any],
    *,
    candidate_text: str | None = None,
) -> float:
    query_norm = normalize(query)
    if not query_norm:
        return 0.0
    query_tokens = tokenize(query_norm)
    if not query_tokens:
        return 0.0

    payload = product_payload(product)
    name = payload.get("display_name") or ""
    name_norm = normalize(name)
    brand = (payload.get("brand") or "").strip()
    brand_norm = normalize(brand)
    aliases = [normalize(alias) for alias in payload.get("aliases") or []]
    product_tokens = tokenize(name)
    product_tokens.update(tokenize(brand))
    product_tokens.update(tokenize(payload.get("package")))
    for alias in payload.get("aliases") or []:
        product_tokens.update(tokenize(alias))

    overlap = len(query_tokens & product_tokens) / len(query_tokens)
    ratio = SequenceMatcher(None, query_norm, name_norm).ratio()
    # A query that is only a brand or only one generic token gets a lower
    # confidence unless the catalog actually has an exact alias for it.
    if len(query_tokens) == 1:
        overlap *= 0.75
        ratio *= 0.85

    brand_bonus = 0.15 if brand_norm and brand_norm in query_tokens else 0.0
    package_bonus = _package_compatibility(payload, query)
    alias_bonus = 0.0
    if query_norm in aliases:
        alias_bonus = 0.10

    score = 0.52 * overlap + 0.28 * ratio + brand_bonus + package_bonus + alias_bonus
    if candidate_text:
        candidate_norm = normalize(candidate_text)
        candidate_tokens = tokenize(candidate_text)
        candidate_ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()
        candidate_overlap = (
            len(query_tokens & candidate_tokens) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        score = (
            0.42 * overlap
            + 0.22 * ratio
            + 0.18 * candidate_overlap
            + 0.10 * candidate_ratio
            + brand_bonus
            + package_bonus
            + alias_bonus
        )
    return max(0.0, min(1.0, score))


def ocr_match_score(
    product: dict[str, Any], candidate_name: str, candidate_confidence: float = 0.5
) -> float:
    """Score a candidate (ABC product or OCR line) against a catalog product."""
    payload = product_payload(product)
    display = payload.get("display_name") or ""
    brand = (payload.get("brand") or "").strip()
    brand_norm = normalize(brand)
    name_tokens = tokenize(payload.get("name"))
    candidate_tokens = tokenize(candidate_name)
    if not name_tokens or not candidate_tokens:
        return 0.0

    product_tokens = tokenize(display)
    product_tokens.update(tokenize(brand))
    product_tokens.update(tokenize(payload.get("package")))
    for alias in payload.get("aliases") or []:
        product_tokens.update(tokenize(alias))
    if not product_tokens:
        return 0.0

    name_overlap = len(name_tokens & candidate_tokens) / len(name_tokens)
    all_overlap = len(product_tokens & candidate_tokens) / len(product_tokens)
    ratio = SequenceMatcher(
        None, normalize(display), normalize(candidate_name)
    ).ratio()
    brand_bonus = 0.12 if brand_norm and brand_norm in candidate_tokens else 0.0
    brand_penalty = (
        -0.30
        if brand_norm and brand_norm not in candidate_tokens
        else 0.0
    )
    package_bonus = _package_compatibility(payload, candidate_name)
    score = (
        0.40 * name_overlap
        + 0.25 * all_overlap
        + 0.20 * ratio
        + brand_bonus
        + brand_penalty
        + package_bonus
    )
    confidence = max(0.0, min(1.0, candidate_confidence))
    # Candidate confidence is useful but cannot rescue a bad text match.
    score = 0.90 * score + 0.10 * confidence
    return max(0.0, min(1.0, score))


def clean_ocr_name(text: str) -> str:
    """Clean product name lines extracted from a flyer."""
    if not text:
        return ""
    text = text.replace("|", " ").replace("—", " ").replace("–", " ")
    # Tesseract frequently sees the "5" in 5KG as an S.
    text = re.sub(r"\bS\s*KG\b", "5KG", text, flags=re.IGNORECASE)
    text = re.sub(r"R\$\s*\d+(?:[.,]\d+)?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bR?\$?\s*\d{1,3}(?:\.\d{3})*,\d{2}\b", " ", text)
    text = _MARKETING_RE.sub(" ", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b", " ", text)
    text = re.sub(r"[^A-Za-zÀ-ÿ0-9%.,/+\- ]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^[\s.,;:/\-]+|[\s.,;:/\-]+$", "", text)
    return text.strip()


def is_plausible_ocr_name(text: str) -> bool:
    """Return whether the text looks like a product name."""
    cleaned = clean_ocr_name(text)
    if len(cleaned) < 5:
        return False
    letters = re.findall(r"[A-Za-zÀ-ÿ]{2,}", cleaned)
    if len(letters) < 2 and not (
        len(letters) == 1 and len(letters[0]) >= 5
    ):
        return False
    # Avoid headers/footer words that are frequently OCR'd from flyers.
    normalized = normalize(cleaned)
    if normalized in {
        "precos validos",
        "oferta",
        "ofertas",
        "encarte",
        "bahamas mix",
        "assai atacadista",
        "clube",
        "app",
    }:
        return False
    return True


class Catalog:
    """In-memory catalog with alias resolution and fuzzy search."""

    def __init__(self, products: dict[str, dict[str, Any]] | None = None) -> None:
        self._products: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, set[str]] = {}
        if products:
            self.load(products)

    def load(self, products: dict[str, dict[str, Any]]) -> None:
        self._products = {}
        self._aliases = {}
        for product in products.values():
            self.add(product, overwrite=True)

    def add(
        self, product: dict[str, Any], *, overwrite: bool = False
    ) -> dict[str, Any]:
        payload = product_payload(product)
        product_id = payload.get("id") or slugify(
            f"{payload.get('name', '')} {payload.get('brand', '')} "
            f"{payload.get('package', '')}"
        )
        payload["id"] = product_id
        if product_id in self._products and not overwrite:
            return self._products[product_id]

        self._products[product_id] = payload
        aliases = set(payload.get("aliases") or [])
        aliases.add(payload.get("name") or "")
        aliases.add(payload.get("display_name") or "")
        if payload.get("brand"):
            aliases.add(payload["brand"])
            aliases.add(f"{payload['name']} {payload['brand']}")
        if payload.get("package"):
            aliases.add(f"{payload['name']} {payload['package']}")
            aliases.add(
                f"{payload['name']} {payload.get('brand', '')} {payload['package']}"
            )
        for alias in aliases:
            norm = normalize(alias)
            if not norm:
                continue
            self._aliases.setdefault(norm, set()).add(product_id)
        return payload

    def get(self, product_id: str | None) -> dict[str, Any] | None:
        if not product_id:
            return None
        product = self._products.get(product_id)
        return product_payload(product) if product else None

    def all(self) -> list[dict[str, Any]]:
        return [
            product_payload(product)
            for product in sorted(
                self._products.values(), key=lambda item: normalize(item.get("name"))
            )
        ]

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {product_id: dict(product) for product_id, product in self._products.items()}

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query_norm = normalize(query)
        if not query_norm:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for product in self._products.values():
            score = _score_query(query, product)
            if score > 0.18:
                payload = product_payload(product)
                payload["score"] = round(score, 4)
                scored.append((score, payload))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in scored[: max(1, limit)]]

    def resolve(
        self, text: str | None
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], float]:
        """Resolve a list item to a product.

        Returns (product, suggestions, score). The product is only returned
        when the match is unambiguous and strong enough for automatic linking.
        """
        if not text:
            return None, [], 0.0
        query_norm = normalize(text)
        exact = self._aliases.get(query_norm)
        if exact:
            products = [self._products[pid] for pid in exact if pid in self._products]
            if len(products) == 1:
                return product_payload(products[0]), [], 1.0
            scored = [
                (self._score_product(query_norm, product), product)
                for product in products
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            suggestions = []
            for score, product in scored:
                payload = product_payload(product)
                payload["score"] = round(score, 4)
                suggestions.append(payload)
            return None, suggestions, scored[0][0] if scored else 0.0

        scored = []
        for product in self._products.values():
            score = self._score_product(query_norm, product)
            if score > 0.12:
                scored.append((score, product))
        scored.sort(key=lambda item: item[0], reverse=True)
        suggestions = []
        for score, product in scored[:8]:
            payload = product_payload(product)
            payload["score"] = round(score, 4)
            suggestions.append(payload)

        if not scored:
            return None, [], 0.0
        best_score = scored[0][0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if best_score >= 0.72 and (best_score - second_score) >= 0.08:
            return product_payload(scored[0][1]), suggestions, best_score
        return None, suggestions, best_score

    @staticmethod
    def _score_product(query_norm: str, product: dict[str, Any]) -> float:
        return _score_query(query_norm, product)


def _product(
    name: str,
    brand: str | None,
    package: str | None,
    *,
    grams: float | None = None,
    unit: str | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    if grams is None and package:
        grams, unit_from_package, _ = parse_package(package)
        unit = unit or unit_from_package
    product = {
        "name": name,
        "brand": brand or "",
        "package": package or "",
        "grams": grams,
        "unit": unit or ("g" if grams else "un"),
        "aliases": aliases or [],
    }
    product["id"] = slugify(
        f"{name} {product['brand']} {product['package']}"
    )
    product["display_name"] = product_display_name(product)
    return product


def seed_products() -> list[dict[str, Any]]:
    """Default catalog with common Brazilian shopping items."""
    return [
        _product(
            "Arroz branco tipo 1",
            "Camil",
            "5 kg",
            aliases=["arroz", "arroz branco", "arroz tipo 1", "arroz camil 5kg"],
        ),
        _product(
            "Arroz integral",
            "Codil",
            "1 kg",
            aliases=["arroz integral", "arroz codil 1kg"],
        ),
        _product(
            "Feijão carioca",
            "Kicaldo",
            "1 kg",
            aliases=["feijao", "feijao carioca", "feijão carioca"],
        ),
        _product(
            "Feijão preto",
            "Kicaldo",
            "1 kg",
            aliases=["feijao preto", "feijão preto"],
        ),
        _product(
            "Leite integral",
            "Porto Alegre",
            "1 L",
            aliases=["leite", "leite integral", "leite porto alegre"],
        ),
        _product(
            "Leite desnatado",
            "Porto Alegre",
            "1 L",
            aliases=["leite desnatado"],
        ),
        _product(
            "Café tradicional",
            "Vasconcelos",
            "500 g",
            aliases=["cafe", "café", "cafe tradicional", "café vasconcelos"],
        ),
        _product(
            "Café extraforte",
            "Vasconcelos",
            "500 g",
            aliases=["cafe extraforte", "café extraforte"],
        ),
        _product(
            "Açúcar refinado",
            "União",
            "1 kg",
            aliases=["acucar", "açúcar", "acucar refinado"],
        ),
        _product(
            "Óleo de soja",
            "Liza",
            "900 ml",
            aliases=["oleo", "óleo", "oleo de soja", "óleo liza"],
        ),
        _product(
            "Sal refinado",
            "Cisne",
            "1 kg",
            aliases=["sal", "sal refinado"],
        ),
        _product(
            "Macarrão espaguete",
            "Renata",
            "500 g",
            aliases=["macarrao", "macarrão", "espaguete", "macarrao renata"],
        ),
        _product(
            "Macarrão parafuso",
            "Renata",
            "500 g",
            aliases=["parafuso", "macarrao parafuso"],
        ),
        _product(
            "Farinha de trigo",
            "Renata",
            "1 kg",
            aliases=["farinha", "farinha de trigo"],
        ),
        _product(
            "Ovos brancos",
            None,
            "12 unidades",
            aliases=["ovo", "ovos", "ovos brancos", "duzia de ovos", "dúzia de ovos"],
        ),
        _product(
            "Pão de forma",
            "Wickbold",
            "500 g",
            aliases=["pao", "pão", "pao de forma", "pão de forma"],
        ),
        _product(
            "Margarina",
            "Qualy",
            "500 g",
            aliases=["margarina", "manteiga", "qualy"],
        ),
        _product(
            "Queijo mussarela fatiado",
            None,
            "200 g",
            aliases=["queijo", "mussarela", "queijo fatiado"],
        ),
        _product(
            "Presunto fatiado",
            None,
            "200 g",
            aliases=["presunto", "presunto fatiado"],
        ),
        _product(
            "Frango inteiro",
            None,
            "1 kg",
            aliases=["frango", "frango inteiro", "galinha"],
        ),
        _product(
            "Carne bovina patinho",
            None,
            "1 kg",
            aliases=["carne", "patinho", "carne bovina"],
        ),
        _product(
            "Banana prata",
            None,
            "1 kg",
            aliases=["banana", "banana prata"],
        ),
        _product(
            "Maçã Fuji",
            None,
            "1 kg",
            aliases=["maca", "maçã", "maca fuji"],
        ),
        _product(
            "Tomate",
            None,
            "1 kg",
            aliases=["tomate", "tomates"],
        ),
        _product(
            "Cebola",
            None,
            "1 kg",
            aliases=["cebola", "cebolas"],
        ),
        _product(
            "Batata inglesa",
            None,
            "1 kg",
            aliases=["batata", "batata inglesa"],
        ),
        _product(
            "Papel higiênico",
            "Bianco",
            "12 rolos",
            aliases=["papel higienico", "papel higiênico", "papel", "bianco"],
        ),
        _product(
            "Sabonete",
            "Dove",
            "90 g",
            aliases=["sabonete", "dove"],
        ),
        _product(
            "Shampoo",
            "Dove",
            "400 ml",
            aliases=["shampoo", "xampu"],
        ),
        _product(
            "Condicionador",
            "Dove",
            "400 ml",
            aliases=["condicionador"],
        ),
        _product(
            "Creme dental",
            "Colgate",
            "90 g",
            aliases=["creme dental", "pasta de dente", "colgate"],
        ),
        _product(
            "Detergente",
            "Ypê",
            "500 ml",
            aliases=["detergente", "ype", "ypê"],
        ),
        _product(
            "Amaciante",
            "Comfort",
            "1 L",
            aliases=["amaciante", "comfort"],
        ),
        _product(
            "Água sanitária",
            "Qboa",
            "1 L",
            aliases=["agua sanitaria", "água sanitária", "qboa", "candida"],
        ),
        _product(
            "Sabão em pó",
            "Omo",
            "2,4 kg",
            aliases=["sabao em po", "sabão em pó", "omo"],
        ),
        _product(
            "Palha de aço",
            "Bombril",
            "45 g",
            aliases=[
                "palha de aco",
                "palha de aço",
                "esponja de aco",
                "esponja de aço",
                "bombril",
                "palha de aco bombril",
            ],
        ),
        _product(
            "Desinfetante",
            "Pinho Sol",
            "500 ml",
            aliases=["desinfetante", "pinho sol"],
        ),
        _product(
            "Papel toalha",
            None,
            "2 rolos",
            aliases=["papel toalha", "toalha de papel"],
        ),
        _product(
            "Saco de lixo",
            None,
            "30 unidades",
            aliases=["saco de lixo", "sacos de lixo", "saco lixo"],
        ),
    ]
