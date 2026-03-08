#!/usr/bin/env python3
"""
K-Boston News & Video Discovery Engine
Discovers daily news articles and YouTube videos relevant to Korean Americans
in Boston and Greater New England.

Sources:
  Articles:
    - NewsAPI          (https://newsapi.org)
    - SerpAPI          (Google News engine)
    - Korean news RSS  (Korea Times, Korea Daily, Yonhap, KBS World, etc.)
  Videos:
    - YouTube Data API v3 (channel feeds + keyword search)

AI filtering:
    - Claude AI scores every item 0–100 for Korean-American relevance

Output:
    - news-articles-YYYY-MM-DD.json   → imported as WP posts via REST API
    - news-videos-YYYY-MM-DD.json     → imported as WP posts with video embed
    - news-latest.json                → always-fresh combined file for WP import
"""

import os
import re
import json
import time
import hashlib
import logging
import requests
import feedparser
import anthropic
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from typing import Optional

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

EASTERN      = ZoneInfo("America/New_York")
DAYS_BACK    = int(os.getenv("DAYS_BACK", 3))        # how far back to pull news
DAYS_AHEAD   = int(os.getenv("DAYS_AHEAD", 7))       # for scheduled/upcoming content
THRESHOLD    = int(os.getenv("RELEVANCE_THRESHOLD", 45))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", 50))
MAX_VIDEOS   = int(os.getenv("MAX_VIDEOS", 30))

KOREAN_KEYWORDS = [
    "korean", "korea", "한국", "조선", "kpop", "k-pop",
    "kimchi", "bibimbap", "bulgogi", "soju", "hanbok", "hangul",
    "taekwondo", "hapkido", "k-drama", "kdrama", "hallyu",
    "korean american", "korean community", "korean culture",
    "south korea", "north korea", "seoul", "busan", "BTS",
    "blackpink", "samsung", "hyundai", "lg electronics",
    "korean war", "comfort women", "demilitarized zone",
    "korean film", "parasite bong", "squid game",
    "yonhap", "korea times", "korea herald",
]

GEO_KEYWORDS = [
    "boston", "cambridge", "somerville", "newton", "brookline",
    "quincy", "malden", "worcester", "providence", "new england",
    "massachusetts", "connecticut", "rhode island", "new hampshire",
]

# YouTube channels relevant to Korean Americans (add your own)
YOUTUBE_CHANNELS = {
    "Korea Times US":    "UCXqF4j9qPBJwNHJSPxiMZPg",
    "KBS World":         "UCecMuMTu3JB5LaW1RxRoBqA",
    "Arirang News":      "UCnUYZLuoy1rq1aVMwx4aTzw",
    "Korean Cultural Center": "UC7kP6vRx4bFoNcmBZDLLSOw",
    "Visit Korea":       "UCHcF5SVFSZp8OVEaE3_BKUg",
    "Asian American News": "UCvC4D8onUfXzvjTOM-dBfEA",
}

# RSS feeds for Korean-language and Korean-American news
NEWS_RSS_FEEDS = [
    # English-language Korean news
    "https://www.koreatimes.co.kr/www2/rss/rss.asp",
    "https://koreajoongangdaily.joins.com/rss/rss.xml",
    "https://en.yna.co.kr/RSS/news.xml",                    # Yonhap English
    "https://world.kbs.co.kr/rss/rss_news.htm",             # KBS World
    "https://www.korea.net/rss/koreaFocus.xml",
    # Korean-American community
    "https://bostonsaram.com/feed/",
    "https://newenglandkorean.com/feed/",
    "https://www.koreadaily.com/rss/rss.aspx?id=NY",
    # Asian-American broader
    "https://www.nbcnews.com/feeds/nbcnews.com/sections/asian-america",
    "https://nextshark.com/feed/",
    "https://www.hyphenmagazine.com/feed",
    "https://18millionrising.org/feed",
    # Boston local
    "https://www.boston.com/feed/",
    "https://www.wbur.org/rss/news",
]


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ").strip()


def quick_relevant(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in KOREAN_KEYWORDS) or any(g in t for g in GEO_KEYWORDS)


