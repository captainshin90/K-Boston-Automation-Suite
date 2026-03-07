#!/usr/bin/env python3
"""
K-Boston News WordPress Importer
Reads news-latest.json and pushes articles + videos to WordPress
via the WP REST API as standard Posts (compatible with WP RSS Aggregator
custom post types too).

Articles are created as WordPress 'post' type with:
  - Title, content, excerpt, featured image, categories, tags
  - Source URL stored as custom meta (for "Read full article →" links)

Videos are created as WordPress 'post' type with:
  - YouTube embed as post content
  - Thumbnail as featured image
  - Custom meta for video_id, channel_name

Env vars required:
  WP_SITE_URL       – https://k-boston.org
  WP_USERNAME       – WordPress admin username
  WP_APP_PASSWORD   – Application Password (WP 5.6+, Users → App Passwords)

Optional:
  WP_NEWS_CATEGORY  – slug of WP category to assign (default: "news")
  WP_VIDEO_CATEGORY – slug of WP category for videos (default: "korean-videos")
  WP_POST_STATUS    – "publish" or "draft" (default: "publish")
  SKIP_DUPLICATES   – "true" to skip posts whose source URL already exists (default: true)
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EASTERN = ZoneInfo("America/New_York")

WP_SITE     = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USER     = os.getenv("WP_USERNAME", "")
WP_PASS     = os.getenv("WP_APP_PASSWORD", "")
POST_STATUS = os.getenv("WP_POST_STATUS", "publish")
SKIP_DUPS   = os.getenv("SKIP_DUPLICATES", "true").lower() == "true"

NEWS_CATEGORY  = os.getenv("WP_NEWS_CATEGORY",  "news")
VIDEO_CATEGORY = os.getenv("WP_VIDEO_CATEGORY", "korean-videos")


# ─────────────────────────────────────────────
# WP REST API Helpers
# ─────────────────────────────────────────────
def wp_auth() -> tuple:
    return (WP_USER, WP_PASS)


def wp_get(endpoint: str, params: dict = None) -> dict:
    r = requests.get(f"{WP_SITE}/wp-json/wp/v2/{endpoint}",
                     auth=wp_auth(), params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def wp_post(endpoint: str, payload: dict) -> dict:
    r = requests.post(f"{WP_SITE}/wp-json/wp/v2/{endpoint}",
                      auth=wp_auth(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def get_or_create_category(name: str, slug: str) -> int:
    """Return WP category ID, creating it if needed."""
    try:
        cats = wp_get("categories", {"slug": slug})
        if cats:
            return cats[0]["id"]
        # Create
        cat = wp_post("categories", {"name": name, "slug": slug})
        return cat["id"]
    except Exception as exc:
        log.warning(f"Category lookup failed ({name}): {exc}")
        return 0


def get_or_create_tags(tag_csv: str) -> list[int]:
    """Return list of WP tag IDs from comma-separated tag names."""
    if not tag_csv:
        return []
    ids = []
    for name in [t.strip() for t in tag_csv.split(",") if t.strip()]:
        try:
            slug = name.lower().replace(" ", "-")
            existing = wp_get("tags", {"slug": slug})
            if existing:
                ids.append(existing[0]["id"])
            else:
                tag = wp_post("tags", {"name": name, "slug": slug})
                ids.append(tag["id"])
        except Exception:
            pass
    return ids


def sideload_image(image_url: str, title: str) -> int:
    """Download remote image and upload to WP Media Library. Returns attachment ID."""
    if not image_url:
        return 0
    try:
        resp = requests.get(image_url, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg")
        ext = {"image/jpeg": "jpg", "image/png": "png",
               "image/webp": "webp", "image/gif": "gif"}.get(content_type, "jpg")
        filename = f"k-boston-{title[:40].replace(' ', '-').lower()}.{ext}"

        r = requests.post(
            f"{WP_SITE}/wp-json/wp/v2/media",
            auth=wp_auth(),
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Content-Type": content_type},
            data=resp.content,
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("id", 0)
    except Exception as exc:
        log.warning(f"Image sideload failed ({image_url}): {exc}")
        return 0


def post_exists(source_url: str) -> bool:
    """Check if a post with this source URL meta already exists."""
    if not SKIP_DUPS or not source_url:
        return False
    try:
        results = wp_get("posts", {"meta_key": "k_boston_source_url",
                                    "meta_value": source_url, "per_page": 1})
        return len(results) > 0
    except Exception:
        return False


# ─────────────────────────────────────────────
# Article importer
# ─────────────────────────────────────────────
def import_article(item: dict, news_cat_id: int) -> bool:
    source_url = item.get("url", "")
    title      = item.get("title", "").strip()
    if not title:
        return False

    if post_exists(source_url):
        log.info(f"  ↩ Skip (exists): {title[:60]}")
        return False

    # Build content: excerpt + "Read full article" link
    excerpt     = item.get("excerpt") or item.get("description", "")
    content_txt = item.get("content") or item.get("description", "")
    source_name = item.get("source_name", "")
    published   = item.get("published_at", "")

    content_html = f"""<div class="k-boston-article-excerpt">
<p>{excerpt or content_txt[:400]}</p>
<p><a href="{source_url}" target="_blank" rel="noopener noreferrer" class="k-boston-read-more">
  Read full article at {source_name} →
