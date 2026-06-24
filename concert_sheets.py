#!/usr/bin/env python3
"""
concert_sheets.py - helper for the concert-tracking scheduled tasks.

Deterministic Google Sheets I/O for two job types (the web research is done by
Claude at runtime). Tab names are configurable, so several routines share this
one script while reading the SAME artist list:

  - a weekly WORLDWIDE run -> "Schedule" (full grouped table with placeholders)
  - a daily NL run         -> "Schedule NL" (only real concerts, no placeholders)

Output tabs are fully REWRITTEN each run: existing rows are read first, merged
with new finds (dedup), then re-sorted and written back, so nothing is lost and
a bad search run can't wipe data. Dedup is add-only.

Country handling: the script applies the country filter (SKIP_COUNTRIES) as a
backstop. Artists with no concerts in the table get a "No Concerts At All" row;
with --no-empty (the NL run) no placeholder rows are written at all.

Auth: GOOGLE_SA_JSON env var = full service-account JSON; SHEET_ID env var =
sheet id. Excluded countries are hardcoded in SKIP_COUNTRIES below.

Usage (defaults in brackets):
  python concert_sheets.py state [--artists Artists] [--festivals Festivals]
  python concert_sheets.py append <events.json>
        [--artists Artists] [--schedule Schedule] [--only ""] [--no-empty]
      --only     comma-sep allowlist of countries (keep ONLY these)
      --no-empty write only artists that have concerts (no placeholder rows)
  python concert_sheets.py append-festivals <festivals.json>
        [--festivals-tab Festivals] [--out "Festival Schedule"]

Event object   : {"artist","date","city","venue","country","event_type","status","on_sale","url"}
Festival object: {"festival","start","end","city","country","status","on_sale","url"}
"""

import argparse
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

PH_NONE_FOUND = "No Concerts At All"        # artist has no concerts in the table

_NAME_HEADER_CELLS = {"artist", "artists", "festival", "festivals", "name"}
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d")

# Countries excluded worldwide: where a Russian passport needs a visa/eVisa AND
# that are outside the EU/Schengen area (which I enter freely as a NL resident).
SKIP_COUNTRIES = [
    "United Kingdom", "United States", "Canada", "Australia", "New Zealand",
    "Japan", "China", "Taiwan", "India", "Ireland",
]

_COUNTRY_ALIASES = {
    "usa": "united states", "us": "united states", "america": "united states",
    "united states of america": "united states",
    "uk": "united kingdom", "great britain": "united kingdom",
    "britain": "united kingdom", "england": "united kingdom",
    "scotland": "united kingdom", "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "uae": "united arab emirates",
    "czechia": "czech republic",
    "holland": "netherlands",
    "republic of korea": "south korea", "korea": "south korea",
    "russian federation": "russia",
}


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


def _norm_country(s):
    s = (s or "").strip().lower().replace(".", "")
    s = " ".join(s.split())
    if s.startswith("the "):
        s = s[4:]
    return _COUNTRY_ALIASES.get(s, s)


def _skip_set():
    return {_norm_country(c) for c in SKIP_COUNTRIES}


def _allow_set(raw):
    return {_norm_country(c) for c in (raw or "").split(",") if c.strip()}


def _country_ok(country, allow, skip):
    norm = _norm_country(country)
    if allow:
        return norm in allow
    return norm not in skip