def uid_of(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────
@dataclass
class Article:
    uid: str
    title: str
    url: str
    source_name: str
    published_at: str          # ISO 8601
    description: str
    content: str
    image_url: str
    author: str
    category: str = "Korean News"
    tags: str = ""
    excerpt: str = ""
    relevance: int = 0
    kind: str = "article"      # "article" or "video"
    video_id: str = ""         # YouTube video ID if kind=="video"
    video_embed: str = ""      # full embed HTML


@dataclass
class Video:
    uid: str
    title: str
    video_id: str
    channel_name: str
    channel_id: str
    published_at: str
    description: str
    thumbnail_url: str
    url: str
    category: str = "Korean Video"
    tags: str = ""
    excerpt: str = ""
    relevance: int = 0
    kind: str = "video"
    embed_html: str = ""

    def __post_init__(self):
        self.url = f"https://www.youtube.com/watch?v={self.video_id}"
        self.embed_html = (
            f'<div class="k-boston-video-embed" style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;">'
            f'<iframe src="https://www.youtube.com/embed/{self.video_id}" '
            f'style="position:absolute;top:0;left:0;width:100%;height:100%;" '
            f'frameborder="0" allowfullscreen></iframe></div>'
        )


# ─────────────────────────────────────────────
# Source 1 – NewsAPI
# ─────────────────────────────────────────────
class NewsAPISource:
    """
    NewsAPI.org — free tier: 100 requests/day, 30-day history.
    Set NEWSAPI_KEY env var.  https://newsapi.org/register
    """
    BASE = "https://newsapi.org/v2"

    def __init__(self):
        self.key = os.getenv("NEWSAPI_KEY", "")

    def fetch(self) -> list[Article]:
        if not self.key:
            log.warning("NEWSAPI_KEY not set – skipping NewsAPI")
            return []

        articles = []
        since = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
        seen: set[str] = set()

        queries = [
            "Korean American Boston",
            "Korean community New England",
            "South Korea news Boston",
            "K-pop Boston",
            "Korean culture Massachusetts",
            "Asian American New England",
        ]

        for q in queries:
            params = {
                "q": q,
                "from": since,
                "sortBy": "publishedAt",
                "language": "en",
                "apiKey": self.key,
                "pageSize": 30,
            }
            try:
                r = requests.get(f"{self.BASE}/everything", params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error(f"NewsAPI error ({q}): {exc}")
                continue

            for a in data.get("articles", []):
                url = a.get("url", "")
                if url in seen or not url:
                    continue
                seen.add(url)

                title   = a.get("title", "") or ""
                desc    = a.get("description", "") or ""
                content = clean_html(a.get("content", "") or desc)

                if not quick_relevant(f"{title} {desc} {content}"):
                    continue

                articles.append(Article(
                    uid=uid_of(url),
                    title=title,
                    url=url,
                    source_name=(a.get("source") or {}).get("name", ""),
                    published_at=a.get("publishedAt", ""),
                    description=desc,
                    content=content,
                    image_url=a.get("urlToImage", "") or "",
                    author=a.get("author", "") or "",
                ))
            time.sleep(0.3)

        log.info(f"NewsAPI: {len(articles)} candidates")
        return articles


# ─────────────────────────────────────────────
# Source 2 – SerpAPI (Google News)
# ─────────────────────────────────────────────
class SerpAPINewsSource:
    BASE = "https://serpapi.com/search"

    def __init__(self):
        self.key = os.getenv("SERPAPI_KEY", "")

    def fetch(self) -> list[Article]:
        if not self.key:
            log.warning("SERPAPI_KEY not set – skipping SerpAPI News")
            return []

        articles = []
        seen: set[str] = set()
        queries = [
            "Korean American Boston news",
            "Korean community New England",
            "South Korea news United States",
            "Korean culture festival Boston",
        ]

        for q in queries:
            params = {
                "engine": "google_news",
                "q": q,
                "gl": "us",
                "hl": "en",
                "api_key": self.key,
            }
            try:
                r = requests.get(self.BASE, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error(f"SerpAPI News error ({q}): {exc}")
                continue

            for item in data.get("news_results", []):
                url = item.get("link", "")
                if url in seen or not url:
                    continue
                seen.add(url)

                title   = item.get("title", "")
                snippet = item.get("snippet", "")

                articles.append(Article(
                    uid=uid_of(url),
                    title=title,
                    url=url,
                    source_name=(item.get("source") or {}).get("name", ""),
                    published_at=item.get("date", ""),
                    description=snippet,
                    content=snippet,
                    image_url=(item.get("thumbnail") or ""),
                    author="",
                ))
            time.sleep(0.5)

        log.info(f"SerpAPI News: {len(articles)} candidates")
        return articles


# ─────────────────────────────────────────────
# Source 3 – RSS News Feeds
# ─────────────────────────────────────────────
class RSSNewsSource:
    def fetch(self) -> list[Article]:
        articles = []
        seen: set[str] = set()
        cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK * 3)

        for feed_url in NEWS_RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries:
                    url = entry.get("link", "")
                    if url in seen or not url:
                        continue
                    seen.add(url)

                    title   = entry.get("title", "")
                    summary = clean_html(
                        entry.get("summary", "")
                        or (entry.get("content") or [{}])[0].get("value", "")
                    )

                    if not quick_relevant(f"{title} {summary}"):
                        continue

                    pub = entry.get("published_parsed") or entry.get("updated_parsed")
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
                    if pub_dt and pub_dt < cutoff:
                        continue

                    # Thumbnail
                    img = ""
                    for m in (entry.get("media_thumbnail") or entry.get("media_content") or []):
                        if isinstance(m, dict) and m.get("url"):
                            img = m["url"]
                            break

                    articles.append(Article(
                        uid=uid_of(url),
                        title=title,
                        url=url,
                        source_name=feed.feed.get("title", feed_url),
                        published_at=pub_dt.isoformat() if pub_dt else "",
                        description=summary[:300],
                        content=summary,
                        image_url=img,
                        author=entry.get("author", ""),
                    ))
            except Exception as exc:
                log.warning(f"RSS feed error ({feed_url}): {exc}")

        log.info(f"RSS News: {len(articles)} candidates")
        return articles


# ─────────────────────────────────────────────
# Source 4 – YouTube Data API v3
# ─────────────────────────────────────────────
class YouTubeSource:
    """
    YouTube Data API v3.
    Set YOUTUBE_API_KEY env var.
    Free quota: 10,000 units/day (search = 100 units each, list = 1 unit each).
    https://console.developers.google.com → Enable YouTube Data API v3
    """
    BASE = "https://www.googleapis.com/youtube/v3"

    def __init__(self):
        self.key = os.getenv("YOUTUBE_API_KEY", "")

    def _search(self, query: str, max_results: int = 15) -> list[dict]:
        if not self.key:
            return []
        published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "regionCode": "US",
            "relevanceLanguage": "en",
            "publishedAfter": published_after,
            "maxResults": max_results,
            "key": self.key,
            "order": "date",
        }
        try:
            r = requests.get(f"{self.BASE}/search", params=params, timeout=15)
            r.raise_for_status()
            return r.json().get("items", [])
        except Exception as exc:
            log.error(f"YouTube search error ({query}): {exc}")
            return []

    def _channel_videos(self, channel_id: str, max_results: int = 10) -> list[dict]:
        if not self.key:
            return []
        published_after = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "order": "date",
            "publishedAfter": published_after,
            "maxResults": max_results,
            "key": self.key,
        }
        try:
            r = requests.get(f"{self.BASE}/search", params=params, timeout=15)
            r.raise_for_status()
            return r.json().get("items", [])
        except Exception as exc:
            log.error(f"YouTube channel error ({channel_id}): {exc}")
            return []

    def _parse_item(self, item: dict, channel_name: str = "") -> Optional[Video]:
        snippet = item.get("snippet") or {}
        vid_id = (item.get("id") or {})
        if isinstance(vid_id, dict):
            vid_id = vid_id.get("videoId", "")
        else:
            vid_id = str(vid_id)
        if not vid_id:
            return None

        thumbs = snippet.get("thumbnails") or {}
        thumb = (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or {}).get("url", "")

        v = Video(
            uid=uid_of(vid_id),
            title=snippet.get("title", ""),
            video_id=vid_id,
            channel_name=channel_name or snippet.get("channelTitle", ""),
            channel_id=snippet.get("channelId", ""),
            published_at=snippet.get("publishedAt", ""),
            description=snippet.get("description", "")[:500],
            thumbnail_url=thumb,
            url="",  # set in __post_init__
        )
        return v

    def fetch(self) -> list[Video]:
        if not self.key:
            log.warning("YOUTUBE_API_KEY not set – skipping YouTube")
            return []

        videos = []
        seen: set[str] = set()

        # 1. Search queries
        search_queries = [
            "Korean American Boston",
            "Korean community New England",
            "K-pop Boston concert",
            "Korean culture Massachusetts",
            "Korean food Boston",
            "Korean language learning",
            "South Korea news English",
            "Korean American documentary",
            "Asian American Boston community",
        ]
        for q in search_queries:
            for item in self._search(q, max_results=10):
                v = self._parse_item(item)
                if v and v.uid not in seen:
                    seen.add(v.uid)
                    if quick_relevant(f"{v.title} {v.description} {v.channel_name}"):
                        videos.append(v)
            time.sleep(0.2)

        # 2. Known channels
        for ch_name, ch_id in YOUTUBE_CHANNELS.items():
            for item in self._channel_videos(ch_id, max_results=5):
                v = self._parse_item(item, channel_name=ch_name)
                if v and v.uid not in seen:
                    seen.add(v.uid)
                    videos.append(v)
            time.sleep(0.2)

        log.info(f"YouTube: {len(videos)} candidates")
        return videos


