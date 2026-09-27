"""Ticketmaster Discovery API source (optional fast lane).

Ticketmaster sells most big NL shows (Ziggo Dome, AFAS Live, Johan Cruijff
ArenA, GelreDome, MOJO / Live Nation). Its events appear the moment a show is
announced, with exact presale and general-sale times - so polling it every hour
catches new arena shows and gives precise reminders. Needs a free API key from
developer.ticketmaster.com (5000 calls/day). The output is ordinary findings,
fed through the same engine as the LLM research.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import requests

from .normalize import parse_when, when_dt
from .watchlist import Watchlist

API = "https://app.ticketmaster.com/discovery/v2"
_REFRESH_DAYS = 30          # re-resolve attraction IDs monthly (new tours can add entries)


class TicketmasterError(RuntimeError):
    pass


class Ticketmaster:
    def __init__(self, api_key: str, session=None, pause: float = 0.25, sleep=time.sleep):
        self.key = api_key
        self.session = session or requests.Session()
        self.pause = pause
        self._sleep = sleep
        self.calls = 0

    def _get(self, path: str, **params) -> dict:
        params["apikey"] = self.key
        for attempt in range(3):
            self._sleep(self.pause)                     # stay under 5 requests / second
            self.calls += 1
            try:
                resp = self.session.get(f"{API}/{path}", params=params, timeout=20)
            except requests.RequestException as exc:
                err = str(exc).replace(self.key, "<key>")
                if attempt == 2:
                    raise TicketmasterError(f"network error: {err[:200]}") from None
                self._sleep(2 ** attempt)
                continue
            if resp.status_code == 429:
                self._sleep(2 + 2 ** attempt)
                continue
            if resp.status_code != 200:
                raise TicketmasterError(f"HTTP {resp.status_code} for {path}: {resp.text[:200]}"
                                        .replace(self.key, "<key>"))
            return resp.json()
        raise TicketmasterError(f"rate limited on {path}")

    def attraction_ids(self, artist: str, wl: Watchlist) -> list[str]:
        """Music attractions whose name resolves to this watchlist artist."""
        data = self._get("attractions.json", keyword=artist, classificationName="music", size=20)
        found = []
        for att in (data.get("_embedded") or {}).get("attractions", []):
            if wl.resolve(att.get("name", "")) == artist:
                upcoming = (att.get("upcomingEvents") or {}).get("_total", 0)
                found.append((upcoming, att["id"]))
        return [aid for _n, aid in sorted(found, reverse=True)[:3]]

    def nl_events(self, attraction_id: str) -> list[dict]:
        data = self._get("events.json", attractionId=attraction_id, countryCode="NL", size=100,
                         sort="date,asc", locale="*")
        return (data.get("_embedded") or {}).get("events", [])


def _status(event: dict, sales: list[dict], now: datetime) -> str:
    code = ((event.get("dates") or {}).get("status") or {}).get("code", "").lower()
    if code in ("canceled", "cancelled"):
        return "cancelled"
    if code in ("postponed", "rescheduled"):
        return "postponed"
    general = next((s for s in sales if s["type"] == "general"), None)
    start = when_dt(general["start"]) if general and general.get("start") else None
    presale_open = any(s["type"] != "general" and (st := when_dt(s.get("start", ""))) and st <= now
                       and (not s.get("end") or (when_dt(s["end"]) or now) > now) for s in sales)
    if start and start > now:
        return "presale" if presale_open else "announced"
    if code == "onsale":
        return "on_sale"
    return ""                # "offsale" is ambiguous (sold out or sales closed)


def event_to_finding(event: dict, wl: Watchlist, now: datetime) -> dict | None:
    emb = event.get("_embedded") or {}
    names = [a.get("name", "") for a in emb.get("attractions", [])]
    artists = []
    for n in names:
        r = wl.resolve(n)
        if r and r not in artists:
            artists.append(r)
    if not artists:
        return None
    start = (event.get("dates") or {}).get("start") or {}
    date = start.get("localDate", "")
    if not date or start.get("dateTBA") or start.get("dateTBD"):
        return None
    venue = (emb.get("venues") or [{}])[0]
    sales_raw = event.get("sales") or {}
    sales = []
    for p in sales_raw.get("presales") or []:
        s = parse_when(p.get("startDateTime", ""))
        if s:
            sales.append({"type": p.get("name") or "presale", "name": p.get("name", ""), "start": s,
                          "end": parse_when(p.get("endDateTime", ""))})
    public = sales_raw.get("public") or {}
    if public.get("startDateTime") and not public.get("startTBD") and not public.get("startTBA"):
        sales.append({"type": "general", "start": parse_when(public["startDateTime"])})
    lineup = " + ".join(n for n in names if n) if len(names) > 1 else ""
    return {
        "artist": artists[0], "artists": artists, "lineup": lineup, "date": date,
        "venue": venue.get("name", ""), "city": (venue.get("city") or {}).get("name", ""),
        "country": "Netherlands", "status": _status(event, sales, now), "sales": sales,
        "url": event.get("url", ""), "source": event.get("url", ""),
    }


def poll(tm: Ticketmaster, wl: Watchlist, cache: dict, now: datetime) -> tuple[list[dict], list[str]]:
    """Findings for every watchlist artist's NL Ticketmaster events.
    `cache` (persisted in the state) remembers attraction IDs between runs."""
    findings, errors = [], []
    ids = cache.setdefault("attractions", {})
    refresh_before = (now - timedelta(days=_REFRESH_DAYS)).isoformat()
    for artist in wl.names:
        entry = ids.get(artist)
        try:
            if not entry or entry.get("at", "") < refresh_before:
                entry = {"ids": tm.attraction_ids(artist, wl), "at": now.isoformat(timespec="seconds")}
                ids[artist] = entry
            seen = set()
            for aid in entry["ids"]:
                for ev in tm.nl_events(aid):
                    if ev.get("id") in seen:
                        continue
                    seen.add(ev.get("id"))
                    f = event_to_finding(ev, wl, now)
                    if f:
                        findings.append(f)
        except TicketmasterError as exc:
            errors.append(f"{artist}: {exc}")
            if "HTTP 401" in str(exc) or "HTTP 403" in str(exc):
                break                     # bad key / blocked: no point in continuing
    return findings, errors
