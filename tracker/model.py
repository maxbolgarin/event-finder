"""State model: shows, ticket windows and the persisted tracker state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from .normalize import fmt_when, words

# Ticket-state progression of a show. "low" = few tickets left.
STATUS_ORDER = ["announced", "registration", "presale", "on_sale", "low", "sold_out"]
TERMINAL_STATUSES = {"cancelled", "postponed"}
ALL_STATUSES = set(STATUS_ORDER) | TERMINAL_STATUSES

# Kinds of ticket windows, in the order they usually happen.
SALE_KINDS = ["registration", "lottery", "presale", "general"]
SALE_LABELS = {
    "registration": "Registration",
    "lottery": "Lottery / ballot sale",
    "presale": "Presale",
    "general": "General sale",
}
STATUS_LABELS = {
    "announced": "Announced",
    "registration": "Registration open",
    "presale": "Presale",
    "on_sale": "On sale",
    "low": "Few tickets left",
    "sold_out": "Sold out",
    "cancelled": "Cancelled",
    "postponed": "Postponed",
}


def _has(s: str, *needles: str) -> bool:
    return any(n in s for n in needles)


def norm_status(text: str) -> str:
    """Map free text (EN/NL) to a status; '' when it says nothing useful."""
    s = f" {words(text)} "
    t = s.strip()
    if not t:
        return ""
    if t.replace(" ", "_") in ALL_STATUSES:
        return t.replace(" ", "_")
    if _has(s, "cancel", "geannuleerd", "afgelast", "gaat niet door"):
        return "cancelled"
    if _has(s, "postpon", "uitgesteld", "verplaatst", "reschedul"):
        return "postponed"
    if _has(s, "sold out", "soldout", "uitverkocht", " waitlist", "wachtlijst"):
        return "sold_out"
    if _has(s, "not yet", "nog niet", "niet in de verkoop", "coming soon", "binnenkort"):
        return "announced"
    if _has(s, "few tickets", "last tickets", "laatste kaarten", "laatste tickets",
            "bijna uitverkocht", "limited", " low "):
        return "low"
    if _has(s, "registration", "register", "sign up", "signup", "aanmeld", "inschrijv", "verified fan"):
        return "registration"
    if _has(s, "presale", "pre sale", "pre order", "priority", "lottery", "ballot", "loting"):
        return "presale"
    if _has(s, "on sale", "onsale", "available", "verkrijgbaar", "in verkoop", "voorverkoop"):
        return "on_sale"
    if _has(s, " tba ", " tbc ", "announced", "aangekondigd", "line up", "lineup", "confirmed",
            "bevestigd"):
        return "announced"
    if _has(s, "tickets"):
        return "on_sale"
    return ""


def norm_sale_kind(text: str) -> str:
    s = f" {words(text)} "
    if s.strip() in SALE_KINDS:
        return s.strip()
    if _has(s, "registration", "register", "sign up", "signup", "aanmeld", "inschrijv", "verified fan"):
        return "registration"
    if _has(s, "lottery", "loting", "ballot", " draw ", "unique code"):
        return "lottery"
    if _has(s, "general", "public", "regular", "voorverkoop", "on sale", "onsale", "algemene"):
        return "general"
    if _has(s, "pre", "fan", "member", "amex", "priority", "club", "artist", "live nation",
            "ticketmaster", "venue", "o2", "spotify"):
        return "presale"
    return "general" if s.strip() else ""


def status_rank(status: str) -> int:
    return STATUS_ORDER.index(status) if status in STATUS_ORDER else -1


@dataclass
class Sale:
    kind: str                 # registration | lottery | presale | general
    start: str = ""           # 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD' Amsterdam time, '' = TBA
    end: str = ""
    name: str = ""            # e.g. "Live Nation presale"
    url: str = ""

    def label(self) -> str:
        base = SALE_LABELS.get(self.kind, self.kind.title())
        name = words(self.name)
        if not name or name in (words(base), self.kind):
            return base
        if name.startswith(self.kind) or name.startswith(words(base)):
            return self.name              # "Registration (closed)", not "Registration (Registration (closed))"
        return f"{base} ({self.name})"

    def when(self, today=None) -> str:
        if self.start and self.end:
            return f"{fmt_when(self.start, today)} → {fmt_when(self.end, today)}"
        if self.start:
            return fmt_when(self.start, today)
        if self.end:
            return f"until {fmt_when(self.end, today)}"
        return "date TBA"


@dataclass
class Show:
    id: str
    artists: list[str]                    # watchlist names on the bill, headliner first
    date: str                             # 'YYYY-MM-DD' or partial 'YYYY-MM' / 'YYYY'
    venue: str = ""
    city: str = ""
    festival: str = ""                    # set for festival appearances
    lineup: str = ""                      # billing as announced, e.g. "Korn + Architects"
    status: str = "announced"
    sales: list[Sale] = field(default_factory=list)
    url: str = ""                         # best ticket / info link
    source: str = ""                      # where the latest info came from
    note: str = ""
    first_seen: str = ""
    updated: str = ""                     # last material change (date)
    last_seen: str = ""                   # last time any source reported it
    origin: str = ""                      # agent | seed | legacy | ticketmaster
    verified: bool = True                 # backed by a trusted (non-resale) source
    dismissed: str = ""                   # reason, when judged bogus
    evidence: list[str] = field(default_factory=list)   # source domains already weighed at dismissal
    muted: bool = False
    pending_status: str = ""              # a step back in status awaiting a 2nd report
    pending_since: str = ""
    history: list[str] = field(default_factory=list)

    @property
    def headliner(self) -> str:
        return self.artists[0] if self.artists else "?"

    def title(self) -> str:
        if self.lineup:
            return self.lineup
        return " + ".join(self.artists)

    def place(self) -> str:
        venue = self.venue
        if self.festival and self.festival.lower() not in venue.lower():
            venue = f"{self.festival} ({venue})" if venue else self.festival
        return ", ".join(p for p in (venue, self.city) if p)

    def log(self, day: str, text: str, keep: int = 20):
        self.history.append(f"{day} {text}")
        del self.history[:-keep]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Show":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        data["sales"] = [Sale(**{k: v for k, v in s.items() if k in {f.name for f in fields(Sale)}})
                         for s in d.get("sales", [])]
        return cls(**data)


@dataclass
class State:
    version: int = 1
    rev: int = 0
    initialized: str = ""
    shows: dict[str, Show] = field(default_factory=dict)
    artists: dict[str, dict] = field(default_factory=dict)   # name -> {"last_checked": ...}
    sent: dict[str, str] = field(default_factory=dict)       # alert key -> ISO time (ledger)
    pending: list[dict] = field(default_factory=list)        # undelivered messages
    news: list[dict] = field(default_factory=list)           # recent news already alerted
    runs: list[dict] = field(default_factory=list)           # recent run summaries
    extra: dict = field(default_factory=dict)                # source-specific caches

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shows"] = {k: s.to_dict() for k, s in self.shows.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "State":
        d = d or {}
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known and k != "shows"}
        st = cls(**data)
        st.shows = {k: Show.from_dict(v) for k, v in (d.get("shows") or {}).items()}
        return st

    def queued_keys(self) -> set[str]:
        return {k for msg in self.pending for k in msg.get("keys", [])}

    def is_known_key(self, key: str) -> bool:
        return key in self.sent or key in self.queued_keys()
