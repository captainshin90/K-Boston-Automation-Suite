#!/usr/bin/env python3
"""
K-Boston Event Discovery Engine
Discovers daily upcoming events of interest to Korean Americans in New England
Outputs CSV compatible with The Events Calendar WordPress plugin

Sources:
  - Eventbrite API
  - Ticketmaster Discovery API
  - SerpAPI (Google Events)
  - Korean community RSS/web sources
  - Claude AI for relevance filtering & enrichment
"""

import os
import re
import csv
import json
import time
import logging
import hashlib
import requests
import feedparser
import anthropic
from io import StringIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from dataclasses import dataclass, field, asdict
from typing import Optional

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

# How many days ahead to pull events
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", 60))

# Relevance score threshold (0-100) – events below this are dropped
RELEVANCE_THRESHOLD = int(os.getenv("RELEVANCE_THRESHOLD", 40))

# Korean-American interest keywords for pre-filtering
KOREAN_KEYWORDS = [
    "korean", "korea", "한국", "kpop", "k-pop", "k pop",
    "kimchi", "bibimbap", "bulgogi", "soju", "hanbok", "hangul",
    "taekwondo", "hapkido", "k-drama", "kdrama",
    "asian american", "korean american", "kaba", "kana",
    "korean cultural", "korean community", "korean festival",
    "korean food", "korean film", "korean art", "korean music",
    "korean language", "korea foundation", "korean war",
    "moon jae", "BTS", "blackpink", "hallyu",
]

GEOGRAPHIC_KEYWORDS = [
    "boston", "cambridge", "somerville", "newton", "brookline",
    "quincy", "malden", "burlington", "worcester", "providence",
    "new england", "massachusetts", "connecticut", "rhode island",
    "new hampshire", "vermont", "maine", "ma ", " ma,",
]

# The Events Calendar CSV column order (must match exactly)
TEC_COLUMNS = [
    "Event Name",
    "Event Description",
    "Event Excerpt",
    "Event Start Date",
    "Event Start Time",
    "Event End Date",
    "Event End Time",
    "All Day Event",
    "Timezone",
    "Hide from Event Listings",
    "Sticky in Month View",
    "Feature Event",
    "Event Show Map Link",
    "Event Show Map",
    "Enable Comments",
    "Event Cost",
    "Event Currency Symbol",
    "Event Currency Position",
    "Event Category",
    "Event Tags",
    "Event Website",
    "Event Featured Image",
    # Venue
    "Venue Name",
    "Venue Address",
    "Venue City",
    "Venue State Province",
    "Venue Zip",
    "Venue Country",
    "Venue Phone",
    "Venue URL",
    # Organizer
    "Organizer Name",
    "Organizer Phone",
    "Organizer Website",
    "Organizer Email",
]

