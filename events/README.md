# K-Boston Event Discovery Engine

Automated daily pipeline that discovers upcoming events of interest to **Korean Americans in Boston and Greater New England**, filters them with Claude AI, and imports them into WordPress via **The Events Calendar** plugin.

---

## Architecture

```
GitHub Actions (daily 6 AM ET)
        │
        ▼
event_discovery.py
  ├── EventbriteSource     → Korean/Asian events in Boston (50mi radius)
  ├── TicketmasterSource   → Korean/K-pop/cultural events
  ├── SerpAPISource        → Google Events search results
  └── RSSSource            → Korean community websites & orgs
        │
        ▼
ClaudeEnricher (Anthropic API)
  ├── Scores 0–100 for Korean-American relevance
  ├── Assigns category & tags
  └── Writes audience-appropriate excerpt
        │
        ▼
k-boston-events-YYYY-MM-DD.csv  (TEC-compatible)
        │
        ▼
wp_importer.py
  ├── Option A: WP REST API  (live push to WordPress)
  └── Option B: FTP Upload   (file drop + WP-CLI cron)
```

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/YOUR_ORG/k-boston-events.git
cd k-boston-events
pip install -r requirements.txt
```

### 2. Set Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | From [console.anthropic.com](https://console.anthropic.com) |
| `EVENTBRITE_TOKEN` | Recommended | From [eventbrite.com/account-settings/apps](https://www.eventbrite.com/account-settings/apps) |
| `TICKETMASTER_KEY` | Recommended | From [developer.ticketmaster.com](https://developer.ticketmaster.com) |
| `SERPAPI_KEY` | Optional | From [serpapi.com](https://serpapi.com) – 100 free/mo |
| `WP_SITE_URL` | For import | `https://k-boston.org` |
| `WP_USERNAME` | For import | WordPress admin username |
| `WP_APP_PASSWORD` | For import | WordPress Application Password |
| `FTP_HOST` | FTP import | Hostinger FTP hostname |
| `FTP_USER` | FTP import | FTP username |
| `FTP_PASS` | FTP import | FTP password |
| `DAYS_AHEAD` | Optional | Days ahead to collect (default: 60) |
| `RELEVANCE_THRESHOLD` | Optional | AI score cutoff 0–100 (default: 40) |

### 3. Run Locally

```bash
# Discover events only (CSV output)
python src/event_discovery.py

# Import to WordPress via REST API
python src/wp_importer.py --csv output/k-boston-events-latest.csv --method rest

# Import via FTP (Hostinger)
python src/wp_importer.py --csv output/k-boston-events-latest.csv --method ftp
```

---

## GitHub Actions Setup

### Step 1 – Add GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

Add all the variables from the table above as **Repository Secrets**.

### Step 2 – Enable the Workflow

The workflow is at `.github/workflows/daily_events.yml`.

It runs automatically every day at **6:00 AM Eastern**.

You can also trigger it manually:
> **Actions → K-Boston Daily Event Discovery → Run workflow**

### Step 3 – Download the CSV

After each run, find the CSV under:
> **Actions → [run] → Artifacts → k-boston-events-[run-id]**

---

## WordPress Setup

### Enable The Events Calendar REST API

1. In WordPress Admin: **Events → Settings → Integrations**
2. Enable **REST API**
3. Under **Users → Your Profile → Application Passwords**, create a new App Password and store it as `WP_APP_PASSWORD`

### Manual CSV Import (alternative)

1. Go to **Events → Import**
2. Upload `k-boston-events-latest.csv`
3. Map fields (they already match TEC's expected column names)
4. Click **Import**

### Recommended TEC Settings for k-boston.org

- **Events → Settings → General**: Set timezone to `America/New_York`
- **Events → Settings → Display**: Enable Google Maps integration
- **Events → Settings → Import**: Allow duplicate detection by URL

---

## CSV Schema (The Events Calendar Compatible)

| Column | Description | Example |
|---|---|---|
| Event Name | Title | `Korean Cultural Festival 2026` |
| Event Description | Full HTML-safe description | |
| Event Excerpt | Short teaser (1-2 sentences) | |
| Event Start Date | `YYYY-MM-DD` | `2026-05-15` |
| Event Start Time | `HH:MM:SS` | `10:00:00` |
| Event End Date | `YYYY-MM-DD` | `2026-05-15` |
| Event End Time | `HH:MM:SS` | `18:00:00` |
| All Day Event | `TRUE` / `FALSE` | `FALSE` |
| Timezone | IANA tz name | `America/New_York` |
| Event Cost | Free or price | `25` or `Free` |
| Event Category | Single category | `Korean Culture` |
| Event Tags | Comma-separated | `kimchi, food, workshop` |
| Event Website | Source URL | |
| Event Featured Image | Direct image URL | |
| Venue Name / Address / City / … | Venue fields | |
| Organizer Name / … | Organizer fields | |

See `output/k-boston-events-sample.csv` for a full example.

---

## Event Sources

| Source | API | Free Tier | Best For |
|---|---|---|---|
| Eventbrite | REST v3 | 2,000 calls/day | Community & cultural events |
| Ticketmaster | Discovery v2 | 5,000 calls/day | Concerts, performances |
| SerpAPI | Google Events | 100/month | Broad Google event discovery |
| RSS Feeds | feedparser | Unlimited | Korean org websites |

### Adding Custom RSS Feeds

Edit the `COMMUNITY_FEEDS` list in `src/event_discovery.py`:

```python
COMMUNITY_FEEDS = [
    "https://your-korean-org.org/feed/",
    "https://another-community-site.com/events/rss",
    ...
]
```

---

## Customizing Relevance Filtering

Edit `KOREAN_KEYWORDS` and `GEOGRAPHIC_KEYWORDS` in `event_discovery.py` to tune pre-filtering before the AI pass.

Raise `RELEVANCE_THRESHOLD` (e.g., 60) for stricter filtering, lower it (e.g., 30) to cast a wider net.

---

## Hostinger-Specific Notes

Hostinger shared hosting does **not** support server-side cron for Python. Use GitHub Actions (this repo) as the cron executor.

For FTP import, configure:
```
FTP_HOST=ftp.k-boston.org
FTP_USER=your-ftp-username
FTP_PASS=your-ftp-password
FTP_REMOTE_PATH=/public_html/wp-content/uploads/tec-import/k-boston-events-latest.csv
```

Then add this WP-CLI command via Hostinger's cron (hPanel → Advanced → Cron Jobs):
```bash
cd /home/u123456/domains/k-boston.org/public_html && wp tec events import --file=wp-content/uploads/tec-import/k-boston-events-latest.csv --format=csv
```

---

## License

MIT – free to use for k-boston.org and the Korean American community 🇰🇷🇺🇸
