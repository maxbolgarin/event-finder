#!/usr/bin/env python3
"""
concert_sheets.py - helper for the concert-tracking scheduled task.

Two independent jobs, both deterministic Google Sheets I/O (the web research is
done by Claude at runtime):

  ARTISTS  -> "Schedule" tab: each artist's concerts, grouped alphabetically by
              artist, sorted by date within the artist, a blank row between
              artists, and an "Artist | No concerts" row for artists with none.
  FESTIVALS-> "Festival Schedule" tab: info about the festivals named in the
              "Festivals" input tab, independent of any lineup.

Both output tabs are fully REWRITTEN each run: existing rows are read first,
merged with new finds (dedup), then re-sorted and written back, so nothing is
lost and a bad search run can't wipe data. Dedup is add-only (existing rows are
never removed or overwritten).

Auth (cloud-friendly): GOOGLE_SA_JSON env var holds the full service-account
JSON. SHEET_ID env var holds the sheet id. Optional SKIP_COUNTRIES =
comma-separated country names to drop on input (e.g. "UK,United Kingdom").

Tabs:
  "Artists"          - INPUT, column A: artist names (optional header). REQUIRED.
  "Festivals"        - INPUT, column A: festival names (optional tab).
  "Schedule"         - OUTPUT, artist concerts. Auto-managed.
  "Festival Schedule"- OUTPUT, festival info. Auto-managed.
  "Log"              - one row per run.

Usage:
  python concert_sheets.py state
      -> {"artists":[...], "festivals":[...]}
  python concert_sheets.py append events.json
      -> rewrites "Schedule"; events = array of objects
         {"artist","date","city","venue","country","event_type","status","on_sale","url"}
  python concert_sheets.py append-festivals festivals.json
      -> rewrites "Festival Schedule"; festivals = array of objects
         {"festival","start","end","city","country","status","on_sale","url"}
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import gspread

ARTISTS_TAB = "Artists"
FESTIVALS_INPUT_TAB = "Festivals"
SCHEDULE_TAB = "Schedule"
FESTIVAL_OUTPUT_TAB = "Festival Schedule"
LOG_TAB = "Log"

SCHEDULE_HEADERS = ["Artist", "Date", "City", "Venue", "Country",
                    "Event Type", "Status", "On-Sale", "URL", "Added"]
FESTIVAL_HEADERS = ["Festival", "Start", "End", "City", "Country",
                    "Status", "On-Sale", "URL", "Added"]
LOG_HEADERS = ["Run (UTC)", "Artists", "Festivals", "Found", "Added",
               "Skipped", "New records (artists)"]

_NAME_HEADER_CELLS = {"artist", "artists", "festival", "festivals", "name"}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
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


def _to_iso(s):
    """Normalise a date cell to ISO YYYY-MM-DD, or None if it isn't a date."""
    s = (s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _read_names(ss, tab, required):
    """Names from column A, stopping at the FIRST blank row.

    A blank row acts as a separator: everything below it (e.g. artists you have
    already bought tickets for) stays in the sheet but is excluded from
    monitoring.
    """
    try:
        ws = ss.worksheet(tab)
    except gspread.WorksheetNotFound:
        if required:
            sys.exit(f"error: tab '{tab}' not found")
        return []
    col = ws.col_values(1)
    if col and col[0].strip().lower() in _NAME_HEADER_CELLS:
        col = col[1:]
    names = []
    for c in col:
        if not c.strip():          # first blank row -> stop
            break
        names.append(c.strip())
    return names


def _get_or_create(ss, title, ncols):
    try:
        return ss.worksheet(title)
    except gspread.WorksheetNotFound:
        return ss.add_worksheet(title=title, rows=2000, cols=ncols)


def _cell(row, i):
    return row[i].strip() if len(row) > i else ""


def _today():
    return datetime.now(timezone.utc).date().isoformat()


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
def cmd_state():
    ss = _open()
    artists = _read_names(ss, ARTISTS_TAB, required=True)
    festivals = _read_names(ss, FESTIVALS_INPUT_TAB, required=False)
    print(json.dumps({"artists": artists, "festivals": festivals}, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# concerts  ->  Schedule
# --------------------------------------------------------------------------- #
def _read_concerts(ws):
    out = []
    for r in ws.get_all_values():
        artist = _cell(r, 0)
        if not artist or artist.lower() == "artist":
            continue
        iso = _to_iso(_cell(r, 1))
        if not iso:               # "No concerts" / blank rows
            continue
        out.append({
            "artist": artist, "date": iso, "city": _cell(r, 2),
            "venue": _cell(r, 3), "country": _cell(r, 4),
            "event_type": _cell(r, 5), "status": _cell(r, 6),
            "on_sale": _cell(r, 7), "url": _cell(r, 8), "added": _cell(r, 9),
        })
    return out


def _concert_key(e):
    return f"{e['artist'].strip().lower()}|{e['date'].strip()}|{e['venue'].strip().lower()}"


def cmd_append(path):
    with open(path, encoding="utf-8") as f:
        incoming = json.load(f)
    if not isinstance(incoming, list):
        sys.exit("error: events file must contain a JSON array")

    ss = _open()
    sched = _get_or_create(ss, SCHEDULE_TAB, len(SCHEDULE_HEADERS))
    existing = _read_concerts(sched)
    keys = {_concert_key(e) for e in existing}
    skip = _skip_countries()
    today = _today()

    added = []
    skipped = 0
    for e in incoming:
        ev = {
            "artist": str(e.get("artist", "")).strip(),
            "date": _to_iso(str(e.get("date", ""))) or "",
            "city": str(e.get("city", "")).strip(),
            "venue": str(e.get("venue", "")).strip(),
            "country": str(e.get("country", "")).strip(),
            "event_type": str(e.get("event_type", e.get("type", ""))).strip(),
            "status": str(e.get("status", "")).strip(),
            "on_sale": str(e.get("on_sale", e.get("onsale", ""))).strip(),
            "url": str(e.get("url", "")).strip(),
            "added": today,
        }
        if not ev["artist"] or not ev["date"]:
            skipped += 1
            continue
        if skip and ev["country"].lower() in skip:
            skipped += 1
            continue
        k = _concert_key(ev)
        if k in keys:
            skipped += 1
            continue
        keys.add(k)
        existing.append(ev)
        added.append(ev)

    # canonical artist names (Artists tab spelling wins)
    artists_list = _read_names(ss, ARTISTS_TAB, required=False)
    canon = {}
    for a in artists_list:
        canon.setdefault(a.lower(), a)
    for e in existing:
        canon.setdefault(e["artist"].lower(), e["artist"])

    groups = defaultdict(list)
    for e in existing:
        groups[e["artist"].lower()].append(e)

    # keep the Artists-tab order; append any event-only artists at the end
    order, seen = [], set()
    for a in artists_list:
        k = a.lower()
        if k not in seen:
            seen.add(k)
            order.append(k)
    for k in sorted(groups):
        if k not in seen:
            seen.add(k)
            order.append(k)

    rows = [SCHEDULE_HEADERS]
    for k in order:
        name = canon.get(k, k)
        evs = sorted(groups.get(k, []), key=lambda e: e["date"])
        if evs:
            for e in evs:
                rows.append([name, e["date"], e["city"], e["venue"], e["country"],
                             e["event_type"], e["status"], e["on_sale"], e["url"],
                             e["added"]])
        else:
            rows.append([name, "No concerts", "", "", "", "", "", "", "", ""])
        rows.append([""])          # blank separator
    if rows and rows[-1] == [""]:
        rows.pop()

    sched.clear()
    sched.append_rows(rows, value_input_option="USER_ENTERED")

    # Log
    new_artists = sorted({canon.get(e["artist"].lower(), e["artist"]) for e in added},
                         key=str.lower)
    note = f"added {len(added)}: " + ", ".join(new_artists) if added else "0"
    log = _get_or_create(ss, LOG_TAB, len(LOG_HEADERS))
    if not log.get_all_values():
        log.append_row(LOG_HEADERS, value_input_option="USER_ENTERED")
    log.append_row(
        [datetime.now(timezone.utc).isoformat(timespec="seconds"),
         len(artists_list), len(_read_names(ss, FESTIVALS_INPUT_TAB, required=False)),
         len(incoming), len(added), skipped, note],
        value_input_option="USER_ENTERED",
    )

    print(json.dumps(
        {"found": len(incoming), "added": len(added), "skipped": skipped,
         "new_artists": new_artists, "added_events": added},
        ensure_ascii=False,
    ))


# --------------------------------------------------------------------------- #
# festivals  ->  Festival Schedule
# --------------------------------------------------------------------------- #
def _read_festivals(ws):
    out = []
    for r in ws.get_all_values():
        name = _cell(r, 0)
        if not name or name.lower() in ("festival", "festivals"):
            continue
        iso = _to_iso(_cell(r, 1))
        if not iso:                # "No info found" / blank rows
            continue
        out.append({
            "festival": name, "start": iso, "end": _to_iso(_cell(r, 2)) or "",
            "city": _cell(r, 3), "country": _cell(r, 4), "status": _cell(r, 5),
            "on_sale": _cell(r, 6), "url": _cell(r, 7), "added": _cell(r, 8),
        })
    return out


def _festival_key(e):
    return f"{e['festival'].strip().lower()}|{e['start'].strip()}"


def cmd_append_festivals(path):
    with open(path, encoding="utf-8") as f:
        incoming = json.load(f)
    if not isinstance(incoming, list):
        sys.exit("error: festivals file must contain a JSON array")

    ss = _open()
    fs = _get_or_create(ss, FESTIVAL_OUTPUT_TAB, len(FESTIVAL_HEADERS))
    existing = _read_festivals(fs)
    keys = {_festival_key(e) for e in existing}
    skip = _skip_countries()
    today = _today()

    added = []
    skipped = 0
    for e in incoming:
        start = _to_iso(str(e.get("start", e.get("date", "")))) or ""
        ev = {
            "festival": str(e.get("festival", e.get("name", ""))).strip(),
            "start": start,
            "end": _to_iso(str(e.get("end", ""))) or start,
            "city": str(e.get("city", "")).strip(),
            "country": str(e.get("country", "")).strip(),
            "status": str(e.get("status", "")).strip(),
            "on_sale": str(e.get("on_sale", e.get("onsale", ""))).strip(),
            "url": str(e.get("url", "")).strip(),
            "added": today,
        }
        if not ev["festival"] or not ev["start"]:
            skipped += 1
            continue
        if skip and ev["country"].lower() in skip:
            skipped += 1
            continue
        k = _festival_key(ev)
        if k in keys:
            skipped += 1
            continue
        keys.add(k)
        existing.append(ev)
        added.append(ev)

    names = _read_names(ss, FESTIVALS_INPUT_TAB, required=False)
    canon = {}
    for n in names:
        canon.setdefault(n.lower(), n)
    for e in existing:
        canon.setdefault(e["festival"].lower(), e["festival"])

    groups = defaultdict(list)
    for e in existing:
        groups[e["festival"].lower()].append(e)

    # keep the Festivals-tab order; append any extra festivals at the end
    order, seen = [], set()
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k)
            order.append(k)
    for k in sorted(groups):
        if k not in seen:
            seen.add(k)
            order.append(k)

    rows = [FESTIVAL_HEADERS]
    for k in order:
        name = canon.get(k, k)
        evs = sorted(groups.get(k, []), key=lambda e: e["start"])
        if evs:
            for e in evs:
                rows.append([name, e["start"], e["end"], e["city"], e["country"],
                             e["status"], e["on_sale"], e["url"], e["added"]])
        else:
            rows.append([name, "No info found", "", "", "", "", "", "", ""])

    fs.clear()
    fs.append_rows(rows, value_input_option="USER_ENTERED")

    print(json.dumps(
        {"found": len(incoming), "added": len(added), "skipped": skipped,
         "added_festivals": added},
        ensure_ascii=False,
    ))


# --------------------------------------------------------------------------- #
def main():
    if len(sys.argv) < 2:
        sys.exit("usage: concert_sheets.py [state | append <f> | append-festivals <f>]")
    cmd = sys.argv[1]
    if cmd == "state":
        cmd_state()
    elif cmd == "append":
        if len(sys.argv) < 3:
            sys.exit("usage: concert_sheets.py append <events.json>")
        cmd_append(sys.argv[2])
    elif cmd == "append-festivals":
        if len(sys.argv) < 3:
            sys.exit("usage: concert_sheets.py append-festivals <festivals.json>")
        cmd_append_festivals(sys.argv[2])
    else:
        sys.exit(f"error: unknown command '{cmd}'")


if __name__ == "__main__":
    main()