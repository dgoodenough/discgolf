# 2027 schedule — what has been announced

Filed for later. Nothing in the pipeline reads this file; it is a record of
what the DGPT has published about 2027 so far, so the full schedule drop can
be diffed against it instead of re-read from scratch.

Source: DGPT press release, "Disc Golf Pro Tour and Ledgestone Disc Golf
Announce Multi-Year Partnership Through 2029", 2026-08-11.

## Announced events

| Class | Tournament | Dates | Days |
| --- | --- | --- | --- |
| DGPT (`elite`) | Supreme Flight Open | 2027-03-12 – 2027-03-14 | Fri–Sun |
| DGPT+ (`elite_plus`) | Ledgestone Open | 2027-08-05 – 2027-08-08 | Thu–Sun |
| DGPT+ (`elite_plus`) | Discraft Great Lakes Open | 2027-08-26 – 2027-08-29 | Thu–Sun |

Weekday labels in the release match the calendar. Supreme Flight Open stays in
Brooksville, FL and opens the season. Class column is the release's own
DGPT/DGPT+ labels mapped to this repo's `cls` vocabulary; PDGA tournament IDs
are not published yet.

## Context worth keeping

- **New cadence.** The release says dates move throughout the calendar in
  coordination with local organizers, and that the season will "begin and end
  slightly later than in previous years." So 2026 dates are a weak prior for
  2027 — expect shifts beyond the three events above.
- **Multi-year lock.** Supreme Flight Open, Ledgestone Open, and Discraft
  Great Lakes Open are committed for 2027, 2028, and 2029.
- **More Ledgestone stops coming.** Ledgestone's operational program expands
  with additional tour stops "to be announced in coordination with the full
  2027 DGPT schedule."
- **Rest of the schedule:** further date announcements "in the coming weeks."

## Delta vs. 2026 (`data/schedule_2026.csv`)

- Supreme Flight Open: 2026-02-27 – 03-01 → 2027-03-12 – 03-14, about two
  weeks later, still the season opener.
- Ledgestone Open: 2026-07-30 – 08-02 → 2027-08-05 – 08-08, about a week later.
- Discraft Great Lakes Open is not on the 2026 schedule — a new stop for this
  repo's data, and a second August DGPT+ event alongside Ledgestone.

## Still unknown

Full event list, the classes and IDs for everything else, whether the points
structure or the counting caps change, and playoff/championship dates. Nothing
here is enough to build `data/schedule_2027.csv`; `dgpt/schedule.py` builds
that from the PDGA API once the events are listed, and `config.SEASON` plus the
per-event TID constants in `dgpt/config.py` are the season-pinned pieces that
will need revisiting then.
