"""Data feeds: fetch outside series straight from their providers into the
candle store, on a schedule or on request, with no CSV file in between (G26).

Before this package the only way in was a CSV someone downloaded and uploaded,
so every series stopped on the day of its last download. A feed does what that
person did, the same way every time:

    fetch the last OVERLAP_DAYS before the newest stored day onward
      -> drop any session that is not finished yet (`core.session_cutoff`)
      -> compare the overlap with what is stored (`core.plan_update`)
      -> import through the same importer as an upload (`runner`)
      -> rebuild the feature table when one of its inputs changed

WHERE A SERIES COMES FROM, in the author's order of preference: (1) history and
current days both from the provider's API: a feed's first refresh of a series
with nothing stored loads its full history (`runner.load_full`); (2) history from
a CSV upload, current days from the API: the feed extends whatever is stored,
and holds if the provider disagrees with the CSV on the overlap; (3) everything
from CSV, for what no API serves.

WHAT A FEED MAY CHANGE ON ITS OWN. New days after the last stored one, and the
last stored day itself: that bar is often preliminary when it is first fetched
(a settling volume, a futures bar caught mid-roll), and the author chose to let
it settle automatically. For a market that trades into the evening
(`Feed.overnight`) the volume of the day before it may settle too, and nothing
newer than yesterday is taken. Anything further back is HELD: nothing is written for
that series, and the run reports what differs until someone applies it. A
held series stays where it is rather than taking the new days alone, because
the new days come from a provider that no longer agrees with the stored
history: for a dividend re-adjustment, appending would put two price bases in
one series. Apply re-downloads the full history and imports it with `force`,
exactly as re-uploading a fresh download would.

WHY STORED TEXT IS REUSED. The importer stores each year as one slice and
compares slices. A refresh rebuilds a year from the archived slice's own lines
plus the new ones, so the stored days reach the importer byte for byte as they
were, and the only difference it can see is the one the feed meant to make.

Modules: `core` (pure: bars, cutoff, overlap, slices), `yahoo`, `fred`, `boc`
(provider clients, shared with the root fetch scripts), `breadth` and `members`
(the Nasdaq-100 breadth series and their member-close cache), `earnings` and `sec`
(the Nasdaq-100 earnings series and the members' SEC filings), `registry` (which
series are fed), `runner` (database and orchestration), `api` (HTTP),
`schedule` (the daily run). This module imports nothing, so the root scripts can
use the provider clients without the database driver installed.
"""
