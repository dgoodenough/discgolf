# 2027 schedule — what has been announced

Filed for later. Nothing in the pipeline reads this file; it is a record of
what the DGPT has published about 2027 so far, so the full schedule drop can
be diffed against it instead of re-read from scratch.

Sources:

1. DGPT press release, "Disc Golf Pro Tour and Ledgestone Disc Golf Announce
   Multi-Year Partnership Through 2029", 2026-08-11.
2. DGPT, "2027 Schedule: First Look",
   `dgpt.com/announcements/2027-schedule-first-look/`, seen 2026-08-19.

Provenance note: dgpt.com is blocked by this environment's egress proxy, so
neither page was fetched here — both were relayed by hand. Treat the rows
below as transcription, and re-verify against the source before anything
downstream depends on them.

## Announced events

| Src | Tournament | Class | Venue | Location | Dates | Days |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | Supreme Flight Open | DGPT (`elite`) | Olympus Disc Golf Course | Brooksville, FL | 03-12 – 03-14 | Fri–Sun |
| 2 | Big Easy Open | not published | Grand Cypress Bayou at Parc des Familles | Jefferson Parish, LA | 03-19 – 03-21 | Fri–Sun |
| 2 | Queen City Classic | not published | Hornets Nest Disc Golf Course | Charlotte, NC | 04-02 – 04-04 | Fri–Sun |
| 1 | Ledgestone Open | DGPT+ (`elite_plus`) | — | — | 08-05 – 08-08 | Thu–Sun |
| 1 | Discraft Great Lakes Open | DGPT+ (`elite_plus`) | — | — | 08-26 – 08-29 | Thu–Sun |

Weekday labels are computed from the 2027 calendar and match release [1] where
it stated them. Source [2] restates the Supreme Flight Open at the same dates
as [1] and adds the venue — no conflict between the two announcements so far.

Two caveats on the names and classes:

- **Classes are inferred for the two new events.** [2] did not publish
  DGPT/DGPT+ labels. Big Easy Open and Queen City Classic were both `elite` in
  2026, and the 3-day Fri–Sun shape matches the elite pattern, but that is an
  inference, not an announcement.
- **These are base names, not PDGA names.** `data/schedule_2026.csv` stores the
  sponsor-decorated strings the PDGA API returns ("DGPT - Discraft's Supreme
  Flight Open Presented by Florida's Adventure Coast", "DGPT - MVP Big Easy
  Open presented by Flight Factory"). 2027 sponsors are not published, so these
  rows will not string-match the eventual schedule CSV.

## Context worth keeping

- **New cadence, now visible.** [1] promised dates moving throughout the
  calendar and a season that begins and ends "slightly later than in previous
  years." [2] shows what that means at the front of the season: 2027 opens with
  back-to-back weekends (Supreme Flight → Big Easy, starts 7 days apart), then
  a two-week gap before Queen City. In 2026 the first three events were spaced
  14 days and 14 days. So 2026 dates are a weak prior for 2027 — expect
  compression and gaps, not a uniform shift.
- **Multi-year lock.** Supreme Flight Open, Ledgestone Open, and Discraft Great
  Lakes Open are committed for 2027, 2028, and 2029.
- **More Ledgestone stops coming.** Ledgestone's operational program expands
  with additional tour stops "to be announced in coordination with the full
  2027 DGPT schedule."
- **Rest of the schedule:** further date announcements "in the coming weeks."

## Delta vs. 2026 (`data/schedule_2026.csv`)

| Tournament | 2026 | 2027 | Shift |
| --- | --- | --- | --- |
| Supreme Flight Open | 02-27 – 03-01 | 03-12 – 03-14 | ~2 weeks later, still the opener |
| Big Easy Open | 03-13 – 03-15 | 03-19 – 03-21 | ~1 week later |
| Queen City Classic | 03-27 – 03-29 | 04-02 – 04-04 | ~1 week later |
| Ledgestone Open | 07-30 – 08-02 | 08-05 – 08-08 | ~1 week later |
| Discraft Great Lakes Open | not on schedule | 08-26 – 08-29 | new stop |

Great Lakes is new to this repo's data and gives August two DGPT+ events
alongside Ledgestone. The opener moving two weeks later while the events
behind it move one compresses the front of the season, which is the cadence
change showing up as arithmetic.

## Still unknown

Everything from mid-April on except the two August DGPT+ stops: the rest of
the event list, classes and PDGA tournament IDs throughout, whether the points
structure or the counting caps change, JomezPro Series membership, and
playoff/championship dates. Nothing here is enough to build
`data/schedule_2027.csv`; `dgpt/schedule.py` builds that from the PDGA API once
the events are listed, and `config.SEASON` plus the per-event TID constants in
`dgpt/config.py` are the season-pinned pieces that will need revisiting then.
