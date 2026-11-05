#!/usr/bin/env python3
"""
Fetch Xbox Marketplace game data via a dual-API strategy.

Primary: marketplace-xb.xboxlive.com (legacy API with parent products)
Fallback: catalog-cdn.xboxlive.com (catalog feed and details)

The script can also enumerate the legacy Xbox 360 catalog into a local
catalog.json. That file supplies the exact media ID for each title, which is
required for the ``D804`` media family, and can be used directly as batch
input afterwards.

Artwork, gallery screenshots, and related products can be gathered from
several locales. With --pick-artwork the script lists the candidates for each
title, lets you preview an image, and writes only the selected URLs to
info.json.

Writes info.json per title, with optional artwork, gallery, and product
downloads. Supports a single title ID, JSON batch input, or catalog input.
"""

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

logger = logging.getLogger("xbox_marketplace")

# =========================
# Configuration
# =========================

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "titles"
DEFAULT_CATALOG_PATH = PROJECT_DIR / "catalog.json"

LOCALES = [
    "es-AR",
    "pt-BR",
    "en-CA",
    "fr-CA",
    "es-CL",
    "es-CO",
    "es-MX",
    "en-US",
    "nl-BE",
    "fr-BE",
    "cs-CZ",
    "da-DK",
    "de-DE",
    "es-ES",
    "fr-FR",
    "en-IE",
    "it-IT",
    "hu-HU",
    "nl-NL",
    "nb-NO",
    "de-AT",
    "pl-PL",
    "pt-PT",
    "de-CH",
    "sk-SK",
    "fr-CH",
    "fi-FI",
    "sv-SE",
    "en-GB",
    "el-GR",
    "ru-RU",
    "en-AU",
    "en-HK",
    "en-IN",
    "id-ID",
    "en-MY",
    "en-NZ",
    "en-PH",
    "en-SG",
    "vi-VN",
    "th-TH",
    "ko-KR",
    "zh-CN",
    "zh-TW",
    "ja-JP",
    "zh-HK",
    "en-ZA",
    "tr-TR",
    "he-IL",
    "ar-AE",
    "ar-SA",
]

ENGLISH_LOCALES = [
    "en-US",
    "en-GB",
    "en-CA",
    "en-AU",
    "en-IE",
    "en-NZ",
    "en-SG",
    "en-HK",
    "en-IN",
    "en-MY",
    "en-PH",
    "en-ZA",
]

CATALOG_URL = "http://catalog-cdn.xboxlive.com/Catalog/Catalog.asmx/Query"
MARKETPLACE_BASE = "https://marketplace-xb.xboxlive.com/marketplacecatalog/v1/product"

MEDIA_ID_PREFIX = "66ACD000-77FE-1000-9115-"
DEFAULT_MEDIA_PREFIX = "D802"
PROBE_MEDIA_PREFIXES = ("D802", "D804")

DETAIL_MEDIA_TYPES = (1, 21, 23, 37, 46)
ENUMERATION_MEDIA_TYPES = (1,)

PRODUCT_TYPES = "1.5.18.19.20.21.22.23.30.34.37.46.47.61"

OLD_RELATIONSHIP_MAP = {
    "33": "boxart",
    "25": "background",
    "23": "icon",
    "27": "banner",
}

NEW_RELATIONSHIP_MAP = {
    "23": "icon",
    "25": "background",
    "27": "banner",
    "33": "boxart",
}

ARTWORK_CATEGORIES = ("background", "banner", "boxart", "icon")
PICKER_CATEGORIES = ARTWORK_CATEGORIES + ("gallery", "products")

GENRE_EXCLUDE_IDS = {"3000", "3027"}

MIME_TYPE_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/vnd.microsoft.icon": ".ico",
}

MAX_WORKERS = 8
MAX_RETRIES = 3
RETRY_DELAY = 2

USER_AGENT = "x360db/1.0 (+https://github.com/xenia-manager/x360db)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT

PREVIEW_DIR = None

OLD_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "": "http://marketplace.xboxlive.com/resource/product/v1",
}

NEW_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "live": "http://www.live.com/marketplace",
}


# =========================
# Helper Functions
# =========================


def extract_number(url):
    """Extract screenshot number from gallery URL for sorting."""
    match = re.search(r"screenlg(\d+)", url)
    return int(match.group(1)) if match else 0


def clean_title(text):
    """Remove the 'Full Game - ' prefix and surrounding whitespace.

    Punctuation and Unicode are preserved. Normalize only for matching, never
    for storage.
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("Full Game - "):
        text = text[len("Full Game - ") :]
    return text.strip() or None


def normalize_media_id(value):
    """Return the queryable GUID form of a media ID (no urn:uuid: prefix)."""
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("urn:uuid:"):
        value = value[len("urn:uuid:") :]
    return value.upper()


def default_media_id(title_id, prefix=DEFAULT_MEDIA_PREFIX):
    """Build the common D802 media ID for a title ID."""
    return f"{MEDIA_ID_PREFIX}{prefix}{title_id.upper()}"


def media_id_suffix(media_id):
    """Return the last hyphen-separated component of a media GUID."""
    return (media_id or "").split("-")[-1]


def media_id_prefix(media_id):
    """Return the D802/D804 family prefix from a media GUID."""
    return media_id_suffix(media_id)[:4].upper()


def title_id_from_media_id(media_id):
    """Return the 8-hex title ID embedded in a media GUID."""
    return media_id_suffix(media_id)[-8:].upper()


def valid_title_id(title_id):
    return bool(re.fullmatch(r"[0-9A-Fa-f]{8}", title_id or ""))


def parse_xml(content):
    """Parse XML bytes, returning None and logging a diagnostic on failure."""
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        logger.debug("XML parse failed: %s", exc)
        return None


def catalog_error_name(content):
    """Extract XONLINE_E_* / HResultName from a catalog error response."""
    root = parse_xml(content)
    if root is None:
        return None
    return root.findtext("HResultName") or root.findtext("Type")


def find_text(parent, tag, namespaces):
    """findtext that tolerates missing elements and whitespace."""
    value = parent.findtext(tag, namespaces=namespaces)
    return value.strip() if value and value.strip() else None


# =========================
# Catalog Enumeration
# =========================


def catalog_params(
    locale,
    page=1,
    detail_view=2,
    media_ids=(),
    media_types=ENUMERATION_MEDIA_TYPES,
    page_size=100,
):
    """Build the repeated Names/Values query list for catalog-cdn."""
    params = [
        ("methodName", "FindGames"),
        ("Names", "Locale"),
        ("Values", locale),
        ("Names", "LegalLocale"),
        ("Values", locale),
        ("Names", "Store"),
        ("Values", "1"),
        ("Names", "PageSize"),
        ("Values", str(page_size)),
        ("Names", "PageNum"),
        ("Values", str(page)),
        ("Names", "DetailView"),
        ("Values", str(detail_view)),
        ("Names", "OfferFilterLevel"),
        ("Values", "1"),
        ("Names", "UserTypes"),
        ("Values", "2"),
    ]
    for media_type in media_types:
        params += [("Names", "MediaTypes"), ("Values", str(media_type))]
    for media_id in media_ids:
        params += [("Names", "MediaIds"), ("Values", normalize_media_id(media_id))]
    return params


def fetch_catalog_page(
    locale,
    page=1,
    detail_view=2,
    media_ids=(),
    media_types=ENUMERATION_MEDIA_TYPES,
    page_size=100,
):
    """Fetch one catalog page. Returns the parsed root or None."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(
                CATALOG_URL,
                params=catalog_params(
                    locale,
                    page=page,
                    detail_view=detail_view,
                    media_ids=media_ids,
                    media_types=media_types,
                    page_size=page_size,
                ),
                timeout=30,
            )
            logger.debug(
                "catalog request locale=%s page=%d detail=%d: status=%d size=%d",
                locale,
                page,
                detail_view,
                resp.status_code,
                len(resp.content),
            )
            if resp.status_code == 200:
                root = parse_xml(resp.content)
                if root is not None:
                    return root
                logger.debug("catalog returned unparseable XML")
            elif resp.status_code == 400:
                logger.warning(
                    "catalog rejected request (%s): %s",
                    catalog_error_name(resp.content) or "HTTP 400",
                    resp.text[:300],
                )
                return None
            else:
                logger.debug("catalog HTTP %d", resp.status_code)
        except requests.RequestException as exc:
            logger.debug(
                "catalog attempt %d failed for page %d: %s", attempt + 1, page, exc
            )
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return None


