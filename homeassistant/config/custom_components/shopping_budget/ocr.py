"""Small OCR pipeline for market flyers.

The integration intentionally shells out to the Tesseract and Poppler
binaries. This keeps the Home Assistant Python environment free from heavyweight
OCR/ML dependencies while still allowing good Portuguese OCR on arm64.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from .catalog import (
    clean_ocr_name,
    is_plausible_ocr_name,
    normalize,
    parse_package,
)

_LOGGER = logging.getLogger(__name__)

_CURRENCY_RE = re.compile(r"^(?:R\$|RS|R5|R8|R\$)$", re.IGNORECASE)
_CURRENCY_INLINE_RE = re.compile(
    r"(?:R\$|RS|R5|R8)\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2}|\d{1,3}(?:\.\d{3})+|\d+)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+[.,]\d{2}$|^\d{1,3}(?:\.\d{3})+$|^\d+$"
)
_PRICE_IN_TEXT_RE = re.compile(
    r"(?:R\$|RS|R5|R8)\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2}|\d{1,3}(?:\.\d{3})+|\d+)",
    re.IGNORECASE,
)


class OCRUnavailableError(RuntimeError):
    """Raised when OCR binaries or language data are unavailable."""


@dataclass
class OCRWord:
    """One Tesseract word with its bounding box."""

    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int
    line_key: tuple[int, int, int]
    page: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class OCRLine:
    """A group of words on the same Tesseract line."""

    text: str
    left: int
    top: int
    right: int
    bottom: int
    words: list[OCRWord] = field(default_factory=list)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def line_key(self) -> tuple[int, int, int]:
        return self.words[0].line_key if self.words else (0, 0, 0)


@dataclass
class OCRPage:
    """OCR result for one flyer page."""

    page: int
    width: int
    height: int
    words: list[OCRWord]
    lines: list[OCRLine]
    text: str
    price_overrides: dict[tuple[int, int], float] = field(default_factory=dict)


@dataclass
class PriceToken:
    """A price found on a page."""

    value: float
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    page: int
    line_key: tuple[int, int, int]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def ocr_available() -> bool:
    """Return True when image/PDF OCR binaries are available."""
    return bool(shutil.which("tesseract")) and bool(shutil.which("pdftoppm"))


def available_packages() -> dict[str, bool]:
    """Return individual OCR dependency availability."""
    return {
        "tesseract": bool(shutil.which("tesseract")),
        "pdftoppm": bool(shutil.which("pdftoppm")),
    }


def _prepare_image(source: str | Path, destination: str | Path) -> tuple[int, int]:
    """Upscale and enhance a flyer image before OCR.

    Price tags are usually white text on a saturated red background. In a
    plain grayscale conversion the red background stays relatively dark and
    Tesseract often loses the white digits. For red pixels we use the inverted
    green channel instead, which gives dark digits on a light background while
    keeping normal black-on-white product names untouched.
    """
    from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

    with Image.open(source) as image:
        image = image.convert("RGB")
        # Assaí images are only ~768 px wide. Tesseract works much better with
        # a 3x upscale. PDF pages already arrive at ~2400 px.
        if image.width < 1400:
            factor = min(3.0, 2400 / max(image.width, 1))
            image = image.resize(
                (int(image.width * factor), int(image.height * factor)),
                Image.Resampling.LANCZOS,
            )
        red, green, blue = image.split()
        red_mask = red.point(lambda value: 255 if value > 120 else 0)
        green_mask = green.point(lambda value: 255 if value < 160 else 0)
        blue_mask = blue.point(lambda value: 255 if value < 160 else 0)
        # Only treat a pixel as a red tag when red is clearly dominant. Purple
        # price tags are left in grayscale; inverting their green channel would
        # turn the background light and erase the white digits.
        scaled_green = green.point(lambda value: min(255, int(value * 1.45)))
        scaled_blue = blue.point(lambda value: min(255, int(value * 1.45)))
        red_over_green = ImageChops.subtract(red, scaled_green).point(
            lambda value: 255 if value > 0 else 0
        )
        red_over_blue = ImageChops.subtract(red, scaled_blue).point(
            lambda value: 255 if value > 0 else 0
        )
        mask = ImageChops.multiply(
            ImageChops.multiply(red_mask, green_mask),
            ImageChops.multiply(
                blue_mask, ImageChops.multiply(red_over_green, red_over_blue)
            ),
        )
        gray = image.convert("L")
        inverted_green = ImageOps.invert(green)
        gray = Image.composite(inverted_green, gray, mask)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(1.3)
        gray = gray.filter(ImageFilter.SHARPEN)
        gray.save(destination, "PNG")
        return gray.width, gray.height


def _read_tsv(path: str | Path, page: int) -> OCRPage:
    """Read Tesseract TSV output into words, lines and plain text."""
    with open(path, encoding="utf-8", errors="replace") as tsv_file:
        rows = list(
            csv.DictReader(tsv_file, delimiter="\t", quoting=csv.QUOTE_NONE)
        )

    words: list[OCRWord] = []
    for row in rows:
        if row.get("level") != "5":
            continue
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1
        if conf < 0:
            continue
        try:
            left = int(row["left"])
            top = int(row["top"])
            width = int(row["width"])
            height = int(row["height"])
            line_key = (
                int(row["block_num"]),
                int(row["par_num"]),
                int(row["line_num"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        words.append(
            OCRWord(
                text=text,
                conf=conf,
                left=left,
                top=top,
                width=width,
                height=height,
                line_key=line_key,
                page=page,
            )
        )

    line_map: dict[tuple[int, int, int], list[OCRWord]] = {}
    for word in words:
        line_map.setdefault(word.line_key, []).append(word)

    width = max((word.right for word in words), default=1)
    height = max((word.bottom for word in words), default=1)
    # Tesseract can put all columns of a flyer on one line. Split a line
    # whenever the horizontal gap is much larger than a normal word space so
    # a product name cannot be associated with the price in another column.
    max_gap = max(50, int(width * 0.04))

    lines: list[OCRLine] = []
    for line_words in line_map.values():
        line_words.sort(key=lambda word: word.left)
        segments: list[list[OCRWord]] = []
        current: list[OCRWord] = []
        for word in line_words:
            if current and word.left - current[-1].right > max_gap:
                segments.append(current)
                current = []
            current.append(word)
        if current:
            segments.append(current)

        for segment in segments:
            if not segment:
                continue
            left = min(word.left for word in segment)
            top = min(word.top for word in segment)
            right = max(word.right for word in segment)
            bottom = max(word.bottom for word in segment)
            text = " ".join(word.text for word in segment)
            lines.append(
                OCRLine(
                    text=text,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    words=segment,
                )
            )
    lines.sort(key=lambda line: (line.top, line.left))

    return OCRPage(
        page=page,
        width=max(width, 1),
        height=max(height, 1),
        words=words,
        lines=lines,
        text="\n".join(line.text for line in lines),
    )


def _refine_price_words(page: OCRPage, prepared: str | Path) -> None:
    """Re-read the numeric part of currency words on a flyer.

    The ``R$`` glyph is frequently glued to the first digit by Tesseract
    (``R$ 4,59`` becomes ``R$44,59``). A crop containing only the number is
    much more reliable, so use a small digit-only OCR pass for those words.
    """
    from PIL import Image, ImageEnhance, ImageOps

    candidates = [
        word
        for word in page.words
        if re.search(r"(?:R\$|RS|R5|R8)", word.text or "", re.IGNORECASE)
        and "," in (word.text or "")
        and word.height >= page.height * 0.012
    ]
    if not candidates:
        return

    try:
        image = Image.open(prepared).convert("L")
    except OSError:
        return

    base = Path(prepared).parent
    for index, word in enumerate(candidates):
        offset = max(35, min(75, int(word.width * 0.18)))
        left = word.left + offset
        right = word.right + 4
        if right - left < 20:
            continue
        top = max(0, word.top - 6)
        bottom = min(page.height, word.bottom + 6)
        crop = image.crop((left, top, right, bottom))
        crop = crop.resize(
            (crop.width * 3, crop.height * 3), Image.Resampling.LANCZOS
        )
        crop = ImageOps.autocontrast(crop)
        crop = ImageEnhance.Contrast(crop).enhance(1.3)
        crop_path = base / f"price_{index}.png"
        crop.save(crop_path, "PNG")
        output_base = base / f"price_{index}"
        value: float | None = None
        for psm in (8, 7, 13, 6):
            try:
                subprocess.run(
                    [
                        "tesseract",
                        str(crop_path),
                        str(output_base),
                        "-l",
                        "por",
                        "--psm",
                        str(psm),
                        "-c",
                        "tessedit_char_whitelist=0123456789.,",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (subprocess.CalledProcessError, OSError):
                continue
            text_path = Path(f"{output_base}.txt")
            if not text_path.exists():
                continue
            raw = text_path.read_text(encoding="utf-8", errors="replace")
            value = _parse_price(raw)
            if value is not None and 0.5 <= value <= 5000:
                break
        if value is not None and 0.5 <= value <= 5000:
            page.price_overrides[(word.left, word.top)] = value


def _run_tesseract(
    image_path: str | Path,
    language: str,
    psm: int,
    output_base: str | Path,
) -> None:
    """Run Tesseract and write TSV output."""
    if not shutil.which("tesseract"):
        raise OCRUnavailableError(
            "Tesseract não está instalado no contêiner do Home Assistant."
        )
    command = [
        "tesseract",
        str(image_path),
        str(output_base),
        "-l",
        language,
        "--psm",
        str(psm),
        "tsv",
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or "") + (err.stdout or "")
        if language != "eng" and "Failed loading language" in stderr:
            _LOGGER.warning("Tesseract language %s unavailable, using eng", language)
            command[command.index(language)] = "eng"
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            return
        raise OCRUnavailableError(
            f"Tesseract falhou: {stderr.strip()[:500] or err}"
        ) from err


def ocr_image(
    image_path: str | Path,
    *,
    language: str = "por",
    psm: int = 6,
    page_number: int = 1,
) -> OCRPage:
    """OCR a single image file."""
    with tempfile.TemporaryDirectory(prefix="shopping_budget_ocr_") as temp_dir:
        prepared = Path(temp_dir) / "prepared.png"
        _prepare_image(image_path, prepared)
        output_base = Path(temp_dir) / "ocr"
        _run_tesseract(prepared, language, psm, output_base)
        page = _read_tsv(f"{output_base}.tsv", page_number)
        _refine_price_words(page, prepared)
        return page


def ocr_pdf(
    pdf_path: str | Path,
    *,
    language: str = "por",
    psm: int = 6,
    max_pages: int = 4,
) -> list[OCRPage]:
    """Render a PDF and OCR its first pages."""
    if not shutil.which("pdftoppm"):
        raise OCRUnavailableError(
            "pdftoppm/poppler não está instalado no contêiner do Home Assistant."
        )
    with tempfile.TemporaryDirectory(prefix="shopping_budget_pdf_") as temp_dir:
        prefix = Path(temp_dir) / "page"
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    str(max(1, max_pages)),
                    "-r",
                    "200",
                    "-png",
                    str(pdf_path),
                    str(prefix),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (subprocess.CalledProcessError, OSError) as err:
            raise OCRUnavailableError(f"Falha ao converter PDF: {err}") from err

        page_files = sorted(
            Path(temp_dir).glob("page-*.png"),
            key=lambda path: int(re.search(r"page-(\d+)", path.name).group(1))
            if re.search(r"page-(\d+)", path.name)
            else 0,
        )
        if not page_files:
            raise OCRUnavailableError("Nenhuma página do PDF pôde ser convertida.")

        pages: list[OCRPage] = []
        for index, page_file in enumerate(page_files, start=1):
            pages.append(
                ocr_image(
                    page_file,
                    language=language,
                    psm=psm,
                    page_number=index,
                )
            )
        return pages


def _parse_price(raw: str) -> float | None:
    """Parse a Brazilian price string into a float."""
    text = (
        raw.replace("O", "0")
        .replace("o", "0")
        .replace(" ", "")
        .replace("R$", "")
        .replace("RS", "")
        .replace("R5", "")
        .replace("R8", "")
        .strip()
    )
    match = re.search(
        r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+[.,]\d{2}|\d{1,3}(?:\.\d{3})+|\d+",
        text,
    )
    if not match:
        return None
    number = match.group(0)
    try:
        if "," in number:
            value = float(number.replace(".", "").replace(",", "."))
        elif number.count(".") == 1 and len(number.split(".")[1]) == 2:
            value = float(number)
        elif "." in number:
            value = float(number.replace(".", ""))
        else:
            value = float(number)
    except ValueError:
        return None
    if value < 0.5 or value > 5000:
        return None
    return round(value, 2)


def _price_tokens(page: OCRPage) -> list[PriceToken]:
    """Extract price tokens from word boxes."""
    prices: list[PriceToken] = []
    seen: set[tuple[float, int, int, int]] = set()
    used_numbers: set[tuple[int, int]] = set()
    units = {
        "kg",
        "g",
        "mg",
        "ml",
        "l",
        "lt",
        "litro",
        "litros",
        "un",
        "und",
        "unid",
        "unidade",
        "unidades",
        "pacote",
        "pacotes",
        "rolo",
        "rolos",
        "folha",
        "folhas",
        "caixa",
        "caixas",
        "cx",
    }

    def add_price(
        value: float,
        text: str,
        left: int,
        top: int,
        right: int,
        bottom: int,
        confidence: float,
    ) -> None:
        key = (round(value, 2), page.page, left // 8, top // 8)
        if key in seen:
            return
        seen.add(key)
        prices.append(
            PriceToken(
                value=value,
                text=text,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=max(0.0, min(1.0, confidence)),
                page=page.page,
                line_key=(0, 0, 0),
            )
        )

    for line in page.lines:
        words = sorted(line.words, key=lambda word: word.left)
        for index, word in enumerate(words):
            text = (word.text or "").strip()
            inline = _CURRENCY_INLINE_RE.search(text)
            if inline:
                value = _parse_price(inline.group(0))
                confidence = word.conf / 100
                refined = page.price_overrides.get((word.left, word.top))
                if refined is not None:
                    value = refined
                    confidence = max(confidence, 0.6)
                if value is not None:
                    add_price(
                        value,
                        inline.group(0),
                        word.left,
                        word.top,
                        word.right,
                        word.bottom,
                        confidence,
                    )
                continue

            if not _CURRENCY_RE.match(text):
                # Even when the currency symbol was lost, a large standalone
                # decimal number is almost certainly a price. Package sizes
                # such as 1,5 kg are filtered by height and by the unit word.
                if not (_NUMBER_RE.match(text) and "," in text):
                    continue
                if word.height < page.height * 0.010:
                    continue
                neighbors = []
                if index > 0:
                    neighbors.append(words[index - 1])
                if index + 1 < len(words):
                    neighbors.append(words[index + 1])
                if any(
                    (neighbor.text or "").strip().lower().strip(".,")
                    in units
                    for neighbor in neighbors
                ):
                    continue
                value = _parse_price(text)
                if value is not None:
                    used_numbers.add((word.left, word.top))
                    add_price(
                        value,
                        text,
                        word.left,
                        word.top,
                        word.right,
                        word.bottom,
                        word.conf / 100,
                    )
                continue

            # Tesseract often puts "R$" and the number in separate words.
            number_word = None
            for next_word in words[index + 1 : index + 4]:
                next_text = (next_word.text or "").strip()
                if _NUMBER_RE.match(next_text):
                    number_word = next_word
                    break
                if next_text.lower() in {"cada", "un", "und", "no", "app"}:
                    continue
                if re.search(r"[A-Za-zÀ-ÿ]{4,}", next_text):
                    break
            if number_word is None:
                continue
            if not re.search(r"[.,]\d{2}", number_word.text):
                # A bare number after R$ is usually a cropped old price or a
                # package fragment, not a usable current price.
                continue
            if (number_word.left, number_word.top) in used_numbers:
                continue
            value = _parse_price(number_word.text)
            if value is None:
                continue
            add_price(
                value,
                f"R$ {number_word.text}",
                min(word.left, number_word.left),
                min(word.top, number_word.top),
                max(word.right, number_word.right),
                max(word.bottom, number_word.bottom),
                ((word.conf + number_word.conf) / 2) / 100,
            )

    return prices


def _line_overlap(line: OCRLine, price: PriceToken, margin: int = 100) -> int:
    """Horizontal overlap between a line and an expanded price box."""
    return min(line.right, price.right + margin) - max(line.left, price.left - margin)


def _find_name_for_price(
    page: OCRPage, price: PriceToken
) -> tuple[str, list[OCRLine], int] | None:
    """Find the product name closest to a price."""
    max_dy = min(460, max(190, int(page.height * 0.17)))
    above = [
        line
        for line in page.lines
        if line.bottom <= price.top + 18
        and line.bottom >= price.top - max_dy
        and _line_overlap(line, price, margin=45) > 25
        and is_plausible_ocr_name(line.text)
    ]
    above.sort(key=lambda line: line.bottom, reverse=True)

    if above:
        selected: list[OCRLine] = []
        previous_top = price.top
        for line in above:
            if selected:
                gap = previous_top - line.bottom
                if gap > max(35, int(line.height * 2.8)):
                    break
            selected.append(line)
            previous_top = line.top
            if len(selected) >= 5:
                break
        selected.sort(key=lambda line: line.top)
        name = clean_ocr_name(" ".join(line.text for line in selected))
        if is_plausible_ocr_name(name):
            gap = max(0, price.top - selected[-1].bottom)
            return name, selected, gap

    below = [
        line
        for line in page.lines
        if line.top >= price.bottom - 12
        and line.top <= price.bottom + 190
        and _line_overlap(line, price, margin=45) > 25
        and is_plausible_ocr_name(line.text)
    ]
    below.sort(key=lambda line: (line.top - price.bottom, abs(line.left - price.left)))
    if below:
        name = clean_ocr_name(below[0].text)
        if is_plausible_ocr_name(name):
            return name, [below[0]], max(0, below[0].top - price.bottom)
    return None


def parse_candidates(
    pages: list[OCRPage],
    *,
    min_confidence: float = 35.0,
) -> list[dict[str, Any]]:
    """Turn OCR pages into product/price candidates."""
    candidates: list[dict[str, Any]] = []
    for page in pages:
        prices = _price_tokens(page)
        for price in prices:
            if price.confidence * 100 < 5:
                continue
            found = _find_name_for_price(page, price)
            if not found:
                continue
            name, name_lines, gap = found
            if not is_plausible_ocr_name(name):
                continue
            # Combine Tesseract confidence with a small distance penalty.
            name_conf = (
                sum(word.conf for line in name_lines for word in line.words)
                / max(1, sum(len(line.words) for line in name_lines))
            ) / 100
            distance_penalty = 1 - min(0.12, max(0, gap - 80) / 600)
            confidence = max(
                0.0,
                min(
                    0.99,
                    (0.7 * price.confidence + 0.3 * name_conf)
                    * distance_penalty,
                ),
            )
            if confidence * 100 < min_confidence:
                continue
            grams, unit, package = parse_package(name)
            candidates.append(
                {
                    "name": name,
                    "price": price.value,
                    "raw": f"{name} | {price.text}",
                    "page": page.page,
                    "confidence": round(confidence, 4),
                    "bbox": [
                        price.left,
                        price.top,
                        price.right - price.left,
                        price.bottom - price.top,
                    ],
                    "grams": grams,
                    "unit": unit,
                    "package": package,
                }
            )

        # Line-level fallback: use the line immediately above a price line.
        for line in page.lines:
            match = _PRICE_IN_TEXT_RE.search(line.text)
            if not match:
                continue
            value = _parse_price(match.group(0))
            if value is None:
                continue
            previous = [
                candidate
                for candidate in page.lines
                if candidate.bottom <= line.top + 8
                and candidate.bottom >= line.top - 90
                and _line_overlap(candidate, PriceToken(
                    value=value,
                    text=match.group(0),
                    left=line.left,
                    top=line.top,
                    right=line.right,
                    bottom=line.bottom,
                    confidence=0.5,
                    page=page.page,
                    line_key=line.line_key,
                )) > 12
                and is_plausible_ocr_name(candidate.text)
            ]
            previous.sort(key=lambda candidate: line.top - candidate.bottom)
            if not previous:
                continue
            name = clean_ocr_name(previous[0].text)
            if not is_plausible_ocr_name(name):
                continue
            grams, unit, package = parse_package(name)
            candidates.append(
                {
                    "name": name,
                    "price": value,
                    "raw": f"{name} | {match.group(0)}",
                    "page": page.page,
                    "confidence": 0.35,
                    "bbox": [line.left, line.top, line.right - line.left, line.height],
                    "grams": grams,
                    "unit": unit,
                    "package": package,
                }
            )

    # Deduplicate by normalized name + price + page, keeping the best score.
    deduped: dict[tuple[str, float, int], dict[str, Any]] = {}
    for candidate in candidates:
        key = (
            normalize(candidate["name"]),
            round(float(candidate["price"]), 2),
            int(candidate["page"]),
        )
        previous = deduped.get(key)
        if previous is None or candidate["confidence"] > previous["confidence"]:
            deduped[key] = candidate

    # Flyers usually show an old "DE" price above the current "POR" price.
    # When the same OCR name has more than one price, keep the lower one; this
    # also collapses a duplicated leading digit from red price tags.
    grouped: dict[tuple[str, int], dict[str, Any]] = {}
    for candidate in list(deduped.values()):
        key = (normalize(candidate["name"]), int(candidate["page"]))
        previous = grouped.get(key)
        if previous is None or candidate["price"] < previous["price"]:
            grouped[key] = candidate

    result = list(grouped.values())
    result.sort(
        key=lambda candidate: (
            candidate["page"],
            candidate["bbox"][1],
            candidate["bbox"][0],
        )
    )
    return result[:250]
