Daily NL concert watcher. Goal: be among the FIRST to know about new shows in the
Netherlands for the artists on my list, and about every ticket step - registration /
verified-fan sign-up, lottery / ballot, presale, general sale - so I can buy early.
You are the researcher. The tracker script decides what is new and sends the Telegram
alerts, so never filter by "newness" yourself. Unattended run: don't ask me anything.
Work in this single agent: no subagents, no workflows.

1. ./tracker.sh brief --out brief.md   and read brief.md completely.

2. Research with web search (most ticket and venue sites can't be opened directly, so
   work from the search results and run several queries per artist):
   - every HOT and ROTATION artist in the brief, e.g.
       "<artist> Nederland concert 2026 2027", "<artist> Amsterdam tickets presale",
       "<artist> tour 2027 Europe dates", "<artist> <venue> tickets" (for known shows),
       "<artist> registration presale" / "<artist> ballot", and festival names they might play.
   - the brief's sweep searches and festival line-up checks.
   Look hardest for: new NL shows and extra dates, registration / sign-up windows,
   lotteries, presale and general-sale dates WITH times, sold-outs, cancellations,
   upgrades to bigger venues. Include shows whose tickets are not on sale yet.
   Trust official artist / festival / venue sites, ticketmaster.nl, livenation.nl,
   mojo.nl, eventim.nl, paylogic, and news (Festileaks, 3voor12, KINK, NME, Kerrang,
   Blabbermouth, ...). Never rely on resale or SEO sites (viagogo, ticketswap, stubhub,
   "tour20xx" pages). Disambiguate names by genre (use the notes in the brief).
   Netherlands only; future dates only.

3. Write findings.json exactly as the brief's "Output" section describes. Report EVERY
   NL show you found for the checked artists - known or new - with its latest status and
   ticket windows, using the known ID. Put news that is not a dated NL show yet (tour
   announced with NL date TBA, registration opened, ...) in "news". Dismiss known shows
   that turn out to be wrong.

4. ./tracker.sh ingest findings.json
   If it prints warnings caused by your findings (unreadable date, unknown artist), fix
   findings.json and run ingest again - it never sends the same alert twice.

5. Final reply: the SUMMARY line. If the script says Telegram failed or is not
   configured, also include the alert texts it printed.
