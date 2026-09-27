"""Google Sheets I/O: the watchlist (Artists tab), the human-readable views
("NL Shows", "NL Alerts") and a reader for the old tracker's tabs."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from .model import STATUS_LABELS, State
from .normalize import date_sort_key, fmt_date, is_full_date
from .watchlist import Artist

ARTISTS_TAB = "Artists"
SHOWS_TAB = "NL Shows"
ALERTS_TAB = "NL Alerts"
LEGACY_TABS = ("Schedule NL", "Schedule")

SHOWS_HEADERS = ["Date", "Artists", "Venue / Festival", "City", "Status", "Ticket windows",
                 "Tickets / info", "Source", "First seen", "Updated", "Verified", "ID"]
ALERTS_HEADERS = ["Sent (Amsterdam)", "Delivery", "Kind", "Artist", "Message", "Keys"]
_HEADER_CELLS = {"artist", "artists", "name", "band"}


def open_spreadsheet():
    import gspread
    raw = os.environ.get("GOOGLE_SA_JSON")
    sheet_id = os.environ.get("SHEET_ID")
    if not raw or not sheet_id:
        sys.exit("error: GOOGLE_SA_JSON and SHEET_ID must be set (or use --state FILE --artists-file FILE)")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.exit(f"error: GOOGLE_SA_JSON is not valid JSON: {exc}")
    return gspread.service_account_from_dict(info).open_by_key(sheet_id)


def _cell(row: list[str], i: int) -> str:
    return row[i].strip() if len(row) > i else ""


def read_watchlist(ss, tab: str = ARTISTS_TAB) -> list[Artist]:
    """Artists tab: A = name, B = aliases (comma separated, optional),
    C = note / disambiguation hint (optional). Blank rows are skipped."""
    rows = ss.worksheet(tab).get_all_values()
    out = []
    for i, row in enumerate(rows):
        name = _cell(row, 0)
        if not name or (i == 0 and name.lower() in _HEADER_CELLS):
            continue
        aliases = [a.strip() for a in _cell(row, 1).split(",") if a.strip()]
        out.append(Artist(name=name, aliases=aliases, note=_cell(row, 2)))
    return out


def read_watchlist_file(path: str) -> list[Artist]:
    """Local watchlist: one artist per line, optional '| aliases | note'."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            aliases = [a.strip() for a in (parts[1] if len(parts) > 1 else "").split(",") if a.strip()]
            out.append(Artist(parts[0], aliases, parts[2] if len(parts) > 2 else ""))
    return out


def legacy_rows_to_findings(rows: list[list[str]], nl_only: bool) -> list[dict]:
    """Rows of the old 'Schedule' tabs -> raw findings for a silent import."""
    out = []
    for row in rows:
        artist, date = _cell(row, 0), _cell(row, 1)
        if not artist or artist.lower() == "artist" or not date:
            continue
        country = _cell(row, 4)
        if nl_only and country.lower() not in ("netherlands", "nl", "the netherlands", "nederland"):
            continue
        # the old "On-Sale" column mixed presale / general / past dates: not imported
        out.append({"artist": artist, "date": date, "city": _cell(row, 2), "venue": _cell(row, 3),
                    "country": country or "Netherlands", "event_type": _cell(row, 5),
                    "status": _cell(row, 6), "url": _cell(row, 8)})
    return out


def read_legacy(ss, tabs=LEGACY_TABS) -> list[dict]:
    import gspread
    findings = []
    for tab in tabs:
        try:
            rows = ss.worksheet(tab).get_all_values()
        except gspread.WorksheetNotFound:
            continue
        findings.extend(legacy_rows_to_findings(rows, nl_only=(tab != "Schedule NL")))
    return findings


def _ws(ss, title: str, headers: list[str]):
    import gspread
    try:
        return ss.worksheet(title), False
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=200, cols=len(headers))
        return ws, True


def shows_table(state: State, today) -> list[list[str]]:
    rows = [SHOWS_HEADERS]
    shows = sorted((s for s in state.shows.values() if not s.dismissed),
                   key=lambda s: (date_sort_key(s.date), s.headliner.lower()))
    for s in shows:
        windows = "; ".join(f"{w.label()}: {w.when(today)}" for w in s.sales)
        place = s.festival + (f" ({s.venue})" if s.venue and s.festival else "") if s.festival else s.venue
        rows.append([
            s.date if is_full_date(s.date) else fmt_date(s.date), s.title(), place, s.city,
            STATUS_LABELS.get(s.status, s.status) + (" (muted)" if s.muted else ""), windows,
            s.url, s.source if s.source != s.url else "", s.first_seen, s.updated,
            "yes" if s.verified else "no", s.id,
        ])
    return rows


def write_shows(ss, state: State, today) -> None:
    rows = shows_table(state, today)
    ws, created = _ws(ss, SHOWS_TAB, SHOWS_HEADERS)
    if ws.row_count < len(rows):
        ws.add_rows(len(rows) - ws.row_count)
    ws.update(range_name=f"A1:{chr(64 + len(SHOWS_HEADERS))}{len(rows)}", values=rows,
              value_input_option="RAW")
    if ws.row_count > len(rows):
        ws.batch_clear([f"A{len(rows) + 1}:{chr(64 + len(SHOWS_HEADERS))}{ws.row_count}"])
    if created:
        ws.freeze(rows=1)
        ws.format("A1:L1", {"textFormat": {"bold": True}})


def log_alerts(ss, entries: list[list[str]]) -> None:
    if not entries:
        return
    ws, created = _ws(ss, ALERTS_TAB, ALERTS_HEADERS)
    if created:
        ws.append_row(ALERTS_HEADERS, value_input_option="RAW")
        ws.freeze(rows=1)
        ws.format("A1:F1", {"textFormat": {"bold": True}})
    ws.append_rows(entries, value_input_option="RAW")


def alert_log_row(now: datetime, via: str, msg) -> list[str]:
    return [now.strftime("%Y-%m-%d %H:%M"), via, ",".join(msg.kinds), msg.artist,
            msg.text[:1000], " ".join(msg.keys)[:500]]
