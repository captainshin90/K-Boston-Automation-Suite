# K-Boston Automation Suite

Automated content pipelines for [k-boston.org](https://k-boston.org) — the Korean American community website for Boston and Greater New England.

## Repository Structure

```
├── events/          # Daily event discovery → The Events Calendar (WordPress)
│   ├── src/
│   │   ├── event_discovery.py   # Scrapes Eventbrite, Ticketmaster, SerpAPI, RSS
│   │   └── wp_importer.py       # Imports CSV into WordPress via REST API or FTP
│   ├── output/                  # Generated CSVs (TEC-compatible)
│   ├── requirements.txt
│   └── .env.example
│
├── news/            # Daily news & video discovery → WordPress Posts
│   ├── src/
│   │   ├── news_discovery.py    # Scrapes NewsAPI, SerpAPI, RSS, YouTube
│   │   └── news_wp_importer.py  # Creates WP posts with articles & video embeds
│   ├── output/                  # Generated JSON files
│   ├── requirements.txt
│   └── .env.example
│
└── .github/
    └── workflows/
        ├── daily_events.yml     # Cron: 6 AM ET daily
        └── daily_news.yml       # Cron: 7 AM ET daily
```

## Setup

See the README in each subfolder for full setup instructions:
- [`events/README.md`](events/README.md) — Events pipeline
- [`news/README.md`](news/README.md) — News & video pipeline (coming soon)

Add all API keys as **GitHub Secrets** (Settings → Secrets → Actions):

| Secret | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | Both pipelines |
| `EVENTBRITE_TOKEN` | Events |
| `TICKETMASTER_KEY` | Events |
| `SERPAPI_KEY` | Both pipelines |
| `NEWSAPI_KEY` | News |
| `YOUTUBE_API_KEY` | News |
| `WP_SITE_URL` | Both pipelines |
| `WP_USERNAME` | Both pipelines |
| `WP_APP_PASSWORD` | Both pipelines |
