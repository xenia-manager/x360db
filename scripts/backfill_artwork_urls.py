#!/usr/bin/env python3
"""
Backfill null artwork URLs in info.json with raw GitHub URLs.

Walks every titles/<id>/info.json, checks if artwork fields (background,
banner, boxart, icon, gallery) are null/empty, looks for corresponding
local files in artwork/ and gallery/ directories, and fills in raw
GitHub content URLs derived from the git remote and current branch.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
TITLES_DIR = Path(__file__).resolve().parent.parent / "titles"

FIELD_FILE_MAP: Dict[str, str] = {
    "background": "background.jpg",
    "banner": "banner.png",
    "boxart": "boxart.jpg",
    "icon": "icon.png",
}

RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/titles/{title_id}/artwork/{filename}"
RAW_URL_TEMPLATE_GALLERY = "https://raw.githubusercontent.com/{owner}/{repo}/refs/heads/{branch}/titles/{title_id}/gallery/{filename}"
RAW_GITHUB_PREFIX = "https://raw.githubusercontent.com/"


# =========================
# Helper Functions
# =========================


def get_git_info() -> Tuple[str, str, str]:
    """Return (owner, repo, branch) from git remote and HEAD."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
        remote_url = result.stdout.strip()
    except subprocess.CalledProcessError:
        logger.error("Failed to get git remote origin URL")
        sys.exit(1)

    # Parse owner/repo from remote URL
    # Handles https://github.com/owner/repo.git and git@github.com:owner/repo.git
    if remote_url.startswith("https://"):
        parts = remote_url.rstrip(".git").split("/")
        owner = parts[-2]
        repo = parts[-1]
    elif remote_url.startswith("git@"):
        path_part = remote_url.split(":", 1)[1].rstrip(".git")
        owner, repo = path_part.split("/")
    else:
        logger.error(f"Unsupported remote URL format: {remote_url}")
        sys.exit(1)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
        branch = result.stdout.strip()
    except subprocess.CalledProcessError:
        logger.error("Failed to get current git branch")
        sys.exit(1)

    return owner, repo, branch


def is_empty(value: Any) -> bool:
    """Check if a value is None or empty string."""
    return value is None or value == ""


def is_empty_gallery(value: Any) -> bool:
    """Check if gallery value is None, empty string, or empty list."""
    if value is None or value == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def build_raw_url(
    owner: str,
    repo: str,
    branch: str,
    title_id: str,
    filename: str,
    gallery: bool = False,
) -> str:
    """Build a raw GitHub content URL for an artwork file."""
    template = RAW_URL_TEMPLATE_GALLERY if gallery else RAW_URL_TEMPLATE
    return template.format(
        owner=owner,
        repo=repo,
        branch=branch,
        title_id=title_id,
        filename=filename,
    )


def _needs_github_update(
    existing: Any, expected_url: str
) -> bool:
    """Check if an existing value is a stale GitHub RAW URL that should be replaced."""
    if not isinstance(existing, str):
        return False
    return existing.startswith(RAW_GITHUB_PREFIX) and existing != expected_url


def collect_proposed_changes(
    title_id: str,
    artwork: Dict[str, Any],
    artwork_dir: Path,
    gallery_dir: Optional[Path],
    owner: str,
    repo: str,
    branch: str,
) -> Dict[str, Any]:
    """Check what artwork fields need updating. Returns dict of {field: new_url}."""
    changes: Dict[str, Any] = {}

    # Scalar artwork fields
    for field, filename in FIELD_FILE_MAP.items():
        file_path = artwork_dir / filename
        if not file_path.is_file():
            continue
        expected = build_raw_url(owner, repo, branch, title_id, filename)
        existing = artwork.get(field)
        if is_empty(existing) or _needs_github_update(existing, expected):
            changes[field] = expected

    # Gallery
    if gallery_dir is not None and gallery_dir.is_dir():
        gallery_files = sorted(p.name for p in gallery_dir.iterdir() if p.is_file())
        if not gallery_files:
            return changes
        expected_gallery = [
            build_raw_url(owner, repo, branch, title_id, f, gallery=True)
            for f in gallery_files
        ]
        existing_gallery = artwork.get("gallery")
        if is_empty_gallery(existing_gallery):
            changes["gallery"] = expected_gallery
        elif isinstance(existing_gallery, list) and existing_gallery != expected_gallery:
            # Only replace if every existing entry is a GitHub URL
            if all(
                isinstance(u, str) and u.startswith(RAW_GITHUB_PREFIX)
                for u in existing_gallery
            ):
                changes["gallery"] = expected_gallery

    return changes


