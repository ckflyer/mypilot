# MyPilot

Self-hosted flight tracking for airline crew and their families.

The pilot pastes his schedule in. His family opens a link and sees where
he is — which flight he is on, whether it has left, where the aeroplane
is on a map, and whether it has landed. No account needed on their side,
no app store, no subscription.

FastAPI + SQLite + Jinja, in Docker, on a TrueNAS box via Dockge.

> `app/version.py` is the only authority on the version number. This file
> does not restate it, because for five releases it did and was wrong.

---

## WHAT IS IN THIS REPOSITORY

```
app/          the application
templates/    the pages
static/       CSS, icons, map libraries
data/         the database (created on first run; not in git)

Dockerfile  docker-compose.yml  requirements.txt  update.sh
README.md     this file

dev/          NOT deployed — excluded from the Docker image
  AI-README.md    the working brief: why everything is the way it is
  tests/          14 suites, ~2,400 assertions
  tools/          one-off checkers and the icon generator
  run_tests.sh    runs all of them
  BACKUP.md       how to back the database up
```

**Everything at the root is needed to serve a request. Everything under
`dev/` is not**, and `.dockerignore` keeps it out of the image entirely.
If you are looking for the reasoning behind a design decision rather than
a description of it, it is in `dev/AI-README.md`.

---

## THINGS THAT HAVE BITTEN BEFORE

Short list, kept deliberately near the top. Each of these was a real bug
that reached the pilot or his family. Full write-ups are in VERSION
HISTORY in `dev/AI-README.md`.

**An import must never rewrite history.** A leg that has already departed
is frozen: an import can add it, and it can remove it, but it can never
change its times. A schedule pasted today is a statement about the
future, not a correction of the past.

**Pasting one trip is not a statement about the rest of the month.** So
the two kinds of "missing from this paste" behave in opposite ways, and
that asymmetry is load-bearing:

| On your roster, not in the paste | Default | Why |
|---|---|---|
| **Upcoming** leg | remove it | the paste contradicts it |
| **Already flown** leg | keep it | the paste is merely silent |

Reverse the second one and importing a single trip deletes a month of
history.

**A leg someone else flew is still on your roster until you remove it.**
If you get reassigned and someone else takes your leg, the app has no way
to know. When you re-import, that leg shows up as *"already flown, and
this paste does not mention it"* — unticked, in plain sight, for you to
decide. It is never hidden and never removed automatically. If you leave
it, the app will go on believing you flew it, and there will probably be
a recorded track attached to it, because the poller followed the real
aeroplane at the time.

**Already-flown flights are folded away on the import page, but only the
ones with nothing to decide.** A leg in both the paste and your roster is
frozen and gets folded. A leg the paste has stopped mentioning never
does. (1.30.1 folded both and hid the reassignment case; 1.30.2 fixed it.)

**Time arithmetic goes through UTC, never wall clocks.** Subtracting a
departure time from an arrival time is wrong the moment a leg crosses a
time zone, which is most of them.

**The diagnostics page must not be the thing that is broken.** It has
twice reported healthy feeds as dead — once through rate limiting, once
because an empty aircraft list was read as a malformed response. If the
feed panel and the recent-lookup history disagree, believe the history.

**A phone can serve you the previous release.** A surprising share of
"bugs" are a cached page from the last version. This is why every bug
report records the build it came from.

---

## QUICK START

```bash
# clone
git clone <your-repo-url>
cd flight-tracker

# install
python3 -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# run
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/` — first visit redirects to `/setup` to
create your pilot account (username, password, optional email). After that,
`/login` is where everyone comes in: pilots use username+password, viewers
(family, whoever you share the code with) use the 5-digit tracking code
shown on `/admin`.

## DEPLOY ON TRUENAS WITH DOCKGE

1. Copy this folder to your TrueNAS box (e.g. into the app-data dataset Dockge watches, or wherever you keep your stacks).
2. In Dockge, create a new stack pointing at this folder, or paste `docker-compose.yml`'s contents into a new stack.
3. Make sure the `./data` folder exists on the host (Dockge/Compose will create it as a bind mount if it doesn't).
4. Start the stack. First boot will build the image and expose port `8000`.
5. Visit `http://<truenas-ip>:8000/` — you'll land on `/setup` to create your pilot account.
6. Nothing to configure for live tracking — Airplanes.live needs no API key or account.
7. On `/admin`, grab the 5-digit share code and send it to whoever should be able to view your flights.
8. Everything in `./data` persists across container restarts/rebuilds since it's a bind-mounted volume.

Login is real now (password-protected pilot account, code-gated viewer
access), but there's still no HTTPS/TLS built in — if you're exposing this
beyond your LAN, put it behind Tailscale, a reverse proxy with TLS, or a VPN
so credentials aren't sent in the clear.

## UPDATING

Dockge's Deploy/Update button won't rebuild the image just because the code
changed — it's a known limitation when a stack uses `build: .` instead of a
pre-built `image:`. Use `update.sh` instead, from the stack's directory on
the TrueNAS host:

```bash
bash update.sh
```

This resets the working copy to match GitHub exactly (`git fetch` +
`git reset --hard origin/main`, not a plain `git pull`), then rebuilds and
restarts the container, tailing the logs so you can confirm it started
cleanly. Using `reset --hard` instead of `pull` means it can never fail
with a "divergent branches" error, even if something was committed locally
on the host and never pushed.

(First run: `chmod +x update.sh` if you want to run it as `./update.sh`
instead — GitHub's browser uploader doesn't preserve the executable bit,
so `bash update.sh` is the safe way to invoke it either way.)

## ACCOUNTS & SHARING

- **Pilots** log in with a username/password created during first-run setup
  at `/setup`. Only a pilot can edit the schedule (`/admin`) or settings
  (`/settings`).
- **Viewers** don't need an account — just the 5-digit code shown at the top
  of `/admin`, entered on the "Viewer access" side of `/login`. Any number of
  people can use the same code at once, and it stays valid indefinitely once
  someone's logged in with it.
- **Regenerating the code** (button on `/admin`) instantly revokes access for
  anyone still using the old one — useful if you want to cut off a specific
  person without affecting anyone else, since you'd just share the new code
  with everyone you still want to have access. There's a **Share** button
  next to it that uses the phone's native share sheet (or copies to
  clipboard) to send the link + code.