# ─────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────
@dataclass
class Event:
    name: str
    description: str = ""
    excerpt: str = ""
    start_date: str = ""           # YYYY-MM-DD
    start_time: str = ""           # HH:MM:SS
    end_date: str = ""
    end_time: str = ""
    all_day: bool = False
    timezone: str = "America/New_York"
    hide_from_listings: bool = False
    sticky_in_month: bool = False
    feature_event: bool = False
    show_map_link: bool = True
    show_map: bool = True
    enable_comments: bool = False
    cost: str = ""
    currency_symbol: str = "$"
    currency_position: str = "prefix"
    category: str = "Community"
    tags: str = ""
    website: str = ""
    featured_image: str = ""
    venue_name: str = ""
    venue_address: str = ""
    venue_city: str = ""
    venue_state: str = "MA"
    venue_zip: str = ""
    venue_country: str = "United States"
    venue_phone: str = ""
    venue_url: str = ""
    organizer_name: str = ""
    organizer_phone: str = ""
    organizer_website: str = ""
    organizer_email: str = ""
    # Internal – not exported
    source: str = ""
    source_id: str = ""
    relevance_score: int = 0

    @property
    def uid(self) -> str:
        return hashlib.md5(f"{self.name}{self.start_date}{self.venue_name}".encode()).hexdigest()

    def to_tec_row(self) -> dict:
        bool_str = lambda b: "TRUE" if b else "FALSE"
        return {
            "Event Name":             self.name,
            "Event Description":      self.description,
            "Event Excerpt":          self.excerpt or self.description[:200],
            "Event Start Date":       self.start_date,
            "Event Start Time":       self.start_time,
            "Event End Date":         self.end_date or self.start_date,
            "Event End Time":         self.end_time,
            "All Day Event":          bool_str(self.all_day),
            "Timezone":               self.timezone,
            "Hide from Event Listings": bool_str(self.hide_from_listings),
            "Sticky in Month View":   bool_str(self.sticky_in_month),
            "Feature Event":          bool_str(self.feature_event),
            "Event Show Map Link":    bool_str(self.show_map_link),
            "Event Show Map":         bool_str(self.show_map),
            "Enable Comments":        bool_str(self.enable_comments),
            "Event Cost":             self.cost,
            "Event Currency Symbol":  self.currency_symbol,
            "Event Currency Position": self.currency_position,
            "Event Category":         self.category,
            "Event Tags":             self.tags,
            "Event Website":          self.website,
            "Event Featured Image":   self.featured_image,
            "Venue Name":             self.venue_name,
            "Venue Address":          self.venue_address,
            "Venue City":             self.venue_city,
            "Venue State Province":   self.venue_state,
            "Venue Zip":              self.venue_zip,
            "Venue Country":          self.venue_country,
            "Venue Phone":            self.venue_phone,
            "Venue URL":              self.venue_url,
            "Organizer Name":         self.organizer_name,
            "Organizer Phone":        self.organizer_phone,
            "Organizer Website":      self.organizer_website,
            "Organizer Email":        self.organizer_email,
        }


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def clean_html(raw: str) -> str:
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    return soup.get_text(separator=" ").strip()

def fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")

def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S")

def quick_is_relevant(text: str) -> bool:
    """Fast pre-filter before calling AI."""
    t = text.lower()
    has_geo = any(g in t for g in GEOGRAPHIC_KEYWORDS)
    has_kw  = any(k in t for k in KOREAN_KEYWORDS)
    return has_geo or has_kw

