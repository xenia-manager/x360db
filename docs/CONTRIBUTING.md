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
│       └── artwork/        # Downloaded artwork images
│           ├── boxart.jpg
│           ├── background.jpg
│           ├── icon.png
│           └── banner.jpg
├── scripts/
│   ├── generate_games_json.py  # Regenerates games.json from info.json files
│   └── xbox_marketplace.py     # Scrapes data from Xbox Marketplace API
└── docs/
    └── CONTRIBUTING.md     # This file
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

### Option 1: Using the Scraper (Preferred for Bulk)

The scraper fetches data from the Xbox Marketplace API and writes it to the `titles/` directory.

1. Set up a JSON file with a list of Title IDs (see `config.py` for the expected format)
2. Set the `GAMES_LIST_URL` environment variable pointing to your JSON
3. Run the scraper:

   ```bash
   python scripts/xbox_marketplace.py
   ```

4. Regenerate `games.json`:

   ```bash
   python scripts/generate_games_json.py
   ```

### Option 2: Manual Entry (Preferred for Single Games)

1. Create the title directory: `titles/{TitleID}/`
2. Create `titles/{TitleID}/info.json` with the full metadata (use an existing entry as a template)
3. Add artwork images to `titles/{TitleID}/artwork/`
4. Regenerate `games.json`:

   ```bash
   python scripts/generate_games_json.py
   ```

### Option 3: Report via Issue

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

Scrapes game data from the Xbox Marketplace API. Requires a `GAMES_LIST_URL` environment variable pointing to a JSON array of `{titleid, media}` objects.

```bash
set GAMES_LIST_URL=https://example.com/games-list.json
python scripts/xbox_marketplace.py
```

### `scripts/config.py`

Shared configuration for the scraper — retry limits, URL templates, artwork download settings.

---

## Pull Request Process

### 1. Fork & Branch

- Fork the repository
- Create a new branch for your changes

### 2. Make Changes

- Add or edit `info.json` files in `titles/{TitleID}/`
- Add artwork to `titles/{TitleID}/artwork/`
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
