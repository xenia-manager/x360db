# Xbox 360 Database — Usage Guide

This guide explains how to consume the database in your own projects.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [games.json Schema](#gamesjson-schema)
3. [info.json Schema](#infojson-schema)
4. [Consumption Examples](#consumption-examples)
5. [Artwork URLs](#artwork-urls)

---

## Quick Start

The database is served at two URLs (primary + fallback):

### games.json (Index)

```
https://<gh-pages-domain>/<repo>/games.json
https://raw.githubusercontent.com/<owner>/<repo>/main/games.json
```

### info.json (Detailed metadata)

```
https://<gh-pages-domain>/<repo>/titles/{TitleID}/info.json
https://raw.githubusercontent.com/<owner>/<repo>/main/titles/{TitleID}/info.json
```

### Artwork

```
https://<gh-pages-domain>/<repo>/titles/{TitleID}/artwork/{type}.jpg
https://raw.githubusercontent.com/<owner>/<repo>/main/titles/{TitleID}/artwork/{type}.jpg
```

Where `{type}` is one of: `boxart`, `background`, `icon`, `banner`.

---

## games.json Schema

A flat array of all ~6000 games. Designed for client-side filtering and search without fetching individual `info.json` files.

| Field | Type | Always | Description |
|-------|------|--------|-------------|
| `id` | `string` | yes | 8-character Title ID (uppercase hex) |
| `alternative_id` | `string[]` | yes | Related Title IDs (DLC, demos, alternate regions) |
| `title` | `string` | yes | Full game title |
| `boxart` | `string \| null` | yes | Box art image URL |
| `media_id` | `string[]` | yes | Disc/media identifiers |
| `genre` | `string[]` | no | Genres (e.g., `["Shooter"]`, `["Racing & Flying", "Sports & Recreation"]`) |
| `user_rating` | `string` | no | User rating as a string (e.g., `"4.30"`) |
| `developer` | `string` | no | Developer name |
| `publisher` | `string` | no | Publisher name |
| `release_date` | `string` | no | Release date (`YYYY-MM-DD`) |

Fields marked "no" are omitted from the JSON when null or empty — handle with a null/default check.

---

## info.json Schema

A detailed object for a single title, fetched per-game on demand.

```typescript
interface GameInfo {
  id: string;
  title: {
    full: string;
    reduced: string;
  };
  genre: string[];
  developer: string;
  publisher: string;
  release_date: string | null;
  user_rating: string | null;
  description: {
    full: string | null;
    short: string | null;
  };
  media: {
    media_id: string;
    title: string;
    edition: string;
    region: string;
  }[];
  artwork: {
    background: string | null;
    banner: string | null;
    boxart: string | null;
    icon: string | null;
    gallery: string[];
  };
  products: {
    parent: { id: string; title: string }[];
    related: string[];
  };
}
```

---

## Consumption Examples

### TypeScript / JavaScript (Browser)

```typescript
interface GamesEntry {
  id: string;
  alternative_id: string[];
  title: string;
  boxart: string | null;
  media_id: string[];
  genre?: string[];
  user_rating?: string;
  developer?: string;
  publisher?: string;
  release_date?: string;
}

// Fetch the index
const response = await fetch("https://<gh-pages-domain>/<repo>/games.json");
const games: GamesEntry[] = await response.json();

// Filter by genre
const shooters = games.filter(g => g.genre?.includes("Shooter"));

// Filter by rating
const topRated = games.filter(g => {
  const rating = parseFloat(g.user_rating ?? "0");
  return rating >= 4.0;
});

// Search by title or ID
const query = "forza";
const results = games.filter(g =>
  g.title.toLowerCase().includes(query) ||
  g.id.toLowerCase().includes(query)
);

// Fetch detailed info for a specific game
const infoResponse = await fetch(
  `https://<gh-pages-domain>/<repo>/titles/${gameId}/info.json`
);
const info = await infoResponse.json();
```

### TypeScript / JavaScript (Node.js with fetch)

```typescript
import { readFileSync } from "fs";

// For local usage
const games = JSON.parse(
  readFileSync("games.json", "utf-8")
);

// Filter by year
const games2024 = games.filter(g =>
  g.release_date?.startsWith("2024")
);
```

### C# (using System.Text.Json)

```csharp
using System.Net.Http;
using System.Text.Json;

public class GameInfo {
    [JsonPropertyName("id")] public string? Id { get; set; }
    [JsonPropertyName("alternative_id")] public List<string>? AlternativeId { get; set; }
    [JsonPropertyName("title")] public string? Title { get; set; }
    [JsonPropertyName("boxart")] public string? Boxart { get; set; }
    [JsonPropertyName("media_id")] public List<string>? MediaId { get; set; }
    [JsonPropertyName("genre")] public List<string>? Genre { get; set; }
    [JsonPropertyName("user_rating")] public string? UserRating { get; set; }
    [JsonPropertyName("developer")] public string? Developer { get; set; }
    [JsonPropertyName("publisher")] public string? Publisher { get; set; }
    [JsonPropertyName("release_date")] public string? ReleaseDate { get; set; }
}

var http = new HttpClient();
var response = await http.GetStringAsync(
    "https://<gh-pages-domain>/<repo>/games.json"
);
var games = JsonSerializer.Deserialize<List<GameInfo>>(response);

// Filter by genre
var shooters = games?.Where(g =>
    g.Genre?.Contains("Shooter") == true
);

// Filter by rating
var topRated = games?.Where(g =>
    double.TryParse(g.UserRating, out var rating) && rating >= 4.0
);
```

### Python

```python
import json
import urllib.request

url = "https://<gh-pages-domain>/<repo>/games.json"
with urllib.request.urlopen(url) as f:
    games = json.load(f)

# Filter by genre
shooters = [g for g in games if "Shooter" in g.get("genre", [])]

# Filter by rating
top_rated = [g for g in games
    if g.get("user_rating") and float(g["user_rating"]) >= 4.0]

# Sort by release date
sorted_by_date = sorted(
    [g for g in games if g.get("release_date")],
    key=lambda g: g["release_date"]
)
```

---

## Artwork URLs

Artwork can be loaded from two locations in order of preference:

### 1. Local / GitHub Pages path (preferred)

```
https://<gh-pages-domain>/<repo>/titles/{TitleID}/artwork/{type}.jpg
```

### 2. Original Xbox Marketplace URL (fallback)

Stored in the `artwork` object of `info.json`:

```json
{
  "artwork": {
    "background": "http://download.xbox.com/.../background.jpg",
    "banner": "http://download.xbox.com/.../banner.png",
    "boxart": "http://download.xbox.com/.../boxartlg.jpg",
    "icon": "http://download.xbox.com/.../tile.png"
  }
}
```

**Recommended loading strategy:**

```
1. Try GitHub Pages path:   https://<gh-pages-domain>/<repo>/titles/{id}/artwork/{type}.jpg
2. Fallback to raw GitHub:  https://raw.githubusercontent.com/<owner>/<repo>/main/titles/{id}/artwork/{type}.jpg
3. Fallback to Xbox URL:    {info.artwork.{type}}  (from info.json)
```

### Image Types

| Type | Filename | Expected Format |
|------|----------|-----------------|
| Boxart | `boxart.jpg` | JPEG |
| Background | `background.jpg` | JPEG |
| Banner | `banner.png` | PNG |
| Icon | `icon.png` | PNG |
| Gallery | `screenlg*.jpg` | JPEG |