# ─────────────────────────────────────────────
# Claude AI – Relevance Scoring & Enrichment
# ─────────────────────────────────────────────
class ClaudeEnricher:
    BATCH = 20

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set – AI scoring disabled")

    def _score_batch(self, items: list[dict]) -> list[dict]:
        if not self.client:
            return [{"id": i["id"], "relevance": 50, "category": "Korean News",
                     "tags": "", "excerpt": ""} for i in items]

        prompt = f"""You are the content curator for k-boston.org, a website for Korean Americans
living in Boston and Greater New England.

Review these news articles and videos. For each return a JSON array.
Return ONLY raw JSON, no markdown fences.

For each item include:
  "id": same integer id as input
  "relevance": 0-100 (100 = essential reading for Korean Americans in Boston,
               0 = irrelevant. Consider: does it concern Korean/Korean-American
               culture, community, politics, entertainment, food, sports, or
               Korean-American life in New England specifically?)
  "category": one of [Korean News, Korean Politics, Korean Culture, Korean Food,
               K-pop & Music, Korean Film & TV, Korean Sports, Korean American Life,
               Boston & New England, Business & Economy, Education, Health,
               Community Events, Travel & Tourism]
  "tags": up to 5 comma-separated keywords
  "excerpt": 1-2 engaging sentences written for the k-boston.org audience.
             Empty string if not enough info.

Items:
{json.dumps(items, ensure_ascii=False, indent=2)}
"""
        try:
            msg = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
            return json.loads(raw)
        except Exception as exc:
            log.error(f"Claude scoring error: {exc}")
            return [{"id": i["id"], "relevance": 50, "category": "Korean News",
                     "tags": "", "excerpt": ""} for i in items]

    def enrich_articles(self, articles: list[Article]) -> list[Article]:
        return self._enrich(articles, kind="article")

    def enrich_videos(self, videos: list[Video]) -> list[Video]:
        return self._enrich(videos, kind="video")

    def _enrich(self, items, kind: str):
        if not items:
            return []
        enriched = []

        for start in range(0, len(items), self.BATCH):
            batch = items[start:start + self.BATCH]
            payload = []
            for j, item in enumerate(batch):
                if kind == "article":
                    payload.append({"id": j, "title": item.title,
                                    "description": item.description[:300],
                                    "source": item.source_name})
                else:
                    payload.append({"id": j, "title": item.title,
                                    "description": item.description[:300],
                                    "channel": item.channel_name})

            scores = {s["id"]: s for s in self._score_batch(payload)}

            for j, item in enumerate(batch):
                s = scores.get(j, {})
                item.relevance = s.get("relevance", 0)
                item.category  = s.get("category", item.category)
                item.tags      = s.get("tags", "")
                item.excerpt   = s.get("excerpt", "")
                if item.relevance >= THRESHOLD:
                    enriched.append(item)

            time.sleep(1)

        log.info(f"Claude enriched {kind}s: {len(enriched)}/{len(items)} passed threshold {THRESHOLD}")
        return enriched


