"""Static knowledge used for canonicalisation: NL venues, NL festivals, common
artist-name variants and ticket-resale / spam domains.

Everything here is generic (no personal data); the watchlist itself lives in
the private Google Sheet.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .normalize import fold, name_key, words

# canonical venue -> (city, aliases). Rooms ("Tivoli Ronda", "Melkweg Max") are
# folded onto the venue by prefix matching in venue_key().
VENUES: dict[str, tuple[str, list[str]]] = {
    "Ziggo Dome": ("Amsterdam", []),
    "AFAS Live": ("Amsterdam", ["Heineken Music Hall", "HMH", "AFAS Arena", "AFAS Live Arena"]),
    "Johan Cruijff ArenA": ("Amsterdam", ["Johan Cruyff Arena", "Amsterdam ArenA", "ArenA Amsterdam"]),
    "Paradiso": ("Amsterdam", []),
    "Paradiso Noord": ("Amsterdam", ["Tolhuistuin"]),
    "Melkweg": ("Amsterdam", []),
    "Gashouder": ("Amsterdam", ["Westergas Gashouder", "Westergasfabriek Gashouder"]),
    "Carré": ("Amsterdam", ["Koninklijk Theater Carré", "Theater Carré"]),
    "Concertgebouw": ("Amsterdam", []),
    "Bitterzoet": ("Amsterdam", []),
    "Q-Factory": ("Amsterdam", []),
    "TivoliVredenburg": ("Utrecht", ["Tivoli Vredenburg", "Tivoli"]),
    "EKKO": ("Utrecht", []),
    "De Helling": ("Utrecht", []),
    "Rotterdam Ahoy": ("Rotterdam", ["Ahoy", "Ahoy Rotterdam", "RTM Stage"]),
    "Maassilo": ("Rotterdam", []),
    "Baroeg": ("Rotterdam", []),
    "Rotown": ("Rotterdam", []),
    "Annabel": ("Rotterdam", []),
    "De Kuip": ("Rotterdam", ["Stadion Feijenoord", "Feijenoord Stadion"]),
    "GelreDome": ("Arnhem", []),
    "Luxor Live": ("Arnhem", []),
    "Musis Sacrum": ("Arnhem", ["Musis"]),
    "Doornroosje": ("Nijmegen", []),
    "Goffertpark": ("Nijmegen", []),
    "013": ("Tilburg", []),
    "Effenaar": ("Eindhoven", []),
    "Dynamo": ("Eindhoven", []),
    "Klokgebouw": ("Eindhoven", []),
    "IJssportcentrum": ("Eindhoven", []),
    "Philips Stadion": ("Eindhoven", []),
    "Patronaat": ("Haarlem", []),
    "Oosterpoort": ("Groningen", []),
    "Vera": ("Groningen", []),
    "Hedon": ("Zwolle", []),
    "Paard": ("Den Haag", ["Paard van Troje"]),
    "AFAS Circustheater": ("Den Haag", ["Circustheater"]),
    "Malieveld": ("Den Haag", []),
    "Boerderij": ("Zoetermeer", []),
    "Gebouw-T": ("Bergen op Zoom", []),
    "MECC": ("Maastricht", []),
    "Muziekgieterij": ("Maastricht", []),
    "Stadsgehoorzaal": ("Leiden", []),
    "Metropool": ("Hengelo", []),
    "Nieuwe Nor": ("Heerlen", []),
    "De Kade": ("Zaandam", []),
    "Megaland": ("Landgraaf", []),
    "Walibi Holland": ("Biddinghuizen", []),
    "Beekse Bergen": ("Hilvarenbeek", ["Safaripark Beekse Bergen", "Strand Beekse Bergen"]),
    "Strand Anders": ("Huissen", []),
}

# canonical festival -> (city, aliases). Used both to recognise festival
# appearances hidden in venue strings and to give the LLM a line-up checklist.
FESTIVALS: dict[str, tuple[str, list[str]]] = {
    "Pinkpop": ("Landgraaf", []),
    "Lowlands": ("Biddinghuizen", ["A Campingflight to Lowlands Paradise"]),
    "Down The Rabbit Hole": ("Beuningen", ["DTRH"]),
    "Best Kept Secret": ("Hilvarenbeek", ["BKS"]),
    "Paaspop": ("Schijndel", []),
    "Zwarte Cross": ("Lichtenvoorde", []),
    "Jera On Air": ("Ysselsteyn", []),
    "Dynamo Metalfest": ("Eindhoven", ["Dynamo Open Air"]),
    "Into The Grave": ("Leeuwarden", []),
    "Fortarock": ("Nijmegen", []),
    "Bospop": ("Weert", []),
    "South of Heaven": ("", ["South of Heaven Open Air"]),
    "Amsterdam Open Air": ("Amsterdam", []),
    "North Sea Jazz": ("Rotterdam", []),
    "Dauwpop": ("Hellendoorn", []),
    "Defqon.1": ("Biddinghuizen", ["Defqon 1", "Defqon1", "Defqon"]),
    "Dominator": ("Eindhoven", []),
    "Intents": ("Oisterwijk", []),
    "Qlimax": ("Arnhem", []),
    "Thunderdome": ("", []),
    "Masters of Hardcore": ("", ["MOH"]),
    "Decibel Outdoor": ("Hilvarenbeek", ["Decibel"]),
    "Harmony of Hardcore": ("Erp", []),
    "Vroeger Was Alles Beter": ("Rosmalen", ["VWAB"]),
    "Mysteryland": ("Haarlemmermeer", []),
    "Awakenings": ("Hilvarenbeek", []),
    "Amsterdam Dance Event": ("Amsterdam", ["ADE"]),
    "Dekmantel": ("Amsterdam", []),
    "DGTL": ("Amsterdam", []),
    "Under The Milky Way": ("Huissen", ["Under The Milkyway"]),
}

# Aliases for well-known name variants; only used when the canonical name is on
# the watchlist. More can be added per artist in the sheet (column B).
ARTIST_ALIASES: dict[str, list[str]] = {
    "Noel Gallagher's High Flying Birds": ["Noel Gallagher", "NGHFB", "High Flying Birds"],
    "Thirty Seconds To Mars": ["30 Seconds To Mars", "30STM"],
    "Twenty One Pilots": ["21 Pilots"],
    "Red Hot Chili Peppers": ["RHCP"],
    "Bring Me The Horizon": ["BMTH"],
    "My Chemical Romance": ["MCR"],
    "Bullet For My Valentine": ["BFMV"],
    "Oxxxymiron": ["Оксимирон", "Oxymiron", "Oxxymiron"],
    "Pornofilmy": ["Порнофильмы", "Pornofilmi"],
    "Tyler The Creator": ["Tyler, The Creator"],
    "Childish Gambino": ["Donald Glover"],
    "The 1975": ["1975"],
    "CHVRCHES": ["Churches"],
    "T78": ["T-78"],
    "Phonk Killazz": ["Phonk Killaz"],
}

# Resale / scalper / SEO-spam domains: never a basis for a "new show" alert.
UNTRUSTED_DOMAINS = {
    "viagogo", "stubhub", "ticketswap", "seatgeek", "vividseats", "gigsberg",
    "ticombo", "ticketbande", "topticketshop", "ticketnetwork", "concertful",
    "tixel", "ticketsource", "livefootballtickets", "stereoboard",
}
_UNTRUSTED_PATTERNS = [re.compile(r"tour-?20\d\d"), re.compile(r"(^|\.)tickets?-?\d{4}\.")]

_GENERIC_VENUE_WORDS = {"arena", "dome", "hall", "stadion", "stadium", "theater", "theatre"}

_NL_CITIES = {
    "amsterdam", "utrecht", "rotterdam", "arnhem", "nijmegen", "tilburg", "eindhoven",
    "haarlem", "groningen", "zwolle", "den haag", "the hague", "zoetermeer",
    "bergen op zoom", "maastricht", "leiden", "hengelo", "heerlen", "zaandam",
    "landgraaf", "biddinghuizen", "hilvarenbeek", "huissen", "breda", "den bosch",
    "s hertogenbosch", "leeuwarden", "enschede", "deventer", "amersfoort", "alkmaar",
    "hellendoorn", "ysselsteyn", "schijndel", "lichtenvoorde", "rosmalen", "weert",
}


def _venue_tokens(text: str) -> list[str]:
    s = fold(text)
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    # "Ziggo Dome, Amsterdam" / "AFAS Live/Arena" / "Venue - Festival" / "Venue w/ X"
    s = re.split(r"\s[-|]\s|/|,|\sw/|\swith\s|\sft\.?\s|\sfeat\.?\s|\ssupport\s", " " + s + " ")[0]
    tokens = [t for t in words(s).split() if t != "poppodium"]
    while tokens and tokens[0] in ("the", "de", "het"):
        tokens.pop(0)
    # a city at either end is noise ("Ziggo Dome Amsterdam", "Utrecht TivoliVredenburg"),
    # unless what's left is generic ("Amsterdam ArenA" must not become "arena")
    for n in (2, 1):
        if (len(tokens) > n and " ".join(tokens[-n:]) in _NL_CITIES
                and " ".join(tokens[:-n]) not in _GENERIC_VENUE_WORDS):
            tokens = tokens[:-n]
        if (len(tokens) > n and " ".join(tokens[:n]) in _NL_CITIES
                and " ".join(tokens[n:]) not in _GENERIC_VENUE_WORDS):
            tokens = tokens[n:]
    return tokens


def _build_venue_lookup():
    lookup = {}
    for canon, (_city, aliases) in VENUES.items():
        ck = "".join(_venue_tokens(canon))
        lookup[ck] = canon
        for a in aliases:
            lookup.setdefault("".join(_venue_tokens(a)), canon)
    return lookup


_VENUE_LOOKUP = _build_venue_lookup()
_VENUE_PREFIXES = sorted((k for k in _VENUE_LOOKUP if len(k) >= 5), key=len, reverse=True)


def canonical_venue(venue: str) -> str:
    """Known NL venue name for a messy venue string, or '' when unknown."""
    key = "".join(_venue_tokens(venue))
    if not key:
        return ""
    if key in _VENUE_LOOKUP:
        return _VENUE_LOOKUP[key]
    if "fest" not in key:
        for p in _VENUE_PREFIXES:
            if key.startswith(p):
                return _VENUE_LOOKUP[p]
    return ""


def venue_key(venue: str) -> str:
    """Stable comparison key: 'Ziggo Dome (w/ Architects)' == 'ziggodome'."""
    canon = canonical_venue(venue)
    if canon:
        return "".join(_venue_tokens(canon))
    return "".join(_venue_tokens(venue))


def venue_city(venue: str) -> str:
    canon = canonical_venue(venue)
    return VENUES[canon][0] if canon else ""


# --------------------------------------------------------------------------- #
_FEST_NOISE = {"festival", "editie", "edition", "weekend"}


def _festival_clean(text: str, keep_brackets: bool = False) -> str:
    s = fold(text)
    if not keep_brackets:
        s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s)
    w = words(s).split()
    w = [t for t in w if t not in _FEST_NOISE] or w
    return " ".join(w)


def _build_festival_lookup():
    lookup, phrases = {}, []
    for canon, (_city, aliases) in FESTIVALS.items():
        for name in [canon, *aliases]:
            cleaned = _festival_clean(name)
            lookup.setdefault(cleaned.replace(" ", ""), canon)
            phrases.append((cleaned, cleaned.replace(" ", ""), canon))
    phrases.sort(key=lambda p: len(p[1]), reverse=True)
    return lookup, phrases


_FEST_LOOKUP, _FEST_PHRASES = _build_festival_lookup()


def festival_key(name: str) -> str:
    """'Dynamo Metal Fest 2026' and 'Dynamo MetalFest' -> 'dynamometalfest'."""
    canon = canonical_festival(name)
    return _festival_clean(canon or name).replace(" ", "")


def canonical_festival(name: str) -> str:
    """Known festival for an explicit festival name, '' when unknown."""
    cleaned = _festival_clean(name)
    return _FEST_LOOKUP.get(cleaned.replace(" ", ""), "") if cleaned else ""


def find_festival(*texts: str) -> str:
    """Known festival mentioned anywhere in the given texts, brackets included
    ("IJssportcentrum (Dynamo Metal Fest)", "Megaland (Pinkpop)")."""
    for text in texts:
        cleaned = _festival_clean(text or "", keep_brackets=True)
        hay_words, hay_compact = f" {cleaned} ", cleaned.replace(" ", "")
        for phrase, compact, canon in _FEST_PHRASES:
            if len(compact) >= 6:
                if compact in hay_compact:
                    return canon
            elif phrase and f" {phrase} " in hay_words:
                return canon
    return ""


def festival_city(name: str) -> str:
    canon = canonical_festival(name)
    return FESTIVALS[canon][0] if canon else ""


# --------------------------------------------------------------------------- #
def domain(url: str) -> str:
    try:
        host = urlparse(url if "://" in (url or "") else f"https://{url}").hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def is_trusted_url(url: str) -> bool:
    """A real, non-resale URL (the only kind that may back a 'new show' alert)."""
    if not url or not re.match(r"^https?://", url.strip(), re.I):
        return False
    host = domain(url)
    if not host or "." not in host:
        return False
    labels = host.split(".")
    if any(lbl in UNTRUSTED_DOMAINS for lbl in labels):
        return False
    return not any(p.search(host) for p in _UNTRUSTED_PATTERNS)


_EVENT_PAGE = re.compile(r"/(event|events|evenement|evenementen|agenda|programma|program|concert|concerten|"
                         r"show|shows)/[^/?#]+|edp\d+", re.I)
_OVERVIEW_PAGE = re.compile(r"/artist/|-tickets-adp\d+|/artists?/\d+|/a/\d+", re.I)


def url_rank(url: str) -> int:
    """How useful a link is for buying: 3 = the show's own event / ticket page
    (Live Nation, Ticketmaster, venue agenda), 2 = official site, news or line-up
    page, 1 = an artist's overview page (proves no specific show), 0 = untrusted."""
    if not is_trusted_url(url):
        return 0
    path = urlparse(url).path
    if _OVERVIEW_PAGE.search(path):
        return 1
    if _EVENT_PAGE.search(path):
        return 3
    return 2


def best_url(*urls: str) -> str:
    """The most useful of the given links (earlier ones win ties)."""
    ranked = [(url_rank(u), -i, u) for i, u in enumerate(urls) if u]
    return max(ranked)[2] if ranked and max(ranked)[0] > 0 else ""


def artist_aliases(name: str) -> list[str]:
    key = name_key(name)
    for canon, aliases in ARTIST_ALIASES.items():
        if name_key(canon) == key:
            return list(aliases)
    return []
