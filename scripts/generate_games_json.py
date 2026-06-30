#!/usr/bin/env python3
"""
Generate games.json from title info files.

Scans the titles directory for info.json files, resolves parent/child
relationships, aggregates media IDs, and writes the result to games.json.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# =========================
# Logging Configuration
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# =========================
# Configuration
# =========================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_TITLES_DIR = PROJECT_DIR / "titles"
DEFAULT_OUTPUT = PROJECT_DIR / "games.json"


# =========================
# Helper Functions
# =========================


def load_info(title_id: str, titles_dir: Path) -> Optional[Dict[str, Any]]:
    """Load info.json for a given title ID."""
    path = titles_dir / title_id / "info.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def get_parent_ids(info: Dict[str, Any]) -> List[str]:
    """Extract parent product IDs from the info dict."""
    parent = info.get("products", {}).get("parent", [])
    if isinstance(parent, list):
        return [p["id"] for p in parent if isinstance(p, dict)]
    return []


def get_media_ids(info: Dict[str, Any]) -> List[str]:
    """Extract sorted media IDs from the info dict."""
    return sorted(m["media_id"] for m in info.get("media", []) if m.get("media_id"))


# =========================
# Main Function
# =========================


def main() -> None:
    start_time = time.time()

    parser = argparse.ArgumentParser(
        description="Generate games.json from title info files"
    )
    parser.add_argument(
        "--output",
        "-o",
        default=str(DEFAULT_OUTPUT),
        help="Path for the generated games.json (default: <project_root>/games.json)",
    )
    parser.add_argument(
        "--titles-dir",
        "-t",
        default=str(DEFAULT_TITLES_DIR),
        help="Path to the titles directory (default: <project_root>/titles)",
    )

    args = parser.parse_args()

    output_path = Path(args.output)
    titles_dir = Path(args.titles_dir)

    logger.info("=" * 60)
    logger.info("Xenia Manager - Games JSON Generator")
    logger.info("=" * 60)
    logger.info(f"Titles directory: {titles_dir}")
    logger.info(f"Output file: {output_path}")

    if not titles_dir.exists() or not titles_dir.is_dir():
        logger.error(f"Titles directory not found: {titles_dir}")
        sys.exit(1)

    child_to_parents: Dict[str, List[str]] = {}

    for dir_entry in sorted(os.listdir(titles_dir)):
        info = load_info(dir_entry, titles_dir)
        if info is None:
            continue
        parents = get_parent_ids(info)
        if parents:
            for pid in parents:
                child_to_parents.setdefault(pid, []).append(info["id"])

    entries: List[Dict[str, Any]] = []

    for dir_entry in sorted(os.listdir(titles_dir)):
        info = load_info(dir_entry, titles_dir)
        if info is None:
            continue

        info_id = info["id"]
        parents = get_parent_ids(info)

        if parents:
            continue

        children = sorted(child_to_parents.get(info_id, []))

        all_media_ids = get_media_ids(info)
        for child_id in children:
            child_info = load_info(child_id, titles_dir)
            if child_info is not None:
                all_media_ids.extend(get_media_ids(child_info))

        all_media_ids = sorted(set(all_media_ids))

        boxart = info.get("artwork", {}).get("boxart", None)
        if boxart == "":
            boxart = None

        entry: Dict[str, Any] = {
            "id": info_id,
            "alternative_id": children,
            "title": info.get("title", {}).get("full", ""),
            "boxart": boxart,
            "media_id": all_media_ids,
        }
        genre = info.get("genre", [])
        if genre:
            entry["genre"] = genre
        for field in ("user_rating", "developer", "publisher", "release_date"):
            val = info.get(field)
            if val is not None:
                entry[field] = val
        entries.append(entry)

    entries.sort(key=lambda e: e["title"].lower())

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
        f.write("\n")

    elapsed_time = time.time() - start_time

    logger.info("-" * 60)
    logger.info(f"Generated {output_path} with {len(entries)} entries")
    logger.info(f"Execution time: {elapsed_time:.2f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