- The data model is user-scoped throughout (separate schedules, separate
  separate everything per pilot account) as groundwork
  for supporting more than one pilot on the same install later. Right now
  there's no public signup — accounts are created only via the one-time
  `/setup` bootstrap.

## SCHEDULE FORMAT

Paste exactly like this (one leg per line):

```
06/26/2026 3729 DFW 1742 OKC 1837
06/26/2026 3729 OKC 1911 DFW 2011
06/26/2026 3566 DFW 2227 ICT 2351
```

`MM/DD/YYYY  FLIGHT  ORIG  DEPTIME  DEST  ARRTIME`

Times are local block times at each airport. Each row has an "×" button on
`/admin` to delete it individually if needed.

## PAGES

| URL | What it is | Who |
|---|---|---|
| `/` | the tracker card and map | pilot + viewers |
| `/calendar` | one month at a time | pilot + viewers |
| `/flights` | ONE PILOT'S SCHEDULE — paste, leg list, month filter | pilot |
| `/admin` | THE INSTALL — inbox, people, test mode, diagnostics, decision log | admins |
| `/settings` | how the app behaves for you | pilot + viewers |

**The `/flights` and `/admin` split is 1.7.0 and it was a real confusion,
not a tidy-up.** The tab bar had labelled the schedule page "Flights" since
v7.5 while its URL said `/admin`; 1.6.0 then piled the install's
administration onto that same page, so somebody's trip list and the control
that deletes every account were in one scroll under a name that matched
neither. Now the word means what it says.

`/admin`'s Live-tracking section is FETCHED, not rendered with the page:
`GET /admin/diagnostics/panel` returns just that markup. It is the only
route in the app that makes outbound network calls while somebody waits,
and the only one deliberately declared `def` rather than `async def` —
see invariant 37 in dev/AI-README.md.

Feedback adds `POST /feedback` (anyone signed in, pilot or viewer) and
three admin routes under `/admin/feedback/`.

Redirects are kept for every moved URL — `/admin/diagnostics` →
`/admin#diagnostics`, `/admin/debug` → `/admin#log`,
`/settings/users/delete/{id}` → `/admin#people`,
`/admin/import/confirm` → `/flights/import/confirm` (307, so the POST and
its parsed schedule survive; added 1.18.0 after nine releases in which
that one was missing and Import was dead). A phone with a page still
open from before the update posts to the old path, and a 404 on a Delete
button is the worst possible way to learn a route moved.

## ADMINISTERING THE INSTALL

Everything that operates the install is on the **Admin page** (1.6.0).
Settings is where a pilot changes how the app behaves for *them*; /admin is
where whoever runs the box runs it. On a shared install those are two
different people, and only one of them sees the admin panels.

See PAGES above for the full split and the redirects that keep old links
working.

### Adding a second admin

**Before 1.6.0 there was no way to do this at all.** `create_user` sets
`is_admin` on whoever registers first and nothing else ever touched the
flag — so on a self-hosted box, losing the first account lost
administration of the install permanently, with the data still sitting
there.

1. Send them this server's `/register` page. They create their own account.
2. `/admin` → **People** → **Make admin…** → **re-enter your own password**.

The password gate is 1.7.0. A single tap was too little friction for an
irreversible grant — the person you promote can then see and delete every
account on the install, including the one that promoted them. Re-entering
the password also means an unlocked phone left on a crew room table is not
enough on its own. Demotion is gated the same way.

**The last admin cannot be demoted or deleted**, including by themselves.
Removing the final admin leaves a database with real flight data that
nobody can administer, and there is no recovery path from the app — it
would mean opening SQLite by hand on the NAS.

## TEST MODE

**Admin page → Admin · Test mode.** Rehearse a flight without waiting for a
real one and without spending an AeroAPI credit.

