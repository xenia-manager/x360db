# Xbox 360 Database — Contributing Guide

This guide explains how to contribute game data, artwork, and metadata to the Xbox 360 Database.

---

## Table of Contents

1. [How to Contribute](#how-to-contribute)
2. [Understanding the Data Structure](#understanding-the-data-structure)
3. [Adding or Updating Game Data](#adding-or-updating-game-data)
4. [Scripts Reference](#scripts-reference)
5. [Pull Request Process](#pull-request-process)
6. [Best Practices](#best-practices)

---

## How to Contribute

You can contribute in several ways:

1. **Add a new game** — Submit metadata for a game not yet in the database
2. **Fix existing data** — Correct incorrect titles, genres, dates, artwork URLs, etc.
3. **Add artwork** — Provide box art, backgrounds, icons, banners, or gallery screenshots
4. **Add media entries** — Document disc releases (media IDs, editions, regions)
5. **Report issues** — Use the [Missing Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=missing-game-entry.yml) or [Invalid Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=invalid-game-entry.yml) templates

---

## Understanding the Data Structure

### Repository Layout

```
x360db/
├── games.json              # Flat index of all games (fast searching/filtering)
├── titles/
│   └── {TitleID}/
│       ├── info.json       # Full metadata for this title
│       ├── artwork/        # Downloaded artwork images
│       │   ├── boxart.jpg
│       │   ├── background.jpg
│       │   ├── icon.png
│       │   └── banner.jpg
│       ├── gallery/        # Gallery screenshots
│       │   ├── screenlg1.jpg
│       │   └── screenlg2.jpg
│       └── products/       # Related product files (manuals, PDFs)
│           └── b1fe1227-...PDF
├── scripts/
│   ├── generate_games_json.py     # Regenerates games.json from info.json files
│   ├── xbox_marketplace.py        # Fetches data from Xbox Marketplace API
│   └── backfill_artwork_urls.py   # Backfills artwork URLs from local files
└── docs/
    └── CONTRIBUTING.md            # This file
```

### games.json

A flat array of all games, designed for fast client-side searching and filtering without fetching individual info files. Each entry contains:

```json
{
    "id": "4D5309C9",
    "alternative_id": [],
    "title": "Forza Horizon",
    "boxart": "http://download.xbox.com/.../boxartlg.jpg",
    "media_id": ["5B1FDAF8"],
    "genre": ["Racing & Flying"],
    "user_rating": "4.30",
    "developer": "Playground Games",
    "publisher": "Microsoft Studios",
    "release_date": "2012-10-23"
}
```

Fields marked with `*` are always present; others are omitted when null/empty.

| Field | Type | Always | Description |
|-------|------|--------|-------------|
| `id` | string | * | 8-character Title ID (hex) |
| `alternative_id` | string[] | * | Related/child Title IDs (DLC, demos, alternate regions) |
| `title` | string | * | Full game title |
| `boxart` | string\|null | * | Box art image URL |
| `media_id` | string[] | * | Disc/media identifiers |
| `genre` | string[] | | Genres (e.g., "Racing & Flying", "Shooter") |
| `user_rating` | string | | User rating (e.g., "4.30") |
| `developer` | string | | Developer name |
| `publisher` | string | | Publisher name |
| `release_date` | string | | Release date (YYYY-MM-DD) |

### info.json

A detailed metadata file stored at `titles/{TitleID}/info.json`. This is the source of truth for the full record.

```json
{
    "id": "4D5309C9",
    "title": { "full": "Forza Horizon", "reduced": "Forza Horizon" },
    "genre": ["Racing & Flying", "Sports & Recreation"],
    "developer": "Playground Games",
    "publisher": "Microsoft Studios",
    "release_date": "2012-10-23",
    "user_rating": "4.30",
    "description": {
        "full": "Full description of the game...",
        "short": "Short description..."
    },
    "media": [
        {
            "media_id": "5B1FDAF8",
            "title": "Forza Horizon",
            "edition": "Original",
            "region": "USA"
        }
    ],
    "artwork": {
        "background": "http://.../background.jpg",
        "banner": "http://.../banner.png",
        "boxart": "http://.../boxartlg.jpg",
        "icon": "http://.../tile.png",
        "gallery": ["http://.../screenlg1.jpg"]
    },
    "products": {
        "parent": [{ "id": "", "title": "" }],
        "related": ["http://.../manual.pdf"]
    }
}
```

#### Artwork Directory

Artwork images live at `titles/{TitleID}/artwork/`:

```
artwork/
├── boxart.jpg
├── background.jpg
├── icon.png
└── banner.jpg
```

---

## Adding or Updating Game Data

### Option 1: Using the Marketplace Scraper (Preferred for Bulk)

The marketplace scraper fetches data from the Xbox Marketplace API and writes it to the `titles/` directory.

Most titles use the `D802` media family, but some — including several backwards-compatible releases — use `D804`.
Run the catalog enumeration once so the script can look up the exact media ID for every title.

1. Enumerate the catalog:

   ```bash
   python scripts/xbox_marketplace.py --enumerate
   ```

   This writes `catalog.json` with every title ID and its exact media ID. The file doubles as batch input.
   If `catalog.json` exists at the project root, later runs use it automatically.

2. Run the scraper with a single Title ID, a JSON file, or the catalog:

   ```bash
   python scripts/xbox_marketplace.py 4D5309C9
   python scripts/xbox_marketplace.py titles.json
   python scripts/xbox_marketplace.py catalog.json
   ```

   A title list is an array of objects with `titleid`, an optional `media` array, and an optional `media_id`:

   ```json
   [
     { "titleid": "4D5309C9", "media": [] },
     { "titleid": "545407F2", "media": [] },
     { "titleid": "33390FA0", "media_id": "66ACD000-77FE-1000-9115-D80433390FA0" }
   ]
   ```

   If a title is missing from the catalog, `--probe-prefixes` tries `D802` and then `D804`.

3. Regenerate `games.json`:

   ```bash
   python scripts/generate_games_json.py
   ```

#### Choosing Artwork from Other Locales

Use `--artwork-locales` to gather boxart, backgrounds, banners, icons, screenshots, and related products
from additional locales. Add `--pick-artwork` to choose interactively before `info.json` is written:

```bash
# List candidates only (does not write info.json)
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales en-US,ja-JP,de-DE --list-artwork

# Choose interactively, previewing with 'v N'
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales en-US,ja-JP,de-DE --pick-artwork

# Gather artwork from every supported locale, but write only info.json
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales all --pick-artwork

# Also download the selected artwork, gallery, and products, replacing existing files
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales all --pick-artwork \
  --artwork --gallery --products --overwrite-artwork
```

`--artwork-locales all` fetches all locales for the picker without writing `info_{locale}.json` files.
Use `--all-locales` instead when you also want those per-locale files.

Without `--overwrite-artwork`, existing files in `artwork/`, `gallery/`, and `products/` are skipped.
With it, matching files are replaced after the new download succeeds, and stale copies with a different
image extension (for example `boxart.png` when the new file is `boxart.jpg`) are removed. Files whose
names differ from the new selection (such as locale-suffixed gallery variants) are left in place.

In the picker, press Enter to accept the default, enter numbers and ranges (`1,3,5-7`) for gallery and
product lists, `a` for all, `n` for none, and `s` or `q` to keep the existing selection. `v N` downloads a
candidate and opens it in your default image viewer.

### Option 2: Manual Entry (Preferred for Single Games)

1. Create the title directory: `titles/{TitleID}/`
2. Create `titles/{TitleID}/info.json` with the full metadata (use an existing entry as a template)
3. Add artwork images to `titles/{TitleID}/artwork/`
4. Backfill artwork URLs:

   ```bash
   python scripts/backfill_artwork_urls.py
   ```

5. Regenerate `games.json`:

   ```bash
   python scripts/generate_games_json.py
   ```

### Option 4: Report via Issue

If you don't have the data or prefer not to edit files directly, use the issue templates:

- [Missing Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=missing-game-entry.yml) — Request a new game be added
- [Invalid Game Entry](https://github.com/xenia-manager/x360db/issues/new?template=invalid-game-entry.yml) — Report incorrect data

---

## Scripts Reference

### `scripts/generate_games_json.py`

Regenerates `games.json` from all `titles/{TitleID}/info.json` files. Handles parent/child relationships and aggregates media IDs.

```bash
python scripts/generate_games_json.py
```

Options:

| Flag | Description |
|------|-------------|
| `--output, -o` | Output path (default: `<project_root>/games.json`) |
| `--titles-dir, -t` | Titles directory (default: `<project_root>/titles`) |

### `scripts/xbox_marketplace.py`

Fetches game data from the Xbox Marketplace API. Supports two APIs: the legacy `marketplace-xb` API (primary) and the newer `catalog-cdn` API (fallback).

```bash
# Build catalog.json with the exact media ID for every title
python scripts/xbox_marketplace.py --enumerate

# Single title
python scripts/xbox_marketplace.py 4D5309C9

# Multiple titles from JSON file or catalog
python scripts/xbox_marketplace.py titles.json
python scripts/xbox_marketplace.py catalog.json

# Fetch artwork from other locales and pick interactively
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales en-US,ja-JP --pick-artwork

# Fetch all locales, pick assets, and replace existing local files
python scripts/xbox_marketplace.py 4D5309C9 --artwork-locales all --pick-artwork \
  --artwork --gallery --products --overwrite-artwork

# With artwork and gallery download
python scripts/xbox_marketplace.py 4D5309C9 --artwork --gallery

# Fetch all locales
python scripts/xbox_marketplace.py 4D5309C9 --all-locales

# Force a specific API
python scripts/xbox_marketplace.py 4D5309C9 --api catalog-cdn
```

Options:

| Flag | Description |
|------|-------------|
| `input` | Single 8-character hex Title ID, a JSON title list, or `catalog.json` |
| `--enumerate` | Enumerate the legacy catalog and write `catalog.json` |
| `--catalog` | Catalog file used to resolve exact media IDs (default: `<project_root>/catalog.json` when present) |
| `--catalog-out` | Output path for `--enumerate` (default: `<project_root>/catalog.json`) |
| `--max-pages`, `--page-delay` | Limit or pace catalog enumeration (useful for testing) |
| `--api` | API to use: `auto` (default), `marketplace-xb`, or `catalog-cdn` |
| `--region` | Locale for the default `info.json` and catalog enumeration (default: `en-US`) |
| `--all-locales` | Fetch all supported locales, saving each as `info_{locale}.json` |
| `--artwork-locales` | Comma-separated extra locales for artwork, gallery, and product URLs, or `all` for every supported locale |
| `--pick-artwork` | Interactively choose artwork, gallery, and product URLs (`v N` previews) |
| `--list-artwork` | List candidates without writing `info.json` |
| `--probe-prefixes` | Try `D802` and then `D804` when the catalog has no media ID |
| `--artwork` | Download artwork (background, banner, boxart, icon) per title |
| `--gallery` | Download gallery screenshots into `gallery/` folder per title |
| `--products` | Download related product files (manuals, PDFs) into `products/` folder per title |
| `--overwrite-artwork` | Replace existing artwork/gallery/product files with the selected versions (alias: `--overwrite-assets`) |
| `--update` | Re-fetch data even if `info.json` already exists |
| `--output-dir` | Directory for title folders (default: `<project_root>/titles`) |
| `--workers` | Concurrent locale requests (default: 8) |
| `-v, --verbose` | Enable debug logging (HTTP requests, parsing, downloads) |

### `scripts/backfill_artwork_urls.py`

Walks all `titles/{TitleID}/info.json` files and populates null or stale artwork fields with raw GitHub content URLs based on the actual files present in the `artwork/` and `gallery/` directories.

```bash
python scripts/backfill_artwork_urls.py
```

Options:

| Flag | Description |
|------|-------------|
| `--dry-run` | Log proposed changes without modifying files |
| `--log-file` | Path to write log output (also printed to console) |
| `--branch` | Override auto-detected git branch (default: current HEAD) |
| `--titles-dir` | Override the titles directory |

When `--dry-run` is used with `--log-file`, a `.summary.json` file is written alongside the log with a complete report of all proposed changes.

---

## Pull Request Process

### 1. Fork & Branch

- Fork the repository
- Create a new branch for your changes

### 2. Make Changes

- Add or edit `info.json` files in `titles/{TitleID}/`
- Add artwork to `titles/{TitleID}/artwork/`
- Backfill artwork URLs from local artwork files:
  ```bash
  python scripts/backfill_artwork_urls.py
  ```
  Use `--dry-run --log-file review.log` to preview changes first.
- Regenerate `games.json`:
  ```bash
  python scripts/generate_games_json.py
  ```

### 3. Commit

Use clear, descriptive commit messages:

- `Add {Game Title} ({TitleID})` — New game entry
- `Fix {field} for {Game Title} ({TitleID})` — Data correction
- `Add artwork for {Game Title} ({TitleID})` — Artwork addition

### 4. Submit a Pull Request

#### Single Game

- **Title:** `feat(addition): Added {TitleID} — {Game Title}`
- **Message:** Summary of what was added and data sources used

#### Multiple Games

- **Title:** `feat(addition): Added {n} Game Entries`
- **Message:**
  - `{TitleID} — {Game Title}`: Data sources
  - `{TitleID} — {Game Title}`: Data sources

#### Data Correction

- **Title:** `fix(data): Corrected {field} for {TitleID} — {Game Title}`
- **Message:** What was wrong, what it was changed to, and source of correct data

---

## Best Practices

### Data Accuracy

- **Verify Title IDs** — Use [DBox](https://dbox.tools/) or the Xbox Marketplace to confirm IDs
- **Use Marketplace as Source** — Prefer data from the official Xbox Marketplace API when available
- **Check Existing Entries** — Search `games.json` to avoid duplicates
- **Validate Dates** — Use `YYYY-MM-DD` format; prefer the original release date

### Media Entries

- Document all known disc releases with correct media IDs
- Include region and edition information
- Use [Redump](http://redump.org) as a reference for media information

### Artwork

- Prefer original Xbox Marketplace URLs for artwork links
- Add downloaded artwork to the `artwork/` directory when possible
- Keep gallery screenshots in sorted order

### Parent/Child Relationships

- If a title is DLC, a demo, or a variant, set its `products.parent` field to point to the main game
- The main game should NOT have a `parent` entry
- `generate_games_json.py` automatically skips child entries and rolls their media IDs into the parent
- Nested children are rolled into the highest available parent entry
- If none of a title's parents exist in `titles/`, the title is kept as a top-level entry instead of being dropped
- Self-referencing `products.parent` entries are ignored
