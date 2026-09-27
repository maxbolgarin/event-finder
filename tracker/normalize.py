"""Text, name and date normalisation shared by matching and parsing.

Everything here is pure and deterministic: the same messy input string always
produces the same key, which is what makes de-duplication stable across runs.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Amsterdam")

_SPECIAL = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "þ": "th", "Þ": "TH", "ð": "d",
    "ı": "i", "’": "'", "‘": "'", "`": "'", "´": "'", "ʼ": "'",
    "–": "-", "—": "-", "‐": "-",
})

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def fold(text: str) -> str:
    """Lower-case ASCII approximation: strips accents, maps ø/æ/ß, transliterates Cyrillic."""
    s = (text or "").translate(_SPECIAL).lower()
    s = "".join(_CYRILLIC.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def words(text: str) -> str:
    """fold() with punctuation turned into single spaces.

    Apostrophes vanish ("Gallagher's" -> "gallaghers") and '&' / '+' read as
    "and", so "Florence + the Machine" == "Florence and the Machine".
    """
    s = fold(text).replace("&", " and ").replace("+", " and ")
    s = s.replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def name_key(text: str) -> str:
    """Compact key for artist/festival names: no spaces, no leading 'the'."""
    w = words(text)
    if w.startswith("the "):
        w = w[4:]
    return w.replace(" ", "")


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def slug(text: str) -> str:
    return "-".join(words(text).split()) or "x"


def strip_brackets(text: str) -> str:
    return " ".join(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "").split())


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
_MONTHS = {
    "jan": 1, "january": 1, "januari": 1,
    "feb": 2, "february": 2, "februari": 2,
    "mar": 3, "march": 3, "maart": 3, "mrt": 3,
    "apr": 4, "april": 4,
    "may": 5, "mei": 5,
    "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8, "augustus": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def _ymd(y, m, d) -> str:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def parse_date(text) -> str:
    """Parse a show date.

    Returns 'YYYY-MM-DD', a partial 'YYYY-MM' / 'YYYY' (e.g. a festival whose
    day is not announced yet), or '' when the input can't be read. Numeric
    D/M/Y input is read European-style (day first).
    """
    s = " ".join(fold(str(text or "")).replace(",", " ").split())
    if not s:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:$|[t\s])", s)
    if m:
        return _ymd(*m.groups())
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if m:
        return _ymd(*m.groups())
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        return _ymd(m.group(3), m.group(2), m.group(1))
    m = re.match(r"^(?:[a-z]+\.?\s+)?(\d{1,2})(?:st|nd|rd|th|e)?\s+([a-z]+)\.?\s+(\d{4})$", s)
    if m and m.group(2) in _MONTHS:
        return _ymd(m.group(3), _MONTHS[m.group(2)], m.group(1))
    m = re.match(r"^(?:[a-z]+\.?\s+)?([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})$", s)
    if m and m.group(1) in _MONTHS:
        return _ymd(m.group(3), _MONTHS[m.group(1)], m.group(2))
    m = re.match(r"^(\d{4})-(\d{1,2})$", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    m = re.match(r"^([a-z]+)\.?\s+(\d{4})$", s)
    if m and m.group(1) in _MONTHS:
        return f"{int(m.group(2)):04d}-{_MONTHS[m.group(1)]:02d}"
    m = re.match(r"^(\d{4})$", s)
    if m:
        return m.group(1)
    return ""


def is_full_date(d: str) -> bool:
    return len(d or "") == 10


def date_year(d: str) -> int:
    return int(d[:4]) if d and d[:4].isdigit() else 0


def is_past(d: str, today: date) -> bool:
    """True when the (possibly partial) date lies entirely before `today`."""
    if not d:
        return False
    if is_full_date(d):
        return d < today.isoformat()
    if len(d) == 7:
        return d < today.isoformat()[:7]
    return date_year(d) < today.year


def date_sort_key(d: str) -> str:
    return ((d or "9999") + "-99-99")[:10]


_TIME_RE = re.compile(r"(?<![\d.:])(\d{1,2})(?::|h)(\d{2})(?![\d:])|(?<![\d.])(\d{1,2})\.(\d{2})\s*(?:uur|u)\b")


def parse_when(text) -> str:
    """Parse a ticket-window moment into Amsterdam local time.

    Returns 'YYYY-MM-DDTHH:MM', a bare 'YYYY-MM-DD' when no time is known, or ''.
    Timezone-aware ISO input (e.g. Ticketmaster's UTC '...Z') is converted.
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    iso = raw
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}", iso):
        iso = iso.replace(" ", "T", 1)
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}", iso):
        candidate = iso.split(" ")[0].replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(TZ)
            return dt.strftime("%Y-%m-%dT%H:%M")
    tm = _TIME_RE.search(raw)
    date_part = raw[:tm.start()] if tm else raw
    date_part = re.sub(r"(?:\s+(?:at|om|vanaf|from|starting))+\s*$", "", date_part.strip(" ,@-"), flags=re.I)
    d = parse_date(date_part or raw)
    if not is_full_date(d):
        return ""
    if tm:
        hh, mm = (tm.group(1), tm.group(2)) if tm.group(1) else (tm.group(3), tm.group(4))
        if int(hh) < 24 and int(mm) < 60:
            return f"{d}T{int(hh):02d}:{int(mm):02d}"
    return d


def when_dt(when: str) -> datetime | None:
    """Aware datetime for a parse_when() value (date-only -> midnight)."""
    if not when:
        return None
    try:
        if "T" in when:
            return datetime.strptime(when, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
        return datetime.strptime(when, "%Y-%m-%d").replace(tzinfo=TZ)
    except ValueError:
        return None


def has_time(when: str) -> bool:
    return "T" in (when or "")


def fmt_date(d: str) -> str:
    """'2026-11-29' -> 'Sun 29 Nov 2026'; partial dates stay readable."""
    if is_full_date(d):
        try:
            return datetime.strptime(d, "%Y-%m-%d").strftime("%a %-d %b %Y")
        except ValueError:
            return d
    if len(d or "") == 7:
        try:
            return datetime.strptime(d, "%Y-%m").strftime("%b %Y") + " (day TBA)"
        except ValueError:
            return d
    return f"{d} (date TBA)" if d else "date TBA"


def fmt_when(when: str, today: date | None = None) -> str:
    """'2026-10-01T10:00' -> 'Wed 1 Oct 10:00' (year added when not this year)."""
    dt = when_dt(when)
    if dt is None:
        return "date TBA"
    fmt = "%a %-d %b"
    if today is None or dt.year != today.year:
        fmt += " %Y"
    out = dt.strftime(fmt)
    return out + dt.strftime(" %H:%M") if has_time(when) else out