# ─────────────────────────────────────────────
# De-duplicate
# ─────────────────────────────────────────────
def dedup_articles(articles: list[Article]) -> list[Article]:
    seen: dict[str, Article] = {}
    for a in articles:
        if a.uid not in seen or a.relevance > seen[a.uid].relevance:
            seen[a.uid] = a
    return sorted(seen.values(), key=lambda x: x.published_at, reverse=True)[:MAX_ARTICLES]


def dedup_videos(videos: list[Video]) -> list[Video]:
    seen: dict[str, Video] = {}
    for v in videos:
        if v.uid not in seen or v.relevance > seen[v.uid].relevance:
            seen[v.uid] = v
    return sorted(seen.values(), key=lambda x: x.published_at, reverse=True)[:MAX_VIDEOS]


# ─────────────────────────────────────────────
# JSON Output
# ─────────────────────────────────────────────
def to_dict(obj) -> dict:
    d = asdict(obj)
    # Ensure embed HTML is populated for videos
    if hasattr(obj, "video_id") and obj.video_id:
        d["embed_html"] = obj.embed_html
        d["url"] = obj.url
    return d


def write_json(items: list, path: str):
    data = {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "count": len(items),
        "items": [to_dict(i) for i in items],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"Wrote {len(items)} items → {path}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    log.info("=== K-Boston News & Video Discovery starting ===")

    # ── Articles ─────────────────────────────
    raw_articles: list[Article] = []
    for Src in [NewsAPISource, SerpAPINewsSource, RSSNewsSource]:
        try:
            raw_articles.extend(Src().fetch())
        except Exception as exc:
            log.error(f"{Src.__name__} failed: {exc}")

    log.info(f"Total article candidates: {len(raw_articles)}")

    # AI enrichment temporarily disabled for faster/cheaper testing.
    # To re-enable, replace this block with:
    #   enricher = ClaudeEnricher()
    #   articles = dedup_articles(enricher.enrich_articles(raw_articles))
    for a in raw_articles:
        a.relevance = 50
    articles = dedup_articles(raw_articles)
    log.info(f"AI enrichment OFF – passing all {len(articles)} articles through")

    # ── Videos ───────────────────────────────
    raw_videos: list[Video] = []
    try:
        raw_videos = YouTubeSource().fetch()
    except Exception as exc:
        log.error(f"YouTubeSource failed: {exc}")

    log.info(f"Total video candidates: {len(raw_videos)}")
    # To re-enable AI: videos = dedup_videos(enricher.enrich_videos(raw_videos))
    for v in raw_videos:
        v.relevance = 50
    videos = dedup_videos(raw_videos)
    log.info(f"AI enrichment OFF – passing all {len(videos)} videos through")

    # ── Write outputs ─────────────────────────
    out = os.getenv("OUTPUT_DIR", "output")
    os.makedirs(out, exist_ok=True)
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")

    article_path = f"{out}/news-articles-{today}.json"
    video_path   = f"{out}/news-videos-{today}.json"
    latest_path  = f"{out}/news-latest.json"

    write_json(articles, article_path)
    write_json(videos,   video_path)

    # Combined latest file for the WP importer
    all_items = [to_dict(i) for i in articles] + [to_dict(i) for i in videos]
    combined = {
        "generated_at": datetime.now(EASTERN).isoformat(),
        "articles": [to_dict(i) for i in articles],
        "videos":   [to_dict(i) for i in videos],
        "total":    len(all_items),
    }
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    log.info(f"=== Done — {len(articles)} articles, {len(videos)} videos ===")


if __name__ == "__main__":
    main()
