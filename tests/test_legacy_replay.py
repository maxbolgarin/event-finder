"""Replay of the old tracker's real 'Schedule NL' / 'Schedule' rows.

The old tracker de-duplicated on the exact venue string, so every re-spelling
("Dynamo Metalfest", "IJssportcentrum (Dynamo MetalFest)", ...) became a "new"
show and a new notification. Replayed as if seen before the shows happened,
the new engine must announce each real show exactly once.
"""

import csv
from collections import Counter
from pathlib import Path

from tracker.engine import Engine
from tracker.model import State
from tracker.sheets import legacy_rows_to_findings
from tracker.watchlist import Watchlist

from .conftest import at

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_nl_rows.csv"


def replay():
    rows = list(csv.reader(FIXTURE.open(encoding="utf-8")))
    findings = legacy_rows_to_findings(rows, nl_only=True)
    wl = Watchlist.from_names(sorted({r[0] for r in rows[1:]} - {"Devin Towndend"}))
    st = State(initialized="x")
    alerts = []
    for f in findings:                    # one report per "day", like the daily routine
        eng = Engine(st, wl, at("2026-01-15T09:00"))
        eng.ingest({"shows": [f]})
        for a in eng.alerts:
            for k in a.keys:
                st.sent[k] = "x"
        alerts += eng.alerts
    return findings, st, alerts


def test_every_real_show_is_announced_once():
    findings, st, alerts = replay()
    new = [a for a in alerts if a.kind == "new"]
    assert len(findings) == 120
    assert len(new) == len({a.show_id for a in new}) == len(
        [s for s in st.shows.values() if s.verified])            # no show announced twice
    announced = [st.shows[a.show_id] for a in new]
    concerts = Counter((a, s.date) for s in announced if not s.festival for a in s.artists)
    festivals = Counter((a, s.festival, s.date[:4]) for s in announced if s.festival for a in s.artists)
    assert max(concerts.values()) == 1 and max(festivals.values()) == 1
    assert len(new) < len(findings) / 2                            # the old tracker notified ~95 times


def test_known_duplicate_clusters_collapse():
    _, st, _ = replay()
    by_artist = Counter(a for s in st.shows.values() for a in s.artists)
    assert by_artist["Lamb of God"] == 2          # Dynamo Metalfest (13 spellings) + AFAS Live (7)
    assert by_artist["Poppy"] == 1                # 5 spellings of the Evanescence support slot
    assert by_artist["Currents"] == 2             # TivoliVredenburg (7 spellings) + South of Heaven
    assert by_artist["Devin Townsend"] == 1       # incl. the "Devin Towndend" typo row
    korn = [s for s in st.shows.values() if "Korn" in s.artists]
    assert len(korn) == 1 and korn[0].artists == ["Korn", "Architects"]


def test_spam_sources_never_alert():
    _, st, alerts = replay()
    alerted = {a.show_id for a in alerts}
    paramore = [s for s in st.shows.values() if "Paramore" in s.artists]
    assert paramore and not paramore[0].verified and paramore[0].id not in alerted
