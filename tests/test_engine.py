from tracker.engine import Engine, prune

from .conftest import at, kinds

TM_MUSE = "https://www.ticketmaster.nl/artist/muse-tickets/26326"


def muse(date="2026-11-29", **extra):
    return {"artist": "Muse", "date": date, "venue": "Ziggo Dome", "city": "Amsterdam",
            "url": TM_MUSE, **extra}


def test_new_show_alerts_once_despite_spelling_changes(runner):
    r1 = runner.run("2026-09-27T09:00", {"shows": [muse(status="announced")]})
    assert kinds(r1.alerts) == ["new"]
    variants = [
        {"artist": "MUSE", "date": "29 Nov 2026", "venue": "Ziggo Dome (Amsterdam)", "url": TM_MUSE},
        {"artist": "muse", "date": "29/11/2026", "venue": "Ziggo Dome, Amsterdam"},
        {"id": "muse-2026-11-29", "artist": "Muse", "date": "2026-11-29", "venue": "ZiggoDome"},
    ]
    for day, v in zip(("2026-09-28", "2026-09-29", "2026-09-30"), variants):
        assert runner.run(f"{day}T09:00", {"shows": [v]}).alerts == []
    assert len(runner.state.shows) == 1


def test_festival_venue_spellings_collapse_into_one_show(runner):
    rows = ["Dynamo Metal Fest / IJssportcentrum", "Dynamo Metalfest", "IJssportcentrum (Dynamo MetalFest)",
            "IJssportcentrum Eindhoven (Dynamo Metalfest)", "Dynamo Metalfest (IJssportcentrum)"]
    alerts = []
    for i, venue in enumerate(rows):
        date = "2027-08-15" if i % 2 else "2027-08-14"         # the old data flip-flopped days too
        alerts += runner.run(f"2026-10-0{i + 1}T09:00", {"shows": [{
            "artist": "Lamb of God", "date": date, "venue": venue, "city": "Eindhoven",
            "event_type": "Festival", "url": "https://dynamo-metalfest.nl/bands/lamb-of-god/"}]}).alerts
    assert kinds(alerts) == ["new"]
    (show,) = runner.state.shows.values()
    assert show.festival == "Dynamo Metalfest" and show.date == "2027-08-14"


def test_support_act_merges_into_headline_show(runner):
    r = runner.run("2026-09-27T09:00", {"shows": [
        {"artist": "Korn", "lineup": "Korn + Architects", "date": "2026-11-08", "venue": "Ziggo Dome",
         "url": "https://www.ticketmaster.nl/artist/korn-tickets/3404"},
        {"artist": "Architects", "date": "2026-11-08", "venue": "Ziggo Dome (support for Korn)"}]})
    assert kinds(r.alerts) == ["new"] and len(runner.state.shows) == 1
    assert runner.state.shows["korn-2026-11-08"].artists == ["Korn", "Architects"]


def test_support_act_added_later_is_a_lineup_alert(runner):
    runner.run("2026-09-27T09:00", {"shows": [{"artist": "Korn", "date": "2026-11-08", "venue": "Ziggo Dome",
                                               "url": "https://www.ticketmaster.nl/x"}]})
    r = runner.run("2026-09-28T09:00", {"shows": [{"artist": "Architects", "date": "2026-11-08",
                                                   "venue": "Ziggo Dome (support to Korn)"}]})
    assert kinds(r.alerts) == ["lineup"]
    assert runner.run("2026-09-29T09:00", {"shows": [{"artist": "Architects", "date": "2026-11-08",
                                                      "venue": "Ziggo Dome"}]}).alerts == []


