Daily morning monitor. Two INDEPENDENT jobs, both written to Google Sheets and
summarized to me in chat. Respond in English. Goal: catch announcements early to
plan ticket purchases.

Env: GOOGLE_SA_JSON and SHEET_ID (read from the environment, NOT a .env file).
SKIP_COUNTRIES is applied by the script.

1. pip install -r requirements.txt
2. python concert_sheets.py state
   -> JSON with "artists" (artist names) and "festivals" (festival names).

JOB A - ARTISTS (independent of the festivals list):
3. For EACH artist, web-search ALL their announced shows over the next 24 months,
   ANYWHERE (any country), tagging each "event_type": Solo / Gig / Festival.
   Include shows even if tickets are NOT on sale yet. Capture "status"
   (Announced / Presale / On sale / Sold out) and "on_sale" date (YYYY-MM-DD).
   Put EVERYTHING into events.json - array of:
   {"artist","date","city","venue","country","event_type","status","on_sale","url"}
   Don't pre-filter or sort; the script dedups, groups and sorts.

JOB B - FESTIVALS (independent of artists):
4. For EACH festival name in "festivals", web-search the FESTIVAL ITSELF -
   its edition dates, city, country, ticket status and on-sale date - regardless
   of the lineup. Put into festivals.json - array of:
   {"festival","start","end","city","country","status","on_sale","url"}
   start/end in YYYY-MM-DD (end = start if it's a one-day festival).

SOURCES (all genres, NL-first): Ticketmaster, Bandsintown, Songkick,
MOJO (mojo.nl), Live Nation (livenation.nl), Podiuminfo, venue sites
(Ziggo Dome, AFAS Live, Paradiso, Melkweg, TivoliVredenburg, 013), official
artist & festival sites + Instagram / Facebook / X; for festivals also
Festileaks and Clashfinder; for electronic also Resident Advisor and Partyflock.
Ignore ticket-resale sites (Ticketswap, viagogo).

5. python concert_sheets.py append events.json
6. python concert_sheets.py append-festivals festivals.json
7. Summarize to me in chat (English): list the NEWLY added concerts (from the
   script output's "added_events") grouped by artist, and any new festival info.
   If nothing new: "No new announcements today."
   The script handles the table layout, "No concerts" rows, sorting and the Log.