def apply_changes(
    info_path: Path, artwork: Dict[str, Any], changes: Dict[str, Any]
) -> None:
    """Mutate the artwork dict in-place and write back to info.json."""
    for field, new_value in changes.items():
        artwork[field] = new_value

    with open(info_path, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)

    data["artwork"] = artwork

    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def process_title(
    title_dir: Path,
    owner: str,
    repo: str,
    branch: str,
    dry_run: bool,
) -> Optional[Dict[str, Any]]:
    """Process a single title directory. Returns changes dict or None."""
    title_id = title_dir.name
    info_path = title_dir / "info.json"
    artwork_dir = title_dir / "artwork"
    gallery_dir = title_dir / "gallery"

    if not info_path.is_file():
        logger.debug(f"  Skipping {title_id}: no info.json")
        return None

    if not artwork_dir.is_dir():
        logger.debug(f"  Skipping {title_id}: no artwork/ directory")
        return None

    try:
        with open(info_path, "r", encoding="utf-8") as f:
            data: Dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"  [{title_id}] Failed to read info.json: {e}")
        return None

    artwork = data.get("artwork")
    if artwork is None:
        logger.debug(f"  Skipping {title_id}: no artwork block")
        return None

    changes = collect_proposed_changes(
        title_id,
        artwork,
        artwork_dir,
        gallery_dir if gallery_dir.is_dir() else None,
        owner,
        repo,
        branch,
    )

    if not changes:
        return None

    logger.info(f"  [{title_id}] {len(changes)} field(s) to update")
    for field, value in changes.items():
        old = artwork.get(field, "(empty)")
        if isinstance(old, list):
            old_str = f"[{len(old)} items]"
        else:
            old_str = repr(old)
        if isinstance(value, list):
            new_str = f"[{len(value)} items]"
        else:
            new_str = value
        logger.info(f"    {field}: {old_str} -> {new_str}")

    if not dry_run:
        apply_changes(info_path, artwork, changes)

    return {
        title_id: {
            "changes": {
                k: {"old": artwork.get(k), "new": v} for k, v in changes.items()
            }
        }
    }


# =========================
# Main Function
# =========================


def main() -> None:
    start_time = time.time()

    parser = argparse.ArgumentParser(
        description="Backfill null artwork URLs in info.json with raw GitHub URLs"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log proposed changes without modifying files",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to write log output (also printed to console)",
    )
    parser.add_argument(
        "--branch",
        type=str,
        default=None,
        help="Override auto-detected git branch (default: current HEAD)",
    )
    parser.add_argument(
        "--titles-dir",
        type=str,
        default=None,
        help="Override the titles directory (default: ../titles relative to script)",
    )

    args = parser.parse_args()

    # Log file setup
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logging.getLogger().addHandler(file_handler)

    titles_dir = Path(args.titles_dir) if args.titles_dir else TITLES_DIR

    if not titles_dir.is_dir():
        logger.error(f"Titles directory not found: {titles_dir}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Xenia Manager - Artwork URL Backfill")
    logger.info("=" * 60)

    # ----- Step 1: Git info -----
    logger.info("--- Step 1: Reading git remote and branch ---")
    owner, repo, branch = get_git_info()
    if args.branch:
        branch = args.branch
    logger.info(f"  Owner: {owner}, Repo: {repo}, Branch: {branch}")

    # ----- Step 2: Process titles -----
    logger.info("--- Step 2: Scanning titles ---")

    title_dirs = sorted(
        d for d in titles_dir.iterdir() if d.is_dir() and d.name != "__pycache__"
    )
    logger.info(f"  Found {len(title_dirs)} title directories")

    all_changes: Dict[str, Dict[str, Any]] = {}
    total_fields = 0

    for i, title_dir in enumerate(title_dirs, 1):
        title_id = title_dir.name
        result = process_title(title_dir, owner, repo, branch, args.dry_run)
        if result is not None:
            all_changes.update(result)
            total_fields += sum(
                1 for c in result.values() for _ in c.get("changes", {})
            )

    # ----- Step 3: Summary -----
    elapsed_time = time.time() - start_time
    mode = "DRY RUN" if args.dry_run else "LIVE"
    logger.info("-" * 60)
    logger.info(f"[{mode}] Titles with changes: {len(all_changes)}")
    logger.info(f"[{mode}] Total field updates:  {total_fields}")
    logger.info(f"Execution time: {elapsed_time:.0f}s")
    logger.info("=" * 60)

    # ----- Step 4: Write summary JSON (dry-run) -----
    if args.dry_run and args.log_file:
        summary_path = Path(args.log_file).with_suffix(".summary.json")
        summary = {
            "dry_run": True,
            "mode": mode,
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "titles_processed": len(title_dirs),
            "titles_with_changes": len(all_changes),
            "total_field_updates": total_fields,
            "titles": all_changes,
        }
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        logger.info(f"Summary written to: {summary_path}")


if __name__ == "__main__":
    main()