def test_ticket_windows_alert_once_each(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse()]})
    r = runner.run("2026-09-27T09:00", {"shows": [muse(sales=[{"type": "presale"}])]})
    assert kinds(r.alerts) == ["sale"]                                   # presale announced, date TBA
    r = runner.run("2026-09-28T09:00", {"shows": [muse(sales=[{"type": "presale", "start": "2026-10-01T10:00"}])]})
    assert kinds(r.alerts) == ["sale"]                                   # ...now with its date
    r = runner.run("2026-09-29T09:00", {"shows": [muse(sales=[
        {"type": "Artist presale", "start": "2026-10-01 10:00"},          # same window, other wording
        {"type": "general", "start": "2026-10-03T10:00"}])]})
    assert kinds(r.alerts) == ["sale"]                                   # only the general sale is new
    assert runner.run("2026-09-30T08:00", {"shows": [muse(sales=[
        {"type": "presale", "start": "2026-10-01T10:00"},
        {"type": "general", "start": "2026-10-03T10:00"}])]}).alerts == []


def test_registration_and_lottery_windows(runner):
    r = runner.run("2026-09-10T09:00", {"shows": [{
        "artist": "Oasis", "date": "2027-07-16", "venue": "Johan Cruijff ArenA", "status": "registration",
        "url": "https://oasisinet.com/", "sales": [
            {"type": "registration", "start": "2026-09-10T09:00", "end": "2026-09-17T17:00"}]}]})
    assert kinds(r.alerts) == ["new"]
    r = runner.run("2026-09-22T09:00", {"shows": [{
        "artist": "Oasis", "date": "2027-07-16", "venue": "Johan Cruyff Arena", "status": "presale",
        "sales": [{"type": "ballot / unique code sale", "start": "2026-09-25", "end": "2026-09-27"}]}]})
    assert kinds(r.alerts) == ["sale"]
    assert r.alerts[0].data["sale"]["kind"] == "lottery"


def test_corrected_window_date_is_not_a_second_presale(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(sales=[{"type": "presale", "start": "2026-10-01T10:00"}])]})
    r = runner.run("2026-09-21T09:00", {"shows": [muse(sales=[{"type": "presale", "start": "2026-10-02T10:00"}])]})
    assert r.alerts == []
    (show,) = runner.state.shows.values()
    assert [w.start for w in show.sales] == ["2026-10-02T10:00"]


def test_two_presales_in_one_report_are_kept(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(sales=[
        {"type": "presale", "start": "2026-10-01T10:00"}, {"type": "presale", "start": "2026-10-02T10:00"}])]})
    (show,) = runner.state.shows.values()
    assert len(show.sales) == 2


def test_status_changes(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(status="on sale")]})
    r = runner.run("2026-09-21T09:00", {"shows": [muse(status="Uitverkocht")]})
    assert kinds(r.alerts) == ["sold_out"] and not r.alerts[0].loud
    assert runner.run("2026-09-22T09:00", {"shows": [muse(status="sold out")]}).alerts == []
    # "on sale" after "sold out" is usually noise ...
    assert runner.run("2026-09-23T09:00", {"shows": [muse(status="on sale")]}).alerts == []
    assert runner.state.shows["muse-2026-11-29"].status == "sold_out"
    # ... unless explicitly a restock
    r = runner.run("2026-09-24T09:00", {"shows": [muse(status="extra tickets released")]})
    assert kinds(r.alerts) == ["restock"]
    r = runner.run("2026-09-25T09:00", {"shows": [muse(status="cancelled")]})
    assert kinds(r.alerts) == ["cancelled"] and r.alerts[0].loud


def test_status_step_back_needs_a_second_report(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(status="sold out")]})
    assert runner.run("2026-09-21T09:00", {"shows": [muse(status="on_sale")]}).alerts == []
    assert runner.run("2026-09-21T18:00", {"shows": [muse(status="on_sale")]}).alerts == []   # same day
    r = runner.run("2026-09-22T09:00", {"shows": [muse(status="on_sale")]})
    assert kinds(r.alerts) == ["restock"]
    assert runner.state.shows["muse-2026-11-29"].status == "on_sale"


