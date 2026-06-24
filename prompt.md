Daily morning monitor of new concert announcements for my artists, written to
Google Sheets AND summarized to me in chat. Respond in English.
The whole point: catch announcements EARLY so I can plan ticket purchases
before shows sell out (Amsterdam rock shows often sell out within a day).

The service-account key and sheet id are in the GOOGLE_SA_JSON and SHEET_ID
environment variables (read from the environment, NOT from a .env file).

STEPS:
1. Install deps: pip install -r requirements.txt
2. Run: python concert_sheets.py state
   Returns the artist list ("artists"), tracked festival names ("festivals"),
   and keys of already-saved events ("existing_keys").

3. For EACH artist (ANY genre - rock first, but also pop, electronic, etc.),
   use web search to find ALL announced shows over the next 24 months. Tag each
   with "event_type":
     - "Solo"     = the artist's own headline concert / tour date
     - "Gig"      = a support slot or shared bill with other acts
     - "Festival" = a festival appearance
   IMPORTANT: include shows even if tickets are NOT on sale yet (announced /
   presale-only). Those are the most valuable - I want to plan the purchase.
   Geography priority: Netherlands (especially Amsterdam) -> Belgium, Germany
   -> elsewhere if notable.

4. For EACH festival in "festivals", look up its lineup / announcements and add
   any set by an artist from the list as a "Festival" event.

5. For every show, capture ticket timing:
     - "status":  Announced (no sale yet) / Presale / On sale / Sold out
     - "on_sale": the on-sale or presale start date (YYYY-MM-DD) if known, else ""

6. Sources, in priority order (all genres, NL-first):
     1. Ticketmaster (ticketmaster.nl)   - primary ticketing
     2. Bandsintown, Songkick            - artist-following, all genres
     3. MOJO Concerts (mojo.nl)          - top NL rock/pop promoter
     4. Live Nation (livenation.nl)
     5. Podiuminfo (podiuminfo.nl)       - NL concert/festival agenda
     6. Venue sites: Ziggo Dome, AFAS Live, Paradiso, Melkweg (Amsterdam);
        TivoliVredenburg, 013, Doornroosje (rest of NL)
     7. Official artist sites + Instagram / Facebook / X
     8. Festivals: Festileaks, Clashfinder, official festival sites
     9. Electronic-specific: Resident Advisor (ra.co), Partyflock (partyflock.nl)
   Ignore ticket-resale sites (Ticketswap, viagogo, etc.).

7. Put EVERYTHING you found into events.json - a JSON array of objects:
   {"artist","date","city","venue","country","event_type","status","on_sale","url"}
   dates in YYYY-MM-DD. Don't pre-filter; the script handles dedup.

8. Run: python concert_sheets.py append events.json

9. Write a summary to chat in English. List only the NEWLY added events (from
   the script output), grouped by country then date. For each:
   Artist - Date - City, Venue - Event Type - Status (+ on-sale date) - URL.
   Put shows that are NOT yet on sale or that just went on sale AT THE TOP and
   flag them clearly, so I know what to act on. If nothing new:
   "No new announcements today."

Never delete or overwrite existing rows - the script only appends.