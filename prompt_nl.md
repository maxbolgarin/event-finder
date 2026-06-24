Daily NL watcher. Reads my full Artists list, deep-searches the Netherlands only,
writes ONLY actual concerts (no placeholder rows) to the "Schedule NL" tab, and
summarizes to me in chat. Respond in English. Goal: catch NL announcements fast.

Env: GOOGLE_SA_JSON and SHEET_ID (from the environment, not .env).

1. pip install -r requirements.txt
2. python concert_sheets.py state
   -> JSON with "artists" (full list). Stops at the first blank row.
3. For EACH artist, do a DEEP search of their shows IN THE NETHERLANDS over the
   next 24 months (Amsterdam + all NL). Go through every NL source - don't stop
   at the first hit. Include shows even if tickets are NOT on sale yet; capture
   "status" (Announced/Presale/On sale/Sold out) and "on_sale" (YYYY-MM-DD). Tag
   "event_type": Solo / Gig / Festival.
   NL sources: Ticketmaster (ticketmaster.nl), MOJO (mojo.nl), Live Nation
   (livenation.nl), Podiuminfo, Paylogic, Eventix, venue sites (Ziggo Dome, AFAS
   Live, Paradiso, Melkweg, TivoliVredenburg, 013, Doornroosje), the artist's
   Songkick / Bandsintown page filtered to NL, Resident Advisor + Partyflock for
   electronic, and official socials. Ignore resale (Ticketswap, viagogo).
   Put ALL NL shows found into events.json - array of:
   {"artist","date","city","venue","country","event_type","status","on_sale","url"}
   country = "Netherlands". Don't sort; the script handles it.
4. python concert_sheets.py append events.json --schedule "Schedule NL" --only Netherlands --no-empty
5. Summarize to me in chat: the NEWLY added shows (from "added_events"), with
   on-sale status up top. If nothing new: "No new NL announcements."