</a></p>
</div>"""

    # Featured image
    image_id = sideload_image(item.get("image_url", ""), title)

    tag_ids = get_or_create_tags(item.get("tags", ""))

    payload = {
        "title":          title,
        "content":        content_html,
        "excerpt":        excerpt[:200] if excerpt else "",
        "status":         POST_STATUS,
        "date":           published[:19] if published else datetime.now(EASTERN).isoformat()[:19],
        "categories":     [news_cat_id] if news_cat_id else [],
        "tags":           tag_ids,
        "meta": {
            "k_boston_source_url":  source_url,
            "k_boston_source_name": source_name,
            "k_boston_author":      item.get("author", ""),
            "k_boston_kind":        "article",
        },
    }
    if image_id:
        payload["featured_media"] = image_id

    try:
        result = wp_post("posts", payload)
        log.info(f"  ✓ Article: {title[:60]} (ID {result.get('id')})")
        return True
    except Exception as exc:
        log.error(f"  ✗ Article failed: {title[:60]} – {exc}")
        return False


# ─────────────────────────────────────────────
# Video importer
# ─────────────────────────────────────────────
def import_video(item: dict, video_cat_id: int) -> bool:
    video_id = item.get("video_id", "")
    title    = item.get("title", "").strip()
    if not title or not video_id:
        return False

    source_url = f"https://www.youtube.com/watch?v={video_id}"
    if post_exists(source_url):
        log.info(f"  ↩ Skip (exists): {title[:60]}")
        return False

    channel  = item.get("channel_name", "")
    desc     = item.get("description", "")
    excerpt  = item.get("excerpt") or desc[:200]
    published = item.get("published_at", "")

    embed_html = (
        item.get("embed_html")
        or f'<div class="k-boston-video-embed" style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;">'
           f'<iframe src="https://www.youtube.com/embed/{video_id}" '
           f'style="position:absolute;top:0;left:0;width:100%;height:100%;" '
           f'frameborder="0" allowfullscreen></iframe></div>'
    )

    content_html = f"""{embed_html}
<div class="k-boston-video-meta">
<p>{excerpt}</p>
<p><strong>Channel:</strong> <a href="https://www.youtube.com/channel/{item.get('channel_id','')}"
   target="_blank" rel="noopener noreferrer">{channel}</a></p>
<p><a href="{source_url}" target="_blank" rel="noopener noreferrer" class="k-boston-read-more">
  Watch on YouTube →
</a></p>
</div>"""

    image_id = sideload_image(item.get("thumbnail_url", ""), title)
    tag_ids  = get_or_create_tags(item.get("tags", ""))

    payload = {
        "title":      title,
        "content":    content_html,
        "excerpt":    excerpt[:200],
        "status":     POST_STATUS,
        "date":       published[:19] if published else datetime.now(EASTERN).isoformat()[:19],
        "categories": [video_cat_id] if video_cat_id else [],
        "tags":       tag_ids,
        "meta": {
            "k_boston_source_url":  source_url,
            "k_boston_source_name": channel,
            "k_boston_video_id":    video_id,
            "k_boston_channel":     channel,
            "k_boston_kind":        "video",
        },
    }
    if image_id:
        payload["featured_media"] = image_id

    try:
        result = wp_post("posts", payload)
        log.info(f"  ✓ Video: {title[:60]} (ID {result.get('id')})")
        return True
    except Exception as exc:
        log.error(f"  ✗ Video failed: {title[:60]} – {exc}")
        return False


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import K-Boston news & videos to WordPress")
    parser.add_argument("--json", default="output/news-latest.json", help="JSON file path")
    parser.add_argument("--dry-run", action="store_true", help="Parse JSON but don't call WP API")
    args = parser.parse_args()

    if not all([WP_SITE, WP_USER, WP_PASS]):
        log.error("WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD are required")
        raise SystemExit(1)

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    articles = data.get("articles", [])
    videos   = data.get("videos", [])
    log.info(f"Loaded {len(articles)} articles, {len(videos)} videos from {args.json}")

    if args.dry_run:
        log.info("DRY RUN – showing titles only:")
        for a in articles:
            log.info(f"  [article] {a.get('title','')[:70]}")
        for v in videos:
            log.info(f"  [video]   {v.get('title','')[:70]}")
        return

    # Get/create WP categories
    news_cat_id  = get_or_create_category("Korean News",   NEWS_CATEGORY)
    video_cat_id = get_or_create_category("Korean Videos", VIDEO_CATEGORY)

    ok_a = fail_a = ok_v = fail_v = 0

    log.info(f"Importing {len(articles)} articles...")
    for item in articles:
        if import_article(item, news_cat_id):
            ok_a += 1
        else:
            fail_a += 1
        time.sleep(0.5)

    log.info(f"Importing {len(videos)} videos...")
    for item in videos:
        if import_video(item, video_cat_id):
            ok_v += 1
        else:
            fail_v += 1
        time.sleep(0.5)

    log.info(f"Done — Articles: {ok_a} imported, {fail_a} skipped/failed")
    log.info(f"       Videos:   {ok_v} imported, {fail_v} skipped/failed")


if __name__ == "__main__":
    main()