def parse_catalog_entries(root, detail_view):
    """Parse an Atom catalog feed into row dicts."""
    rows = []
    for entry in root.findall("a:entry", NEW_NS):
        media = entry.find("live:media", NEW_NS)
        if media is None:
            continue

        media_id = normalize_media_id(
            find_text(media, "live:gameTitleMediaId", NEW_NS)
        )
        if not media_id:
            continue

        title_id = title_id_from_media_id(media_id)
        if not valid_title_id(title_id):
            continue

        decimal_text = find_text(media, "live:titleId", NEW_NS)
        try:
            decimal = int(decimal_text) if decimal_text else None
        except ValueError:
            decimal = None

        title = (
            find_text(media, "live:reducedTitle", NEW_NS)
            or find_text(media, "live:fullTitle", NEW_NS)
            or find_text(entry, "a:title", NEW_NS)
        )

        rows.append(
            {
                "titleid": title_id,
                "title_id_decimal": decimal,
                "media_id": media_id,
                "media_prefix": media_id_prefix(media_id),
                "title": title,
                "developer": find_text(media, "live:developer", NEW_NS),
                "publisher": find_text(media, "live:publisher", NEW_NS),
                "release_date": (find_text(media, "live:releaseDate", NEW_NS) or "")[
                    :10
                ]
                or None,
                "user_rating": find_text(media, "live:ratingAggregate", NEW_NS),
                "number_of_ratings": find_text(media, "live:numberOfRatings", NEW_NS),
                "detail_view": detail_view,
            }
        )
    return rows


def enumerate_catalog(
    locale="en-US", max_pages=None, page_delay=0.3, page_size=100
):
    """Walk the legacy catalog feed and return unique title rows."""
    unique = {}
    duplicate_entries = 0
    entries_seen = 0
    pages = 0
    total = None

    page = 1
    while True:
        root = fetch_catalog_page(
            locale,
            page=page,
            detail_view=2,
            media_types=ENUMERATION_MEDIA_TYPES,
            page_size=page_size,
        )
        if root is None:
            logger.warning("Stopping enumeration at page %d (request failed)", page)
            break

        if total is None:
            total_text = find_text(root, "live:totalItems", NEW_NS)
            total = int(total_text) if total_text else 0

        items_text = find_text(root, "live:numItems", NEW_NS)
        items = int(items_text) if items_text else 0
        rows = parse_catalog_entries(root, detail_view=2)
        pages += 1
        entries_seen += items

        for row in rows:
            title_id = row["titleid"]
            existing = unique.get(title_id)
            if existing is not None:
                duplicate_entries += 1
                # Prefer a real title over the "-" placeholder.
                if existing.get("title") in (None, "", "-") and row.get("title") not in (
                    None,
                    "",
                    "-",
                ):
                    unique[title_id] = row
                continue
            unique[title_id] = row

        print(
            f"  page {page}: {items} entries, {len(unique)} unique title IDs",
            flush=True,
        )

        if items < page_size:
            break
        if max_pages is not None and page >= max_pages:
            break
        if total and page >= math.ceil(total / page_size):
            break

        page += 1
        time.sleep(max(0.0, page_delay))

    rows = sorted(unique.values(), key=lambda row: row["titleid"])
    stats = {
        "pages": pages,
        "total_items": total,
        "entries_seen": entries_seen,
        "unique": len(rows),
        "duplicates": duplicate_entries,
        "d802": sum(1 for row in rows if row["media_prefix"] == "D802"),
        "d804": sum(1 for row in rows if row["media_prefix"] == "D804"),
    }
    return rows, stats


