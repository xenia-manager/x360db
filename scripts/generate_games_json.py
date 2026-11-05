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
    parent_ids: Dict[str, List[str]] = {}
    infos: Dict[str, Dict[str, Any]] = {}

    for dir_entry in sorted(os.listdir(titles_dir)):
        info = load_info(dir_entry, titles_dir)
        if info is None:
            continue
        info_id = str(info.get("id", dir_entry)).upper()
        infos[info_id] = info

    self_parents: List[str] = []
    for info_id, info in infos.items():
        parents: List[str] = []
        for parent_id in get_parent_ids(info):
            parent_id = parent_id.upper()
            if parent_id == info_id:
                self_parents.append(info_id)
                continue
            parents.append(parent_id)
        parent_ids[info_id] = parents
        for parent_id in parents:
            if parent_id in infos:
                child_to_parents.setdefault(parent_id, []).append(info_id)

    if self_parents:
        logger.warning(
            "Ignoring self-referencing parent(s) for: %s",
            ", ".join(sorted(set(self_parents))),
        )

    roots = [
        info_id
        for info_id, parents in parent_ids.items()
        if not any(parent_id in infos for parent_id in parents)
    ]

    orphaned = [
        info_id
        for info_id, parents in parent_ids.items()
        if parents and not any(parent_id in infos for parent_id in parents)
    ]
    if orphaned:
        logger.warning(
            "Treating %d title(s) with missing parents as top-level: %s",
            len(orphaned),
            ", ".join(sorted(orphaned)),
        )

    def collect_descendants(root_id: str) -> List[str]:
        """Collect all descendant title IDs, guarding against cycles."""
        descendants: List[str] = []
        seen = {root_id}
        stack = [root_id]
        while stack:
            current = stack.pop()
            for child_id in child_to_parents.get(current, []):
                if child_id in seen:
                    continue
                seen.add(child_id)
                descendants.append(child_id)
                stack.append(child_id)
        return sorted(descendants)

    descendants_by_root = {
        root_id: collect_descendants(root_id) for root_id in roots
    }

    # Promote anything unreachable (for example, a non-self parent cycle) so
    # that no title silently disappears from games.json.
    reachable = set()
    for root_id, descendants in descendants_by_root.items():
        reachable.add(root_id)
        reachable.update(descendants)

    for info_id in sorted(infos):
        if info_id in reachable:
            continue
        roots.append(info_id)
        descendants_by_root[info_id] = collect_descendants(info_id)
        reachable.add(info_id)
        reachable.update(descendants_by_root[info_id])

    entries: List[Dict[str, Any]] = []

    for info_id in roots:
        info = infos[info_id]
        children = descendants_by_root[info_id]

        all_media_ids = get_media_ids(info)
        for child_id in children:
            all_media_ids.extend(get_media_ids(infos[child_id]))

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
