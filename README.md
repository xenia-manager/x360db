# Xbox 360 Database

An open-source archive of [Xbox 360](https://en.wikipedia.org/wiki/Xbox_360) console metadata and artwork.

This project began as a recreation of the Xbox Marketplace database, aiming to collect comprehensive information and artwork for Xbox 360 games, primarily for use with [Xenia Manager](https://github.com/xenia-manager/xenia-manager).

**Disclaimer:** All content remains the property of its respective owners. Information and artwork are provided strictly for identification and archival purposes.

## Quick Start

```bash
# Fetch the full game index
curl https://<gh-pages-domain>/<repo>/games.json

# Fetch detailed metadata for a specific game
curl https://<gh-pages-domain>/<repo>/titles/4D5309C9/info.json
```

See the [Usage Guide](docs/USAGE.md) for schema documentation and consumption examples in TypeScript, C#, and Python.

## Repository Contents

- Recreated Xbox Marketplace metadata for over 6,000 games
- Scraped artwork (backgrounds, box art, icons, and banners)
- Indexed for filtering by genre, rating, developer, publisher, and release date

## Documentation

- [Usage Guide](docs/USAGE.md) — How to consume the database in your own projects
- [Contributing Guide](docs/CONTRIBUTING.md) — How to add or update game data

## Work in Progress

- Higher quality artwork (potentially from additional sources)
- Game gallery
- Enhanced metadata and further improvements

## Credits

Data and resources have been gathered from:
- [DBox](https://dbox.tools/) — for a comprehensive list of Title IDs
- [Redump Disc Preservation Project](https://redump.info/) — for release information
- [Xbox Marketplace](https://xbox.com) — for original metadata