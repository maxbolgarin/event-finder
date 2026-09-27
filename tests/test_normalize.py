import pytest

from tracker.catalog import (canonical_festival, festival_key, find_festival, is_trusted_url,
                             venue_key)
from tracker.normalize import fmt_when, is_past, name_key, parse_date, parse_when
from tracker.watchlist import Watchlist

from .conftest import at


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-29", "2026-11-29"),
    ("2026-11-29T20:00", "2026-11-29"),
    ("29-11-2026", "2026-11-29"),
    ("29/11/2026", "2026-11-29"),
    ("Sun 29 Nov 2026", "2026-11-29"),
    ("Nov 29, 2026", "2026-11-29"),
    ("29 november 2026", "2026-11-29"),
    ("zondag 29 november 2026", "2026-11-29"),
    ("2027-06", "2027-06"),
    ("June 2027", "2027-06"),
    ("2027", "2027"),
    ("2026-02-30", ""),
    ("TBA", ""),
    ("", ""),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-01T10:00", "2026-10-01T10:00"),
    ("2026-10-01 10:00", "2026-10-01T10:00"),
    ("2026-10-01T08:00:00Z", "2026-10-01T10:00"),          # UTC -> Amsterdam (CEST)
    ("2026-12-01T09:00:00Z", "2026-12-01T10:00"),          # UTC -> Amsterdam (CET)
    ("2026-10-01T10:00:00+02:00", "2026-10-01T10:00"),
    ("1 Oct 2026 10:00", "2026-10-01T10:00"),
    ("Wed 1 Oct 2026 at 10.00 uur", "2026-10-01T10:00"),
    ("2026-10-01", "2026-10-01"),
    ("soon", ""),
])
def test_parse_when(raw, expected):
    assert parse_when(raw) == expected


def test_is_past_handles_partial_dates():
    today = at("2026-09-27T12:00").date()
    assert is_past("2026-09-26", today)
    assert not is_past("2026-09-27", today)
    assert is_past("2026-08", today) and not is_past("2026-09", today)
    assert is_past("2025", today) and not is_past("2026", today)


def test_fmt_when_omits_current_year():
    today = at("2026-09-27T12:00").date()
    assert fmt_when("2026-10-01T10:00", today) == "Thu 1 Oct 10:00"
    assert fmt_when("2027-01-05", today) == "Tue 5 Jan 2027"


@pytest.mark.parametrize("variants", [
    ["AFAS Live", "AFAS Live (support for Five Finger Death Punch)",
     "AFAS Live (w/ Five Finger Death Punch)", "AFAS Arena", "AFAS Live/Arena", "Heineken Music Hall"],
    ["Ziggo Dome", "Ziggo Dome (w/ Architects)", "Ziggo Dome (support to Korn)",
     "Ziggo Dome, Amsterdam", "Ziggodome"],
    ["TivoliVredenburg", "Tivoli Vredenburg", "TivoliVredenburg (with Erra)", "Tivoli GZ",
     "TivoliVredenburg (Grote Zaal)", "TivoliVredenburg Ronda", "Utrecht TivoliVredenburg"],
    ["Johan Cruijff ArenA", "Johan Cruyff Arena", "Amsterdam ArenA"],
    ["013", "Poppodium 013"],
    ["EKKO", "Ekko"],
    ["Oosterpoort", "De Oosterpoort"],
])
def test_venue_variants_share_a_key(variants):
    assert len({venue_key(v) for v in variants}) == 1


def test_distinct_venues_stay_distinct():
    assert venue_key("Paradiso") != venue_key("Paradiso Noord")
    assert venue_key("Dynamo") != venue_key("Dynamo Metalfest")


@pytest.mark.parametrize("text,festival", [
    ("IJssportcentrum (Dynamo MetalFest)", "Dynamo Metalfest"),
    ("Dynamo Metal Fest / IJssportcentrum", "Dynamo Metalfest"),
    ("Megaland (Pinkpop)", "Pinkpop"),
    ("Safaripark Beekse Bergen (Decibel Outdoor 2026)", "Decibel Outdoor"),
    ("Defqon.1 Weekend Festival (Walibi Holland)", "Defqon.1"),
    ("Ziggo Dome", ""),
    ("Arcade Club", ""),                       # 'ADE' must not match inside words
])
def test_find_festival(text, festival):
    assert find_festival(text) == festival


def test_festival_names_canonicalise():
    assert festival_key("Dynamo Metal Fest 2026") == festival_key("Dynamo MetalFest")
    assert canonical_festival("Pinkpop Festival") == "Pinkpop"
    assert canonical_festival("Rock am Ring") == ""


@pytest.mark.parametrize("url,ok", [
    ("https://www.ticketmaster.nl/artist/oasis-tickets/3668", True),
    ("https://kink.nl/nieuws/x", True),
    ("https://www.ticketbande.co.uk/concert-tickets/oasis/1", False),
    ("https://www.topticketshop.com/placebo-tickets/1", False),
    ("https://tour2027.com/paramore-tour-2027/", False),
    ("https://linkinparktour2027.com/", False),
    ("https://www.viagogo.com/x", False),
    ("https://www.ticketswap.nl/event/x", False),
    ("not a url", False),
    ("", False),
])
def test_trusted_urls(url, ok):
    assert is_trusted_url(url) is ok


def test_name_key_folds_accents_quotes_and_the():
    assert name_key("Noel Gallagher’s High Flying Birds") == name_key("noel gallagher's high flying birds")
    assert name_key("twenty øne piløts") == name_key("Twenty One Pilots")
    assert name_key("The 1975") == name_key("1975")


def test_watchlist_resolution():
    wl = Watchlist.from_names(["Devin Townsend", "Noel Gallagher's High Flying Birds", "Liam Gallagher",
                               "Thirty Seconds To Mars", "A.N.I", "Oxxxymiron", "Muse", "Korn",
                               "Architects", "Poppy", "Lamb of God"])
    assert wl.resolve("Devin Towndend") == "Devin Townsend"                 # typo
    assert wl.resolve("Noel Gallagher") == "Noel Gallagher's High Flying Birds"
    assert wl.resolve("Liam Gallagher") == "Liam Gallagher"
    assert wl.resolve("30 Seconds to Mars") == "Thirty Seconds To Mars"
    assert wl.resolve("ANI") == wl.resolve("A.N.I.") == "A.N.I"
    assert wl.resolve("Оксимирон") == "Oxxxymiron"
    assert wl.resolve("Museum Night") is None
    assert wl.find_all("Korn + Architects") == ["Korn", "Architects"]
    assert wl.find_all("Evanescence w/ Poppy (support)") == ["Poppy"]
    assert wl.find_all("Five Finger Death Punch, Lamb of God") == ["Lamb of God"]
    assert wl.find_all("Museum Night Fever") == []