Every bug this app has shipped was found the same way: the owner flew a
trip, something looked wrong, and the evidence was gone by the time anyone
could look at it. Reproducing a closure bug used to cost a duty period.

**What it does, and what it deliberately does not.** The simulator produces
ONE thing: position reports, in the exact shape `livesource.live_state`
returns. Those then go through the same `flightmatch.observe`,
`flightmatch.evaluate`, `tags` and `closure.maybe_close` a real flight
does. If closure is wrong, test mode is wrong in the same way and by the
same amount — which is the only property that makes it worth having. A
simulator that wrote `closed = 1` directly would prove nothing.

**Three isolation rules**, each guarding a different way this could do harm:

| Rule | Enforced at |
|---|---|
| Never spend | `flights.simulated = 1` checked at the top of `enrichment.refresh` AND `backfill_gate_in`, before the key is read |
| Never ask ADS-B | `poller` routes a simulated leg to `simulator.state_for`, so the shared rate limiter is untouched |
| Never count | excluded from the gate-in sweep; **must** be excluded from any export |

Beyond the money, the first rule matters because a real flight somewhere
may share an invented callsign, and letting its data into a simulated row
would mix invention with fact in one place. Simulated legs use flight
numbers from 9900 up, which no US regional operates, so an invented leg can
never collide with a real one on the shared flight id.

### Scenarios

Each is named for the BUG IT REPRODUCES, not the flight it describes.

| Scenario | Proves |
|---|---|
| Normal leg | the happy path; closes on the short observed route |
| Taxi-in trap | 1.4.0 — parks, keeps transmitting, no silence anywhere. Age 30m |
| Coverage lost in cruise | never seen to land; only the backstop can end it. Age 3h |
| Blocked in, no airline gate-in | 1.5.0 — the abandonment cliff. Age 3h |
| Two-leg turn | 1.5.0 — the card hands over the moment leg 1 is down and leg 2's window opens |
| Scheduled, never departs | the `has_departed` guard; nothing may close this, at any age |

### Why there is no speed control

The obvious design is a clock multiplier. It was rejected: the poller, the
card, `get_current_info` and every stored timestamp run on the real clock,
so a leg running at 60× is judged at one time and displayed at another, and
every discrepancy that produced would be a property of the simulator rather
than of the app.

Two honest mechanisms instead. Scenarios use SHORT legs (10–14 minutes of
block time), so a full gate-to-gate happens while you watch. And for rules
that mature on a long clock, **Age this leg** shifts the row's recorded
timestamps backwards. Nothing is faked and no threshold is lowered: after
ageing 30 minutes the leg genuinely has been stopped for 30 minutes, and
the real production rule fires on the real number with no knowledge that
test mode exists. Ageing never touches `date`, `dep_time_local` or
`arr_time_local` — those define the leg's window, and moving them would
change which rules are in play rather than just making the leg older.

**Stop & delete** removes simulated legs outright, with their roster rows
and tracks. Not retired — retention protects a record of flights that
happened, and a rehearsal did not happen.

## STORAGE & MIGRATION

Everything in `data/flighttracker.db` (SQLite, WAL). `data/secret_key.txt`
signs session cookies — generated once, must stay stable, never packaged.

Migration runs on boot, is **idempotent**, and handles two source shapes:

| From | Path |
|---|---|
| v4 (7 tables) | `legs` → `flights` + `roster`; `flight_tracks` → `positions`; dead `aircraft` and old user-scoped `positions` dropped |
| v5.0 (per-user `flights`) | renamed `flights_v50`, merged to one shared row per flight; observed/airline columns copied field-by-field (non-null wins) |

**Carries over:** accounts, settings, schedule, all flown tracks, and (from
v5.0 only) observed and airline data.

**Does NOT carry over from v4:** enrichment and closeout JSON blobs. ≤30
days old, re-fetchable, and mapping two nested documents into 80 columns is
a one-off guess. Symptom: past flights show route and path but no gate
times until re-flown.

**Not dropped:** v4 tables holding real data (`legs`, `flight_tracks`,
`flight_aircraft`, `flight_enrichment`, `flight_closeout`) and
`flights_v50` are left in place for recovery. Drop by hand once satisfied.

First-boot log lines to expect: `dropped the dead v4 positions table`,
`carried N track points over from v4`, `carried N schedule legs over from
v4`, or `merged N per-user v5.0 rows into shared flights`.

---

## HOW IT WORKS

The rest of this file is the system logic: what the app decides, when it
decides it, and why the timings are what they are. You do not need it to
run the app. You do need it before changing any of it.

## THE TWO PILLS

Independent. Status renders first, phase second.

**Phase** — always theme blue. Forward-only ladder:
`Scheduled → Taxi-out → In air → Landing → Taxi-in → Arrived`.
Landing = airborne within 8nm of destination, or airline `actual_on`
without `actual_in`. Legs with zero ADS-B still get a phase from airline
OOOI.

**Status** — the ONLY coloured pill. **Blank when nothing to say; there is
no "on time" pill** (a badge on every normal flight is wallpaper).