def test_live_alert_only_when_sale_start_is_news(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(status="announced")]})
    r = runner.run("2026-09-21T09:00", {"shows": [muse(status="on_sale")]})
    assert kinds(r.alerts) == ["live"]                                   # nothing told before -> tell now
    runner.run("2026-09-20T09:00", {"shows": [muse(date="2026-11-30", status="announced",
                                                   sales=[{"type": "general", "start": "2026-10-03T10:00"}])]})
    assert runner.run("2026-10-03T12:00", {"shows": [muse(date="2026-11-30", status="on_sale")]}).alerts == []


def test_old_sale_is_not_reported_as_just_on_sale(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(status="announced")]})
    r = runner.run("2026-09-21T09:00", {"shows": [muse(status="on_sale", sales=[
        {"type": "general", "start": "2026-04-24T10:00"}])]})
    assert r.alerts == []


def test_date_change_by_id(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse()]})
    r = runner.run("2026-09-21T09:00", {"shows": [{"id": "muse-2026-11-29", "artist": "Muse",
                                                   "date": "2026-12-05", "venue": "Ziggo Dome"}]})
    assert kinds(r.alerts) == ["date"]
    assert runner.state.shows["muse-2026-11-29"].date == "2026-12-05"


def test_multi_night_run_is_two_shows(runner):
    r = runner.run("2026-09-20T09:00", {"shows": [muse("2026-11-29"), muse("2026-11-30")]})
    assert kinds(r.alerts) == ["new", "new"] and len(runner.state.shows) == 2


def test_wrong_year_does_not_create_a_duplicate(runner):
    runner.run("2026-06-01T09:00", {"shows": [{"artist": "Oasis", "date": "2027-07-16",
                                               "venue": "Johan Cruijff ArenA", "url": "https://oasisinet.com/"}]})
    r = runner.run("2026-06-02T09:00", {"shows": [{"artist": "Oasis", "date": "2026-07-16",
                                                   "venue": "Johan Cruyff Arena", "url": "https://oasisinet.com/"}]})
    assert r.alerts == [] and len(runner.state.shows) == 1
    assert any("wrong year" in w for w in r.warnings)


def test_filters_past_foreign_unknown_and_unreadable(runner):
    r = runner.run("2026-09-27T09:00", {"shows": [
        muse(date="2026-04-04"),
        muse(date="2026-12-01", country="Germany"),
        {"artist": "Evanescence", "date": "2026-12-01", "venue": "Ziggo Dome", "url": TM_MUSE},
        muse(date="sometime soon"),
    ]})
    assert r.alerts == [] and runner.state.shows == {}
    assert r.skipped_past == 1 and len(r.warnings) == 3


def test_untrusted_source_waits_for_confirmation(runner):
    r = runner.run("2026-09-27T09:00", {"shows": [{"artist": "Paramore", "date": "2027-10-05",
                                                   "venue": "Ziggo Dome", "url": "https://tour2027.com/paramore"}]})
    assert r.alerts == [] and not runner.state.shows["paramore-2027-10-05"].verified
    r = runner.run("2026-09-28T09:00", {"shows": [{"artist": "Paramore", "date": "2027-10-05",
                                                   "venue": "Ziggo Dome",
                                                   "url": "https://www.ziggodome.nl/en/events/paramore"}]})
    assert kinds(r.alerts) == ["new"]


def test_dismissed_show_stays_quiet(runner):
    runner.run("2026-09-27T09:00", {"shows": [{"artist": "Paramore", "date": "2027-10-05",
                                               "venue": "Ziggo Dome", "url": "https://tour2027.com/x"}],
                                    "dismiss": []})
    runner.run("2026-09-27T10:00", {"dismiss": [{"id": "paramore-2027-10-05", "reason": "spam"}]})
    r = runner.run("2026-09-28T09:00", {"shows": [{"artist": "Paramore", "date": "2027-10-05",
                                                   "venue": "Ziggo Dome", "status": "sold out",
                                                   "url": "https://tour2027.com/x"}]})
    assert r.alerts == [] and runner.state.shows["paramore-2027-10-05"].dismissed