def save_catalog(rows, path):
    """Write catalog rows as a JSON array usable as script input."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_catalog_map(path):
    """Load a catalog file into a title ID -> row mapping."""
    if not path:
        return {}
    path = Path(path)
    if not path.is_file():
        logger.debug("Catalog file not found: %s", path)
        return {}

    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read catalog %s: %s", path, exc)
        return {}

    if isinstance(data, dict):
        data = data.get("titles") or data.get("entries") or data.get("items") or []
    if not isinstance(data, list):
        logger.warning("Catalog %s is not a JSON array or known object", path)
        return {}

    catalog = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        title_id = (
            item.get("titleid") or item.get("title_id") or item.get("id") or ""
        )
        title_id = str(title_id).strip().upper()
        if valid_title_id(title_id):
            catalog[title_id] = item
    logger.debug("Loaded %d catalog entries from %s", len(catalog), path)
    return catalog


# =========================
# XML Parsers
# =========================


def empty_game_data(title_id, media):
    return {
        "id": title_id,
        "title": {"full": None, "reduced": None},
        "genre": [],
        "developer": None,
        "publisher": None,
        "release_date": None,
        "user_rating": None,
        "description": {"full": None, "short": None},
        "media": media,
        "artwork": {
            "background": None,
            "banner": None,
            "boxart": None,
            "icon": None,
            "gallery": [],
        },
        "products": {"parent": [], "related": []},
    }


def extract_game_data_old(xml_content, title_id, media):
    """Parse OLD API (marketplace-xb) XML response into game data dict."""
    logger.debug("Parsing OLD API XML for %s (%d bytes)", title_id, len(xml_content))
    root = parse_xml(xml_content)
    if root is None:
        return None
    entry = root.find(".//a:entry", namespaces=OLD_NS)
    if entry is None:
        logger.debug("No <entry> found in OLD API response for %s", title_id)
        return None

    game_data = empty_game_data(title_id, media)

    def old_text(tag):
        return find_text(entry, tag, OLD_NS)

    game_data["title"]["full"] = clean_title(old_text("fullTitle"))
    game_data["title"]["reduced"] = clean_title(old_text("reducedTitle"))
    game_data["developer"] = old_text("developerName")
    game_data["publisher"] = old_text("publisherName")
    game_data["description"]["full"] = old_text("fullDescription")
    game_data["description"]["short"] = old_text("reducedDescription")
    game_data["user_rating"] = old_text("userRating")

    release = old_text("globalOriginalReleaseDate")
    if release:
        game_data["release_date"] = re.sub(r"T.*$", "", release)

    categories = entry.findall("categories/category", namespaces=OLD_NS)
    for category in categories:
        category_id = category.find("categoryId", namespaces=OLD_NS)
        system = category.find("categorySystemId", namespaces=OLD_NS)
        name = category.find("categoryName", namespaces=OLD_NS)
        if (
            system is not None
            and system.text == "3000"
            and category_id is not None
            and category_id.text not in GENRE_EXCLUDE_IDS
            and name is not None
            and name.text
        ):
            game_data["genre"].append(name.text)
    game_data["genre"] = sorted(game_data["genre"])

    for image in entry.findall("images/image", namespaces=OLD_NS):
        relationship = image.find("relationshipType", namespaces=OLD_NS)
        url = image.find("fileUrl", namespaces=OLD_NS)
        if relationship is None or url is None or not url.text:
            continue
        key = OLD_RELATIONSHIP_MAP.get(relationship.text)
        if key and game_data["artwork"][key] is None:
            game_data["artwork"][key] = url.text
    # Relationship 15 is the legacy icon; use it only when no tile/icon (23)
    # was present.
    if game_data["artwork"]["icon"] is None:
        for image in entry.findall("images/image", namespaces=OLD_NS):
            relationship = image.find("relationshipType", namespaces=OLD_NS)
            url = image.find("fileUrl", namespaces=OLD_NS)
            if relationship is not None and relationship.text == "15" and url is not None:
                if url.text:
                    game_data["artwork"]["icon"] = url.text
                    break

    slideshows = entry.find("slideShows", namespaces=OLD_NS)
    if slideshows is not None:
        for slideshow in slideshows.findall("slideShow", namespaces=OLD_NS):
            for image in slideshow.findall("image", namespaces=OLD_NS):
                url = image.find("fileUrl", namespaces=OLD_NS)
                if url is not None and url.text:
                    game_data["artwork"]["gallery"].append(url.text)
    game_data["artwork"]["gallery"] = sorted(
        game_data["artwork"]["gallery"], key=extract_number
    )

    parent_products = entry.find("parentProducts", namespaces=OLD_NS)
    if parent_products is not None:
        for parent_product in parent_products.findall(
            "parentProduct", namespaces=OLD_NS
        ):
            parent = {"id": None, "title": None}
            parent_id = parent_product.find("parentProductId", namespaces=OLD_NS)
            if parent_id is not None and parent_id.text:
                parent["id"] = parent_id.text[-8:].upper()
            parent_title = parent_product.find(
                "parentReducedTitle", namespaces=OLD_NS
            )
            if parent_title is not None and parent_title.text:
                parent["title"] = parent_title.text
            if parent["id"] and parent["id"] != title_id:
                game_data["products"]["parent"].append(parent)

    related_urls = entry.find("relatedUrls", namespaces=OLD_NS)
    if related_urls is not None:
        for related in related_urls.findall("relatedUrl", namespaces=OLD_NS):
            url = related.find("relatedUrl", namespaces=OLD_NS)
            if url is not None and url.text:
                game_data["products"]["related"].append(url.text)

    logger.debug(
        "OLD API %s: title=%r, dev=%r, pub=%r, genres=%d, artwork=%s, gallery=%d",
        title_id,
        game_data["title"]["full"],
        game_data["developer"],
        game_data["publisher"],
        len(game_data["genre"]),
        {
            key: value
            for key, value in game_data["artwork"].items()
            if key != "gallery" and value
        },
        len(game_data["artwork"]["gallery"]),
    )
    return game_data


def extract_game_data_new(xml_content, title_id, media):
    """Parse NEW API (catalog-cdn) XML response into game data dict."""
    logger.debug("Parsing NEW API XML for %s (%d bytes)", title_id, len(xml_content))
    root = parse_xml(xml_content)
    if root is None:
        return None
    entry = root.find("a:entry", namespaces=NEW_NS)
    if entry is None:
        logger.debug("No <entry> found in NEW API response for %s", title_id)
        return None

    game_data = empty_game_data(title_id, media)

    media_element = entry.find("live:media", namespaces=NEW_NS)
    if media_element is not None:
        game_data["title"]["full"] = clean_title(
            find_text(media_element, "live:fullTitle", NEW_NS)
            or find_text(media_element, "live:reducedTitle", NEW_NS)
        )
        game_data["title"]["reduced"] = clean_title(
            find_text(media_element, "live:reducedTitle", NEW_NS)
        )
        game_data["developer"] = find_text(media_element, "live:developer", NEW_NS)
        game_data["publisher"] = find_text(media_element, "live:publisher", NEW_NS)
        game_data["description"]["full"] = find_text(
            media_element, "live:description", NEW_NS
        )
        game_data["description"]["short"] = find_text(
            media_element, "live:reducedDescription", NEW_NS
        )

        release = find_text(media_element, "live:releaseDate", NEW_NS)
        if release:
            game_data["release_date"] = re.sub(r"T.*$", "", release)

        game_data["user_rating"] = find_text(
            media_element, "live:ratingAggregate", NEW_NS
        )

    categories_element = entry.find("live:categories", namespaces=NEW_NS)
    if categories_element is not None:
        for category in categories_element.findall(
            "live:category", namespaces=NEW_NS
        ):
            category_id = category.find("live:categoryId", namespaces=NEW_NS)
            system = category.find("live:system", namespaces=NEW_NS)
            name = category.find("live:name", namespaces=NEW_NS)
            if (
                system is not None
                and system.text == "3000"
                and category_id is not None
                and category_id.text not in GENRE_EXCLUDE_IDS
                and name is not None
                and name.text
            ):
                game_data["genre"].append(name.text)
    game_data["genre"] = sorted(game_data["genre"])

    images_element = entry.find("live:images", namespaces=NEW_NS)
    if images_element is not None:
        for image in images_element.findall("live:image", namespaces=NEW_NS):
            relationship = image.find("live:relationshipType", namespaces=NEW_NS)
            url = image.find("live:fileUrl", namespaces=NEW_NS)
            if relationship is None or url is None or not url.text:
                continue
            key = NEW_RELATIONSHIP_MAP.get(relationship.text)
            if key and game_data["artwork"][key] is None:
                game_data["artwork"][key] = url.text
        if game_data["artwork"]["icon"] is None:
            for image in images_element.findall("live:image", namespaces=NEW_NS):
                relationship = image.find("live:relationshipType", namespaces=NEW_NS)
                url = image.find("live:fileUrl", namespaces=NEW_NS)
                if (
                    relationship is not None
                    and relationship.text == "15"
                    and url is not None
                    and url.text
                ):
                    game_data["artwork"]["icon"] = url.text
                    break

    slideshows_element = entry.find("live:slideShows", namespaces=NEW_NS)
    if slideshows_element is not None:
        for slideshow in slideshows_element.findall(
            "live:slideShow", namespaces=NEW_NS
        ):
            for image in slideshow.findall("live:image", namespaces=NEW_NS):
                url = image.find("live:fileUrl", namespaces=NEW_NS)
                if url is not None and url.text:
                    game_data["artwork"]["gallery"].append(url.text)
    game_data["artwork"]["gallery"] = sorted(
        game_data["artwork"]["gallery"], key=extract_number
    )

    logger.debug(
        "NEW API %s: title=%r, dev=%r, pub=%r, genres=%d, artwork=%s, gallery=%d",
        title_id,
        game_data["title"]["full"],
        game_data["developer"],
        game_data["publisher"],
        len(game_data["genre"]),
        {
            key: value
            for key, value in game_data["artwork"].items()
            if key != "gallery" and value
        },
        len(game_data["artwork"]["gallery"]),
    )
    return game_data


# =========================
# API Fetch Functions
# =========================


def marketplace_params():
    return {
        "bodytypes": "1.3",
        "detailview": "detaillevel5",
        "pagenum": 1,
        "pagesize": 1,
        "stores": 1,
        "tiers": "2.3",
        "offerfilter": 1,
        "producttypes": PRODUCT_TYPES,
    }


def fetch_from_old_api(title_id, locale, media, media_id=None):
    """Fetch game data from the legacy marketplace-xb API with retries."""
    media_id = normalize_media_id(media_id) or default_media_id(title_id)
    url = f"{MARKETPLACE_BASE}/{locale}/{media_id}"
    logger.debug("OLD API request: %s", url)
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(url, params=marketplace_params(), timeout=30)
            logger.debug(
                "OLD API %s response: status=%d, size=%d bytes",
                title_id,
                resp.status_code,
                len(resp.content),
            )
            if resp.status_code == 200:
                root = parse_xml(resp.content)
                if root is None:
                    return None
                total = find_text(root, "totalItems", OLD_NS) or "0"
                if total == "0":
                    logger.debug("OLD API %s: empty feed", title_id)
                    return None
                game_data = extract_game_data_old(resp.content, title_id, media)
                if game_data is not None:
                    return game_data
                return None
            if resp.status_code == 400:
                logger.warning(
                    "OLD API %s rejected request: %s",
                    title_id,
                    catalog_error_name(resp.content) or resp.text[:200],
                )
                return None
        except requests.RequestException as exc:
            logger.debug("OLD API %s attempt %d failed: %s", title_id, attempt + 1, exc)
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return None


def fetch_from_new_api(title_id, locale, media, media_id=None):
    """Fetch game data from catalog-cdn."""
    media_id = normalize_media_id(media_id) or default_media_id(title_id)
    logger.debug("NEW API request: %s %s", locale, media_id)

    root = fetch_catalog_page(
        locale,
        page=1,
        detail_view=5,
        media_ids=(media_id,),
        media_types=DETAIL_MEDIA_TYPES,
    )
    if root is None:
        return None

    total = find_text(root, "live:totalItems", NEW_NS) or "0"
    if total == "0":
        logger.debug("NEW API %s: empty feed", title_id)
        return None

    return extract_game_data_new(
        ET.tostring(root, encoding="utf-8"), title_id, media
    )


def fetch_game_data(title_id, locale, media, api="auto", media_id=None):
    """Fetch game data using the specified API or fallback chain.

    Returns ``(data, source, media_id_used)``.
    """
    logger.debug(
        "fetch_game_data(%s, locale=%s, api=%s, media_id=%s)",
        title_id,
        locale,
        api,
        media_id,
    )
    media_id = normalize_media_id(media_id) or default_media_id(title_id)

    if api == "marketplace-xb":
        data = fetch_from_old_api(title_id, locale, media, media_id)
        return (data, "marketplace-xb", media_id) if data else (None, None, media_id)
    if api == "catalog-cdn":
        data = fetch_from_new_api(title_id, locale, media, media_id)
        return (data, "catalog-cdn", media_id) if data else (None, None, media_id)

    data = fetch_from_old_api(title_id, locale, media, media_id)
    if data is not None:
        logger.debug("Auto: %s found via OLD API", title_id)
        return data, "marketplace-xb", media_id

    logger.debug("Auto: %s not found via OLD API, trying NEW", title_id)
    data = fetch_from_new_api(title_id, locale, media, media_id)
    if data is not None:
        logger.debug("Auto: %s found via NEW API", title_id)
        return data, "catalog-cdn", media_id

    logger.debug("Auto: %s not found via either API", title_id)
    return None, None, media_id


def fetch_locale_old_api(args):
    """Fetch one locale via the OLD API (thread pool worker)."""
    title_id, locale, media, media_id = args
    media_id = normalize_media_id(media_id) or default_media_id(title_id)
    url = f"{MARKETPLACE_BASE}/{locale}/{media_id}"
    try:
        resp = SESSION.get(url, params=marketplace_params(), timeout=30)
        logger.debug(
            "OLD API locale %s %s: status=%d, size=%d bytes",
            title_id,
            locale,
            resp.status_code,
            len(resp.content),
        )
        if resp.status_code != 200:
            return None
        root = parse_xml(resp.content)
        if root is None:
            return None
        if (find_text(root, "totalItems", OLD_NS) or "0") == "0":
            return None
        game_data = extract_game_data_old(resp.content, title_id, media)
        if game_data is not None:
            return {"locale": locale, "url": url, "data": game_data}
    except requests.RequestException as exc:
        logger.debug("OLD API locale %s %s failed: %s", title_id, locale, exc)
    return None


def fetch_locale_new_api(args):
    """Fetch one locale via catalog-cdn (thread pool worker)."""
    title_id, locale, media, media_id = args
    media_id = normalize_media_id(media_id) or default_media_id(title_id)
    try:
        root = fetch_catalog_page(
            locale,
            page=1,
            detail_view=5,
            media_ids=(media_id,),
            media_types=DETAIL_MEDIA_TYPES,
        )
        if root is None:
            return None
        if (find_text(root, "live:totalItems", NEW_NS) or "0") == "0":
            return None
        game_data = extract_game_data_new(
            ET.tostring(root, encoding="utf-8"), title_id, media
        )
        if game_data is not None:
            return {
                "locale": locale,
                "url": f"{CATALOG_URL}?MediaIds={media_id}&Locale={locale}",
                "data": game_data,
            }
    except (ET.ParseError, requests.RequestException) as exc:
        logger.debug("NEW API locale %s %s failed: %s", title_id, locale, exc)
    return None


def fetch_locales(title_id, media, media_id, locales, workers=MAX_WORKERS):
    """Fetch several locales. OLD API first, catalog-cdn for failures."""
    locales = list(dict.fromkeys(locales))
    tasks = [(title_id, locale, media, media_id) for locale in locales]
    results = {}
    failed = []

    logger.debug("Phase 1: fetching %d locale(s) via OLD API", len(tasks))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(fetch_locale_old_api, task): task[1] for task in tasks
        }
        for future in as_completed(futures):
            locale = futures[future]
            result = future.result()
            if result:
                results[locale] = {"url": result["url"], "data": result["data"]}
            else:
                failed.append(locale)

    if failed:
        logger.debug(
            "Phase 2: retrying %d locale(s) via catalog-cdn: %s",
            len(failed),
            failed,
        )
        retry_tasks = [(title_id, locale, media, media_id) for locale in failed]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(fetch_locale_new_api, task): task[1]
                for task in retry_tasks
            }
            for future in as_completed(futures):
                locale = futures[future]
                result = future.result()
                if result:
                    results[locale] = {"url": result["url"], "data": result["data"]}

    return results


def fetch_all_locales(title_id, media, media_id=None, workers=MAX_WORKERS):
    """Fetch every configured locale."""
    return fetch_locales(title_id, media, media_id, LOCALES, workers=workers)


# =========================
# Artwork Candidate Selection
# =========================


def gather_candidates(locale_results, category, preferred_locale):
    """Collect unique URLs for a category across locales.

    The preferred locale is listed first. Duplicate URLs keep the first
    occurrence.
    """
    ordered_locales = sorted(
        locale_results.keys(), key=lambda locale: (locale != preferred_locale, locale)
    )
    seen = set()
    candidates = []

    for locale in ordered_locales:
        data = locale_results[locale]["data"]
        if category == "gallery":
            urls = data.get("artwork", {}).get("gallery") or []
        elif category == "products":
            urls = data.get("products", {}).get("related") or []
        elif category in ARTWORK_CATEGORIES:
            url = data.get("artwork", {}).get(category)
            urls = [url] if url else []
        else:
            urls = []

        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append({"locale": locale, "url": url})

    return candidates


def default_candidate_indices(candidates, preferred_locale):
    """Indices of candidates from the preferred locale, or the first locale."""
    indices = [
        index
        for index, candidate in enumerate(candidates)
        if candidate["locale"] == preferred_locale
    ]
    if indices or not candidates:
        return indices
    fallback_locale = candidates[0]["locale"]
    return [
        index
        for index, candidate in enumerate(candidates)
        if candidate["locale"] == fallback_locale
    ]


def format_candidate(label, candidates, selected_indices=()):
    print(f"\n{label}:")
    for index, candidate in enumerate(candidates, start=1):
        marker = "*" if (index - 1) in selected_indices else " "
        print(f" {marker}[{index}] {candidate['locale']:<8} {candidate['url']}")


def parse_selection(text, count):
    """Parse '1,3,5-7' into zero-based indices. Returns None if invalid."""
    if not text:
        return None
    selected = []
    for part in re.split(r"[,\s]+", text.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            return None
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > count:
            return None
        for number in range(start, end + 1):
            if number not in selected:
                selected.append(number)
    return [number - 1 for number in selected]


def sanitize_filename(name):
    return re.sub(r"[^\w.-]+", "_", name).strip("._") or "preview"


def preview_candidate(candidate, label):
    """Download a candidate and open it in the default application."""
    global PREVIEW_DIR
    if PREVIEW_DIR is None:
        PREVIEW_DIR = Path(tempfile.mkdtemp(prefix="x360db-preview-"))

    url = candidate["url"]
    print(f"  Previewing {candidate['locale']} {label}: {url}")
    try:
        resp = SESSION.get(
            url, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT}
        )
        if resp.status_code != 200 or not resp.content:
            print(f"  Preview failed: HTTP {resp.status_code}")
            webbrowser.open(url)
            return

        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        extension = MIME_TYPE_TO_EXTENSION.get(content_type)
        if not extension:
            filename = url.rsplit("/", 1)[-1].split("?")[0]
            extension = os.path.splitext(filename)[1] or ".img"

        path = PREVIEW_DIR / (
            sanitize_filename(f"{label}_{candidate['locale']}") + extension
        )
        with open(path, "wb") as handle:
            handle.write(resp.content)

        details = f"{len(resp.content) / 1024:.0f} KB"
        if content_type.startswith("image/"):
            try:
                with Image.open(BytesIO(resp.content)) as image:
                    details = f"{image.width}x{image.height}, {details}"
            except Exception:
                pass

        print(f"  Opened preview ({details}): {path}")
        if hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except (requests.RequestException, OSError) as exc:
        print(f"  Preview failed: {exc}")
        webbrowser.open(url)


def prompt_single(category, candidates, default_index):
    """Prompt for one candidate. Returns a candidate or None to keep current."""
    print(f"\nChoose {category} (Enter = default, v N = preview, s = keep, q = keep):")
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            print("  No input available; keeping the default.")
            return candidates[default_index] if candidates else None
        except KeyboardInterrupt:
            print("\n  Keeping current selection.")
            return None

        if answer in ("", "d"):
            return candidates[default_index] if candidates else None
        if answer in ("s", "q"):
            return None
        if answer in ("?", "help"):
            print("  Enter a number to select it, 'v N' to preview, 's' to keep.")
            continue
        if answer.lower().startswith("v "):
            selection = parse_selection(answer[2:], len(candidates))
            if selection and len(selection) == 1:
                preview_candidate(candidates[selection[0]], category)
            else:
                print("  Usage: v <number>")
            continue

        selection = parse_selection(answer, len(candidates))
        if selection and len(selection) == 1:
            return candidates[selection[0]]
        print("  Enter one number from the list.")


def prompt_multi(category, candidates, default_indices):
    """Prompt for several candidates. Returns a list or None to keep current."""
    selected = set(default_indices)
    print(
        f"\nChoose {category} (Enter = default, 1,3,5 = list, a = all, "
        "n = none, v N = preview, s = keep, q = keep):"
    )
    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            print("  No input available; keeping the default.")
            return [candidates[index] for index in sorted(selected)]
        except KeyboardInterrupt:
            print("\n  Keeping current selection.")
            return None

        if answer in ("", "d"):
            return [candidates[index] for index in sorted(selected)]
        if answer in ("s", "q"):
            return None
        if answer in ("?", "help"):
            print(
                "  Enter numbers separated by commas or ranges, 'a' for all, "
                "'n' for none, 'v N' to preview."
            )
            continue
        if answer.lower() == "a":
            return list(candidates)
        if answer.lower() == "n":
            return []
        if answer.lower().startswith("v "):
            selection = parse_selection(answer[2:], len(candidates))
            if selection and len(selection) == 1:
                preview_candidate(candidates[selection[0]], category)
            else:
                print("  Usage: v <number>")
            continue

        selection = parse_selection(answer, len(candidates))
        if selection is not None:
            return [candidates[index] for index in selection]
        print("  Invalid selection.")


def pick_artwork(
    game_data,
    locale_results,
    preferred_locale,
    interactive=True,
    list_only=False,
):
    """Apply artwork/gallery/product selections from locale results."""
    for category in PICKER_CATEGORIES:
        candidates = gather_candidates(locale_results, category, preferred_locale)
        if not candidates:
            continue

        default_indices = default_candidate_indices(candidates, preferred_locale)
        default_index = default_indices[0] if default_indices else 0

        if list_only:
            format_candidate(category, candidates)
            continue

        if len(candidates) == 1:
            chosen = candidates
        elif interactive:
            if category in ARTWORK_CATEGORIES:
                format_candidate(category, candidates, {default_index})
                chosen = prompt_single(category, candidates, default_index)
                if chosen is None:
                    continue
                chosen = [chosen]
            else:
                format_candidate(category, candidates, set(default_indices))
                chosen = prompt_multi(category, candidates, default_indices)
                if chosen is None:
                    continue
        else:
            chosen = [candidates[index] for index in default_indices]

        urls = [candidate["url"] for candidate in chosen]
        if category in ARTWORK_CATEGORIES:
            game_data["artwork"][category] = urls[0] if urls else None
        elif category == "gallery":
            game_data["artwork"]["gallery"] = urls
        elif category == "products":
            game_data["products"]["related"] = urls

        if urls and interactive:
            locales = sorted({candidate["locale"] for candidate in chosen})
            print(f"  {category}: {len(urls)} URL(s) from {', '.join(locales)}")


# =========================
# Download & Save Functions
# =========================


def find_existing_artwork(artwork_dir, base_name):
    """Check if an artwork file exists with any supported extension."""
    for extension in [".jpg", ".png", ".bmp", ".webp", ".tiff", ".ico"]:
        path = os.path.join(artwork_dir, base_name + extension)
        if os.path.isfile(path):
            return path
    return None


def remove_stale_artwork(base_path, keep_path):
    """Remove other-extension copies of an artwork file after a replacement."""
    directory = os.path.dirname(base_path)
    base_name = os.path.basename(base_path)
    keep_path = os.path.abspath(keep_path)
    for extension in [".jpg", ".png", ".bmp", ".webp", ".tiff", ".ico"]:
        candidate = os.path.join(directory, base_name + extension)
        if os.path.abspath(candidate) == keep_path:
            continue
        if not os.path.isfile(candidate):
            continue
        try:
            os.remove(candidate)
            logger.debug("Removed stale artwork: %s", candidate)
        except OSError as exc:
            logger.debug("Could not remove stale artwork %s: %s", candidate, exc)


def download_artwork(url, save_path, overwrite=False):
    """Download an image, detecting its extension from Content-Type.

    With ``overwrite``, an existing file is replaced only after the new
    download succeeds, and stale copies with other extensions are removed.
    """
    logger.debug("Downloading artwork: %s -> %s", url, save_path)
    for attempt in range(MAX_RETRIES):
        try:
            resp = SESSION.get(
                url,
                timeout=30,
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            if "image" in content_type:
                extension = MIME_TYPE_TO_EXTENSION.get(content_type, "")
                if not extension:
                    try:
                        with Image.open(BytesIO(resp.content)) as image:
                            extension = f".{image.format.lower()}"
                    except Exception:
                        extension = ".png"
                final_path = save_path + extension
                temp_path = final_path + ".part"
                try:
                    with open(temp_path, "wb") as handle:
                        handle.write(resp.content)
                    os.replace(temp_path, final_path)
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
                if overwrite:
                    remove_stale_artwork(save_path, final_path)
                logger.debug(
                    "Saved artwork: %s (%d bytes)", final_path, len(resp.content)
                )
                return final_path
            logger.debug("Non-image response: Content-Type=%s", content_type)
        except (requests.RequestException, OSError) as exc:
            logger.debug("Download attempt %d failed: %s", attempt + 1, exc)
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    logger.warning("Failed to download artwork after %d attempts: %s", MAX_RETRIES, url)
    return None


def save_artwork(game_data, title_dir, overwrite=False):
    """Download background, banner, boxart, and icon artwork."""
    artwork_dir = os.path.join(title_dir, "artwork")
    os.makedirs(artwork_dir, exist_ok=True)
    for artwork_type in ARTWORK_CATEGORIES:
        url = game_data["artwork"].get(artwork_type)
        if not url:
            logger.debug("No %s artwork URL for %s", artwork_type, game_data["id"])
            continue
        if find_existing_artwork(artwork_dir, artwork_type) and not overwrite:
            logger.debug("Skipping %s artwork (already exists)", artwork_type)
            continue
        save_path = os.path.join(artwork_dir, artwork_type)
        downloaded = download_artwork(url, save_path, overwrite=overwrite)
        if downloaded:
            print(f"  Saved artwork/{os.path.basename(downloaded)}")


def disambiguate_filenames(urls):
    """Return (url, base_name, extension) with collisions made unique.

    Screenshots and manuals often use the same filename in every locale
    folder (``screenlg1.jpg``). When two selected URLs share a base name,
    suffix each with its parent folder (for example ``screenlg1_1041``).
    """
    entries = []
    for url in urls:
        filename = url.rsplit("/", 1)[-1].split("?")[0]
        base, extension = os.path.splitext(filename)
        entries.append((url, base or filename, extension))

    counts = Counter(base.lower() for _, base, _ in entries)
    seen = set()
    result = []
    for url, base, extension in entries:
        if counts[base.lower()] > 1:
            parent = url.rstrip("/").split("/")[-2] if "/" in url else "alt"
            base = f"{base}_{sanitize_filename(parent)}"
        candidate = base
        number = 2
        while candidate.lower() in seen:
            candidate = f"{base}_{number}"
            number += 1
        seen.add(candidate.lower())
        result.append((url, candidate, extension))
    return result


def save_gallery(game_data, title_dir, overwrite=False):
    """Download gallery screenshots, keeping original filenames when unique."""
    gallery_urls = game_data["artwork"].get("gallery", [])
    if not gallery_urls:
        return
    gallery_dir = os.path.join(title_dir, "gallery")
    os.makedirs(gallery_dir, exist_ok=True)
    for url, base_name, _extension in disambiguate_filenames(gallery_urls):
        if find_existing_artwork(gallery_dir, base_name) and not overwrite:
            logger.debug("Skipping gallery image %s (already exists)", base_name)
            continue
        save_path = os.path.join(gallery_dir, base_name)
        downloaded = download_artwork(url, save_path, overwrite=overwrite)
        if downloaded:
            print(f"  Saved gallery/{os.path.basename(downloaded)}")


def save_products(game_data, title_dir, overwrite=False):
    """Download related product URLs (manuals, etc.) into products/."""
    related = game_data["products"].get("related", [])
    if not related:
        return
    products_dir = os.path.join(title_dir, "products")
    os.makedirs(products_dir, exist_ok=True)
    for url, base_name, extension in disambiguate_filenames(related):
        filename = base_name + extension
        save_path = os.path.join(products_dir, filename)
        if os.path.exists(save_path) and not overwrite:
            logger.debug("Skipping product %s (already exists)", filename)
            continue
        logger.debug("Downloading product: %s", url)
        try:
            resp = SESSION.get(
                url, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT}
            )
            if resp.status_code == 200:
                temp_path = save_path + ".part"
                try:
                    with open(temp_path, "wb") as handle:
                        handle.write(resp.content)
                    os.replace(temp_path, save_path)
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
                print(f"  Saved products/{filename}")
        except (requests.RequestException, OSError) as exc:
            logger.debug("Failed to download %s: %s", url, exc)


# =========================
# Input & Locale Helpers
# =========================


def load_input(input_path, catalog=None):
    """Load titles from a JSON file or a single hex title ID.

    Returns a list of ``(title_id, media, media_id)``. The catalog map is used
    to fill in media IDs, and entries may override it explicitly.
    """
    catalog = catalog or {}
    if os.path.isfile(input_path):
        with open(input_path, encoding="utf-8-sig") as handle:
            data = json.load(handle)

        if isinstance(data, dict):
            data = data.get("titles") or data.get("entries") or data.get("items")
        if not isinstance(data, list):
            print(f"Invalid input file: {input_path} (expected a JSON array)")
            sys.exit(1)

        titles = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title_id = (
                item.get("titleid") or item.get("title_id") or item.get("id") or ""
            )
            title_id = str(title_id).strip().upper()
            if not valid_title_id(title_id):
                logger.warning("Skipping invalid title ID: %r", title_id)
                continue

            media_id = normalize_media_id(item.get("media_id"))
            if not media_id and title_id in catalog:
                media_id = normalize_media_id(catalog[title_id].get("media_id"))
            if not media_id:
                media_id = default_media_id(title_id)

            media = item.get("media", [])
            titles.append((title_id, media, media_id))
        return titles

    title_id = input_path.strip().upper()
    if not valid_title_id(title_id):
        print(f"Invalid title ID: {title_id} (must be 8 hex characters)")
        sys.exit(1)

    media_id = None
    if title_id in catalog:
        media_id = normalize_media_id(catalog[title_id].get("media_id"))
    return [(title_id, [], media_id or default_media_id(title_id))]


def parse_locale_list(value):
    if not value:
        return []
    if value.strip().lower() == "all":
        return list(LOCALES)
    return [item.strip() for item in value.split(",") if item.strip()]


def load_existing_media(info_path):
    """Read media variants from an existing info.json, if present.

    Catalog rows do not carry the repository's ``media`` list (regional and
    edition variants), so an update should not silently erase it.
    """
    info_path = Path(info_path)
    if not info_path.is_file():
        return []
    try:
        with open(info_path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not read existing %s: %s", info_path, exc)
        return []
    media = data.get("media") if isinstance(data, dict) else None
    return media if isinstance(media, list) else []


def pick_default_locale(locale_results, preferred):
    """Select best locale from results, preferring English variants."""
    if preferred and preferred in locale_results:
        return preferred
    for locale in ENGLISH_LOCALES:
        if locale in locale_results:
            return locale
    if locale_results:
        return sorted(locale_results.keys())[0]
    return None


# =========================
# Main
# =========================


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Xbox Marketplace game data. Use --enumerate to build a "
            "catalog of title IDs and media IDs."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        help=(
            "JSON file with title entries, a catalog.json, or a single "
            "8-character hex title ID"
        ),
    )
    parser.add_argument(
        "--enumerate",
        action="store_true",
        help="Enumerate the legacy Xbox 360 catalog and write catalog.json",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help=(
            "Catalog file used to resolve exact media IDs "
            f"(default: {DEFAULT_CATALOG_PATH} if it exists)"
        ),
    )
    parser.add_argument(
        "--catalog-out",
        default=str(DEFAULT_CATALOG_PATH),
        help="Output path for --enumerate (default: <project>/catalog.json)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Stop --enumerate after this many pages (for testing)",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.3,
        help="Delay between catalog pages in seconds (default: 0.3)",
    )
    parser.add_argument(
        "--api",
        choices=["auto", "marketplace-xb", "catalog-cdn"],
        default="auto",
        help="API for info.json: marketplace-xb, catalog-cdn, or auto (default: auto)",
    )
    parser.add_argument(
        "--region",
        default="en-US",
        help="Locale for the default info.json and catalog enumeration (default: en-US)",
    )
    parser.add_argument(
        "--all-locales",
        action="store_true",
        help="Fetch all supported locales and save each as info_{locale}.json",
    )
    parser.add_argument(
        "--artwork-locales",
        default=None,
        help=(
            "Comma-separated extra locales to gather artwork/gallery/products "
            "from, e.g. en-US,ja-JP,de-DE, or 'all' for every supported locale "
            "(default: --region only)"
        ),
    )
    parser.add_argument(
        "--pick-artwork",
        action="store_true",
        help=(
            "Interactively choose artwork, gallery, and product URLs from the "
            "fetched locales. Use 'v N' to preview a candidate."
        ),
    )
    parser.add_argument(
        "--list-artwork",
        action="store_true",
        help="List artwork/gallery/product candidates without writing info.json",
    )
    parser.add_argument(
        "--probe-prefixes",
        action="store_true",
        help=(
            "When no catalog media ID is available, try D802 then D804 "
            "instead of assuming D802"
        ),
    )
    parser.add_argument(
        "--artwork",
        action="store_true",
        help="Download artwork (background, banner, boxart, icon) per title",
    )
    parser.add_argument(
        "--gallery",
        action="store_true",
        help="Download gallery screenshots into gallery/ folder per title",
    )
    parser.add_argument(
        "--products",
        action="store_true",
        help="Download related products into products/ folder per title",
    )
    parser.add_argument(
        "--overwrite-artwork",
        "--overwrite-assets",
        dest="overwrite_artwork",
        action="store_true",
        help=(
            "Overwrite existing artwork, gallery, and product files with the "
            "selected versions"
        ),
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-fetch data even if info.json already exists",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for title folders (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Concurrent locale requests (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (HTTP requests, parsing details, downloads)",
    )
    return parser


def resolve_catalog_path(explicit):
    if explicit:
        return Path(explicit)
    if DEFAULT_CATALOG_PATH.is_file():
        return DEFAULT_CATALOG_PATH
    return None


def run_enumerate(args):
    print(f"Enumerating Xbox 360 catalog ({args.region})...")
    rows, stats = enumerate_catalog(
        locale=args.region,
        max_pages=args.max_pages,
        page_delay=args.page_delay,
    )
    output = Path(args.catalog_out)
    save_catalog(rows, output)

    print(
        f"\nEnumerated {stats['unique']} unique title IDs from "
        f"{stats['entries_seen']} scanned entries ({stats['pages']} pages; "
        f"catalog reports {stats['total_items']})."
    )
    print(
        f"  D802: {stats['d802']}  D804: {stats['d804']}  "
        f"duplicate entries: {stats['duplicates']}"
    )
    print(f"  Wrote {output}")
    if stats["duplicates"]:
        print("  Duplicate title IDs were merged; the first usable row was kept.")


def fetch_title_data(args, title_id, media, media_id, catalog_entry):
    """Fetch the default-locale data, optionally probing media prefixes."""
    explicit_media = None
    if catalog_entry:
        explicit_media = normalize_media_id(catalog_entry.get("media_id"))

    candidates = []
    if explicit_media:
        candidates = [explicit_media]
    else:
        candidates = [normalize_media_id(media_id) or default_media_id(title_id)]
        if args.probe_prefixes:
            base = normalize_media_id(media_id) or default_media_id(title_id)
            prefix = media_id_prefix(base)
            for probe in PROBE_MEDIA_PREFIXES:
                if probe != prefix:
                    candidates.append(default_media_id(title_id, probe))

    for candidate in candidates:
        game_data, source, used = fetch_game_data(
            title_id, args.region, media, args.api, candidate
        )
        if game_data is not None:
            return game_data, source, used
        logger.debug("No data for %s with media ID %s", title_id, candidate)
    return None, None, candidates[0]


def main():
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )

    if args.enumerate:
        run_enumerate(args)
        return

    if not args.input:
        parser.error("input is required unless --enumerate is used")

    catalog_path = resolve_catalog_path(args.catalog)
    catalog = load_catalog_map(catalog_path) if catalog_path else {}
    if catalog_path:
        logger.debug("Using catalog: %s", catalog_path)

    titles = load_input(args.input, catalog)
    output_dir = Path(args.output_dir)
    preferred_locale = args.region
    artwork_locales = parse_locale_list(args.artwork_locales)
    locales_to_fetch = (
        list(LOCALES) if args.all_locales else [preferred_locale] + artwork_locales
    )
    locales_to_fetch = list(dict.fromkeys(locales_to_fetch))

    print(f"Processing {len(titles)} title ID(s)...")
    if artwork_locales:
        print(f"Artwork locales: {', '.join(locales_to_fetch)}")
    print()

    for index, (title_id, media, media_id) in enumerate(titles, start=1):
        title_dir = output_dir / title_id
        info_path = title_dir / "info.json"

        if info_path.exists() and not args.update and not args.list_artwork:
            print(f"[{index}/{len(titles)}] {title_id} - already exists, skipping")
            continue

        if not media:
            media = load_existing_media(info_path)
            if media:
                logger.debug(
                    "Preserving %d existing media entries for %s",
                    len(media),
                    title_id,
                )

        catalog_entry = catalog.get(title_id)

        game_data, source, used_media_id = fetch_title_data(
            args, title_id, media, media_id, catalog_entry
        )
        if game_data is None:
            print(f"[{index}/{len(titles)}] {title_id} - no data for {args.region}")
            continue

        logger.debug("Using media ID %s (%s)", used_media_id, source)

        locale_results = {}
        if len(locales_to_fetch) > 1 or args.all_locales:
            print(
                f"[{index}/{len(titles)}] {title_id} - fetching "
                f"{len(locales_to_fetch)} locale(s)..."
            )
            locale_results = fetch_locales(
                title_id,
                media,
                used_media_id,
                locales_to_fetch,
                workers=args.workers,
            )
        else:
            locale_results[preferred_locale] = {
                "url": used_media_id,
                "data": game_data,
            }

        # The preferred-locale data is already canonical; keep it even if the
        # locale fan-out failed.
        locale_results.setdefault(
            preferred_locale, {"url": used_media_id, "data": game_data}
        )

        print(
            f"[{index}/{len(titles)}] {title_id} - "
            f"{game_data['title']['full'] or 'Unknown'} "
            f"({source}, {len(locale_results)} locale(s), {used_media_id})"
        )

        if args.list_artwork:
            pick_artwork(
                game_data,
                locale_results,
                preferred_locale,
                interactive=False,
                list_only=True,
            )
            continue

        pick_artwork(
            game_data,
            locale_results,
            preferred_locale,
            interactive=args.pick_artwork,
        )

        title_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Writing %s", info_path)
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump(game_data, handle, ensure_ascii=False, indent=2)

        if args.all_locales:
            for locale, loc_result in locale_results.items():
                if locale == preferred_locale:
                    continue
                locale_info_path = title_dir / f"info_{locale}.json"
                logger.debug("Writing %s", locale_info_path)
                with open(locale_info_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        loc_result["data"], handle, ensure_ascii=False, indent=2
                    )

        if args.artwork:
            save_artwork(
                game_data, str(title_dir), overwrite=args.overwrite_artwork
            )

        if args.gallery:
            save_gallery(
                game_data, str(title_dir), overwrite=args.overwrite_artwork
            )

        if args.products:
            save_products(
                game_data, str(title_dir), overwrite=args.overwrite_artwork
            )

    print("\nDone.")


if __name__ == "__main__":
    main()