| Status | Trigger | Sticky |
|---|---|---|
| Cancelled | airline | yes — also hides the phase pill |
| Diverted | airline | yes |
| Delayed | see invariant 4 in dev/AI-README.md | no — clears if the airline pulls the time back |

The lateness NOTE ("out 12 min late") is separate, measured against the
FFDO bid line, and shown regardless of the pill. Both can be true.

## AIRCRAFT MATCHING

Callsigns are not unique to a leg: regional turns fly out and back under
one number, and the return departs inside the outbound's window.

Behaviour unchanged since v4 — the most correct part of the app. Storage
moved into columns.

0. **Arbitrate** — of legs sharing a callsign that day, only the latest
   whose scheduled departure has passed may claim the aircraft.
   Deterministic; needs no observation (outstations often lack coverage).
1. **Acquire** — adopt on callsign when at ORIGIN (≤30nm) or within
   T-20/T+45. The window covers no-receiver outstations; safe against
   turns because the return cannot depart until this leg lands.
2. **Hold** — thereafter only that hex, unconditionally, anywhere.
   Diversions and returns-to-field are followed.
3. **Release** — a closed leg accepts nothing further from any source.

## CLOSURE

| `closed_by` | Meaning |
|---|---|
| `airline` | airline gate-in, or cancellation |
| `relaunch` | aircraft took off again. Unambiguous, free |
| `observed` | confirmed landing + 5 min stopped + 8 min silent |
| `backstop` | 3h past revised arrival, quiet, no fresh airline data |
| `observed` (long stop) | landed + stationary 30 min, **transmitting or not** |

