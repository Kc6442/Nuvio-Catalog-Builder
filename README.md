# Nuvio Catalog Builder

Reusable GitHub Actions catalog generator based on the working X-MEN catalog pattern.

## Setup
1. Add your TMDB API Read Access Token as the Actions repository secret `TMDB_READ_TOKEN`.
2. Edit `catalog_config.json` with your catalog name, ID, description, and ordered movie pairs `[title, year]`.
3. Push the change to `main`. The build workflow starts automatically.
4. Verify the Actions run succeeded. For a public catalog repository, enable GitHub Pages from `main` and `/(root)`.

The example config contains two Matrix movies for a first smoke test. Do not publish it as a finished franchise catalog.

## Notes
- No TMDB token is written into published JSON.
- The builder currently generates a single catalog within this repository. Creating and publishing separate repositories is a future phase.
- Exact TMDB title/year matches are required; missing data fails the build rather than publishing a partial catalog.
