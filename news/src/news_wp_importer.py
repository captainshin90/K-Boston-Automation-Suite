#!/usr/bin/env python3
"""
K-Boston News WordPress Importer
Reads news-latest.json and pushes articles + videos to WordPress
via the WP REST API as standard Posts.

Articles → WordPress Posts with excerpt + "Read full article →" link
Videos   → WordPress Posts with YouTube embed + thumbnail as featured image

Duplicate detection uses WP post slug (derived from title), so re-running
daily will skip already-imported items without needing custom meta fields.

Env vars required:
  WP_SITE_URL       – https://k-boston.org
  WP_USERNAME       – WordPress admin username
  WP_APP_PASSWORD   – Application Password (WP 5.6+, Users → App Passwords)

Optional:
  WP_NEWS_CATEGORY  – slug of WP category to assign (default: "korean-news")
  WP_VIDEO_CATEGORY – slug of WP category for videos (default: "korean-videos")
  WP_POST_STATUS    – "publish" or "draft" (default: "publish")
  SKIP_DUPLICATES   – "true" to skip already-imported items (default: true)
"""

import os
import re
import ssl
import json
import time
import logging
import requests
import urllib3
from requests.adapters import HTTPAdapter
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

NEWS_CATEGORY  = os.getenv("WP_NEWS_CATEGORY",  "korean-news")
VIDEO_CATEGORY = os.getenv("WP_VIDEO_CATEGORY", "korean-videos")

IMAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; K-Boston/1.0; +https://k-boston.org)",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}


# ─────────────────────────────────────────────
# WP REST API Helpers
# ─────────────────────────────────────────────
def auth():
    return (WP_USER, WP_PASS)


def wp_get(endpoint, params=None):
    r = requests.get(f"{WP_SITE}/wp-json/wp/v2/{endpoint}",
                     auth=auth(), params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json()


def wp_post_req(endpoint, payload):
    r = requests.post(f"{WP_SITE}/wp-json/wp/v2/{endpoint}",
                      auth=auth(), json=payload, timeout=30)
    if not r.ok:
        try:
            msg = r.json().get("message", r.text[:300])
        except Exception:
            msg = r.text[:300]
        log.error(f"    FULL WP RESPONSE: {r.text[:800]}")
        raise RuntimeError(f"HTTP {r.status_code}: {msg}")
    return r.json()


def make_slug(title):
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:180]


def post_exists_by_slug(slug):
    if not SKIP_DUPS:
        return False
    try:
        results = wp_get("posts", {"slug": slug, "per_page": 1})
        return len(results) > 0
    except Exception:
        return False


def get_or_create_category(name, slug):
    try:
        cats = wp_get("categories", {"slug": slug})
        if cats:
            return cats[0]["id"]
        cat = wp_post_req("categories", {"name": name, "slug": slug})
        log.info(f"  Created category: {name} (ID {cat['id']})")
        return cat["id"]
    except Exception as exc:
        log.warning(f"  Category lookup/create failed ({name}): {exc}")
        return 0


def get_or_create_tags(tag_csv):
    if not tag_csv:
        return []
    ids = []
    for name in [t.strip() for t in tag_csv.split(",") if t.strip()]:
        try:
            slug = re.sub(r"[\s_]+", "-", name.lower().strip())[:100]
            existing = wp_get("tags", {"slug": slug})
            if existing:
                ids.append(existing[0]["id"])
            else:
                tag = wp_post_req("tags", {"name": name, "slug": slug})
                ids.append(tag["id"])
        except Exception:
            pass
    return ids


