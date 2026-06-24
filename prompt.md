Weekly worldwide monitor. Reads my full Artists + Festivals lists, deep-searches
each artist EVERYWHERE step by step, writes the full schedule + festivals to
Google Sheets, and summarizes to me in chat. Respond in English.

Env: GOOGLE_SA_JSON and SHEET_ID (from the environment, not .env). The script
applies the country filter and labels artists - so dump EVERYTHING you find,
including countries you think are excluded.

1. pip install -r requirements.txt
2. python concert_sheets.py state
   -> JSON with "artists" and "festivals". Both stop at the first blank row.

JOB A - ARTISTS:
3. Go through the artists ONE BY ONE (step by step). For EACH artist, find their
   COMPLETE upcoming tour over the next 24 months. Apply this geography filter -
   include an event only if its country is:
     (a) EU or Schengen (always OK - I'm a NL resident; incl. Switzerland,
         Norway, Iceland), OR
     (b) visa-free / visa-on-arrival for a Russian passport (Thailand, Turkey,
         Serbia, Montenegro, UAE, Qatar, Brazil, Argentina, Chile, South Africa,
         Israel, Georgia, Armenia, Kazakhstan, Indonesia, etc.).
   SKIP where a Russian passport needs a visa/eVisa (UK, USA, Canada, Australia,
   New Zealand, Japan, China, Taiwan, India; Ireland is EU but not Schengen).
   METHOD (high recall):
   - Open the artist's Songkick AND Bandsintown page - one page lists the whole
     tour at once. Confirm on the official site / "tour" page.
   - Disambiguate tricky names (Currents, Nervy, Oasis) with genre or
     "band / tour / tickets"; verify on the official site.
   - Run several queries per artist; don't stop at the first result.
   Tag each "event_type": Solo / Gig / Festival. Include shows even if tickets
   are NOT on sale yet. Capture "status" and "on_sale" (YYYY-MM-DD).
   Put the allowed-region shows into events.json - array of:
   {"artist","date","city","venue","country","event_type","status","on_sale","url"}

JOB B - FESTIVALS:
4. For EACH festival in "festivals", search the FESTIVAL ITSELF (dates, city,
   country, ticket status, on-sale) regardless of lineup. Put into
   festivals.json - array of:
   {"festival","start","end","city","country","status","on_sale","url"}
   start/end YYYY-MM-DD (end = start if one day).

5. python concert_sheets.py append events.json
6. python concert_sheets.py append-festivals festivals.json
7. Summarize to me in chat: the NEWLY added concerts (from "added_events") in
   Artists-tab order, and any new festival info. If nothing new: "No new
   announcements this week."

The script writes the schedule grouped by artist (Artists-tab order), sorted by
date, blank row between artists. Artists with no concerts get "No Concerts".