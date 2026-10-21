# Best Real Estate Markets

Interactive map of US cities (5,000+ people) colored by rent-to-price ratio or home value growth, with economic filters.

- `fetch_data.py` downloads and merges data from Zillow Research, the U.S. Census Bureau and the U.S. Bureau of Labor Statistics into `data/cities.json`.
- `build.mjs` embeds the data and state shapes into `app.html` → `dist/index.html`.
- `build_embed.py` wraps it as a standalone page → `embed/rtp-map.html`.
- `.github/workflows/refresh.yml` reruns all of this on the 22nd of each month and publishes to GitHub Pages.

Run locally: `npm ci && python3 fetch_data.py && node build.mjs && python3 build_embed.py`

© blakechoisnet.com. All rights reserved.