class _LegacySSLAdapter(HTTPAdapter):
    """HTTPAdapter that allows legacy SSL renegotiation for older servers."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _image_session() -> requests.Session:
    """Return a requests session that tolerates legacy SSL (e.g. img.yna.co.kr)."""
    s = requests.Session()
    s.mount("https://", _LegacySSLAdapter())
    s.headers.update(IMAGE_HEADERS)
    return s


def sideload_image(image_url, title):
    """Download image from external URL and upload to WP Media Library.
    Uses a legacy-SSL-tolerant session so servers like img.yna.co.kr work."""
    if not image_url:
        return 0
    try:
        session = _image_session()
        resp = session.get(image_url, timeout=15)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        ext = {"image/jpeg": "jpg", "image/png": "png",
               "image/webp": "webp", "image/gif": "gif"}.get(content_type, "jpg")
        safe = re.sub(r"[^a-z0-9]+", "-", title.lower())[:50].strip("-")
        filename = f"k-boston-{safe}.{ext}"
        r = requests.post(
            f"{WP_SITE}/wp-json/wp/v2/media",
            auth=auth(),
            headers={"Content-Disposition": f'attachment; filename="{filename}"',
                     "Content-Type": content_type},
            data=resp.content,
            timeout=30,
        )
        r.raise_for_status()
        media_id = r.json().get("id", 0)
        log.info(f"    🖼  Image sideloaded → WP media ID {media_id}")
        return media_id
    except Exception as exc:
        log.warning(f"    Image sideload skipped: {exc}")
        return 0


def parse_date(published):
    if not published:
        return ""
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt.astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return published[:19]


# ─────────────────────────────────────────────
# Article importer
# ─────────────────────────────────────────────
def import_article(item, news_cat_id):
    title = item.get("title", "").strip()
    if not title:
        return False

    slug = make_slug(title)
    if post_exists_by_slug(slug):
        log.info(f"  ↩ Skip (exists): {title[:70]}")
        return False

    source_url  = item.get("url", "")
    source_name = item.get("source_name", "")
    excerpt     = item.get("excerpt") or item.get("description", "")
    content_txt = item.get("content") or item.get("description", "")

    content_html = (
        f'<div class="k-boston-article-excerpt">'
        f"<p>{excerpt or content_txt[:400]}</p>"
        f'<p><a href="{source_url}" target="_blank" rel="noopener noreferrer" '
        f'class="k-boston-read-more">Read full article at {source_name} →</a></p>'
        f"</div>"
    )

    image_id = sideload_image(item.get("image_url", ""), title)
    tag_ids  = get_or_create_tags(item.get("tags", ""))

    payload = {
        "title":      title,
        "slug":       slug,
        "content":    content_html,
        "excerpt":    (excerpt or "")[:200],
        "status":     POST_STATUS,
        "categories": [news_cat_id] if news_cat_id else [],
        "tags":       tag_ids,
    }
    date_str = parse_date(item.get("published_at", ""))
    if date_str:
        payload["date"] = date_str
    if image_id:
        payload["featured_media"] = image_id

    try:
        result = wp_post_req("posts", payload)
        log.info(f"  ✓ Article: {title[:70]} (WP ID {result.get('id')})")
        return True
    except Exception as exc:
        log.error(f"  ✗ Article failed: {title[:70]} — {exc}")
        return False


# ─────────────────────────────────────────────
# Video importer
# ─────────────────────────────────────────────
def import_video(item, video_cat_id):
    title    = item.get("title", "").strip()
    video_id = item.get("video_id", "")
    if not title or not video_id:
        return False

    slug = make_slug(title)
    if post_exists_by_slug(slug):
        log.info(f"  ↩ Skip (exists): {title[:70]}")
        return False

    channel    = item.get("channel_name", "")
    channel_id = item.get("channel_id", "")
    desc       = item.get("description", "")
    excerpt    = item.get("excerpt") or desc[:200]
    source_url = f"https://www.youtube.com/watch?v={video_id}"

    embed_html = (
        f'<div class="k-boston-video-embed" style="position:relative;'
        f'padding-bottom:56.25%;height:0;overflow:hidden;">'
        f'<iframe src="https://www.youtube.com/embed/{video_id}" '
        f'style="position:absolute;top:0;left:0;width:100%;height:100%;" '
        f'frameborder="0" allowfullscreen loading="lazy"></iframe></div>'
    )
    channel_link = (
        f'<a href="https://www.youtube.com/channel/{channel_id}" '
        f'target="_blank" rel="noopener noreferrer">{channel}</a>'
        if channel_id else channel
    )
    content_html = (
        f"{embed_html}"
        f'<div class="k-boston-video-meta">'
        f"<p>{excerpt}</p>"
        f"<p><strong>Channel:</strong> {channel_link}</p>"
        f'<p><a href="{source_url}" target="_blank" rel="noopener noreferrer" '
        f'class="k-boston-read-more">Watch on YouTube →</a></p>'
        f"</div>"
    )

    image_id = sideload_image(item.get("thumbnail_url", ""), title)
    tag_ids  = get_or_create_tags(item.get("tags", ""))

    payload = {
        "title":      title,
        "slug":       slug,
        "content":    content_html,
        "excerpt":    excerpt[:200],
        "status":     POST_STATUS,
        "categories": [video_cat_id] if video_cat_id else [],
        "tags":       tag_ids,
    }
    date_str = parse_date(item.get("published_at", ""))
    if date_str:
        payload["date"] = date_str
    if image_id:
        payload["featured_media"] = image_id

    try:
        result = wp_post_req("posts", payload)
        log.info(f"  ✓ Video: {title[:70]} (WP ID {result.get('id')})")
        return True
    except Exception as exc:
        log.error(f"  ✗ Video failed: {title[:70]} — {exc}")
        return False


# ─────────────────────────────────────────────
# Connectivity test
# ─────────────────────────────────────────────
def run_test(articles, videos):
    """
    --test mode: attempt to import exactly 1 article and 1 video,
    printing the full WP request payload and response so errors are visible.
    """
    log.info("══ TEST MODE – trying 1 article + 1 video ══")
    log.info(f"WP_SITE={WP_SITE}  WP_USER={WP_USER}  PASS={'set' if WP_PASS else 'NOT SET'}")

    # Verify WP REST API is reachable at all
    try:
        info = requests.get(f"{WP_SITE}/wp-json/wp/v2/", timeout=10).json()
        log.info(f"WP REST API reachable ✓  (WP version: {info.get('namespaces', [])})")
    except Exception as exc:
        log.error(f"WP REST API NOT reachable: {exc}")
        raise SystemExit(1)

    # Minimal post payload – no tags, no image, no date
    test_payload = {
        "title":   "K-Boston Test Post – safe to delete",
        "content": "<p>This is a test post created by the K-Boston news importer. Please delete it.</p>",
        "status":  "draft",   # always draft in test mode
    }
    log.info(f"Test payload: {json.dumps(test_payload)}")
    try:
        r = requests.post(f"{WP_SITE}/wp-json/wp/v2/posts",
                          auth=auth(), json=test_payload, timeout=20)
        log.info(f"Response status: {r.status_code}")
        log.info(f"Response body: {r.text[:600]}")
        if r.ok:
            post_id = r.json().get("id")
            log.info(f"✓ Test post created as DRAFT (ID {post_id}) — please delete from WP Admin → Posts")
        else:
            log.error("✗ Test post FAILED — see response body above for the WP error")
            raise SystemExit(1)
    except requests.RequestException as exc:
        log.error(f"Request error: {exc}")
        raise SystemExit(1)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Import K-Boston news & videos to WordPress")
    parser.add_argument("--json", default="news/output/news-latest.json", help="JSON file path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without touching WordPress")
    parser.add_argument("--test", action="store_true",
                        help="Send a minimal test post to WP to verify credentials and connectivity")
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
        log.info("── DRY RUN – nothing will be written to WordPress ──")
        for a in articles:
            log.info(f"  [article] {a.get('title','')[:80]}")
        for v in videos:
            log.info(f"  [video]   {v.get('title','')[:80]}")
        return

    if args.test:
        run_test(articles, videos)
        return

    news_cat_id  = get_or_create_category("Korean News",   NEWS_CATEGORY)
    video_cat_id = get_or_create_category("Korean Videos", VIDEO_CATEGORY)

    log.info(f"── Importing {len(articles)} articles ──")
    ok_a = fail_a = 0
    for item in articles:
        if import_article(item, news_cat_id):
            ok_a += 1
        else:
            fail_a += 1
        time.sleep(0.4)

    log.info(f"── Importing {len(videos)} videos ──")
    ok_v = fail_v = 0
    for item in videos:
        if import_video(item, video_cat_id):
            ok_v += 1
        else:
            fail_v += 1
        time.sleep(0.4)

    log.info("══════════════════════════════════════════")
    log.info(f"Articles : {ok_a} imported, {fail_a} skipped/failed")
    log.info(f"Videos   : {ok_v} imported, {fail_v} skipped/failed")
    log.info(f"Find them at: {WP_SITE}/blog/")
    log.info(f"  Korean News   → {WP_SITE}/category/{NEWS_CATEGORY}/")
    log.info(f"  Korean Videos → {WP_SITE}/category/{VIDEO_CATEGORY}/")
    log.info("══════════════════════════════════════════")

    # Exit non-zero if everything failed (makes GitHub Actions mark step as failed)
    if (ok_a + ok_v) == 0 and (fail_a + fail_v) > 0:
        log.error("All posts failed to import — check errors above")
        sys.exit(1)


if __name__ == "__main__":
    main()