def _to_iso(s):
    s = (s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _read_names(ss, tab, required):
    """Names from column A, stopping at the FIRST blank row."""
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
        if not c.strip():
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
def cmd_state(artists_tab, festivals_tab):
    ss = _open()
    artists = _read_names(ss, artists_tab, required=True)
    festivals = _read_names(ss, festivals_tab, required=False)
    print(json.dumps({"artists": artists, "festivals": festivals}, ensure_ascii=False))


# --------------------------------------------------------------------------- #
def _read_concerts(ws):
    out = []
    for r in ws.get_all_values():
        artist = _cell(r, 0)
        if not artist or artist.lower() == "artist":
            continue
        iso = _to_iso(_cell(r, 1))
        if not iso:                       # placeholder / blank rows
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


def cmd_append(path, artists_tab, schedule_tab, only_raw, no_empty):
    with open(path, encoding="utf-8") as f:
        incoming = json.load(f)
    if not isinstance(incoming, list):
        sys.exit("error: events file must contain a JSON array")

    ss = _open()
    sched = _get_or_create(ss, schedule_tab, len(SCHEDULE_HEADERS))
    existing = _read_concerts(sched)
    keys = {_concert_key(e) for e in existing}
    skip = _skip_set()
    allow = _allow_set(only_raw)
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
        if not _country_ok(ev["country"], allow, skip):
            skipped += 1
            continue
        k = _concert_key(ev)
        if k in keys:
            skipped += 1
            continue
        keys.add(k)
        existing.append(ev)
        added.append(ev)

    artists_list = _read_names(ss, artists_tab, required=False)
    canon = {}
    for a in artists_list:
        canon.setdefault(a.lower(), a)
    for e in existing:
        canon.setdefault(e["artist"].lower(), e["artist"])

    groups = defaultdict(list)
    for e in existing:
        groups[e["artist"].lower()].append(e)

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
        block = []
        if evs:
            for e in evs:
                block.append([name, e["date"], e["city"], e["venue"], e["country"],
                              e["event_type"], e["status"], e["on_sale"], e["url"],
                              e["added"]])
        elif not no_empty:
            block.append([name, PH_NONE_FOUND, "", "", "", "", "", "", "", ""])
        if block:
            rows.extend(block)
            rows.append([""])
    if rows and rows[-1] == [""]:
        rows.pop()

    sched.clear()
    sched.append_rows(rows, value_input_option="USER_ENTERED")

    new_artists = sorted({canon.get(e["artist"].lower(), e["artist"]) for e in added},
                         key=str.lower)
    note = f"[{schedule_tab}] " + (f"added {len(added)}: " + ", ".join(new_artists)
                                   if added else "0")
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
        {"schedule_tab": schedule_tab, "found": len(incoming), "added": len(added),
         "skipped": skipped, "new_artists": new_artists, "added_events": added},
        ensure_ascii=False,
    ))


# --------------------------------------------------------------------------- #
def _read_festivals(ws):
    out = []
    for r in ws.get_all_values():
        name = _cell(r, 0)
        if not name or name.lower() in ("festival", "festivals"):
            continue
        iso = _to_iso(_cell(r, 1))
        if not iso:
            continue
        out.append({
            "festival": name, "start": iso, "end": _to_iso(_cell(r, 2)) or "",
            "city": _cell(r, 3), "country": _cell(r, 4), "status": _cell(r, 5),
            "on_sale": _cell(r, 6), "url": _cell(r, 7), "added": _cell(r, 8),
        })
    return out


def _festival_key(e):
    return f"{e['festival'].strip().lower()}|{e['start'].strip()}"


def cmd_append_festivals(path, festivals_tab, out_tab):
    with open(path, encoding="utf-8") as f:
        incoming = json.load(f)
    if not isinstance(incoming, list):
        sys.exit("error: festivals file must contain a JSON array")

    ss = _open()
    fs = _get_or_create(ss, out_tab, len(FESTIVAL_HEADERS))
    existing = _read_festivals(fs)
    keys = {_festival_key(e) for e in existing}
    skip = _skip_set()
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
        if _norm_country(ev["country"]) in skip:
            skipped += 1
            continue
        k = _festival_key(ev)
        if k in keys:
            skipped += 1
            continue
        keys.add(k)
        existing.append(ev)
        added.append(ev)

    names = _read_names(ss, festivals_tab, required=False)
    canon = {}
    for n in names:
        canon.setdefault(n.lower(), n)
    for e in existing:
        canon.setdefault(e["festival"].lower(), e["festival"])

    groups = defaultdict(list)
    for e in existing:
        groups[e["festival"].lower()].append(e)

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
        {"out_tab": out_tab, "found": len(incoming), "added": len(added),
         "skipped": skipped, "added_festivals": added},
        ensure_ascii=False,
    ))


# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Concert tracker sheets helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("state")
    s.add_argument("--artists", default=ARTISTS_TAB)
    s.add_argument("--festivals", default=FESTIVALS_INPUT_TAB)

    a = sub.add_parser("append")
    a.add_argument("events")
    a.add_argument("--artists", default=ARTISTS_TAB)
    a.add_argument("--schedule", default=SCHEDULE_TAB)
    a.add_argument("--only", default="", help="comma-sep allowlist of countries")
    a.add_argument("--no-empty", action="store_true",
                   help="write only artists with concerts (no placeholder rows)")

    f = sub.add_parser("append-festivals")
    f.add_argument("file")
    f.add_argument("--festivals-tab", default=FESTIVALS_INPUT_TAB)
    f.add_argument("--out", default=FESTIVAL_OUTPUT_TAB)

    args = p.parse_args()
    if args.cmd == "state":
        cmd_state(args.artists, args.festivals)
    elif args.cmd == "append":
        cmd_append(args.events, args.artists, args.schedule, args.only, args.no_empty)
    elif args.cmd == "append-festivals":
        cmd_append_festivals(args.file, args.festivals_tab, args.out)


if __name__ == "__main__":
    main()