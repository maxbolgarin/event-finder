"""Merge research findings into the state and decide which alerts are due.

Sources (the LLM researcher, Ticketmaster, a legacy import) only *report* what
they see; this module is the single judge of what is new. Every alert carries a
stable ledger key that is recorded once delivered, so the same news can never
be sent twice - no matter how the source spells the venue next time.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .catalog import (canonical_festival, canonical_venue, domain, festival_city, festival_key,
                      find_festival, is_trusted_url, venue_city, venue_key)
from .model import (SALE_KINDS, TERMINAL_STATUSES, Sale, Show, State, norm_sale_kind,
                    norm_status, status_rank)
from .normalize import (TZ, date_year, has_time, is_full_date, is_past, name_key, parse_date,
                        parse_when, slug, strip_brackets, when_dt, words)
from .watchlist import Watchlist

NL_NAMES = {"netherlands", "the netherlands", "nl", "nederland", "holland", "nld"}
NOTABLE_STATUSES = {"registration", "presale", "on_sale", "low", "sold_out", "cancelled", "postponed"}
# "sold out" -> "on sale" is usually research noise; only explicit wording counts as a restock
_RESTOCK_RE = re.compile(r"extra|additional|new tickets|released|re-?release|bijverkoop|restock|"
                         r"back on sale|opnieuw|nieuwe kaarten|nieuwe tickets", re.I)
# a live status maps to the ticket windows that would already have announced it
_LIVE_WINDOWS = {"registration": ("registration",), "presale": ("presale", "lottery"),
                 "on_sale": ("general",)}
_STOPWORDS = {"with", "from", "that", "this", "tour", "their", "tickets", "ticket", "will",
              "have", "been", "announced", "announce", "announces", "netherlands", "amsterdam"}


@dataclass
class Alert:
    kind: str                 # new | lineup | sale | live | restock | low | sold_out | cancelled |
                              # postponed | date | news | remind | digest
    key: str                  # ledger key: an alert is delivered at most once per key
    show_id: str = ""
    artist: str = ""
    loud: bool = True
    data: dict = field(default_factory=dict)
    extra_keys: list[str] = field(default_factory=list)   # recorded together with `key`

    @property
    def keys(self) -> list[str]:
        return [self.key, *self.extra_keys]


@dataclass
class Finding:
    artists: list[str]
    date: str
    id: str = ""
    lineup: str = ""
    venue: str = ""
    city: str = ""
    festival: str = ""
    status: str = ""
    sales: list[Sale] = field(default_factory=list)
    url: str = ""
    source: str = ""
    note: str = ""
    doubtful: bool = False        # seed/import flag: keep, but ask the researcher to confirm
    status_text: str = ""         # the status as reported, before normalisation

    @property
    def trusted(self) -> bool:
        return is_trusted_url(self.url) or is_trusted_url(self.source)


@dataclass
class IngestResult:
    alerts: list[Alert] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    skipped_past: int = 0
    checked: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Parsing raw findings
# --------------------------------------------------------------------------- #
def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)] if str(v).strip() else []


def _clean_title(text: str) -> str:
    return " ".join(strip_brackets(text).split())


def display_venue(venue: str, festival: str = "") -> str:
    canon = canonical_venue(venue)
    if canon:
        return canon
    v = re.split(r"\s[-|]\s|/|,|\sw/|\swith\s|\ssupport\s", f" {strip_brackets(venue)} ")[0].strip()
    if festival and find_festival(v) == festival:
        return ""          # the "venue" was just the festival's name
    return v


def parse_sales(raw: dict) -> list[Sale]:
    items = raw.get("sales") or []
    if isinstance(items, dict):
        items = [items]
    out: list[Sale] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kind = norm_sale_kind(str(it.get("type") or it.get("kind") or it.get("name") or ""))
        if not kind:
            continue
        sale = Sale(kind=kind,
                    start=parse_when(it.get("start") or it.get("opens") or it.get("date")),
                    end=parse_when(it.get("end") or it.get("closes")),
                    name=str(it.get("name") or "").strip(),
                    url=str(it.get("url") or "").strip())
        if not any(s.kind == sale.kind and s.start == sale.start for s in out):
            out.append(sale)
    legacy = parse_when(raw.get("on_sale") or raw.get("onsale") or "")
    if legacy and not any(s.start[:10] == legacy[:10] for s in out):
        out.append(Sale(kind="general", start=legacy))
    return out


def parse_finding(raw: dict, wl: Watchlist, today) -> tuple[Finding | None, str]:
    """Normalise one reported show. Returns (finding, "") or (None, reason);
    reason is "" for silently skipped past shows."""
    if not isinstance(raw, dict):
        return None, f"not an object: {raw!r}"
    primary = str(raw.get("artist") or "").strip()
    lineup = str(raw.get("lineup") or raw.get("billing") or raw.get("title") or "").strip()
    artists: list[str] = []
    first = wl.resolve(primary) if primary else None
    if first:
        artists.append(first)
    for name in wl.find_all(primary, *_as_list(raw.get("artists")), lineup):
        if name not in artists:
            artists.append(name)
    label = primary or lineup or "?"
    if not artists:
        return None, f"'{label}' is not on the watchlist - ignored"
    date = parse_date(raw.get("date")) or parse_date(str(raw.get("year") or ""))
    if not date:
        return None, f"{artists[0]}: unreadable date {raw.get('date')!r} - ignored"
    if is_past(date, today):
        return None, ""
    country = words(str(raw.get("country") or ""))
    if country and country not in NL_NAMES:
        return None, f"{artists[0]} {date}: country {raw.get('country')!r} is not NL - ignored"
    venue_raw = str(raw.get("venue") or "").strip()
    fest_raw = str(raw.get("festival") or "").strip()
    festival = ""
    if fest_raw and words(fest_raw) not in ("no", "none", "false", "n a", "na"):
        festival = canonical_festival(fest_raw) or _clean_title(re.sub(r"\b(19|20)\d{2}\b", "", fest_raw))
    if not festival:
        festival = find_festival(venue_raw, lineup)
    venue = display_venue(venue_raw, festival)
    city = (str(raw.get("city") or "").strip() or venue_city(venue)
            or (festival_city(festival) if festival else ""))
    return Finding(
        artists=artists, date=date, id=str(raw.get("id") or "").strip(),
        lineup=lineup if (lineup and words(lineup) != words(artists[0])) else "",
        venue=venue, city=city, festival=festival,
        status=norm_status(str(raw.get("status") or "")),
        sales=parse_sales(raw),
        url=str(raw.get("url") or "").strip(), source=str(raw.get("source") or "").strip(),
        note=str(raw.get("note") or raw.get("notes") or "").strip(),
        doubtful=bool(raw.get("unverified")),
        status_text=str(raw.get("status") or ""),
    ), ""


def url_key(url: str) -> str:
    path = re.sub(r"[?#].*$", "", (url or "").split("://", 1)[-1]).rstrip("/")
    return f"{domain(url)}{path[path.find('/'):] if '/' in path else ''}".lower()


def _content_words(text: str) -> set[str]:
    return {w for w in words(text).split() if len(w) > 3 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def known_domains(show: Show) -> set[str]:
    return {domain(u) for u in (show.url, show.source) if u} | set(show.evidence)


def dismiss_show(show: Show, reason: str, day: str) -> None:
    """Mark a show as bogus, remembering which sites were already weighed."""
    show.dismissed = reason
    show.evidence = sorted(known_domains(show) - {""})
    show.log(day, f"dismissed: {reason}")


def parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(ts).split(" ")[0])
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self, state: State, watchlist: Watchlist, now: datetime,
                 origin: str = "agent", baseline: bool = False):
        self.st = state
        self.wl = watchlist
        self.now = now
        self.today = now.date()
        self.day = self.today.isoformat()
        self.origin = origin
        self.baseline = baseline          # record everything as already-notified
        self.alerts: list[Alert] = []
        self.run_keys: set[str] = set()
        self._new_alerts: dict[str, Alert] = {}

    # ----------------------------------------------------------------- alerts
    def _known(self, key: str) -> bool:
        return key in self.run_keys or self.st.is_known_key(key)

    def _emit(self, alert: Alert) -> bool:
        if self._known(alert.key):
            return False
        self.run_keys.update(alert.keys)
        if self.baseline:
            stamp = f"{self.now.isoformat(timespec='seconds')} baseline"
            for k in alert.keys:
                self.st.sent.setdefault(k, stamp)
        else:
            self.alerts.append(alert)
        return True

    def _attach(self, show_id: str, key: str):
        """Fold a key into this run's 'new show' alert (its message shows it anyway)."""
        alert = self._new_alerts.get(show_id)
        if alert and key not in self.run_keys:
            alert.extra_keys.append(key)
            self.run_keys.add(key)
            if self.baseline:
                self.st.sent.setdefault(key, f"{self.now.isoformat(timespec='seconds')} baseline")

    @staticmethod
    def sale_key(show: Show, sale: Sale) -> str:
        return f"sale|{show.id}|{sale.kind}|{sale.start[:10] or 'tba'}"

    def _window_alerted(self, show_id: str, kind: str) -> bool:
        prefix = f"sale|{show_id}|{kind}|"
        return (any(k.startswith(prefix) for k in self.run_keys)
                or any(k.startswith(prefix) for k in self.st.sent)
                or any(k.startswith(prefix) for k in self.st.queued_keys()))

    def _emit_new(self, show: Show):
        keys = [self.sale_key(show, s) for s in show.sales]
        if show.status in NOTABLE_STATUSES:
            keys.append(f"status|{show.id}|{show.status}")
        alert = Alert("new", f"new|{show.id}", show.id, show.headliner, extra_keys=keys)
        if self._emit(alert):
            self._new_alerts[show.id] = alert

    # ---------------------------------------------------------------- matching
    def _by_artist_date(self, artists: set[str], date: str) -> Show | None:
        for s in self.st.shows.values():
            if s.date == date and artists & set(s.artists):
                return s
        return None

    def match(self, f: Finding) -> tuple[Show | None, str]:
        shows = self.st.shows
        fa = set(f.artists)
        if f.id and f.id in shows and fa & set(shows[f.id].artists):
            s = shows[f.id]
            if is_full_date(f.date) and f.date != s.date:
                other = self._by_artist_date(fa, f.date)
                if other:
                    return other, "artist-date"
            return s, "id"
        if is_full_date(f.date):
            s = self._by_artist_date(fa, f.date)
            if s:
                return s, "artist-date"
        if f.festival:
            fk, year = festival_key(f.festival), date_year(f.date)
            for s in shows.values():
                if (s.festival and festival_key(s.festival) == fk and date_year(s.date) == year
                        and fa & set(s.artists)):
                    return s, "festival"
        if is_full_date(f.date) and f.venue and not f.festival:
            vk = venue_key(f.venue)
            for s in shows.values():
                if not s.festival and s.date == f.date and s.venue and venue_key(s.venue) == vk:
                    return s, "venue-date"
        if is_full_date(f.date):
            for s in shows.values():
                if not (is_full_date(s.date) and s.date[5:] == f.date[5:]
                        and abs(date_year(s.date) - date_year(f.date)) == 1 and fa & set(s.artists)):
                    continue
                same_venue = f.venue and s.venue and venue_key(f.venue) == venue_key(s.venue)
                same_fest = (f.festival and s.festival
                             and festival_key(f.festival) == festival_key(s.festival))
                if same_venue or same_fest:
                    return s, "year-typo"
        for s in shows.values():
            if (s.date and not is_full_date(s.date) and f.date.startswith(s.date)
                    and fa & set(s.artists)
                    and (not s.festival or (f.festival and festival_key(f.festival) == festival_key(s.festival)))):
                return s, "refine"
        return None, ""

    def _new_id(self, f: Finding) -> str:
        base = slug(f.artists[0])
        if is_full_date(f.date) or not f.festival:
            base += f"-{f.date}"
        else:
            base += f"-{slug(f.festival)}-{f.date[:4]}"
        sid, n = base, 2
        while sid in self.st.shows:
            sid, n = f"{base}-{n}", n + 1
        return sid

    # ------------------------------------------------------------------ ingest
    def ingest(self, findings: dict) -> IngestResult:
        res = IngestResult()
        if not isinstance(findings, dict):
            findings = {"shows": findings if isinstance(findings, list) else []}
        for raw in findings.get("shows") or []:
            f, why = parse_finding(raw, self.wl, self.today)
            if f is None:
                if why:
                    res.warnings.append(why)
                else:
                    res.skipped_past += 1
                continue
            show, how = self.match(f)
            if show is None:
                self._create(f, res)
            else:
                self._merge(show, f, how, res)
        for d in findings.get("dismiss") or []:
            self._dismiss(d, res)
        for n in findings.get("news") or []:
            self._news(n, res)
        res.checked = self._mark_checked(findings.get("checked"))
        res.alerts = list(self.alerts)
        return res

    def _create(self, f: Finding, res: IngestResult) -> Show:
        # the old tracker invented shows (wrong years, old pages) even behind real ticket
        # links, so its rows only count once the researcher confirms them
        trusted = ((f.trusted or self.origin in ("seed", "ticketmaster"))
                   and not f.doubtful and self.origin != "legacy")
        show = Show(
            id=self._new_id(f), artists=list(f.artists), date=f.date, venue=f.venue, city=f.city,
            festival=f.festival, lineup=f.lineup, status=f.status or "announced",
            url=f.url, source=f.source, note=f.note, first_seen=self.day, updated=self.day,
            last_seen=self.day, origin=self.origin, verified=trusted,
        )
        for w in f.sales:
            self._add_sale(show, w, f.sales)
        self.st.shows[show.id] = show
        show.log(self.day, f"created by {self.origin}")
        res.created.append(show.id)
        if trusted or self.baseline:        # baseline: the user already heard about it
            self._emit_new(show)
        else:
            res.warnings.append(f"{show.id}: no trusted source URL - stored as unverified, not alerted")
        return show

    def _merge(self, show: Show, f: Finding, how: str, res: IngestResult):
        before = repr(show.to_dict() | {"last_seen": "", "history": []})
        show.last_seen = self.day
        is_new = show.id in self._new_alerts
        quiet = show.dismissed or show.muted or not show.verified
        if show.dismissed and how in ("id", "artist-date"):
            # only evidence from a site not weighed before counts: a stale page that
            # fooled the researcher once (an old line-up page) must not undo a dismissal
            fresh = {domain(u) for u in (f.url, f.source) if is_trusted_url(u)} - known_domains(show)
            if fresh:
                show.dismissed = ""
                show.log(self.day, f"restored (confirmed by {', '.join(sorted(fresh))})")
        for a in f.artists:
            if a not in show.artists:
                show.artists.append(a)
                show.log(self.day, f"+ {a} on the bill")
                key = f"lineup|{show.id}|{name_key(a)}"
                if is_new:
                    self._attach(show.id, key)
                elif not quiet:
                    self._emit(Alert("lineup", key, show.id, a, data={"added": a}))
        if not show.verified and f.trusted:
            show.verified = True
            show.log(self.day, "verified")
            if not self._known(f"new|{show.id}"):
                self._emit_new(show)
                is_new = show.id in self._new_alerts
            quiet = bool(show.dismissed or show.muted)
        self._merge_date(show, f, how, is_new, quiet, res)
        if f.venue and (not show.venue or (how == "id" and venue_key(f.venue) != venue_key(show.venue))):
            if show.venue:
                show.log(self.day, f"venue {show.venue} -> {f.venue}")
            show.venue = f.venue
        show.city = show.city or f.city
        show.festival = show.festival or f.festival
        show.lineup = show.lineup or f.lineup
        if is_trusted_url(f.url) and not is_trusted_url(show.url):
            show.url = f.url
        show.source = f.source or show.source
        show.note = f.note or show.note
        for w in f.sales:
            added = self._add_sale(show, w, f.sales)
            if added is None:
                continue
            key = self.sale_key(show, added)
            if is_new:
                self._attach(show.id, key)
            elif not quiet and not self._stale(added):
                self._emit(Alert("sale", key, show.id, show.headliner, data={"sale": asdict(added)}))
        self._merge_status(show, f.status, is_new, quiet,
                           restock=bool(_RESTOCK_RE.search(f"{f.status_text} {f.note}")))
        if repr(show.to_dict() | {"last_seen": "", "history": []}) != before:
            show.updated = self.day
            if show.id not in res.created and show.id not in res.updated:
                res.updated.append(show.id)
        else:
            res.unchanged += 1

    def _merge_date(self, show: Show, f: Finding, how: str, is_new: bool, quiet: bool, res):
        if not is_full_date(f.date) or f.date == show.date:
            return
        if how == "year-typo":
            res.warnings.append(f"{show.id}: reported as {f.date}; kept {show.date} (likely a wrong year)")
            return
        if how == "id" and is_full_date(show.date) and not show.festival:
            old, show.date = show.date, f.date
            show.log(self.day, f"date {old} -> {f.date}")
            key = f"date|{show.id}|{f.date}"
            if is_new:
                self._attach(show.id, key)
            elif not quiet:
                self._emit(Alert("date", key, show.id, show.headliner, data={"old": old, "new": f.date}))
        elif not is_full_date(show.date) or (show.festival and how == "id"):
            # a festival day only moves when the researcher says so explicitly (by ID);
            # otherwise conflicting day reports would make it flip-flop
            show.log(self.day, f"date {show.date} -> {f.date}")
            show.date = f.date

    def _add_sale(self, show: Show, w: Sale, siblings: list[Sale]) -> Sale | None:
        """Store a ticket window; return it when it is news (new window, or a TBA
        window that just got its date), else None."""
        same = [s for s in show.sales if s.kind == w.kind]
        day = w.start[:10]

        def fill(s: Sale):
            s.end = s.end or w.end
            s.name = s.name or w.name
            s.url = s.url or w.url

        for s in same:
            if day and s.start[:10] == day:
                if has_time(w.start) and not has_time(s.start):
                    s.start = w.start
                fill(s)
                return None
        if not day:
            if same:
                fill(same[0])
                return None
            show.sales.append(w)
            return w
        for s in same:
            if not s.start:
                s.start = w.start
                fill(s)
                return s
        # a different date for an existing unnamed window of this kind that this report
        # doesn't repeat is a correction of a previous (wrong) report, not a second presale
        sibling_days = {x.start[:10] for x in siblings}
        for s in same:
            sd = when_dt(s.start[:10])
            wd = when_dt(day)
            if (sd and wd and abs((sd - wd).days) <= 2 and s.start[:10] not in sibling_days
                    and (not s.name or not w.name or words(s.name) == words(w.name))):
                show.log(self.day, f"{w.kind} {s.start} -> {w.start}")
                s.start = w.start
                fill(s)
                return None
        show.sales.append(w)
        show.sales.sort(key=lambda s: (SALE_KINDS.index(s.kind) if s.kind in SALE_KINDS else 9, s.start or "9"))
        return w

    def _stale(self, sale: Sale) -> bool:
        """A window that is already over isn't worth an alert."""
        if sale.kind in ("registration", "lottery") and sale.end:
            end = when_dt(sale.end)
            return bool(end and end < self.now)
        start = when_dt(sale.start)
        return bool(start and start < self.now - timedelta(days=1))

    def _merge_status(self, show: Show, new: str, is_new: bool, quiet: bool, restock: bool = False):
        old = show.status
        if not new:
            return
        if new == old:
            show.pending_status = show.pending_since = ""
            return

        def alert(kind: str, key: str, loud: bool = True):
            if is_new:
                self._attach(show.id, key)
            elif not quiet:
                self._emit(Alert(kind, key, show.id, show.headliner, loud=loud,
                                 data={"old": old, "new": new}))

        def change(note: str = ""):
            show.status = new
            show.pending_status = show.pending_since = ""
            show.log(self.day, f"status {old} -> {new}{note}")

        if new in TERMINAL_STATUSES:
            change()
            alert(new, f"status|{show.id}|{new}")
            return
        if old in TERMINAL_STATUSES:
            change()
            return
        if new == "sold_out":
            change()
            alert("sold_out", f"status|{show.id}|sold_out", loud=False)
            return
        if old == "sold_out" and new in ("presale", "on_sale", "low") and restock:
            change()
            alert("restock", f"restock|{show.id}|{self.day[:7]}")
            return
        if old == "sold_out" or status_rank(new) <= status_rank(old):
            # A step back is usually research noise. Accept it only when a later
            # run reports it again (self-heals a wrong status without flapping).
            if show.pending_status == new and show.pending_since and show.pending_since < self.day:
                change(" (confirmed)")
                if old == "sold_out" and new in ("presale", "on_sale", "low"):
                    alert("restock", f"restock|{show.id}|{self.day[:7]}")
            elif show.pending_status != new:
                show.pending_status, show.pending_since = new, self.day
            return
        change()
        if new == "low":
            alert("low", f"status|{show.id}|low", loud=False)
        elif new in _LIVE_WINDOWS:
            kinds = _LIVE_WINDOWS[new]
            if any(self._window_alerted(show.id, k) for k in kinds):
                return      # the user was already told when this sale opens
            opened = [when_dt(w.start) for w in show.sales if w.kind in kinds and w.start]
            if any(dt and dt < self.now - timedelta(days=1) for dt in opened):
                return      # the sale opened a while ago: status news, not a "just went on sale"
            alert("live", f"status|{show.id}|{new}")

    def _dismiss(self, d, res: IngestResult):
        sid = str((d or {}).get("id") if isinstance(d, dict) else d or "").strip()
        show = self.st.shows.get(sid)
        if not show:
            res.warnings.append(f"dismiss: unknown id {sid!r}")
            return
        reason = str(d.get("reason") or "") if isinstance(d, dict) else ""
        if not show.dismissed:
            dismiss_show(show, reason or "judged bogus", self.day)
            res.updated.append(sid)

    def _news(self, n, res: IngestResult):
        if not isinstance(n, dict):
            return
        text = str(n.get("text") or n.get("summary") or n.get("headline") or "").strip()
        url = str(n.get("url") or n.get("source") or "").strip()
        names = self.wl.find_all(str(n.get("artist") or "")) or self.wl.find_all(text)
        if not names or not text:
            res.warnings.append(f"news ignored (no watchlist artist or text): {text[:60]!r}")
            return
        if not is_trusted_url(url):
            res.warnings.append(f"news ignored (no trusted URL): {names[0]}: {text[:60]!r}")
            return
        ntype = words(str(n.get("type") or ""))
        ntype = next((t for t in ("registration", "lottery", "presale", "tour") if t in ntype), "other")
        key = f"news|{name_key(names[0])}|{url_key(url)}"
        if (self._known(key) or any(old.get("key") == key for old in self.st.news)
                or self._similar_news(names[0], text)):
            return
        self.st.news.append({"date": self.day, "artist": names[0], "type": ntype,
                             "text": text, "url": url, "key": key})
        if ntype == "other":
            return          # informational only: remembered (so it isn't re-reported), never sent
        self._emit(Alert("news", key, "", names[0], data={"type": ntype, "text": text, "url": url}))

    def _similar_news(self, artist: str, text: str) -> bool:
        cutoff = (self.today - timedelta(days=60)).isoformat()
        cw = _content_words(text)
        return any(old.get("artist") == artist and old.get("date", "") >= cutoff
                   and _jaccard(cw, _content_words(old.get("text", ""))) >= 0.5
                   for old in self.st.news)

    def _mark_checked(self, checked) -> list[str]:
        names = []
        for c in _as_list(checked):
            r = self.wl.resolve(c)
            if r and r not in names:
                names.append(r)
                self.st.artists.setdefault(r, {})["last_checked"] = self.day
        return names

    # --------------------------------------------------------------- reminders
    def _fresh(self, show: Show, sale: Sale) -> bool:
        """Was this window just announced (no point reminding right after)?"""
        for key in (self.sale_key(show, sale), f"new|{show.id}"):
            if key in self.run_keys:
                return True
            ts = parse_ts(self.st.sent.get(key, "")) if key in self.st.sent else None
            if ts and self.now - ts < timedelta(hours=12):
                return True
        return False

    def reminders(self) -> list[Alert]:
        """'Opens tomorrow 10:00', 'opens in 40 min', 'registration closes today'."""
        start_idx = len(self.alerts)
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        for show in list(self.st.shows.values()):
            if show.dismissed or show.muted or not show.verified or is_past(show.date, self.today):
                continue
            for w in show.sales:
                base = f"remind|{show.id}|{w.kind}"
                data = {"sale": asdict(w)}
                if has_time(w.start):
                    start = when_dt(w.start)
                    if start and start > self.now:
                        left = start - self.now
                        if left <= timedelta(hours=30) and not self._fresh(show, w):
                            self._emit(Alert("remind", f"{base}|{w.start}|day", show.id, show.headliner,
                                             data={**data, "stage": "opens", "at": w.start}))
                        if left <= timedelta(minutes=75):
                            self._emit(Alert("remind", f"{base}|{w.start}|soon", show.id, show.headliner,
                                             data={**data, "stage": "soon", "at": w.start}))
                elif w.start in (self.day, tomorrow) and not self._fresh(show, w):
                    self._emit(Alert("remind", f"{base}|{w.start}|day", show.id, show.headliner,
                                     data={**data, "stage": "opens", "at": w.start}))
                if w.kind in ("registration", "lottery") and w.end:
                    end, start = when_dt(w.end), when_dt(w.start)
                    if (end and self.now < end <= self.now + timedelta(hours=30)
                            and (not start or start <= self.now)):
                        self._emit(Alert("remind", f"{base}|{w.end}|closing", show.id, show.headliner,
                                         data={**data, "stage": "closes", "at": w.end}))
        return self.alerts[start_idx:]

    def digest(self, weekday: int = 0) -> Alert | None:
        """Weekly silent check-in, so silence never hides a broken tracker."""
        if self.now.weekday() != weekday:
            return None
        year, week, _ = self.now.isocalendar()
        upcoming = [s for s in self.st.shows.values()
                    if not s.dismissed and not is_past(s.date, self.today)]
        horizon = self.now + timedelta(days=14)
        windows = []
        for s in upcoming:
            for w in s.sales:
                dt = when_dt(w.start)
                if dt and self.now <= dt + timedelta(hours=23) and dt <= horizon:
                    windows.append((w.start, s.headliner, w.label(), s.id))
        week_ago = (self.today - timedelta(days=7)).isoformat()
        checked = sum(1 for n in self.wl.names
                      if self.st.artists.get(n, {}).get("last_checked", "") >= week_ago)
        alert = Alert("digest", f"digest|{year}-W{week:02d}", loud=False, data={
            "upcoming": len(upcoming),
            "unverified": sum(1 for s in upcoming if not s.verified),
            "checked": checked, "artists": len(self.wl.names),
            "windows": sorted(windows)[:8],
            "runs": sum(1 for r in self.st.runs if r.get("at", "") >= week_ago),
        })
        return alert if self._emit(alert) else None


# --------------------------------------------------------------------------- #
def prune(state: State, today, keep_days: int = 2) -> int:
    """Forget finished shows, stale ledger entries, old news and undeliverable messages."""
    cutoff = today - timedelta(days=keep_days)
    gone = [sid for sid, s in state.shows.items() if is_past(s.date, cutoff)]
    for sid in gone:
        del state.shows[sid]
    now = datetime.combine(today, datetime.min.time(), TZ)
    for key, ts in list(state.sent.items()):
        parts = key.split("|")
        stamp = parse_ts(ts)
        age = (now - stamp).days if stamp else 0
        show_scoped = parts[0] in ("new", "sale", "status", "lineup", "date", "restock", "remind")
        if (show_scoped and len(parts) > 1 and parts[1] not in state.shows and age > 45) or age > 400:
            del state.sent[key]
    news_cutoff = (today - timedelta(days=180)).isoformat()
    state.news = [n for n in state.news if n.get("date", "") >= news_cutoff]
    state.runs = state.runs[-60:]
    fresh = []
    for msg in state.pending:
        stamp = parse_ts(msg.get("created", ""))
        if stamp and (now - stamp) <= timedelta(days=3):
            fresh.append(msg)
    state.pending = fresh
    return len(gone)
