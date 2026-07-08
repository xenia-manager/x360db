#!/usr/bin/env python3
"""
Auto-close redundant or invalid issues.

- missing label: closes if the Title ID already exists in titles/
- data label: closes if the Incorrect Fields table is empty (no corrections)

Triggered via:
  issues: [opened, reopened, edited]  — checks just the triggering issue
  workflow_dispatch                  — batch checks all open issues (dry-run toggle)

Requires GITHUB_TOKEN with issues: write scope.

Requires the "issue-invalid" label to exist in the repo. Create it with:
  gh label create issue-invalid --color b60205 --description "Issue was closed as invalid"
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TITLES_DIR = Path(__file__).resolve().parent.parent / "titles"
API_BASE = "https://api.github.com"
INVALID_LABEL = "issue-invalid"

DEFAULT_TABLE_PATTERN = re.compile(
    r"^\|\s*(Field|[-]+|[\s]+)\s*\|\s*(Current Value|[-]+|[\s]+)\s*\|"
    r"\s*(Correct Value|[-]+|[\s]+)\s*\|\s*(Source|[-]+|[\s]+)\s*\|$"
)


def get_owner_repo() -> tuple:
    repo_full = os.getenv("GITHUB_REPOSITORY", "")
    if repo_full and "/" in repo_full:
        parts = repo_full.split("/", 1)
        return parts[0], parts[1]
    logger.warning("GITHUB_REPOSITORY not set, using fallback")
    return "xenia-manager", "x360db"


def get_headers() -> dict:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN not found in environment")
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def parse_issue_body(body: str) -> dict:
    sections = {}
    pattern = re.compile(
        r"^### (.+?)$\n\n(.*?)(?=\n^### |\Z)", re.MULTILINE | re.DOTALL
    )
    for match in pattern.finditer(body):
        key = match.group(1).strip().lower().replace(" ", "_")
        value = match.group(2).strip()
        sections[key] = value
    return sections


def extract_title_id(sections: dict) -> Optional[str]:
    raw = sections.get("title_id", "")
    match = re.search(r"[0-9A-Fa-f]{8}", raw)
    return match.group(0).upper() if match else None


def has_actual_corrections(text: str) -> bool:
    lines = text.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if DEFAULT_TABLE_PATTERN.match(stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if any(c for c in cells):
                return True
        else:
            if stripped:
                return True
    return False


def api_request(
    method: str, url: str, headers: dict, json_data: Optional[dict] = None
) -> Optional[dict]:
    try:
        resp = requests.request(
            method, url, headers=headers, json=json_data, timeout=15
        )
        resp.raise_for_status()
        return resp.json() if resp.content else None
    except requests.RequestException as e:
        logger.error(f"API request failed: {e}")
        return None


def add_comment(
    owner: str, repo: str, issue_number: int, headers: dict, body: str, dry_run: bool
) -> bool:
    if dry_run:
        logger.info(f"[DRY-RUN] Would add comment on #{issue_number}")
        return True
    url = f"{API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments"
    result = api_request("POST", url, headers, {"body": body})
    return result is not None


def close_issue(
    owner: str, repo: str, issue_number: int, headers: dict, dry_run: bool
) -> bool:
    if dry_run:
        logger.info(f"[DRY-RUN] Would close #{issue_number}")
        return True
    url = f"{API_BASE}/repos/{owner}/{repo}/issues/{issue_number}"
    result = api_request("PATCH", url, headers, {"state": "closed"})
    return result is not None


def add_label(
    owner: str, repo: str, issue_number: int, headers: dict, label: str, dry_run: bool
) -> bool:
    if dry_run:
        logger.info(f"[DRY-RUN] Would add label '{label}' to #{issue_number}")
        return True
    url = f"{API_BASE}/repos/{owner}/{repo}/issues/{issue_number}/labels"
    result = api_request("POST", url, headers, {"labels": [label]})
    return result is not None


def process_missing_issue(
    owner: str,
    repo: str,
    issue_number: int,
    headers: dict,
    sections: dict,
    dry_run: bool,
) -> None:
    title_id = extract_title_id(sections)
    if not title_id:
        logger.info(f"#{issue_number}: could not parse Title ID, skipping")
        return

    title_dir = TITLES_DIR / title_id
    if not title_dir.is_dir():
        logger.info(
            f"#{issue_number}: Title ID {title_id} not in database, leaving open"
        )
        return

    logger.info(f"#{issue_number}: Title ID {title_id} already exists — closing")
    comment = (
        f"This game entry (**{title_id}**) already exists in the database. "
        "If you believe the data is incorrect, please use the "
        "[Invalid Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=invalid-game-entry.yml) template instead."
    )
    add_comment(owner, repo, issue_number, headers, comment, dry_run)
    close_issue(owner, repo, issue_number, headers, dry_run)
    add_label(owner, repo, issue_number, headers, INVALID_LABEL, dry_run)


def process_data_issue(
    owner: str,
    repo: str,
    issue_number: int,
    headers: dict,
    sections: dict,
    dry_run: bool,
) -> None:
    incorrect_fields = sections.get("incorrect_fields", "")
    if has_actual_corrections(incorrect_fields):
        logger.info(f"#{issue_number}: has actual corrections, leaving open")
        return

    logger.info(
        f"#{issue_number}: Incorrect Fields table is empty — closing as invalid"
    )
    comment = (
        "This issue has been automatically closed because the **Incorrect Fields** "
        "table is empty. If you're reporting incorrect data, please fill in the table "
        "with the specific fields that need to be corrected and the correct values.\n\n"
        "If this game is missing from the database entirely, please use the "
        "[Missing Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=missing-game-entry.yml) template instead."
    )
    add_comment(owner, repo, issue_number, headers, comment, dry_run)
    close_issue(owner, repo, issue_number, headers, dry_run)
    add_label(owner, repo, issue_number, headers, INVALID_LABEL, dry_run)


def process_issue(
    owner: str, repo: str, issue: dict, headers: dict, dry_run: bool
) -> None:
    issue_number = issue["number"]
    labels = {lbl["name"] for lbl in issue.get("labels", [])}
    body = issue.get("body", "")

    if not body:
        logger.info(f"#{issue_number}: no body content, skipping")
        return

    sections = parse_issue_body(body)

    is_missing = "missing" in labels
    is_data = "data" in labels

    if is_missing:
        process_missing_issue(owner, repo, issue_number, headers, sections, dry_run)
    elif is_data:
        process_data_issue(owner, repo, issue_number, headers, sections, dry_run)
    else:
        logger.info(f"#{issue_number}: no relevant labels (missing/data), skipping")


def process_all_open_issues(
    owner: str, repo: str, headers: dict, dry_run: bool
) -> None:
    page = 1
    per_page = 100
    processed = 0

    while True:
        url = f"{API_BASE}/repos/{owner}/{repo}/issues?state=open&per_page={per_page}&page={page}"
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        issues = resp.json()
        if not issues:
            break

        for issue in issues:
            if "pull_request" in issue:
                continue
            process_issue(owner, repo, issue, headers, dry_run)
            processed += 1

        page += 1

    logger.info(f"Processed {processed} open issues")


def main():
    parser = argparse.ArgumentParser(description="Auto-close redundant/invalid issues")
    parser.add_argument(
        "--dry-run", action="store_true", help="Log actions without modifying"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process all open issues (for workflow_dispatch)",
    )
    args = parser.parse_args()

    owner, repo = get_owner_repo()
    headers = get_headers()
    if not headers:
        logger.error("No GITHUB_TOKEN available, exiting")
        sys.exit(1)

    if args.batch:
        logger.info("Batch mode: processing all open issues")
        process_all_open_issues(owner, repo, headers, args.dry_run)
        return

    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        logger.error("GITHUB_EVENT_PATH not set, exiting")
        sys.exit(1)

    with open(event_path, encoding="utf-8") as f:
        event = json.load(f)

    issue = event.get("issue")
    if not issue:
        logger.warning("No issue in event payload, skipping")
        return

    action = event.get("action", "")
    if action == "edited" and not event.get("changes", {}).get("body"):
        logger.info(f"#{issue['number']}: edit didn't change body, skipping")
        return

    process_issue(owner, repo, issue, headers, args.dry_run)


if __name__ == "__main__":
    main()