Observed closes the leg **even with an API key** (owner's decision).
`actual_in` is the OOOI field most often missing; v5.0 waited on it and
hung. A late airline gate-in **upgrades** an observed/backstop close — and
as of 1.5.0 that upgrade can actually happen, see below.

Both halves of the SHORT `observed` route are required: a plane holding
off-gate stays stationary while transmitting; a coverage hole is silence
without a stop.

**The LONG STOP route (1.4.0) exists because requiring silence everywhere
created a trap with no exit.** An aircraft that lands, taxis in, parks and
keeps transmitting — ordinary, especially with the APU running — never
reaches `SIGNAL_GONE_MIN`. `observed` could not fire, and the backstop's
`quiet` test could not either. The only remaining exits were an airline
gate-in (the OOOI field most often missing) and `relaunch`, which on the
last leg of a day means the following morning. So the leg sat in taxi-in
indefinitely and, because it never closed, **the next leg never became
current** — the app appeared frozen on a finished flight.

Thirty minutes stationary is itself the evidence. Pairing five minutes with
silence is a fair way to tell "parked" from "holding for a gate"; at thirty
it is not, and closing early is much the smaller error than blocking every
leg behind it.

**The 1.4.0 fix was correct and could not work, because nothing was asking
(fixed 1.5.0).** Every rule in this table was written as though a leg is
judged forever. It is not. `poller.active_flights()` returns only the
current leg and imminent upcoming ones, and `get_current_info` releases a
leg 3 hours past its SCHEDULED arrival unless it is demonstrably still
airborne. After that instant nothing in the app ever looked at the leg
again. So three of the five routes expired together:

  * the **backstop** matures 3h past the REVISED arrival, which on any late
    flight is later than 3h past the scheduled one — so on exactly the
    population it exists for, the leg was abandoned before its own backstop
    came due;
  * **relaunch** needs a later sweep to notice the aircraft flying again;
  * the **long stop** needs a sweep at the 30-minute mark, which a leg
    blocking in near the end of its grace never got.

Reported as: blocked in at 07:00, still open at 11:30.

**Fix: `poller._closeout_sweep`.** After each normal sweep, unclosed
rostered legs from the last 7 days are re-judged. It costs NOTHING and is
deliberately kept that way — every value `closure.decide` reads is already
on the row, so this is pure re-evaluation with no ADS-B and no AeroAPI
call. `stopped_since` and `last_signal_at` are timestamps, so stopped-for
and signal-gap keep growing correctly without anyone fetching anything. A
leg still inside its live window is skipped, so the two sweeps can never
both judge one leg in a pass.

**The upgrade path was a door with a wall behind it (fixed 1.5.0).**
`maybe_close` has always been able to upgrade a provisional close to the
airline's own gate-in. It could never fire: a closed leg was never polled
again, and `should_query` refuses to spend on one, so the single value that
could trigger the upgrade was the one value nothing would ever fetch.

**Fix: `poller._gate_in_sweep` + `enrichment.should_backfill_gate_in`.** A
leg that closed on anything other than `airline` and is still missing
`in_actual_api` gets three late attempts — +90 min, +6 h, +18 h from the
previous attempt, reaching roughly 24 hours past block-in. The owner's
report set those numbers: usually the airline is quick (already covered by
the leg's own live tickets), but a 07:00 block-in had nothing by 11:30.
Attempts are recorded BEFORE the call goes out, the `carrier.py` lesson, so
a timeout still counts. A silent airline costs exactly three queries no
matter how many thousand sweeps run — under two cents, enforced by test.

This matters more for any future record-keeping than for the tracker:
`in_actual_api` is what an export is allowed to use, because an observed
time must never masquerade as a reported one in a legal record.

## WHICH LEG IS CURRENT

Clock first, evidence on top. The window runs T-20 to scheduled arrival
+3h (`CURRENT_GRACE`). Then two overrides, each fixing a failure the clock
alone produced in the opposite direction:

  * `_still_flying()` — holds the card past the grace while the aircraft
    is demonstrably UP and has not come down. Three hours late and still
    at altitude is a normal bad day, and the card used to drop into past
    flights mid-cruise, exactly when the family is watching hardest.
  * ...but NOT once it is down. `landed_seen` / `on_actual_api` /
    `in_actual_api` end the hold even though the leg never closed. Gate-in
    is the OOOI field most often missing entirely, so a leg can sit open
    forever with the aeroplane parked — holding the card on that one is
    what stopped the next flight ever becoming current.
  * `_has_started()` — when several legs qualify at once, one with real
    evidence of having departed beats one that has merely reached its
    scheduled time. Without it a delayed leg 2 took the card off an
    airborne leg 1, because leg 2's window opened first.
  * ...but a leg that is DOWN hands the card on as soon as the next leg's
    window opens (`_on_ground`, 1.5.0). The rule above, alone, meant a
    landed leg held the card for the full three-hour grace while the crew
    were already boarding the next one — because selection asked which leg
    had STARTED, and a finished leg has still started. Fixing closure in
    the same release did NOT fix this; selection never asked whether a leg
    had closed.

`_on_ground` is deliberately BROAD, and that is the opposite of how the
rest of this app reasons. Everywhere else — `has_departed`, `_has_started`,
closure's guards — the demand is for strong evidence, because the cost of
being wrong is ending a flight that is still going. Here the cost runs the
other way: being wrong shows a family member a finished leg while the pilot
is boarding the next one, and the recovery is automatic, since the moment
the aeroplane is airborne again `_still_flying` takes over. So any ONE of
six signals is enough: `closed`, `landed_seen`, `on_actual_api`,
`in_actual_api`, `in_observed`, or `airborne_seen AND last_on_ground`.

That last one carries the outstations. `landed_seen` needs a SUSTAINED
touchdown to be observed, which a field with no ground coverage never
provides; but an aircraft we watched get airborne and are now seeing on the
ground has landed, whatever the confirmation timer thinks. The
`airborne_seen` half is what keeps pushback from reading as arrival.

Handover is ONE LEG AT A TIME — the earliest later candidate whose window
has opened, not the last leg of the day — so a four-leg duty steps forward
properly.

`MAX_AIRBORNE_HOLD` (12h) is the ceiling, so a stuck `airborne_seen` flag
cannot own the card indefinitely. A candidate that loses is appended to
`past`, not dropped — it is behind the leg now flying.

## WHICH TRIP THE TRACKER SHOWS

Two functions, and keeping them apart is the point.

`tracker_anchor(info, now)` picks ONE leg. `tracker_window(all_legs,
anchor_id)` returns the trip that leg belongs to, and nothing else. The
tracker renders that. So "which trip" is never computed — it is derived
from a single leg, and there is exactly one place to look when it is
wrong.

The anchor, in order:

  1. A live leg wins. Nothing competes.
  2. Else the last leg to land, if it landed less than `TRIP_HANDOVER`
     ago. Without this the trip vanishes the instant the final leg goes
     past, and someone opening the app while he is still in the crew van
     is shown a trip weeks away with no sign the one that just finished
     happened.
  3. Else the next leg he flies.

**`TRIP_HANDOVER` is ten hours because FAR 117 is ten hours.** That is
the minimum rest between duty periods, so the next trip cannot legally
begin inside the window — which is what makes it safe to hold a finished
trip that long without ever hiding the next one. Do not "round it up" to
twelve for comfort: the number is load-bearing, and the moment it exceeds
the legal rest minimum it can hide a trip the pilot is about to fly.

There is deliberately NO cap at the next departure. It could only fire on
an illegal or mis-imported schedule, and rule 1 already covers that,
since a leg goes live twenty minutes before it pushes.

**The card resolves its default through the same anchor** —
`resolve_selected_leg` calls `tracker_anchor`. These used to compute the
same thing from the same fallbacks in two places, which is a bug waiting
for whichever one gets edited next: the card would show the first leg of
a trip the list does not contain, and tapping it would select a flight
that is not there.

`tracker_window` returning None means "no opinion, show everything". Used
when the anchor cannot be placed, so a bug here degrades to the old
behaviour rather than to a blank page. A roster pasted without the blank
lines the parser keys on is ONE trip containing everything, which
degrades the same way.

Older trips are the calendar's job. "When does he go again" is a question
about a date; `/calendar` answers it. (1.16.0)

### Legs settling out of the list

Inside the window, a leg leaves `LEG_SETTLE` (30 min) after its CLOSEOUT.
Not after its scheduled arrival: closeout is a conclusion, a schedule is a
guess, and a two-hour delay makes the guess a lie. `settled_out` in
main.py.

Two things it will not do, both deliberate:

  * **A leg with no `closed_at` never drops**, however old. No closeout
    means the app does not know how the flight ended; removing it quietly
    would present that as resolved.
  * **The last remaining leg never drops.** Otherwise the list empties
    thirty minutes after the final landing and stays empty for the rest of
    the ten-hour handover — the window in which someone is most likely to
    open the app to check he got in.

That second guard is why the function takes the whole list rather than
being asked leg by leg. "Is this one still needed" cannot be answered
without knowing what else is left. Applied AFTER `tracker_window`, so
"last remaining" means last of this trip. (1.17.0)

## ONE AEROPLANE

**There were three.** The app icon cut one silhouette, the map marker cut a
second (the same shape with its engines deleted), and the tab bar and
progress bar had a third hardcoded inline that the icon system never
touched. Changing the icon style in Settings moved two of the four.

Now: `make_icons.py` is the single source. It generates the PNGs *and*
`static/planes.js`, which the map reads; `templates/partials/plane_glyph.html`
carries the same path for the tab bar; the progress bar uses it inline. A
path edit in `make_icons.py` changes all four or none.

**Every style is a SINGLE CLOSED PATH, and that is load-bearing.** "Modern"
used to be three overlapping shapes plus two nacelle rectangles. Filled
flat on a tile that is fine. On the map it is not: the marker strokes a
dark outline round *every* shape, so outlines ran through the middle of the
aeroplane and at 40px over a busy tile the whole thing read as a smudge.
One closed path strokes once, on its own edge. `paint-order="stroke"` puts
that outline *under* the fill so it reads as a halo rather than eating into
the silhouette.

**The progress-bar plane was invisible.** It was `color: var(--accent)`
riding on a `.route-fill` that is also `var(--accent)` — the same colour as
the bar it sits on, so it only appeared once it had moved past the fill. It
now has its own paint: card-coloured body, accent outline, which reads
against the flown half on its left and the empty track on its right, in
either theme.

**The tile has artwork now.** Night-sky gradient, the earth's limb across
the bottom, a great-circle arc, three stars, and the plane banked along the
arc at its apex rather than sitting nose-up in the middle of a square.

**The arc is split, and that is the point:** solid behind the plane, dashed
ahead of it. Same flown/remaining reading as the route strip on the tracker
card, so the icon says what the app *does* rather than just being a plane
in a box.

Everything in `_backdrop()` is a fraction of the icon size, so the 16px
favicon is the same picture as the 512px tile and not the same picture with
a giant arc across it. Below 64px the fine detail is dropped rather than
shrunk — a 3-unit dash pattern and a 1-unit star are mush at favicon size,
and mush reads as a smeared icon, not as detail. Maskable icons skip the
artwork entirely: Android crops them to whatever shape the launcher wants
and only the inner 80% is guaranteed, so an arc drawn to the edges would be
sliced at an arbitrary radius.

## WHEN AEROAPI IS QUERIED

**One rule.** Every leg is handed `TICKETS_PER_LEG` (18) tickets and spends
them like this:

    time left in the window / tickets left = how long to wait

The window runs from 30 minutes before SCHEDULED departure to an hour after
the BEST CURRENTLY KNOWN arrival. That last part is the whole delay story:
when the airline publishes a revised arrival, the window stretches and the
remaining tickets re-space themselves across it automatically. A six-hour
delay widens the gaps instead of draining the budget.

Two clamps hold the edges:

  * `MIN_QUERY_GAP` (5 min) — never faster, so a garbage timestamp can't
    empty the wallet in a single sweep.
  * `MAX_QUERY_GAP` (20 min) — never slower. This is also what covers a
    flight overrunning its window with nothing published: the remaining
    time goes negative, the formula falls through to this, and the leg
    ticks over quietly instead of stopping dead.

`ARRIVAL_RESERVE` (4) of the tickets are locked until the aircraft is
actually down, or its arrival time has passed. Gate-in is the one answer
that ENDS a leg, so it can't be starved by a long delay upstream. The clock
half of that condition matters for legs with no ADS-B coverage, where no
touchdown can ever be observed.

The leg stops spending the moment there's nothing left to learn — gate-in
received, cancelled, or closed. Unspent tickets simply go unspent; there is
no prize for using them.

**One exception, added 1.5.0: the late gate-in chase.** The rule above is
right for the LIVE allowance — once a leg is closed there is nothing left
to watch. But it also meant `in_actual_api` could be permanently missing on
any leg whose airline reported late, and that is the one figure a record
export is allowed to use. So a closed leg that is still missing gate-in
gets up to three attempts on a SEPARATE allowance (`gatein_tries`, not
`api_queries_used`) at +90 min / +6 h / +18 h. Two different questions, so
two different counters and two different functions —
`should_backfill_gate_in`, never `should_query`.

Worst case is 3 queries × $0.005 on only those legs that closed without an
airline gate-in: about **$0.46/month** if every one of 46 legs needed all
three, and realistically pennies, since most legs get gate-in from their
own live tickets and never enter the chase at all.

The background poller has to reach a leg BEFORE it becomes the current
flight, or the first look can never happen: a leg isn't `current` until
T-20, so the poller carries its own `PREVIEW_WINDOW` (35 min) and sweeps
imminent upcoming legs too. That window is deliberately in the poller
rather than in `get_current_info`, because "current" also drives flight
selection, the map and the card, and moving that boundary would change all
of them.

AeroAPI's own `departure_delay` / `arrival_delay` fields are fetched but
deliberately NOT stored. They are measured against the airline's published
schedule; every delay figure in this app is measured against the FFDO bid
line, because that is what the pilot flies. Keeping both would mean two
numbers for one thing and an invitation to trust the wrong one. The full
raw record is kept in `api_raw` regardless, so nothing is lost.

### What this replaced (v5.1 and earlier)

Six independent triggers — first look at T-30, a ground watch at T+20 and
every 30 min after, three evenly spaced cruise checks, wheels-down+5, a
closeout loop, and a no-ADS-B arrival fallback — each with its own cap and
its own counter column. They worked. But the interactions between them were
where the bugs lived, and three cruise checks on a 95-minute regional leg
bought the same answer three times over. The `closeout_tries`,
`fallback_tries` and `delay_watch_tries` columns still exist on the table
(migrations here are append-only) but nothing reads or writes them.

### Measured against a real schedule

Two actual months of FFDO lines, simulated against the shipped
`should_query()` at 20-second poller resolution:

| Scenario | July (26 legs) | August (41 legs) |
|---|---|---|
| Normal, gate-in published | $1.70 | $2.76 |
| Gate-in never published | $2.34 | $3.69 |
| 45-min delay, published | $1.86 | $2.97 |
| 6-hour delay, published | $1.95 | $3.08 |
| 6-hour delay, airline silent | $2.34 | $3.69 |
| No ADS-B coverage at all | $1.70 | $2.76 |

The per-leg ceiling is a hard 18 in every scenario, so a 50-leg month
cannot exceed $4.50 even if every single flight goes wrong.

## ADS-B FEEDS

**airplanes.live withdrew its free API in 2026.** Their reasoning, from the
notice sent to every API user: 2 billion requests a week, the month's egress
allowance gone in four days, hosting up nearly 300% in 18 months, and AI
agents and scrapers named directly as the cause. It is now $25/mo
sponsorship — **or free from a feeder's own IP.**

Defaults as of 1.8.0, tried in order until one answers:

| Feed | Access | Terms |
|---|---|---|
| `api.adsb.lol/v2` | open to everyone | ODbL 1.0 |
| `opendata.adsb.fi/api/v2` | open, 1 req/sec | personal, non-commercial; attribution required |
| `api.airplanes.live/v2` | **off** — feeders and sponsors | enable it if you feed or sponsor |

All three are ADSBexchange-v2 compatible, which is why swapping between
them is a URL change and not a rewrite. Attribution for adsb.fi and adsb.lol
is on the diagnostics panel, where their terms require it to be.

**If the feed list on your install still names a dead feed**, use *Reset
feeds to current defaults* on the admin page. `load_endpoints` prefers a
list saved in the database over the built-in one, so an install that ever
saved a list stays pinned to it while a fresh install of the same version
works — a difference no amount of reading the code explains.

**The best long-term answer is to feed.** A ~$30 RTL-SDR dongle and an
antenna on the NAS earns free API access at airplanes.live and adsb.fi's
feeder endpoints, adds coverage where the pilot actually flies, and removes
this whole category of problem. It is the only option here that gets better
rather than worse over time.

## COST CONTROL

`/flights/{ident}` costs $0.005 per result set; `/schedules` costs $0.02,
four times as much, which is why deadhead carrier resolution is capped,
counted at four units, and stored permanently once it succeeds.

At 18 tickets per leg, a heavy 50-leg month has a hard ceiling of $4.50
(plus at most $0.75 of late gate-in chasing if every single leg needed it,
which would mean the airline never reported once all month), and real spend
lands well under because most legs stop early the moment gate-in arrives. The per-pilot monthly limit is a hard stop on top of that
— queries cease entirely once it's reached, so the app can never quietly
produce a bill.

That limit is set by each pilot in Settings ("Monthly spend limit"), stored
on the `users` row, and defaults to $4.90 — just under the Personal tier's
$5 free credit. It was $4.50 through v5.1; the v5.2 migration moves any row
still sitting on exactly the old default, and leaves any other value alone
on the grounds that a pilot who typed a number meant it.

### Deadhead carrier resolution

An FFDO line gives a bare flight number, never an airline. For the pilot's
own legs that's fine — they're Envoy. A deadhead is usually on mainline
American or another wholly-owned regional, each broadcasting its own
callsign, so looking up ENY4110 when the aircraft squawks AAL4110 means the
leg never tracks at all.

Resolution order, and the caps on it:

  1. **The free ADS-B probe goes first.** Try the handful of callsigns
     American's family actually uses and see which one has an aircraft
     within 40 nm of the origin around departure. Costs nothing.
  2. **Then, at most twice ever, a paid `/schedules` lookup**, spaced an
     hour apart, recorded on the row in `carrier_tries` / `carrier_tried_at`
     BEFORE the call is made — so a timeout or a crash mid-request still
     counts. It goes through `payer_for()`, so it obeys the same monthly
     cap as everything else.

Through v5.1 a FAILED lookup wrote nothing down. The poller sweeps every 20
seconds and a deadhead sits in its window for five or six hours, so the
identical failing question was asked roughly a thousand times — at $0.02
each, outside the budget check, and invisible to the local counter. One bad
deadhead could spend the entire month in an afternoon with nothing on
screen changing. `tests_carrier_cap.py` drives 900 sweeps and asserts at
most two paid lookups.

### FFDO placeholder lines

An FFDO block carries non-flying lines that fit the same shape as a leg —
`07/05/2026 0 DFW 1946 DFW 1946` is a duty or hotel marker. Same airport
both ends, flight number zero. Through v5.1 the parser accepted them, so
each became a tracked "flight" that looked up a callsign nobody broadcasts
and spent its ticket allowance discovering that. They're dropped in
`parser.py` now, before they reach the schedule, the poller or the card.

The cap is enforced against FlightAware's own usage figure, which is
refreshed every 15 minutes (`USAGE_REFRESH`). That endpoint is free, and
the one number that must never be stale is the one deciding whether to stop
spending. A reading older than an hour is treated as a FLOOR rather than
the truth, and the local count takes over.
`AEROAPI_MONTHLY_BUDGET` only supplies the fallback for a row that has no
value. A limit of $0 stops all AeroAPI queries while keeping the key saved;
live ADS-B tracking is free and is never affected.

Spend shown in Settings is **FlightAware's own figure and nothing else**,
read hourly from the free `GET /account/usage`. A local estimate used to be
shown beside it, but two numbers for one thing invites the question of which
to believe, and the estimate was the wrong one — it prices every query at
the `/flights` rate and undercounts any leg that needed `/schedules`. Until
a reading arrives the page says so rather than showing a number.

Enforcement is separate and more paranoid than the display: a fresh reading
is used as-is, and a stale or missing one falls back to the higher of the
last reading and the local count. A stale figure is a floor, not a ceiling
— querying has continued since. That fallback never reaches the screen, and
it exists so an unreachable usage endpoint can't quietly disable the one
control that prevents a bill.

The limit is **always enforced**. The old "keep querying past $X" toggle
was removed in v4.6: the one setting that exists to prevent a surprise bill
should not itself be switchable off. Anyone on a paid tier who doesn't mind
the overage raises the number instead, which is the same outcome stated
honestly. The `aeroapi_allow_overage` column remains on the table because
migrations here are append-only; nothing reads it.

Note that the local estimate prices every query at the `/flights` rate, so
a leg that needed a `/schedules` lookup is undercounted by about $0.015.
That's one more reason to prefer FlightAware's own figure below.

Spend is taken from FlightAware's OWN meter where possible:
`GET /account/usage` is free, is polled at most every 20 minutes, and
replaces the local estimate. Their figure updates every 10 minutes rather
than in real time, so anything older than six hours is treated as stale and
the estimate takes over. Settings shows the poll count and dollars against
the cap, when the figure was last pulled, and which source it came from.

## NOTES

- **`viewer.html` loses JavaScript to colliding edits.** It has happened
  twice: in v4.5 nothing toggled `#expand-details` and `togglePast()` was
  called by an inline `onclick` but never defined; both were restored, and
  `#expand-wrap` no longer needs toggling at all as of v5.6. The failure
  mode is silent — the page still renders, a control just does nothing.
  `test_template_contract` in `tests_ui_fixes.py` now guards this, so run
  that suite after any template edit rather than grepping by hand.

  A related trace of the same failure sits above `applyEnrichment()`, where
  the docstring for `selectLeg()` has been absorbed into the following
  comment block. Verify after every multi-part edit.
- All times shown are **local to the airport**. On the collapsed card the
  zone abbreviation is behind a tap (see v4.6); the expanded detail rows
  still print it inline.
- Callsigns / tracking links use the **ENY** prefix.
- **On time means exactly on time.** `ON_TIME_TOLERANCE_MIN` is 0, so a
  one-minute-late departure reads as late and is tinted red. An earlier
  5-minute grace meant the card printed 5:59 beside a crossed-out 5:57 and
  called it on time, which is an argument with itself.
- **Past flights keep their detail.** Actual times, gates and the frozen
  closeout record stay visible after a leg ages out of the current window
  — useful when a spouse is driving to the airport for a pickup. Nothing
  recomputes and no query is spent; it's all read from disk.
- Taxi-out/Taxi-in/Landing phase detection depends on each airport having
  enough ADS-B ground coverage to see it — busier fields (DFW) are reliable,
  smaller regional stations are a toss-up. When there's no coverage for a
  phase, it's skipped silently rather than guessed.
- No payments, public signup, or email/SMTP integration yet — those are
  intentionally deferred, not missing by accident.

