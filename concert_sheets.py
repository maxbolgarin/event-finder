#!/usr/bin/env python3
"""
concert_sheets.py - helper for the concert-tracking scheduled task.

Handles only the deterministic Google Sheets I/O. The web research (finding new
concert announcements) is done by Claude at runtime via web search; this script
does NOT search the web.

Auth (cloud-friendly): reads the FULL service-account JSON from the
GOOGLE_SA_JSON environment variable, so nothing sensitive is committed to git.
Target sheet: the SHEET_ID environment variable (the long id in the sheet URL:
https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit).

Optional: SKIP_COUNTRIES = comma-separated country names to drop on input,
e.g. "UK,United Kingdom,England,Scotland,Wales,Great Britain".

Tabs:
  "Artists"   - column A: artist names, one per row (optional header). REQUIRED.
  "Festivals" - column A: festival names to track by name (OPTIONAL tab).
  "Schedule"  - auto-created; appended to. Columns: see SCHEDULE_HEADERS.
                A header row is added/repaired automatically if missing.
  "Log"       - auto-created; one row per run with counts.

Usage:
  python concert_sheets.py state
      -> prints JSON: {"artists":[...], "festivals":[...], "existing_keys":[...]}

  python concert_sheets.py append events.json
      -> events.json: JSON array of event objects, e.g.
         [{"artist","date","city","venue","country","event_type",
           "status","on_sale","url"}]
         date / on_sale = YYYY-MM-DD ; event_type = Solo | Gig | Festival ;
         status = Announced | Presale | On sale | Sold out.
         Dump EVERYTHING found (including shows not yet on sale); the script
         dedups (by artist+date+venue), appends only new rows, writes a Log
         row, and prints a summary.
"""

import json
import os
import sys
from datetime import datetime, timezone

import gspread

ARTISTS_TAB = "Artists"
FESTIVALS_TAB = "Festivals"
SCHEDULE_TAB = "Schedule"
LOG_TAB = "Log"

SCHEDULE_HEADERS = ["Artist", "Date", "City", "Venue", "Country",
                    "Event Type", "Status", "On-Sale", "URL", "Added"]
LOG_HEADERS = ["Run (UTC)", "Artists", "Festivals", "Found", "Added", "Skipped"]

_NAME_HEADER_CELLS = {"artist", "artists", "festival", "festivals", "name"}


def _client():
    raw = os.environ.get("GOOGLE_SA_JSON")
    if not raw:
        sys.exit("error: GOOGLE_SA_JSON env var is not set")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"error: GOOGLE_SA_JSON is not valid JSON: {exc}")
    return gspread.service_account_from_dict(info)


def _open():
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        sys.exit("error: SHEET_ID env var is not set")
    return _client().open_by_key(sheet_id)


def _skip_countries():
    raw = os.environ.get("SKIP_COUNTRIES", "")
    return {c.strip().lower() for c in raw.split(",") if c.strip()}


def _read_names(ss, tab, required):
    try:
        ws = ss.worksheet(tab)
    except gspread.WorksheetNotFound:
        if required:
            sys.exit(f"error: tab '{tab}' not found")
        return []
    names = [c.strip() for c in ws.col_values(1) if c.strip()]
    if names and names[0].lower() in _NAME_HEADER_CELLS:
        names = names[1:]
    return names


def _key(artist, d, venue):
    return f"{artist.strip().lower()}|{d.strip()}|{venue.strip().lower()}"


def _ensure_tab(ss, title, headers):
    """Return the worksheet, creating it or repairing a missing header row."""
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=2000, cols=len(headers))
        ws.append_row(headers, value_input_option="USER_ENTERED")
        return ws
    values = ws.get_all_values()
    if not values:
        ws.append_row(headers, value_input_option="USER_ENTERED")
    elif not values[0] or values[0][0].strip().lower() != headers[0].lower():
        # data present but first row is not the header -> insert one on top
        ws.insert_row(headers, index=1, value_input_option="USER_ENTERED")
    return ws


def _existing_keys(sched_ws):
    """All dedup keys already in Schedule, header row excluded if present."""
    keys = set()
    for r in sched_ws.get_all_values():
        if len(r) >= 4 and r[0].strip() and r[0].strip().lower() != "artist":
            keys.add(_key(r[0], r[1], r[3]))
    return keys


def cmd_state():
    ss = _open()
    artists = _read_names(ss, ARTISTS_TAB, required=True)
    festivals = _read_names(ss, FESTIVALS_TAB, required=False)
    sched = _ensure_tab(ss, SCHEDULE_TAB, SCHEDULE_HEADERS)
    keys = sorted(_existing_keys(sched))
    print(json.dumps(
        {"artists": artists, "festivals": festivals, "existing_keys": keys},
        ensure_ascii=False,
    ))


def cmd_append(path):
    with open(path, encoding="utf-8") as f:
        events = json.load(f)
    if not isinstance(events, list):
        sys.exit("error: events file must contain a JSON array")

    ss = _open()
    sched = _ensure_tab(ss, SCHEDULE_TAB, SCHEDULE_HEADERS)
    existing = _existing_keys(sched)
    skip = _skip_countries()

    new_rows = []
    added_events = []
    skipped = 0
    today = datetime.now(timezone.utc).date().isoformat()

    for e in events:
        artist = str(e.get("artist", "")).strip()
        d = str(e.get("date", "")).strip()
        venue = str(e.get("venue", "")).strip()
        country = str(e.get("country", "")).strip()
        if not artist or not d:
            skipped += 1
            continue
        if skip and country.lower() in skip:
            skipped += 1
            continue
        k = _key(artist, d, venue)
        if k in existing:
            skipped += 1
            continue
        existing.add(k)
        new_rows.append([
            artist,
            d,
            str(e.get("city", "")).strip(),
            venue,
            country,
            str(e.get("event_type", e.get("type", ""))).strip(),
            str(e.get("status", "")).strip(),
            str(e.get("on_sale", e.get("onsale", ""))).strip(),
            str(e.get("url", "")).strip(),
            today,
        ])
        added_events.append(e)

    if new_rows:
        sched.append_rows(new_rows, value_input_option="USER_ENTERED")

    # write one Log row per run
    artists = _read_names(ss, ARTISTS_TAB, required=False)
    festivals = _read_names(ss, FESTIVALS_TAB, required=False)
    log = _ensure_tab(ss, LOG_TAB, LOG_HEADERS)
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.append_row(
        [run_ts, len(artists), len(festivals), len(events), len(new_rows), skipped],
        value_input_option="USER_ENTERED",
    )

    print(json.dumps(
        {"found": len(events), "added": len(new_rows), "skipped": skipped,
         "added_events": added_events},
        ensure_ascii=False,
    ))


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: concert_sheets.py [state | append <events.json>]")
    cmd = sys.argv[1]
    if cmd == "state":
        cmd_state()
    elif cmd == "append":
        if len(sys.argv) < 3:
            sys.exit("usage: concert_sheets.py append <events.json>")
        cmd_append(sys.argv[2])
    else:
        sys.exit(f"error: unknown command '{cmd}'")


if __name__ == "__main__":
    main()