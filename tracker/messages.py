"""Turn alerts into Telegram messages (HTML) plus a plain-text twin for logs."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .catalog import festival_key, venue_key
from .engine import Alert
from .model import STATUS_LABELS, Sale, Show, State
from .normalize import date_year, fmt_date, fmt_when, has_time, when_dt

SALE_ICONS = {"registration": "📝", "lottery": "🎲", "presale": "🔐", "general": "🎫"}
_PRIORITY = ["cancelled", "postponed", "date", "restock", "live", "sale", "lineup", "low", "sold_out"]


@dataclass
class Message:
    html: str
    keys: list[str]
    loud: bool = True
    kinds: list[str] = field(default_factory=list)
    artist: str = ""

    @property
    def text(self) -> str:
        return plain(self.html)


def plain(markup: str) -> str:
    text = re.sub(r'<a href="([^"]*)">([^<]*)</a>', lambda m: f"{m.group(2)}: {html.unescape(m.group(1))}", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def link(url: str, label: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{esc(label)}</a>'


def _rel_day(when: str, today: date) -> str:
    dt = when_dt(when)
    if not dt:
        return "date TBA"
    d = dt.date()
    if d == today:
        word = "today"
    elif d == today + timedelta(days=1):
        word = "tomorrow"
    else:
        return fmt_when(when, today)
    return f"{word} {dt.strftime('%H:%M')}" if has_time(when) else word


def _sale_line(w: Sale, today: date) -> str:
    return f"{SALE_ICONS.get(w.kind, '🎟')} {esc(w.label())}: {esc(w.when(today))}"


def _links(show: Show, extra_url: str = "") -> str:
    parts, seen = [], set()
    for url, label in ((extra_url, "Tickets / info"), (show.url, "Tickets / info"), (show.source, "Source")):
        if url and url.startswith("http") and url not in seen:
            seen.add(url)
            parts.append(link(url, label if not parts else "Source"))
    return "🔗 " + " · ".join(parts) if parts else ""


def _where_line(show: Show) -> str:
    return f"📍 {esc(show.place())}" if show.place() else ""


def _date_line(shows: list[Show]) -> str:
    return "📅 " + " · ".join(esc(fmt_date(s.date)) for s in shows)


def _name(show: Show) -> str:
    return esc(show.title())


def _join(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------- #
def _render_new(shows: list[Show], today: date) -> str:
    s0 = shows[0]
    if s0.festival:
        year = date_year(s0.date)
        head = f"🆕 <b>{_name(s0)}</b> — confirmed for {esc(s0.festival)} {year}"
    else:
        head = f"🆕 <b>{_name(s0)}</b> — new NL show{'s' if len(shows) > 1 else ''}"
    windows: dict[tuple, Sale] = {}
    for s in shows:          # multi-night runs usually share their windows
        for w in s.sales:
            k = (w.kind, w.start)
            if k not in windows or (w.name and not windows[k].name):
                windows[k] = w
    sale_lines = [_sale_line(w, today) for w in windows.values()]
    status = ""
    if s0.status not in ("announced", ""):
        status = f"ℹ️ {esc(STATUS_LABELS.get(s0.status, s0.status))}"
    elif not sale_lines:
        status = "ℹ️ Ticket sale date not announced yet"
    return _join([head, _date_line(shows), _where_line(s0), *sale_lines, status,
                  f"📝 {esc(s0.note)}" if s0.note else "", _links(s0)])


def _render_show_changes(show: Show, alerts: list[Alert], now: datetime) -> str:
    today = now.date()
    alerts = sorted(alerts, key=lambda a: _PRIORITY.index(a.kind) if a.kind in _PRIORITY else 99)
    top = alerts[0]
    name = f"<b>{_name(show)}</b>"
    lines = []
    new_sales = [Sale(**a.data["sale"]) for a in alerts if a.kind == "sale"]
    if top.kind == "cancelled":
        lines.append(f"❌ {name} — CANCELLED")
    elif top.kind == "postponed":
        lines.append(f"⏸ {name} — postponed")
    elif top.kind == "date":
        lines.append(f"📅 {name} — date changed: {esc(fmt_date(top.data['old']))} → "
                     f"<b>{esc(fmt_date(top.data['new']))}</b>")
    elif top.kind == "restock":
        lines.append(f"🔁 {name} — tickets available again!")
    elif top.kind == "live":
        what = {"registration": "registration is open now", "presale": "presale is live now",
                "on_sale": "tickets are on sale now"}.get(top.data.get("new"), "tickets on sale now")
        lines.append(f"🟢 {name} — {what}")
    elif top.kind == "sale":
        first = new_sales[0]
        verb = {"registration": "registration announced", "lottery": "lottery / ballot sale announced",
                "presale": "presale announced", "general": "general sale announced"}[first.kind]
        start = when_dt(first.start)
        if first.kind == "registration" and (not start or start <= now):
            verb = "registration is open"
        lines.append(f"{SALE_ICONS[first.kind]} {name} — {verb}")
    elif top.kind == "lineup":
        added = ", ".join(a.data.get("added", "") for a in alerts if a.kind == "lineup")
        lines.append(f"➕ <b>{esc(added)}</b> added to {esc(show.title())}")
    elif top.kind == "low":
        lines.append(f"🟠 {name} — few tickets left")
    elif top.kind == "sold_out":
        lines.append(f"🔴 {name} — sold out")
    lines.append(_date_line([show]) + (f" · {esc(show.place())}" if show.place() else ""))
    for w in new_sales:
        lines.append(_sale_line(w, today))
    for a in alerts[1:]:
        if a.kind in ("cancelled", "postponed", "sold_out", "low", "restock"):
            lines.append(f"ℹ️ {esc(STATUS_LABELS.get(a.data.get('new', a.kind), a.kind))}")
        elif a.kind == "live" and top.kind != "live":
            lines.append("🟢 Sale is live now")
    sale_url = next((w.url for w in new_sales if w.url), "")
    lines.append(_links(show, sale_url))
    return _join(lines)


def _render_remind(shows: list[Show], alert: Alert, today: date, now: datetime) -> str:
    show = shows[0]
    w = Sale(**alert.data["sale"])
    at = alert.data.get("at", w.start)
    kind = {"registration": "registration", "lottery": "lottery sale", "presale": "presale",
            "general": "general sale"}.get(w.kind, w.kind)
    name = f"<b>{_name(show)}</b>"
    stage = alert.data.get("stage")
    if stage == "soon":
        dt = when_dt(at)
        mins = max(1, int((dt - now).total_seconds() // 60)) if dt else 0
        head = f"🚨 {name} — {kind} opens in <b>{mins} min</b> ({dt.strftime('%H:%M') if dt else '?'})"
    elif stage == "closes":
        head = f"⏳ {name} — {kind} closes <b>{esc(_rel_day(at, today))}</b>"
    else:
        head = f"⏰ {name} — {kind} opens <b>{esc(_rel_day(at, today))}</b>"
    return _join([head, f"🎟 {esc(w.name)}" if w.name else "",
                  _date_line(shows) + (f" · {esc(show.place())}" if show.place() else ""),
                  _links(show, w.url)])


def _render_news(alert: Alert) -> str:
    icon = {"registration": "📝", "lottery": "🎲", "presale": "🔐"}.get(alert.data.get("type"), "📰")
    return _join([f"{icon} <b>{esc(alert.artist)}</b> — {esc(alert.data.get('text', ''))}",
                  f"🔗 {link(alert.data['url'], 'Source')}" if alert.data.get("url") else ""])


def _render_digest(alert: Alert, today: date) -> str:
    d = alert.data
    lines = ["📋 <b>Weekly check-in</b> — the NL tracker is running",
             f"• {d['checked']}/{d['artists']} artists checked in the last 7 days ({d['runs']} runs)",
             f"• {d['upcoming']} upcoming NL shows tracked"
             + (f" ({d['unverified']} unverified)" if d.get("unverified") else "")]
    if d.get("windows"):
        lines.append("Next ticket windows:")
        for start, artist, label, _sid in d["windows"]:
            lines.append(f"• {esc(fmt_when(start, today))} — {esc(artist)}: {esc(label)}")
    else:
        lines.append("No ticket windows in the next 14 days.")
    return _join(lines)


# --------------------------------------------------------------------------- #
def render(alerts: list[Alert], state: State, now: datetime) -> list[Message]:
    today = now.date()
    messages: list[Message] = []
    new_groups: dict[tuple, list[Alert]] = {}
    by_show: dict[str, list[Alert]] = {}
    singles: list[Alert] = []
    for a in alerts:
        show = state.shows.get(a.show_id)
        if a.kind == "new" and show:
            place = festival_key(show.festival) if show.festival else venue_key(show.venue)
            new_groups.setdefault((show.headliner, place, show.festival), []).append(a)
        elif a.kind in ("remind", "news", "digest") or not show:
            singles.append(a)
        else:
            by_show.setdefault(a.show_id, []).append(a)

    for group in sorted(new_groups.values(), key=lambda g: min(state.shows[a.show_id].date for a in g)):
        shows = sorted((state.shows[a.show_id] for a in group), key=lambda s: s.date)
        keys = [k for a in group for k in a.keys]
        messages.append(Message(_render_new(shows, today), keys, True, ["new"], shows[0].headliner))

    for sid, group in by_show.items():
        show = state.shows[sid]
        keys = [k for a in group for k in a.keys]
        messages.append(Message(_render_show_changes(show, group, now), keys,
                                any(a.loud for a in group), sorted({a.kind for a in group}), show.headliner))

    reminders: dict[tuple, list[Alert]] = {}
    for a in singles:        # one reminder for all nights sharing the same window
        show = state.shows.get(a.show_id)
        if a.kind == "remind" and show:
            w = a.data["sale"]
            gk = (show.headliner, venue_key(show.venue), w["kind"], a.data.get("at"), a.data.get("stage"))
            reminders.setdefault(gk, []).append(a)
    for group in reminders.values():
        shows = sorted((state.shows[a.show_id] for a in group), key=lambda s: s.date)
        messages.append(Message(_render_remind(shows, group[0], today, now),
                                [k for a in group for k in a.keys], True, ["remind"], shows[0].headliner))

    for a in singles:
        if a.kind == "remind":
            continue
        if a.kind == "news":
            body = _render_news(a)
        elif a.kind == "digest":
            body = _render_digest(a, today)
        else:
            continue
        messages.append(Message(body, a.keys, a.loud, [a.kind], a.artist))
    return messages
