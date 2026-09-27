"""The research brief handed to the LLM at the start of each run.

It decides *what* to research today (rotation + hot artists + sweeps) and tells
the researcher everything that is already known, with stable IDs, so it can
report updates on known shows instead of "discovering" them again.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .catalog import FESTIVALS
from .model import STATUS_LABELS, State
from .normalize import date_sort_key, fmt_date, fmt_when, is_past, when_dt
from .watchlist import Watchlist

_NL_MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
              "september", "oktober", "november", "december"]

SWEEPS = [
    "nieuwe concerten aangekondigd {month_nl} {year}",
    "Ziggo Dome nieuw concert aangekondigd voorverkoop",
    "AFAS Live nieuwe concerten aangekondigd {year}",
    "Live Nation Nederland aangekondigd tickets voorverkoop {year}",
    "MOJO concerts nieuw aangekondigd {year}",
    "European tour {next_year} announced Amsterdam presale",
    "tour {next_year} Amsterdam Ziggo Dome AFAS Live presale registration",
    "TivoliVredenburg 013 Paradiso Melkweg nieuw concert aangekondigd",
    "festival line-up {next_year} Nederland nieuwe namen bekend",
    "Festileaks nieuwe namen {next_year}",
    "presale registration Europe tour {next_year} sign up",
    "hardcore festival {next_year} line-up aankondiging Q-dance",
]

OUTPUT_EXAMPLE = """{
  "checked": ["Muse", "Placebo"],
  "shows": [
    {"id": "muse-2026-11-29", "artist": "Muse", "date": "2026-11-29",
     "venue": "Ziggo Dome", "city": "Amsterdam", "status": "presale",
     "sales": [
       {"type": "presale", "name": "Muse fan presale", "start": "2026-10-01T10:00"},
       {"type": "general", "start": "2026-10-03T10:00"}],
     "url": "https://www.ticketmaster.nl/...", "source": "https://www.3voor12.nl/..."},
    {"artist": "Architects", "lineup": "Architects + Loathe", "date": "2027-06-19",
     "festival": "Pinkpop", "venue": "Megaland", "city": "Landgraaf", "status": "on_sale",
     "url": "https://www.pinkpop.nl/...", "source": "https://www.pinkpop.nl/..."}
  ],
  "news": [
    {"artist": "Radiohead", "type": "registration",
     "text": "Presale registration for the 2027 European tour is open until Fri 3 Oct 12:00; NL dates TBA",
     "url": "https://www.radiohead.com/..."}
  ],
  "dismiss": [{"id": "paramore-2027-10-05", "reason": "no official source; tour2027.com is a spam site"}]
}"""


def hot_artists(state: State, wl: Watchlist, now: datetime, limit: int = 8) -> list[tuple[str, str]]:
    """Artists whose shows are in a ticket phase that changes fast: re-check daily."""
    today = now.date()
    scored: dict[str, tuple[float, str]] = {}

    def bump(name: str, score: float, why: str):
        if name in wl.names and (name not in scored or score < scored[name][0]):
            scored[name] = (score, why)

    for s in state.shows.values():
        if s.dismissed or s.muted or is_past(s.date, today):
            continue
        for w in s.sales:
            start, end = when_dt(w.start), when_dt(w.end)
            if start and now - timedelta(days=1) <= start <= now + timedelta(days=10):
                bump(s.headliner, (start - now).total_seconds() / 3600,
                     f"{w.label()} {fmt_when(w.start, today)}")
            if w.kind in ("registration", "lottery") and end and (not start or start <= now) and now <= end:
                bump(s.headliner, 0, f"{w.label()} open until {fmt_when(w.end, today)}")
        if (s.origin not in ("seed", "legacy") and s.status == "announced" and not s.sales
                and s.first_seen >= (today - timedelta(days=4)).isoformat()):
            bump(s.headliner, 200, f"announced {s.first_seen}, ticket sale date still unknown")
        if s.status in ("registration", "presale") and not s.sales:
            bump(s.headliner, 250, f"status '{STATUS_LABELS[s.status]}' without known dates")
    ordered = sorted(scored.items(), key=lambda kv: kv[1][0])
    return [(name, why) for name, (_s, why) in ordered[:limit]]


def pick_batch(state: State, wl: Watchlist, now: datetime, size: int,
               exclude: set[str] = frozenset()) -> list[str]:
    """Least-recently-checked artists first (never checked = first of all)."""
    rest = [n for n in wl.names if n not in exclude]
    rest.sort(key=lambda n: (state.artists.get(n, {}).get("last_checked", ""), wl.order(n)))
    return rest[:size]


def _sweeps(now: datetime, n: int) -> list[str]:
    start = (now.timetuple().tm_yday * n) % len(SWEEPS)
    fill = {"month_nl": _NL_MONTHS[now.month - 1], "year": now.year, "next_year": now.year + 1}
    return [SWEEPS[(start + i) % len(SWEEPS)].format(**fill) for i in range(n)]


def _festival_checks(state: State, now: datetime, n: int) -> list[tuple[str, list[str]]]:
    names = list(FESTIVALS)
    start = (now.timetuple().tm_yday * n) % len(names)
    picked = [names[(start + i) % len(names)] for i in range(n)]
    out = []
    for fest in picked:
        acts = sorted({a for s in state.shows.values() if s.festival == fest and not s.dismissed
                       for a in s.artists})
        out.append((fest, acts))
    return out


def build(state: State, wl: Watchlist, now: datetime, batch_size: int = 15,
          sweeps: int = 3, festivals: int = 3) -> tuple[str, dict]:
    today = now.date()
    hot = hot_artists(state, wl, now)
    hot_names = {n for n, _ in hot}
    batch = pick_batch(state, wl, now, batch_size, exclude=hot_names)
    upcoming = sorted((s for s in state.shows.values() if not s.dismissed and not is_past(s.date, today)),
                      key=lambda s: (date_sort_key(s.date), s.headliner))
    horizon = now + timedelta(days=14)
    soon = sum(1 for s in upcoming for w in s.sales
               if (dt := when_dt(w.start)) and now - timedelta(hours=12) <= dt <= horizon)

    L = [f"# NL concert tracker - research brief for {now.strftime('%a %-d %b %Y %H:%M')} (Amsterdam)",
         "",
         f"Watchlist: {len(wl.names)} artists. Known upcoming NL shows: {len(upcoming)}. "
         f"Ticket windows in the next 14 days: {soon}.",
         "",
         "## 1. Artists to research this run"]
    if hot:
        L.append("HOT - ticket window soon / just announced (always re-check):")
        L += [f"- {n} - {why}" for n, why in hot]
    L.append(f"ROTATION ({len(batch)}, least recently checked first):")
    for n in batch:
        last = state.artists.get(n, {}).get("last_checked") or "never"
        art = wl.get(n)
        hint = f" - note: {art.note}" if art and art.note else ""
        aka = f" (a.k.a. {', '.join(art.aliases)})" if art and art.aliases else ""
        L.append(f"- {n}{aka} - last checked {last}{hint}")
    L += ["", "## 2. Sweep searches (catch surprise announcements for ANY watchlist artist)"]
    L += [f'- "{q}"' for q in _sweeps(now, sweeps)]
    L += ["", "## 3. Festival line-up checks (report every watchlist artist you find on the bill)"]
    for fest, acts in _festival_checks(state, now, festivals):
        known = f" - already known: {', '.join(acts)}" if acts else ""
        L.append(f"- {fest} {today.year if today.month <= 8 else today.year + 1}{known}")

    L += ["", "## Known upcoming NL shows",
          "Re-report a known show only with its ID; the script works out what changed.",
          "`?` = unverified (stored by the old tracker / from a weak source): confirm it with a "
          "trusted URL or dismiss it.",
          "ID | date | bill | venue, city | status | ticket windows"]
    for s in upcoming:
        windows = "; ".join(f"{w.label()}: {w.when(today)}" for w in s.sales) or "-"
        mark = "" if s.verified else "? "
        L.append(f"- {mark}{s.id} | {fmt_date(s.date)} | {s.title()} | {s.place() or '?'} | "
                 f"{STATUS_LABELS.get(s.status, s.status)} | {windows}")
    if not upcoming:
        L.append("- (none yet)")

    recent = [n for n in state.news if n.get("date", "") >= (today - timedelta(days=30)).isoformat()]
    if recent:
        L += ["", "## Recent news, already known (don't repeat; report only real new developments)"]
        L += [f"- {n['date']} {n['artist']}: {n['text']}" for n in recent[-20:]]
    dismissed = [s for s in state.shows.values() if s.dismissed and not is_past(s.date, today)]
    if dismissed:
        L += ["", "## Dismissed as bogus (ignore unless an official source confirms)"]
        L += [f"- {s.id}: {s.dismissed}" for s in dismissed]

    L += ["", "## Full watchlist (recognise these names in sweep / festival results)",
          ", ".join(wl.names), "",
          "## Output: write findings.json exactly in this shape",
          "- `checked`: every artist you actually researched this run.",
          "- `shows`: EVERY Netherlands show you found for them - known or new - with the latest "
          "status and all ticket windows, one entry per date (a two-night run = two entries). Use "
          "the watchlist spelling for `artist`; put the full bill in `lineup` when there are other "
          "acts; set `festival` for festival appearances (date YYYY or YYYY-MM if the day is unknown).",
          "- `status`: announced | registration | presale | on_sale | low | sold_out | cancelled | postponed",
          "- `sales[].type`: registration (sign-up / verified fan) | lottery (ballot / unique-code sale) | "
          "presale | general. Times are Amsterdam local: YYYY-MM-DDTHH:MM (date only if no time).",
          "- `url`: the official ticket/info page; `source`: where you read it. Real URLs only.",
          "- `news`: NEW, actionable NL-relevant developments that are not a dated NL show yet (tour "
          "announced with NL dates TBA, a registration or ballot opening, ...). Never restate what the "
          "known-shows list already says. `type`: registration | lottery | presale | tour | other "
          "('other' is only remembered, never sent).",
          "- `dismiss`: IDs of known shows that turned out to be wrong.",
          "", OUTPUT_EXAMPLE]
    meta = {"date": today.isoformat(), "hot": [n for n, _ in hot], "batch": batch}
    return "\n".join(L) + "\n", meta
