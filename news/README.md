# K-Boston News & Video Discovery Engine

Automated daily pipeline that discovers news articles and YouTube videos relevant to **Korean Americans in Boston and Greater New England**, scores them with Claude AI, and imports them into WordPress as standard posts.

---

## Architecture

```
GitHub Actions (daily 7 AM ET)
        │
        ▼
news_discovery.py
  ├── NewsAPISource       → Korean/Asian American news (100 req/day free)
  ├── SerpAPINewsSource   → Google News search results
  ├── RSSNewsSource       → 14 Korean & Asian American RSS feeds
  └── YouTubeSource       → Channel feeds + keyword search (10k units/day free)
        │
        ▼
ClaudeEnricher (Anthropic API)
  ├── Scores 0–100 for Korean-American relevance
  ├── Assigns category & tags
  └── Writes audience-appropriate excerpt
        │
        ▼
news-latest.json
  ├── articles[]   – scored & enriched news items
  └── videos[]     – scored & enriched YouTube videos
        │
        ▼
news_wp_importer.py
  ├── Articles → WordPress Posts (excerpt + "Read full article →" link)
  └── Videos   → WordPress Posts (YouTube embed + thumbnail as featured image)
```

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/captainshin90/K-Boston-Automation-Suite.git
cd K-Boston-Automation-Suite
pip install -r news/requirements.txt
```

### 2. Set Environment Variables

Copy `news/.env.example` to `news/.env` and fill in your keys:

```bash
cp news/.env.example news/.env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | From [console.anthropic.com](https://console.anthropic.com) |
| `NEWSAPI_KEY` | Recommended | From [newsapi.org](https://newsapi.org) – free, 100 req/day |
| `YOUTUBE_API_KEY` | Recommended | From [console.developers.google.com](https://console.developers.google.com) → Enable YouTube Data API v3 – free 10,000 units/day |
| `SERPAPI_KEY` | Optional | From [serpapi.com](https://serpapi.com) – 100 free searches/month |
| `WP_SITE_URL` | For import | `https://k-boston.org` |
| `WP_USERNAME` | For import | WordPress admin username |
| `WP_APP_PASSWORD` | For import | WordPress Application Password (Users → App Passwords) |
| `WP_POST_STATUS` | Optional | `publish` or `draft` (default: `publish`) |
| `WP_NEWS_CATEGORY` | Optional | WP category slug for articles (default: `news`) |
| `WP_VIDEO_CATEGORY` | Optional | WP category slug for videos (default: `korean-videos`) |
| `DAYS_BACK` | Optional | How many days back to pull news (default: `3`) |
| `RELEVANCE_THRESHOLD` | Optional | AI score cutoff 0–100 (default: `45`) |
| `MAX_ARTICLES` | Optional | Max articles to output (default: `50`) |
| `MAX_VIDEOS` | Optional | Max videos to output (default: `30`) |
| `SKIP_DUPLICATES` | Optional | Skip posts already in WordPress (default: `true`) |

### 3. Run Locally

```bash
# Discover articles & videos (JSON output only)
python news/src/news_discovery.py

# Import to WordPress
python news/src/news_wp_importer.py --json news/output/news-latest.json

# Dry run – see what would be imported without touching WordPress
python news/src/news_wp_importer.py --json news/output/news-latest.json --dry-run
```

---

## GitHub Actions Setup

### Step 1 – Add GitHub Secrets

In your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `NEWSAPI_KEY` | [newsapi.org](https://newsapi.org) |
| `YOUTUBE_API_KEY` | [console.developers.google.com](https://console.developers.google.com) |
| `SERPAPI_KEY` | [serpapi.com](https://serpapi.com) |
| `WP_SITE_URL` | `https://k-boston.org` |
| `WP_USERNAME` | Your WordPress admin username |
| `WP_APP_PASSWORD` | Generated under WP Admin → Users → Application Passwords |

> **Note:** `WP_SITE_URL`, `WP_USERNAME`, and `WP_APP_PASSWORD` are shared with the Events pipeline — if you've already added them, no need to add them again.

### Step 2 – Enable the Workflow

The workflow lives at `.github/workflows/daily_news.yml` and runs automatically every day at **7:00 AM Eastern** (one hour after the Events pipeline).

Trigger it manually anytime:
> **Actions → K-Boston Daily News & Video Discovery → Run workflow**

### Step 3 – Download the JSON

After each run, find the output under:
> **Actions → [run] → Artifacts → k-boston-news-[run-id]**

---

## WordPress Setup

### Recommended Plugins

| Plugin | Purpose | Cost |
|---|---|---|
| [WP RSS Aggregator](https://wordpress.org/plugins/wp-rss-aggregator/) | Manage passive RSS feed imports alongside the Python pipeline | Free / $99 yr Pro |
| [Smash Balloon Feeds for YouTube](https://wordpress.org/plugins/feeds-for-youtube/) | Persistent YouTube channel display section on your site | Free / ~$49 yr Pro |

### Enable the WP REST API

The importer uses the standard WordPress REST API — no extra plugin needed. Just ensure:

1. WordPress is version 5.6 or higher (Application Passwords require this)
2. Go to **WP Admin → Users → Your Profile → Application Passwords**
3. Create a new password, name it `k-boston-automation`, and save it as `WP_APP_PASSWORD`

### Enable Custom Meta Fields (for duplicate detection)

The importer stores each article's source URL as a post meta field (`k_boston_source_url`) to prevent re-importing the same content daily. To expose this via the REST API, add this to your theme's `functions.php`:

```php
// Allow k_boston_* meta fields via REST API
add_action('init', function() {
    $fields = ['k_boston_source_url', 'k_boston_source_name',
               'k_boston_author', 'k_boston_kind',
               'k_boston_video_id', 'k_boston_channel'];
    foreach ($fields as $field) {
        register_post_meta('post', $field, [
            'show_in_rest' => true,
            'single'       => true,
            'type'         => 'string',
        ]);
    }
});
```

### WordPress Post Categories

The importer auto-creates these two categories on first run if they don't exist:
- **Korean News** (slug: `news`) — for articles
- **Korean Videos** (slug: `korean-videos`) — for YouTube videos

You can customize the slugs via `WP_NEWS_CATEGORY` and `WP_VIDEO_CATEGORY` env vars.

---

## JSON Output Schema

```json
{
  "generated_at": "2026-03-07T07:00:00-05:00",
  "articles": [
    {
      "uid": "a1b2c3d4e5f6",
      "title": "Article title",
      "url": "https://source.com/article",
      "source_name": "Korea Times",
      "published_at": "2026-03-07T08:30:00Z",
      "description": "Short description...",
      "content": "Full content...",
      "image_url": "https://...",
      "author": "Reporter Name",
      "category": "Korean Culture",
      "tags": "tag1, tag2, tag3",
      "excerpt": "AI-written teaser for k-boston.org audience.",
      "relevance": 88,
      "kind": "article"
    }
  ],
  "videos": [
    {
      "uid": "c3d4e5f6a7b8",
      "title": "Video title",
      "video_id": "YouTubeVideoID",
      "channel_name": "Channel Name",
      "channel_id": "UCxxxxxx",
      "published_at": "2026-03-05T20:00:00Z",
      "description": "Video description...",
      "thumbnail_url": "https://i.ytimg.com/vi/.../maxresdefault.jpg",
      "url": "https://www.youtube.com/watch?v=...",
      "category": "K-pop & Music",
      "tags": "K-pop, Boston, concert",
      "excerpt": "AI-written teaser for k-boston.org audience.",
      "relevance": 95,
      "kind": "video",
      "embed_html": "<div>...</div>"
    }
  ],
  "total": 2
}
```

See `output/news-sample.json` for a full working example.

---

## News Sources

### Articles

| Source | API | Free Tier | Best For |
|---|---|---|---|
| NewsAPI | REST | 100 req/day | English-language Korean news |
| SerpAPI | Google News | 100 searches/month | Broad Google News discovery |
| RSS Feeds | feedparser | Unlimited | Direct Korean org & media feeds |

### Pre-wired RSS Feeds

- Korea Times, JoongAng Daily, Yonhap (English)
- KBS World Radio, Korea.net
- Boston Saram, New England Korean
- Korea Daily (NY edition)
- NBC Asian America, NextShark, Hyphen Magazine
- WBUR, Boston.com (geo-filtered)

### Adding Custom RSS Feeds

Edit the `NEWS_RSS_FEEDS` list in `news/src/news_discovery.py`:

```python
NEWS_RSS_FEEDS = [
    "https://your-korean-org.org/feed/",
    "https://another-news-site.com/rss",
    ...
]
```

### YouTube

| Method | Details |
|---|---|
| Keyword search | 9 queries targeting Korean American Boston/New England content |
| Channel feeds | Pre-wired to Korea Times US, KBS World, Arirang News, and others |

### Adding YouTube Channels

Edit the `YOUTUBE_CHANNELS` dict in `news/src/news_discovery.py`:

```python
YOUTUBE_CHANNELS = {
    "My Channel Name": "UCxxxxxxxxxxxxxxxxxxxxxx",   # channel ID from youtube.com
    ...
}
```

---

## Customizing Relevance Filtering

- Edit `KOREAN_KEYWORDS` and `GEO_KEYWORDS` in `news_discovery.py` for fast pre-filtering before the AI pass
- Raise `RELEVANCE_THRESHOLD` (e.g., 65) for a tighter, high-quality feed
- Lower it (e.g., 30) to cast a wider net

---

## Hostinger Notes

Hostinger shared hosting does not support server-side Python cron jobs. GitHub Actions handles all scheduling for this pipeline — no server-side configuration needed beyond the WordPress REST API being accessible over HTTPS.

---

## License

MIT – free to use for k-boston.org and the Korean American community 🇰🇷🇺🇸