def parse_datetime_str(s: str) -> Optional[datetime]:
    """Try several ISO / common formats."""
    fmts = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s, f)
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────
# Source 1 – Eventbrite
# ─────────────────────────────────────────────
class EventbriteSource:
    """
    Uses Eventbrite API v3.
    Set EVENTBRITE_TOKEN env var with your private OAuth token.
    Free account: 50 calls / 1 s, 2 000 / day.
    """
    BASE = "https://www.eventbriteapi.com/v3"

    def __init__(self):
        self.token = os.getenv("EVENTBRITE_TOKEN", "")

    def fetch(self, days_ahead: int = DAYS_AHEAD) -> list[Event]:
        if not self.token:
            log.warning("EVENTBRITE_TOKEN not set – skipping Eventbrite")
            return []

        events: list[Event] = []
        now = datetime.now(EASTERN)
        end = now + timedelta(days=days_ahead)

        queries = [
            "Korean Boston", "Korean American Boston",
            "Korean New England", "K-pop Boston",
            "Korean cultural festival Massachusetts",
            "Asian American Boston community",
        ]

        seen: set[str] = set()
        headers = {"Authorization": f"Bearer {self.token}"}

        for q in queries:
            params = {
                "q": q,
                "location.address": "Boston, MA",
                "location.within": "100mi",
                "start_date.range_start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "start_date.range_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expand": "venue,organizer,ticket_classes,logo",
                "page_size": 50,
            }
            try:
                r = requests.get(f"{self.BASE}/events/search/", headers=headers,
                                 params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error(f"Eventbrite error ({q}): {exc}")
                continue

            for e in data.get("events", []):
                eid = e.get("id", "")
                if eid in seen:
                    continue
                seen.add(eid)

                name = e.get("name", {}).get("text", "")
                desc = clean_html(e.get("description", {}).get("html", ""))
                if not quick_is_relevant(f"{name} {desc}"):
                    continue

                start_str = e.get("start", {}).get("local", "")
                end_str   = e.get("end",   {}).get("local", "")
                sd = parse_datetime_str(start_str)
                ed = parse_datetime_str(end_str)

                venue  = e.get("venue") or {}
                addr   = venue.get("address") or {}
                org    = e.get("organizer") or {}
                logo   = e.get("logo") or {}
                img_url = logo.get("original", {}).get("url", "") or logo.get("url", "")

                # Cost
                ticket_classes = e.get("ticket_classes") or []
                cost = ""
                if ticket_classes:
                    prices = [t.get("cost", {}).get("display", "") for t in ticket_classes if t.get("cost")]
                    if prices:
                        cost = prices[0]

                ev = Event(
                    name=name,
                    description=desc,
                    start_date=fmt_date(sd) if sd else "",
                    start_time=fmt_time(sd) if sd else "",
                    end_date=fmt_date(ed)   if ed else "",
                    end_time=fmt_time(ed)   if ed else "",
                    website=f"https://www.eventbrite.com/e/{eid}",
                    featured_image=img_url,
                    cost=cost,
                    venue_name=venue.get("name", ""),
                    venue_address=addr.get("address_1", ""),
                    venue_city=addr.get("city", "Boston"),
                    venue_state=addr.get("region", "MA"),
                    venue_zip=addr.get("postal_code", ""),
                    venue_country=addr.get("country", "United States"),
                    organizer_name=org.get("name", ""),
                    organizer_website=org.get("website", ""),
                    source="eventbrite",
                    source_id=eid,
                )
                events.append(ev)
            time.sleep(0.3)

        log.info(f"Eventbrite: {len(events)} candidate events")
        return events


# ─────────────────────────────────────────────
# Source 2 – Ticketmaster
# ─────────────────────────────────────────────
class TicketmasterSource:
    """
    Ticketmaster Discovery API v2.
    Set TICKETMASTER_KEY env var. Free: 5 000 calls / day.
    """
    BASE = "https://app.ticketmaster.com/discovery/v2"

    def __init__(self):
        self.key = os.getenv("TICKETMASTER_KEY", "")

    def fetch(self, days_ahead: int = DAYS_AHEAD) -> list[Event]:
        if not self.key:
            log.warning("TICKETMASTER_KEY not set – skipping Ticketmaster")
            return []

        events: list[Event] = []
        now = datetime.now(EASTERN)
        end = now + timedelta(days=days_ahead)

        keyword_sets = [
            "Korean", "Kpop", "Korean American", "K-pop", "Taekwondo",
            "Korean Cultural", "Korean Festival",
        ]
        seen: set[str] = set()

        for kw in keyword_sets:
            params = {
                "apikey": self.key,
                "keyword": kw,
                "city": "Boston",
                "stateCode": "MA",
                "radius": "100",
                "unit": "miles",
                "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "size": 50,
                "countryCode": "US",
            }
            try:
                r = requests.get(f"{self.BASE}/events.json", params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error(f"Ticketmaster error ({kw}): {exc}")
                continue

            items = (data.get("_embedded") or {}).get("events", [])
            for e in items:
                eid = e.get("id", "")
                if eid in seen:
                    continue
                seen.add(eid)

                name = e.get("name", "")
                desc = clean_html(e.get("description", "") or e.get("info", ""))

                dates  = e.get("dates", {}).get("start", {})
                sd_str = dates.get("dateTime", "") or dates.get("localDate", "")
                sd = parse_datetime_str(sd_str)

                venues = (e.get("_embedded") or {}).get("venues", [{}])
                venue  = venues[0] if venues else {}
                v_addr = venue.get("address") or {}
                v_city = venue.get("city") or {}
                v_state= venue.get("state") or {}
                v_zip  = venue.get("postalCode", "")
                v_country = (venue.get("country") or {}).get("name", "United States")

                images = e.get("images", [])
                img_url = ""
                if images:
                    big = sorted(images, key=lambda x: x.get("width", 0), reverse=True)
                    img_url = big[0].get("url", "")

                price_ranges = e.get("priceRanges", [])
                cost = ""
                if price_ranges:
                    pr = price_ranges[0]
                    mn = pr.get("min", "")
                    mx = pr.get("max", "")
                    cost = f"{mn}–{mx}" if mn != mx else str(mn)

                cats = e.get("classifications", [{}])
                category = ""
                if cats:
                    seg = cats[0].get("segment") or {}
                    category = seg.get("name", "")

                ev = Event(
                    name=name,
                    description=desc,
                    start_date=fmt_date(sd) if sd else "",
                    start_time=fmt_time(sd) if sd and not dates.get("noSpecificTime") else "",
                    website=e.get("url", ""),
                    featured_image=img_url,
                    cost=cost,
                    venue_name=venue.get("name", ""),
                    venue_address=v_addr.get("line1", ""),
                    venue_city=v_city.get("name", "Boston"),
                    venue_state=v_state.get("stateCode", "MA"),
                    venue_zip=v_zip,
                    venue_country=v_country,
                    category=category or "Entertainment",
                    source="ticketmaster",
                    source_id=eid,
                )
                events.append(ev)
            time.sleep(0.2)

        log.info(f"Ticketmaster: {len(events)} candidate events")
        return events


# ─────────────────────────────────────────────
# Source 3 – SerpAPI (Google Events)
# ─────────────────────────────────────────────
class SerpAPISource:
    """
    Uses SerpAPI's Google Events engine.
    Set SERPAPI_KEY env var. Starter: 100 free searches / month.
    https://serpapi.com/google-events-api
    """
    BASE = "https://serpapi.com/search"

    def __init__(self):
        self.key = os.getenv("SERPAPI_KEY", "")

    def fetch(self, days_ahead: int = DAYS_AHEAD) -> list[Event]:
        if not self.key:
            log.warning("SERPAPI_KEY not set – skipping SerpAPI")
            return []

        events: list[Event] = []
        queries = [
            "Korean American events Boston",
            "Korean cultural events New England",
            "Korean community Boston MA",
            "Korean festival Massachusetts",
            "K-pop concert Boston",
            "Korean food festival Boston",
        ]
        seen: set[str] = set()

        for q in queries:
            params = {
                "engine": "google_events",
                "q": q,
                "location": "Boston, Massachusetts",
                "hl": "en",
                "gl": "us",
                "api_key": self.key,
                "htichips": f"date:range:{datetime.now().strftime('%Y-%m-%d')},{(datetime.now()+timedelta(days=days_ahead)).strftime('%Y-%m-%d')}",
            }
            try:
                r = requests.get(self.BASE, params=params, timeout=20)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                log.error(f"SerpAPI error ({q}): {exc}")
                continue

            for e in data.get("events_results", []):
                title = e.get("title", "")
                uid = hashlib.md5(title.encode()).hexdigest()
                if uid in seen:
                    continue
                seen.add(uid)

                date_info = e.get("date") or {}
                date_str  = date_info.get("start_date", "")
                when_str  = date_info.get("when", "")
                sd = parse_datetime_str(date_str)

                venue  = e.get("venue") or {}
                addr   = e.get("address") or []
                addr_str = ", ".join(addr) if isinstance(addr, list) else str(addr)

                thumbnail = e.get("thumbnail", "")
                link      = e.get("link", "")
                desc      = e.get("description", "")

                ev = Event(
                    name=title,
                    description=desc,
                    start_date=fmt_date(sd) if sd else date_str,
                    start_time=fmt_time(sd) if sd else "",
                    website=link,
                    featured_image=thumbnail,
                    venue_name=venue.get("name", ""),
                    venue_address=addr_str,
                    venue_city="Boston",
                    venue_state="MA",
                    venue_country="United States",
                    source="google_events",
                    source_id=uid,
                )
                events.append(ev)
            time.sleep(0.5)

        log.info(f"SerpAPI: {len(events)} candidate events")
        return events


# ─────────────────────────────────────────────
# Source 4 – Korean Community RSS Feeds
# ─────────────────────────────────────────────
COMMUNITY_FEEDS = [
    # Korean-American organization feeds & calendars (add your own)
    "https://www.koreansocietyboston.org/feed/",          # Korean Society of Boston
    "https://www.kagro.org/feed/",                         # Korean American Grocers
    "https://www.kaba-boston.org/feed/",                   # Korean American Business Assoc
    "https://newenglandkorean.com/feed/",                  # New England Korean
    "https://bostonsaram.com/feed/",                       # Boston Saram (Boston people)
    "https://koreatimes.com/feed/",                        # Korea Times US
    "https://koreadaily.com/feed/",                        # Korea Daily
    # Asian American community
    "https://www.maacboston.org/events/?ical=1",           # MAAC Boston
    "https://www.aaca-usa.org/rss/events/",
    # Broader Boston events with Korean filter
    "https://www.boston.com/events/rss",
]


class RSSSource:
    def fetch(self, days_ahead: int = DAYS_AHEAD) -> list[Event]:
        events: list[Event] = []
        now = datetime.now(EASTERN)
        cutoff = now + timedelta(days=days_ahead)

        for url in COMMUNITY_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    title = entry.get("title", "")
                    summary = clean_html(entry.get("summary", "") or entry.get("content", [{}])[0].get("value", ""))
                    combined = f"{title} {summary}"

                    if not quick_is_relevant(combined):
                        continue

                    # Try to extract date
                    pub = entry.get("published_parsed") or entry.get("updated_parsed")
                    sd = datetime(*pub[:6], tzinfo=EASTERN) if pub else None
                    if sd and sd > cutoff:
                        continue

                    link = entry.get("link", "")
                    img_url = ""
                    # Look for media thumbnail
                    media = entry.get("media_thumbnail") or entry.get("media_content")
                    if media and isinstance(media, list):
                        img_url = media[0].get("url", "")

                    ev = Event(
                        name=title,
                        description=summary,
                        start_date=fmt_date(sd) if sd else "",
                        start_time=fmt_time(sd) if sd else "",
                        website=link,
                        featured_image=img_url,
                        venue_city="Boston",
                        venue_state="MA",
                        venue_country="United States",
                        source="rss",
                        source_id=hashlib.md5(link.encode()).hexdigest(),
                    )
                    events.append(ev)
            except Exception as exc:
                log.warning(f"RSS feed error ({url}): {exc}")

        log.info(f"RSS: {len(events)} candidate events")
        return events


# ─────────────────────────────────────────────
# Claude AI – Relevance Scoring & Enrichment
# ─────────────────────────────────────────────
class ClaudeEnricher:
    """
    Uses Claude claude-sonnet-4-20250514 to:
    1. Score each event for Korean-American relevance (0-100)
    2. Suggest categories / tags
    3. Improve descriptions for the k-boston.org audience
    """

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            log.warning("ANTHROPIC_API_KEY not set – AI enrichment disabled")
            self.client = None
        else:
            self.client = anthropic.Anthropic(api_key=api_key)

    def _batch_score(self, events: list[Event]) -> list[dict]:
        """Send up to 20 events at once and get JSON scores back."""
        if not self.client:
            return [{"relevance": 50, "category": "Community", "tags": "", "improved_desc": ""} for _ in events]

        items = []
        for i, ev in enumerate(events):
            items.append({
                "id": i,
                "name": ev.name,
                "description": ev.description[:400],
                "venue_city": ev.venue_city,
                "tags": ev.tags,
            })

        prompt = f"""You are the curator for k-boston.org, a website for Korean Americans
living in Boston and greater New England.

Review these upcoming events and for each return a JSON array.
Return ONLY raw JSON, no markdown fences.

For each item include:
  "id": same id as input
  "relevance": integer 0-100 (100 = extremely relevant to Korean Americans,
                0 = completely unrelated)
  "category": one of [Korean Culture, Korean Food, Arts & Entertainment,
               Community, Education, Sports & Fitness, Business & Networking,
               Religion, Family, Health & Wellness, Music & K-pop, Language,
               Film, Festival, Travel]
  "tags": comma-separated keywords (max 5)
  "improved_desc": a 1-2 sentence engaging description for the k-boston.org
                   audience (English, warm tone). Empty string if you have
                   nothing to add.

Events:
{json.dumps(items, ensure_ascii=False, indent=2)}
"""
        try:
            msg = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            # Strip potential markdown fences
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
            return json.loads(raw)
        except Exception as exc:
            log.error(f"Claude enrichment error: {exc}")
            return [{"relevance": 50, "category": "Community", "tags": "", "improved_desc": ""} for _ in events]

    def enrich(self, events: list[Event], threshold: int = RELEVANCE_THRESHOLD) -> list[Event]:
        if not events:
            return []

        BATCH = 20
        enriched: list[Event] = []

        for i in range(0, len(events), BATCH):
            batch = events[i:i + BATCH]
            scores = self._batch_score(batch)
            score_map = {s["id"]: s for s in scores}

            for j, ev in enumerate(batch):
                s = score_map.get(j, {})
                ev.relevance_score = s.get("relevance", 0)
                ev.category        = s.get("category", ev.category)
                ev.tags            = s.get("tags", ev.tags)

                better_desc = s.get("improved_desc", "")
                if better_desc and len(better_desc) > 30:
                    ev.excerpt = better_desc

                if ev.relevance_score >= threshold:
                    enriched.append(ev)

            time.sleep(1)  # Respect rate limits

        log.info(f"Claude enrichment: {len(enriched)}/{len(events)} events passed threshold {threshold}")
        return enriched


# ─────────────────────────────────────────────
# De-duplicate & sort
# ─────────────────────────────────────────────
def deduplicate(events: list[Event]) -> list[Event]:
    seen: dict[str, Event] = {}
    for ev in events:
        uid = ev.uid
        if uid not in seen or ev.relevance_score > seen[uid].relevance_score:
            seen[uid] = ev
    deduped = sorted(seen.values(), key=lambda e: (e.start_date, e.relevance_score), reverse=False)
    log.info(f"After dedup: {len(deduped)} events")
    return deduped


# ─────────────────────────────────────────────
# CSV Writer
# ─────────────────────────────────────────────
def write_csv(events: list[Event], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TEC_COLUMNS)
        writer.writeheader()
        for ev in events:
            writer.writerow(ev.to_tec_row())
    log.info(f"Wrote {len(events)} events → {output_path}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    log.info("=== K-Boston Event Discovery starting ===")
    log.info(f"Collecting events for next {DAYS_AHEAD} days")

    all_events: list[Event] = []

    # Collect from all sources
    for SourceClass in [EventbriteSource, TicketmasterSource, SerpAPISource, RSSSource]:
        try:
            src = SourceClass()
            all_events.extend(src.fetch())
        except Exception as exc:
            log.error(f"{SourceClass.__name__} failed: {exc}")

    log.info(f"Total candidates before AI filter: {len(all_events)}")

    # AI relevance scoring
    enricher = ClaudeEnricher()
    filtered = enricher.enrich(all_events)

    # De-duplicate
    final = deduplicate(filtered)

    # Write output
    out_dir = os.getenv("OUTPUT_DIR", "output")
    os.makedirs(out_dir, exist_ok=True)
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    out_file = os.path.join(out_dir, f"k-boston-events-{today}.csv")
    write_csv(final, out_file)

    # Also write a "latest" file for easy WordPress import
    latest_file = os.path.join(out_dir, "k-boston-events-latest.csv")
    write_csv(final, latest_file)

    log.info(f"=== Done. {len(final)} events exported ===")
    return final


if __name__ == "__main__":
    main()
