#!/usr/bin/env python3
"""
k-boston-events WordPress Importer
Uploads the latest events CSV to WordPress via:
  Option A: WP REST API  (preferred, no plugins needed beyond The Events Calendar)
  Option B: FTP upload   (fallback – triggers WP-CLI import on server)

Set env vars:
  WP_SITE_URL       – e.g. https://k-boston.org
  WP_USERNAME       – WordPress admin username
  WP_APP_PASSWORD   – Application Password (WP 5.6+)
  WP_CSV_ENDPOINT   – optional custom endpoint (default: /wp-json/tribe/events/v1/import)
  FTP_HOST / FTP_USER / FTP_PASS / FTP_REMOTE_PATH  (for FTP fallback)
"""

import os
import csv
import json
import logging
import ftplib
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

EASTERN = ZoneInfo("America/New_York")

WP_SITE     = os.getenv("WP_SITE_URL", "").rstrip("/")
WP_USER     = os.getenv("WP_USERNAME", "")
WP_PASS     = os.getenv("WP_APP_PASSWORD", "")

TEC_DATE_FMT = "%Y-%m-%d"
TEC_TIME_FMT = "%H:%M:%S"


def load_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def row_to_tec_payload(row: dict) -> dict:
    """Convert CSV row → The Events Calendar REST API payload."""
    def combine_dt(date_str, time_str):
        if not date_str:
            return None
        try:
            dt_str = f"{date_str} {time_str}" if time_str else date_str
            fmt    = "%Y-%m-%d %H:%M:%S" if time_str else "%Y-%m-%d"
            return datetime.strptime(dt_str, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None

    start = combine_dt(row.get("Event Start Date"), row.get("Event Start Time"))
    end   = combine_dt(row.get("Event End Date"),   row.get("Event End Time"))

    payload = {
        "title":       row.get("Event Name", ""),
        "description": row.get("Event Description", ""),
        "excerpt":     row.get("Event Excerpt", ""),
        "start_date":  start,
        "end_date":    end,
        "all_day":     row.get("All Day Event", "FALSE").upper() == "TRUE",
        "timezone":    row.get("Timezone", "America/New_York"),
        "cost":        row.get("Event Cost", ""),
        "url":         row.get("Event Website", ""),
        "image":       row.get("Event Featured Image", ""),
        "status":      "publish",
        "venue": {
            "venue":    row.get("Venue Name", ""),
            "address":  row.get("Venue Address", ""),
            "city":     row.get("Venue City", ""),
            "province": row.get("Venue State Province", ""),
            "zip":      row.get("Venue Zip", ""),
            "country":  row.get("Venue Country", "United States"),
            "phone":    row.get("Venue Phone", ""),
            "url":      row.get("Venue URL", ""),
        },
        "organizer": {
            "organizer": row.get("Organizer Name", ""),
            "phone":     row.get("Organizer Phone", ""),
            "website":   row.get("Organizer Website", ""),
            "email":     row.get("Organizer Email", ""),
        },
        "categories": [{"name": c.strip()} for c in row.get("Event Category", "").split(",") if c.strip()],
        "tags":        [{"name": t.strip()} for t in row.get("Event Tags", "").split(",") if t.strip()],
    }
    return payload


def import_via_rest_api(csv_path: str) -> tuple[int, int]:
    """
    Push events one-by-one via The Events Calendar REST API.
    Returns (success_count, fail_count).
    """
    if not all([WP_SITE, WP_USER, WP_PASS]):
        log.error("WP_SITE_URL, WP_USERNAME, WP_APP_PASSWORD must be set for REST import")
        return 0, 0

    rows = load_csv(csv_path)
    base = f"{WP_SITE}/wp-json/tribe/events/v1/events"
    auth = (WP_USER, WP_PASS)
    ok, fail = 0, 0

    for row in rows:
        payload = row_to_tec_payload(row)
        if not payload.get("title") or not payload.get("start_date"):
            log.warning(f"Skipping row with missing title/date: {row.get('Event Name')}")
            fail += 1
            continue
        try:
            r = requests.post(base, json=payload, auth=auth, timeout=30)
            if r.status_code in (200, 201):
                log.info(f"✓ Imported: {payload['title']}")
                ok += 1
            else:
                log.warning(f"✗ Failed ({r.status_code}): {payload['title']} – {r.text[:200]}")
                fail += 1
        except Exception as exc:
            log.error(f"REST API error: {exc}")
            fail += 1

    return ok, fail


def import_via_ftp_upload(csv_path: str) -> bool:
    """
    Upload CSV to server via FTP. Pair this with a WP-CLI cron on the server:
      wp tec events import --file=/path/to/latest.csv --format=csv
    """
    host      = os.getenv("FTP_HOST", "")
    user      = os.getenv("FTP_USER", "")
    password  = os.getenv("FTP_PASS", "")
    remote    = os.getenv("FTP_REMOTE_PATH", "/public_html/wp-content/uploads/tec-import/k-boston-events-latest.csv")

    if not all([host, user, password]):
        log.error("FTP_HOST, FTP_USER, FTP_PASS not set")
        return False

    try:
        with ftplib.FTP(host) as ftp:
            ftp.login(user, password)
            with open(csv_path, "rb") as f:
                ftp.storbinary(f"STOR {remote}", f)
        log.info(f"FTP upload successful → {remote}")
        return True
    except Exception as exc:
        log.error(f"FTP upload failed: {exc}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import events CSV into WordPress")
    parser.add_argument("--csv", default="output/k-boston-events-latest.csv", help="CSV file path")
    parser.add_argument("--method", choices=["rest", "ftp"], default="rest", help="Import method")
    args = parser.parse_args()

    if args.method == "rest":
        ok, fail = import_via_rest_api(args.csv)
        log.info(f"REST import done: {ok} ok, {fail} failed")
    else:
        success = import_via_ftp_upload(args.csv)
        log.info("FTP upload " + ("succeeded" if success else "failed"))


if __name__ == "__main__":
    main()