def test_dismissal_survives_the_same_stale_page(runner):
    stale = "https://www.pinkpop.nl/line-up/twenty-one-pilots/"
    report = {"artist": "Twenty One Pilots", "date": "2027-06-19", "festival": "Pinkpop", "url": stale}
    runner.run("2026-09-27T09:00", {"shows": [report]})
    runner.run("2026-09-27T10:00", {"dismiss": [{"id": "twenty-one-pilots-2027-06-19",
                                                 "reason": "2027 line-up not announced; stale 2026 page"}]})
    show = runner.state.shows["twenty-one-pilots-2027-06-19"]
    assert show.evidence == ["pinkpop.nl"]
    # the researcher keeps finding the same stale page (another path on the same site) ...
    other_path = {**report, "id": show.id, "url": "https://www.pinkpop.nl/en/line-up/twenty-one-pilots/"}
    assert runner.run("2026-09-28T09:00", {"shows": [other_path]}).alerts == []
    assert show.dismissed
    # ... only an independent source brings it back (quietly: it was announced before)
    confirmed = {**report, "id": show.id, "source": "https://festileaks.com/2026/11/pinkpop-2027-eerste-namen/"}
    assert runner.run("2026-11-20T09:00", {"shows": [confirmed]}).alerts == []
    assert not show.dismissed and "festileaks.com" in show.history[-1]


def test_festival_partial_date_gets_refined_silently(runner):
    r = runner.run("2026-11-20T09:00", {"shows": [{"artist": "Architects", "date": "2027",
                                                   "festival": "Pinkpop 2027", "url": "https://www.pinkpop.nl/x"}]})
    assert kinds(r.alerts) == ["new"]
    r = runner.run("2027-01-20T09:00", {"shows": [{"artist": "Architects", "date": "2027-06-19",
                                                   "festival": "Pinkpop", "venue": "Megaland"}]})
    assert r.alerts == []
    (show,) = runner.state.shows.values()
    assert show.date == "2027-06-19" and show.festival == "Pinkpop"


def test_news_is_deduplicated_by_url_and_meaning(runner):
    item = {"artist": "Radiohead", "type": "registration", "url": "https://www.radiohead.com/news/1",
            "text": "Radiohead open presale registration for the 2027 European tour, NL dates TBA; closes 3 Oct"}
    assert kinds(runner.run("2026-09-27T09:00", {"news": [item]}).alerts) == ["news"]
    assert runner.run("2026-09-28T09:00", {"news": [item]}).alerts == []
    reworded = {**item, "url": "https://www.nme.com/news/radiohead",
                "text": "Radiohead 2027 European tour: presale registration now open (NL dates TBA), closes Oct 3"}
    assert runner.run("2026-09-28T09:00", {"news": [reworded]}).alerts == []
    spam = {**item, "url": "https://radioheadtour2027.com/", "text": "Radiohead Amsterdam 2027 confirmed"}
    assert runner.run("2026-09-28T09:00", {"news": [spam]}).alerts == []


def test_informational_news_is_remembered_not_sent(runner):
    recap = {"artist": "Oasis", "type": "other", "url": "https://help.ticketmaster.com/hc/en-us/articles/1",
             "text": "No general sale for the NL shows: only fans with a unique code from the ballot can buy"}
    assert runner.run("2026-09-27T09:00", {"news": [recap]}).alerts == []
    assert [n["artist"] for n in runner.state.news] == ["Oasis"]            # shown in the brief as known
    assert runner.run("2026-09-28T09:00", {"news": [recap]}).alerts == []
    assert len(runner.state.news) == 1
    tour = {"artist": "Oasis", "type": "tour", "url": "https://oasisinet.com/news/2028",
            "text": "Oasis announce 2028 European stadium tour; Amsterdam date to be confirmed"}
    alerts = runner.run("2026-09-29T09:00", {"news": [tour]}).alerts
    assert [a.kind for a in alerts] == ["news"] and alerts[0].loud


