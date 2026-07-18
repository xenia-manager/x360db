#!/usr/bin/env python3
"""
Fetch Xbox Marketplace game data via dual-API strategy.

Primary: marketplace-xb.xboxlive.com (legacy API with parent products)
Fallback: catalog-cdn.xboxlive.com (newer API for other locales)

Writes info.json per title, with optional artwork, gallery, and
parent product fetching. Supports single Title ID or JSON batch input.
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

logger = logging.getLogger("xbox_marketplace")

# =========================
# Configuration
# =========================

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

MARKETPLACE_URL_TEMPLATE = (
    "http://marketplace-xb.xboxlive.com/marketplacecatalog/v1/product/{locale}/"
    "66ACD000-77FE-1000-9115-D802{title_id}"
    "?bodytypes=1.3&detailview=detaillevel5&pagenum=1&pagesize=1&stores=1"
    "&tiers=2.3&offerfilter=1&producttypes=1.5.18.19.20.21.22.23.30.34.37.46.47.61"
)

CATALOG_URL_TEMPLATE = (
    "http://catalog-cdn.xboxlive.com/Catalog/Catalog.asmx/Query"
    "?methodName=FindGames"
    "&Names=Locale&Values={locale}"
    "&Names=LegalLocale&Values={locale}"
    "&Names=Store&Values=1"
    "&Names=PageSize&Values=100"
    "&Names=PageNum&Values=1"
    "&Names=DetailView&Values=5"
    "&Names=OfferFilterLevel&Values=1"
    "&Names=MediaIds&Values=66acd000-77fe-1000-9115-d802{title_id}"
    "&Names=UserTypes&Values=2"
    "&Names=MediaTypes&Values=1"
    "&Names=MediaTypes&Values=21"
    "&Names=MediaTypes&Values=23"
    "&Names=MediaTypes&Values=37"
    "&Names=MediaTypes&Values=46"
)

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

GENRE_EXCLUDE_IDS = {"3000", "3027"}

MIME_TYPE_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/vnd.microsoft.icon": ".ico",
}

MAX_WORKERS = 32
MAX_RETRIES = 3
RETRY_DELAY = 2

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
    """Remove 'Full Game - ' prefix and special characters from title."""
    if text and text.startswith("Full Game - "):
        text = text.replace("Full Game - ", "", 1)
    return re.sub(r"[^\w\s-]", "", text).rstrip() if text else None


def extract_game_data_old(xml_content, title_id, media):
    """Parse OLD API (marketplace-xb) XML response into game data dict."""
    logger.debug("Parsing OLD API XML for %s (%d bytes)", title_id, len(xml_content))
    root = ET.fromstring(xml_content)
    entry = root.find(".//a:entry", namespaces=OLD_NS)
    if entry is None:
        logger.debug("No <entry> found in OLD API response for %s", title_id)
        return None
    logger.debug("Found <entry> in OLD API response for %s", title_id)

    def find_text(tag):
        elem = entry.find(tag, namespaces=OLD_NS)
        return elem.text if elem is not None and elem.text else None

    game_data = {
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

    game_data["title"]["full"] = clean_title(find_text("fullTitle"))
    game_data["title"]["reduced"] = clean_title(find_text("reducedTitle"))
    game_data["developer"] = find_text("developerName")
    game_data["publisher"] = find_text("publisherName")
    game_data["description"]["full"] = find_text("fullDescription")
    game_data["description"]["short"] = find_text("reducedDescription")
    game_data["user_rating"] = find_text("userRating")

    release = find_text("globalOriginalReleaseDate")
    if release:
        game_data["release_date"] = re.sub(r"T.*$", "", release)

    categories = entry.findall("categories/category", namespaces=OLD_NS)
    for cat in categories:
        cat_id = cat.find("categoryId", namespaces=OLD_NS)
        system = cat.find("categorySystemId", namespaces=OLD_NS)
        name = cat.find("categoryName", namespaces=OLD_NS)
        if (
            system is not None
            and system.text == "3000"
            and cat_id is not None
            and cat_id.text not in GENRE_EXCLUDE_IDS
            and name is not None
            and name.text
        ):
            game_data["genre"].append(name.text)
    game_data["genre"] = sorted(game_data["genre"])

    images = entry.findall("images/image", namespaces=OLD_NS)
    for image in images:
        rel_elem = image.find("relationshipType", namespaces=OLD_NS)
        url_elem = image.find("fileUrl", namespaces=OLD_NS)
        if rel_elem is None or url_elem is None:
            continue
        rel_type = rel_elem.text
        if rel_type in OLD_RELATIONSHIP_MAP:
            key = OLD_RELATIONSHIP_MAP[rel_type]
            if game_data["artwork"][key] is None:
                game_data["artwork"][key] = url_elem.text

    slideshows = entry.find("slideShows", namespaces=OLD_NS)
    if slideshows is not None:
        for slideshow in slideshows.findall("slideShow", namespaces=OLD_NS):
            for image in slideshow.findall("image", namespaces=OLD_NS):
                url_elem = image.find("fileUrl", namespaces=OLD_NS)
                if url_elem is not None and url_elem.text:
                    game_data["artwork"]["gallery"].append(url_elem.text)
    game_data["artwork"]["gallery"] = sorted(
        game_data["artwork"]["gallery"], key=extract_number
    )

    parent_products = entry.find("parentProducts", namespaces=OLD_NS)
    if parent_products is not None:
        for pp in parent_products.findall("parentProduct", namespaces=OLD_NS):
            parent = {"id": None, "title": None}
            pid = pp.find("parentProductId", namespaces=OLD_NS)
            if pid is not None and pid.text:
                parent["id"] = pid.text[-8:].upper()
            ptitle = pp.find("parentReducedTitle", namespaces=OLD_NS)
            if ptitle is not None and ptitle.text:
                parent["title"] = ptitle.text
            if parent["id"] and parent["id"] != title_id:
                game_data["products"]["parent"].append(parent)

    related_urls = entry.find("relatedUrls", namespaces=OLD_NS)
    if related_urls is not None:
        for ru in related_urls.findall("relatedUrl", namespaces=OLD_NS):
            url_elem = ru.find("relatedUrl", namespaces=OLD_NS)
            if url_elem is not None and url_elem.text:
                game_data["products"]["related"].append(url_elem.text)

    logger.debug(
        "OLD API %s: title=%r, dev=%r, pub=%r, genres=%d, artworks=%s, gallery=%d",
        title_id,
        game_data["title"]["full"],
        game_data["developer"],
        game_data["publisher"],
        len(game_data["genre"]),
        {k: v for k, v in game_data["artwork"].items() if k != "gallery" and v},
        len(game_data["artwork"]["gallery"]),
    )
    return game_data


def extract_game_data_new(xml_content, title_id, media):
    """Parse NEW API (catalog-cdn) XML response into game data dict."""
    logger.debug("Parsing NEW API XML for %s (%d bytes)", title_id, len(xml_content))
    root = ET.fromstring(xml_content)
    entry = root.find("a:entry", namespaces=NEW_NS)
    if entry is None:
        logger.debug("No <entry> found in NEW API response for %s", title_id)
        return None
    logger.debug("Found <entry> in NEW API response for %s", title_id)

    game_data = {
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

    media_elem = entry.find("live:media", namespaces=NEW_NS)
    if media_elem is not None:
        full_title_elem = media_elem.find("live:fullTitle", namespaces=NEW_NS)
        if full_title_elem is not None and full_title_elem.text:
            game_data["title"]["full"] = clean_title(full_title_elem.text)

        reduced_title_elem = media_elem.find("live:reducedTitle", namespaces=NEW_NS)
        if reduced_title_elem is not None and reduced_title_elem.text:
            game_data["title"]["reduced"] = clean_title(reduced_title_elem.text)

        dev = media_elem.find("live:developer", namespaces=NEW_NS)
        if dev is not None:
            game_data["developer"] = dev.text

        pub = media_elem.find("live:publisher", namespaces=NEW_NS)
        if pub is not None:
            game_data["publisher"] = pub.text

        desc = media_elem.find("live:description", namespaces=NEW_NS)
        if desc is not None:
            game_data["description"]["full"] = desc.text

        short_desc = media_elem.find("live:reducedDescription", namespaces=NEW_NS)
        if short_desc is not None:
            game_data["description"]["short"] = short_desc.text

        rel = media_elem.find("live:releaseDate", namespaces=NEW_NS)
        if rel is not None and rel.text:
            game_data["release_date"] = re.sub(r"T.*$", "", rel.text)

        rating = media_elem.find("live:ratingAggregate", namespaces=NEW_NS)
        if rating is not None and rating.text:
            game_data["user_rating"] = rating.text

    categories_elem = entry.find("live:categories", namespaces=NEW_NS)
    if categories_elem is not None:
        for cat in categories_elem.findall("live:category", namespaces=NEW_NS):
            cat_id_elem = cat.find("live:categoryId", namespaces=NEW_NS)
            system_elem = cat.find("live:system", namespaces=NEW_NS)
            name_elem = cat.find("live:name", namespaces=NEW_NS)
            if (
                system_elem is not None
                and system_elem.text == "3000"
                and cat_id_elem is not None
                and cat_id_elem.text not in GENRE_EXCLUDE_IDS
                and name_elem is not None
                and name_elem.text
            ):
                game_data["genre"].append(name_elem.text)
    game_data["genre"] = sorted(game_data["genre"])

    images_elem = entry.find("live:images", namespaces=NEW_NS)
    if images_elem is not None:
        for image in images_elem.findall("live:image", namespaces=NEW_NS):
            rel_type_elem = image.find("live:relationshipType", namespaces=NEW_NS)
            fileurl_elem = image.find("live:fileUrl", namespaces=NEW_NS)
            if rel_type_elem is None or fileurl_elem is None:
                continue
            if rel_type_elem.text in NEW_RELATIONSHIP_MAP:
                key = NEW_RELATIONSHIP_MAP[rel_type_elem.text]
                if game_data["artwork"][key] is None:
                    game_data["artwork"][key] = fileurl_elem.text

    slideshows_elem = entry.find("live:slideShows", namespaces=NEW_NS)
    if slideshows_elem is not None:
        for slideshow in slideshows_elem.findall("live:slideShow", namespaces=NEW_NS):
            for image in slideshow.findall("live:image", namespaces=NEW_NS):
                fileurl_elem = image.find("live:fileUrl", namespaces=NEW_NS)
                if fileurl_elem is not None:
                    game_data["artwork"]["gallery"].append(fileurl_elem.text)
    game_data["artwork"]["gallery"] = sorted(
        game_data["artwork"]["gallery"], key=extract_number
    )

    logger.debug(
        "NEW API %s: title=%r, dev=%r, pub=%r, genres=%d, artworks=%s, gallery=%d",
        title_id,
        game_data["title"]["full"],
        game_data["developer"],
        game_data["publisher"],
        len(game_data["genre"]),
        {k: v for k, v in game_data["artwork"].items() if k != "gallery" and v},
        len(game_data["artwork"]["gallery"]),
    )
    return game_data


# =========================
# API Fetch Functions
# =========================


def fetch_from_old_api(title_id, locale):
    """Fetch game data from legacy marketplace-xb API with retries."""
    url = MARKETPLACE_URL_TEMPLATE.format(locale=locale, title_id=title_id)
    logger.debug("OLD API request: %s", url)
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=10)
            logger.debug(
                "OLD API %s response: status=%d, size=%d bytes",
                title_id,
                resp.status_code,
                len(resp.content),
            )
            if resp.status_code == 200 and b"entry" in resp.content:
                game_data = extract_game_data_old(resp.text, title_id, [])
                if game_data is not None:
                    return game_data
                logger.debug("OLD API %s: entry found but extraction failed", title_id)
                return None
            logger.debug("OLD API %s: no data (status=%d)", title_id, resp.status_code)
        except Exception as e:
            logger.debug("OLD API %s attempt %d failed: %s", title_id, attempt + 1, e)
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return None


def fetch_from_new_api(title_id, locale, media):
    """Fetch game data from catalog-cdn API with retries."""
    url = CATALOG_URL_TEMPLATE.format(locale=locale, title_id=title_id.lower())
    logger.debug("NEW API request: %s", url)
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=10)
            logger.debug(
                "NEW API %s response: status=%d, size=%d bytes",
                title_id,
                resp.status_code,
                len(resp.content),
            )
            if resp.status_code == 200 and b"entry" in resp.content:
                game_data = extract_game_data_new(resp.text, title_id, media)
                if game_data is not None:
                    return game_data
                logger.debug("NEW API %s: entry found but extraction failed", title_id)
                return None
            logger.debug("NEW API %s: no data (status=%d)", title_id, resp.status_code)
        except Exception as e:
            logger.debug("NEW API %s attempt %d failed: %s", title_id, attempt + 1, e)
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    return None


def fetch_game_data(title_id, locale, media, api="auto"):
    """Fetch game data using specified API or fallback chain."""
    logger.debug("fetch_game_data(%s, locale=%s, api=%s)", title_id, locale, api)
    if api == "marketplace-xb":
        game_data = fetch_from_old_api(title_id, locale)
        return (game_data, "marketplace-xb") if game_data else (None, None)
    if api == "catalog-cdn":
        game_data = fetch_from_new_api(title_id, locale, media)
        return (game_data, "catalog-cdn") if game_data else (None, None)
    game_data = fetch_from_old_api(title_id, locale)
    if game_data is not None:
        logger.debug("Auto: %s found via OLD API", title_id)
        return game_data, "marketplace-xb"
    logger.debug("Auto: %s not found via OLD API, trying NEW", title_id)
    game_data = fetch_from_new_api(title_id, locale, media)
    if game_data is not None:
        logger.debug("Auto: %s found via NEW API", title_id)
        return game_data, "catalog-cdn"
    logger.debug("Auto: %s not found via either API", title_id)
    return None, None


def fetch_locale_new_api(args):
    """Fetch game data for a single locale via NEW API (used in thread pool)."""
    title_id, locale, media = args
    url = CATALOG_URL_TEMPLATE.format(locale=locale, title_id=title_id.lower())
    logger.debug("NEW API locale fetch: %s for %s", locale, title_id)
    try:
        resp = requests.get(url, timeout=10)
        logger.debug(
            "NEW API locale %s %s: status=%d, size=%d bytes",
            title_id,
            locale,
            resp.status_code,
            len(resp.content),
        )
        if resp.status_code == 200 and b"entry" in resp.content:
            game_data = extract_game_data_new(resp.text, title_id, media)
            if game_data is not None:
                logger.debug(
                    "NEW API locale %s %s: title=%r",
                    title_id,
                    locale,
                    game_data["title"]["full"],
                )
                return {"locale": locale, "url": url, "data": game_data}
            logger.debug(
                "NEW API locale %s %s: entry found but extraction failed", title_id, locale
            )
    except Exception as e:
        logger.debug("NEW API locale %s %s failed: %s", title_id, locale, e)
    return None


def fetch_locale_old_api(args):
    """Fetch game data for a single locale via OLD API (used in thread pool)."""
    title_id, locale, media = args
    url = MARKETPLACE_URL_TEMPLATE.format(locale=locale, title_id=title_id)
    logger.debug("OLD API locale fetch: %s for %s", locale, title_id)
    try:
        resp = requests.get(url, timeout=10)
        logger.debug(
            "OLD API locale %s %s: status=%d, size=%d bytes",
            title_id,
            locale,
            resp.status_code,
            len(resp.content),
        )
        if resp.status_code == 200 and b"entry" in resp.content:
            game_data = extract_game_data_old(resp.text, title_id, media)
            if game_data is not None:
                logger.debug(
                    "OLD API locale %s %s: title=%r",
                    title_id,
                    locale,
                    game_data["title"]["full"],
                )
                return {"locale": locale, "url": url, "data": game_data}
            logger.debug(
                "OLD API locale %s %s: entry found but extraction failed", title_id, locale
            )
    except Exception as e:
        logger.debug("OLD API locale %s %s failed: %s", title_id, locale, e)
    return None


def fetch_all_locales(title_id, media):
    """Fetch game data for all 50 locales. Tries OLD API first, falls back to NEW API for failures."""
    tasks = [(title_id, locale, media) for locale in LOCALES]
    locale_results = {}
    failed_locales = []

    logger.debug("Phase 1: Fetching all locales via OLD API")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_locale_old_api, task): task[1] for task in tasks}
        for future in as_completed(futures):
            locale = futures[future]
            result = future.result()
            if result:
                locale_results[locale] = {
                    "url": result["url"],
                    "data": result["data"],
                }
            else:
                failed_locales.append(locale)

    if failed_locales:
        logger.debug("Phase 2: Retrying %d failed locales via NEW API: %s", len(failed_locales), failed_locales)
        retry_tasks = [(title_id, locale, media) for locale in failed_locales]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_locale_new_api, task): task[1] for task in retry_tasks}
            for future in as_completed(futures):
                locale = futures[future]
                result = future.result()
                if result:
                    locale_results[locale] = {
                        "url": result["url"],
                        "data": result["data"],
                    }

    return locale_results


# =========================
# Download & Save Functions
# =========================


def find_existing_artwork(artwork_dir, base_name):
    """Check if artwork file exists with any supported extension."""
    for ext in [".jpg", ".png", ".bmp", ".webp", ".tiff", ".ico"]:
        path = os.path.join(artwork_dir, base_name + ext)
        if os.path.isfile(path):
            return path
    return None


def download_artwork(url, save_path):
    """Download image from URL with retries, detecting extension from Content-Type."""
    logger.debug("Downloading artwork: %s -> %s", url, save_path)
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
            content_type = resp.headers.get("Content-Type", "")
            if "image" in content_type:
                ext = MIME_TYPE_TO_EXTENSION.get(content_type, "")
                if not ext:
                    try:
                        img = Image.open(BytesIO(resp.content))
                        ext = f".{img.format.lower()}"
                    except Exception:
                        ext = ".png"
                final_path = save_path + ext
                with open(final_path, "wb") as f:
                    f.write(resp.content)
                logger.debug(
                    "Saved artwork: %s (%d bytes)", final_path, len(resp.content)
                )
                return final_path
            logger.debug("Non-image response: Content-Type=%s", content_type)
        except Exception as e:
            logger.debug("Download attempt %d failed: %s", attempt + 1, e)
        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)
    logger.warning("Failed to download artwork after %d attempts: %s", MAX_RETRIES, url)
    return None


def save_artwork(game_data, title_dir):
    """Download and save background, banner, boxart, and icon artwork."""
    artwork_dir = os.path.join(title_dir, "artwork")
    os.makedirs(artwork_dir, exist_ok=True)
    for artwork_type in ["background", "banner", "boxart", "icon"]:
        url = game_data["artwork"].get(artwork_type)
        if not url:
            logger.debug("No %s artwork URL for %s", artwork_type, game_data["id"])
            continue
        if find_existing_artwork(artwork_dir, artwork_type):
            logger.debug("Skipping %s artwork (already exists)", artwork_type)
            continue
        save_path = os.path.join(artwork_dir, artwork_type)
        downloaded = download_artwork(url, save_path)
        if downloaded:
            print(f"  Saved artwork/{os.path.basename(downloaded)}")


def save_gallery(game_data, title_dir):
    """Download and save gallery screenshots keeping original filenames."""
    gallery_urls = game_data["artwork"].get("gallery", [])
    if not gallery_urls:
        return
    gallery_dir = os.path.join(title_dir, "gallery")
    os.makedirs(gallery_dir, exist_ok=True)
    for url in gallery_urls:
        filename = url.rsplit("/", 1)[-1].split("?")[0]
        base_name = filename.rsplit(".", 1)[0]
        if find_existing_artwork(gallery_dir, base_name):
            logger.debug("Skipping gallery image %s (already exists)", filename)
            continue
        save_path = os.path.join(gallery_dir, base_name)
        downloaded = download_artwork(url, save_path)
        if downloaded:
            print(f"  Saved gallery/{os.path.basename(downloaded)}")


def save_products(game_data, title_dir, locale, media):
    """Download related product URLs (manuals, etc.) into products/ subdirectory."""
    related = game_data["products"].get("related", [])
    if not related:
        return
    products_dir = os.path.join(title_dir, "products")
    os.makedirs(products_dir, exist_ok=True)
    for url in related:
        filename = url.rsplit("/", 1)[-1].split("?")[0]
        save_path = os.path.join(products_dir, filename)
        if os.path.exists(save_path):
            logger.debug("Skipping product %s (already exists)", filename)
            continue
        logger.debug("Downloading product: %s", url)
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                print(f"  Saved products/{filename}")
        except Exception as e:
            logger.debug("Failed to download %s: %s", url, e)


# =========================
# Input & Locale Helpers
# =========================


def load_input(input_path):
    """Load title IDs from JSON file or single hex ID string."""
    if os.path.isfile(input_path):
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [(item["titleid"].upper(), item.get("media", [])) for item in data]
    else:
        title_id = input_path.strip().upper()
        if re.match(r"^[0-9A-Fa-f]{8}$", title_id):
            return [(title_id, [])]
        else:
            print(f"Invalid title ID: {title_id} (must be 8 hex characters)")
            sys.exit(1)


def pick_default_locale(locale_results, preferred):
    """Select best locale from results, preferring English variants."""
    if preferred and preferred in locale_results:
        return preferred
    for loc in ENGLISH_LOCALES:
        if loc in locale_results:
            return loc
    if locale_results:
        return sorted(locale_results.keys())[0]
    return None


# =========================
# Main
# =========================


def main():
    parser = argparse.ArgumentParser(description="Fetch Xbox Marketplace game data.")
    parser.add_argument(
        "input",
        help="JSON file with title entries or a single 8-character hex title ID",
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
        help="Locale to use as default for info.json (default: en-US)",
    )
    parser.add_argument(
        "--all-locales",
        action="store_true",
        help="Fetch all 50 locales and save each as info_{locale}.json",
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
        help="Fetch parent product info into products/ folder per title",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-fetch data even if info.json already exists",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging (HTTP requests, parsing details, artwork downloads)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(
        logging.INFO if args.verbose else logging.WARNING
    )

    titles = load_input(args.input)
    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "titles"
    )

    print(f"Processing {len(titles)} title ID(s)...\n")

    for i, (title_id, media) in enumerate(titles):
        title_dir = os.path.join(output_dir, title_id)
        info_path = os.path.join(title_dir, "info.json")

        if os.path.exists(info_path) and not args.update:
            print(f"[{i+1}/{len(titles)}] {title_id} - already exists, skipping")
            continue

        os.makedirs(title_dir, exist_ok=True)

        if args.all_locales:
            logger.debug("Fetching all locales for %s", title_id)
            locale_results = fetch_all_locales(title_id, media)

            game_data, source = fetch_game_data(title_id, args.region, media, args.api)
            if game_data is None:
                print(f"[{i+1}/{len(titles)}] {title_id} - no data for {args.region}")
                continue

            default_locale = args.region if source else args.region

            print(
                f"[{i+1}/{len(titles)}] {title_id} - "
                f"{game_data['title']['full'] or 'Unknown'} "
                f"({source}, {len(locale_results)} locales)"
            )

            logger.debug("Writing %s", info_path)
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(game_data, f, ensure_ascii=False, indent=2)

            for loc, loc_result in locale_results.items():
                if loc == default_locale:
                    continue
                locale_info_path = os.path.join(title_dir, f"info_{loc}.json")
                logger.debug("Writing %s", locale_info_path)
                with open(locale_info_path, "w", encoding="utf-8") as f:
                    json.dump(
                        loc_result["data"],
                        f,
                        ensure_ascii=False,
                        indent=2,
                    )

            if args.artwork:
                save_artwork(game_data, title_dir)

            if args.gallery:
                save_gallery(game_data, title_dir)

            if args.products:
                save_products(game_data, title_dir, args.region, media)

        else:
            game_data, source = fetch_game_data(title_id, args.region, media, args.api)
            if game_data is None:
                print(f"[{i+1}/{len(titles)}] {title_id} - no data for {args.region}")
                continue

            print(
                f"[{i+1}/{len(titles)}] {title_id} - "
                f"{game_data['title']['full'] or 'Unknown'} ({source})"
            )

            logger.debug("Writing %s", info_path)
            with open(info_path, "w", encoding="utf-8") as f:
                json.dump(game_data, f, ensure_ascii=False, indent=2)

            if args.artwork:
                save_artwork(game_data, title_dir)

            if args.gallery:
                save_gallery(game_data, title_dir)

            if args.products:
                save_products(game_data, title_dir, args.region, media)

    print("\nDone.")


if __name__ == "__main__":
    main()