def test_baseline_records_everything_without_alerting(state, wl):
    eng = Engine(state, wl, at("2026-09-27T09:00"), origin="legacy", baseline=True)
    eng.ingest({"shows": [muse(sales=[{"type": "presale", "start": "2026-10-01T10:00"}]),
                          {"artist": "Paramore", "date": "2027-10-05", "venue": "Ziggo Dome",
                           "url": "https://tour2027.com/x"}]})
    assert eng.alerts == []
    assert "new|muse-2026-11-29" in state.sent and "new|paramore-2027-10-05" in state.sent
    # confirming the doubtful legacy show later must not announce it as new
    eng2 = Engine(state, wl, at("2026-09-28T09:00"))
    eng2.ingest({"shows": [{"artist": "Paramore", "date": "2027-10-05", "venue": "Ziggo Dome",
                            "url": "https://www.ziggodome.nl/x"}]})
    assert eng2.alerts == [] and state.shows["paramore-2027-10-05"].verified


def test_old_tracker_rows_start_unconfirmed(state, wl):
    from tracker.sheets import shows_table
    eng = Engine(state, wl, at("2026-09-27T09:00"), origin="legacy", baseline=True)
    eng.ingest({"shows": [{"artist": "Tom Odell", "date": "2026-11-03", "venue": "Ziggo Dome",
                           "url": "https://www.ticketmaster.nl/artist/tom-odell-tickets/912725"}]})
    show = state.shows["tom-odell-2026-11-03"]
    assert not show.verified and "new|tom-odell-2026-11-03" in state.sent     # known, but doubtful
    row = shows_table(state, at("2026-09-27T09:00").date())[1]
    assert row[4] == "Announced (unconfirmed)" and row[-2] == "no"


def test_sale_label_does_not_repeat_itself():
    from tracker.model import Sale
    assert Sale("registration", name="Registration (closed)").label() == "Registration (closed)"
    assert Sale("presale", name="Live Nation presale").label() == "Presale (Live Nation presale)"
    assert Sale("presale", name="presale").label() == "Presale"


def test_reminders(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse(sales=[{"type": "presale", "start": "2026-10-01T10:00"}]),
                                              muse("2026-11-30", sales=[{"type": "presale", "start": "2026-10-01T10:00"}])]})
    assert runner.remind("2026-09-29T09:00") == []                       # > 30h ahead
    day = runner.remind("2026-09-30T12:00")
    assert [a.data["stage"] for a in day] == ["opens", "opens"]
    assert runner.remind("2026-09-30T18:00") == []                       # already reminded
    soon = runner.remind("2026-10-01T09:10")
    assert [a.data["stage"] for a in soon] == ["soon", "soon"]
    assert runner.remind("2026-10-01T10:30") == []                       # window opened


def test_no_reminder_right_after_the_announcement(runner):
    r = runner.run("2026-09-30T12:00", {"shows": [muse(sales=[{"type": "presale", "start": "2026-10-01T10:00"}])]},
                   reminders=True)
    assert kinds(r.alerts) == ["new"]


def test_registration_closing_reminder(runner):
    runner.run("2026-09-10T09:00", {"shows": [{
        "artist": "Oasis", "date": "2027-07-16", "venue": "Johan Cruijff ArenA", "url": "https://oasisinet.com/",
        "sales": [{"type": "registration", "start": "2026-09-10T09:00", "end": "2026-09-17T17:00"}]}]})
    closing = runner.remind("2026-09-16T20:00")
    assert [a.data["stage"] for a in closing] == ["closes"]


def test_digest_weekly(state, wl):
    eng = Engine(state, wl, at("2026-09-28T08:00"))                     # a Monday
    assert eng.digest() is not None
    for k in eng.alerts[0].keys:
        state.sent[k] = "2026-09-28T08:00:00+02:00"
    assert Engine(state, wl, at("2026-09-28T20:00")).digest() is None
    assert Engine(state, wl, at("2026-09-29T08:00")).digest() is None   # not Monday


def test_prune_forgets_finished_shows(runner):
    runner.run("2026-09-20T09:00", {"shows": [muse("2026-09-25")]})
    prune(runner.state, at("2026-10-01T09:00").date())
    assert runner.state.shows == {}
