"""Working out what a paste would actually CHANGE. (N1, 1.5.0)

Before this, importing a schedule replaced the roster: any leg not in the
new paste had its roster row deleted. Pasting September erased August.
Flight ROWS were never the problem — they are shared and adopted, never
duplicated — but the LINK from a pilot to a flight was pruned, and that
link is what the tracker, the calendar and (soon) the logbook read.

Two rules make this safe, and they are different rules for different
reasons:

  SCOPE IS THE MONTH THE PASTE COVERS. A bid line is published a month at
  a time, so a September paste is a statement about September and says
  nothing whatever about August. Reconciling outside its own months would
  let a partial paste delete a whole month it never mentioned.

  THE IMPORT HAS THE FINAL SAY, BUT NEVER SILENTLY. (Owner's call,
  1.20.0, replacing "only the future is reconciled".) The old rule said a
  departed leg could never be removed by an import. The hole in it is the
  one the owner found: if a trip is dropped from your line and you forget
  to remove it, and somebody else flies it, the app has a flight you did
  not fly and no way to say so. The paste is the authority on what was
  yours.

  So a flown leg the paste does not mention IS offered for removal — but
  UNTICKED, in its own section, while an upcoming one stays ticked. That
  distinction is the whole safety mechanism. The help text invites
  pasting "one trip or all of them", so a one-trip paste routinely says
  nothing about the rest of the month; if flown legs arrived pre-ticked,
  the ordinary act of importing one trip would delete a month of logbook
  by default. Ticked means "the paste positively contradicts this";
  unticked means "the paste is silent, look at it yourself".

  A FLOWN LEG IS NEVER MODIFIED. (Owner's call, 1.20.0; made absolute in
  1.22.0.) Not its times, not its deadhead flag, not its trip break. The
  FFDO time is the SCHEDULE, and the schedule for a flight that already
  happened is set in stone. What actually happened is a different fact,
  it lives in the OOOI columns, and it is not what a paste is talking
  about. Re-pasting a month used to list every flown leg as "changed"
  because the airline's record had settled to what actually occurred —
  noise on every single re-import, describing a change the confirm step
  did not even make.

  And nothing is applied silently. This module only DESCRIBES the change;
  `main.admin_import_confirm` applies whatever the pilot approves. The
  diff is the only place two invisible failures can be caught: a trip
  dropped from the line that the pilot forgot to remove, and a leg flown
  that was never on the line at all.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

from .flights import flight_key
from .models import FlightLeg

# What the pilot is shown, in this order. "unchanged" is included on
# purpose: a diff that hides the untouched legs makes the pilot count rows
# to satisfy himself nothing was lost, which is the anxiety the diff exists
# to remove.
ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"
UNCHANGED = "unchanged"


def months_covered(legs: List[FlightLeg]) -> Set[str]:
    """The set of "YYYY-MM" this paste makes a statement about."""
    return {leg.date.strftime("%Y-%m") for leg in legs}


def _shape(leg: FlightLeg) -> Tuple:
    """The fields a re-paste is allowed to correct.

    Route and date are NOT here — they are baked into the flight id, so
    changing either produces a different flight. That is correct: a leg
    that moved to another airport is a different leg, and it shows up as
    one removal and one addition rather than a silent edit.
    """
    return (leg.dep_time_local.isoformat(),
            leg.arr_time_local.isoformat(),
            bool(leg.is_deadhead))


def _departed(leg: FlightLeg, now: datetime) -> bool:
    """Has this leg's scheduled departure passed?

    Resolved through the airport's zone (models does this via
    timezones.py), never by comparing local clock times. On the rare leg
    whose airport will not resolve, treat it as PAST — still the safe
    direction under the 1.20.0 rules: a leg wrongly called past keeps its
    stored times and arrives UNTICKED, so the failure is that the pilot
    is asked rather than that something is deleted or overwritten.
    """
    dep = leg.dep_datetime_utc()
    return True if dep is None else dep <= now


def build_diff(pasted: List[FlightLeg], current: List[FlightLeg],
               now: Optional[datetime] = None) -> Dict[str, List[Dict]]:
    """Categorise every leg the pilot needs to see before approving.

    Returns four lists of {"leg": FlightLeg, ...}, plus the months in
    scope. Removals carry `was` so the page can say what is going.
    """
    now = now or datetime.now(timezone.utc)
    scope = months_covered(pasted)

    paste_by_id = {flight_key(l.id): l for l in pasted}
    current_by_id = {flight_key(l.id): l for l in current}

    out: Dict[str, List[Dict]] = {ADDED: [], REMOVED: [], CHANGED: [],
                                  UNCHANGED: []}

    for fid, leg in paste_by_id.items():
        have = current_by_id.get(fid)
        if have is None:
            out[ADDED].append({"leg": leg})
        elif _departed(have, now):
            # FLOWN, so NOTHING about it changes — not the times, not the
            # deadhead flag (1.22.0, simplified from 1.20.0, which made an
            # exception for the flag on the strength of a logbook that is
            # no longer being built).
            #
            # The diff has to agree with what the merge will actually do,
            # and merge freezes flown legs outright. Listing a flown leg
            # as "changed" would promise an edit the confirm step declines
            # to make — the same false promise `INSERT OR IGNORE` was
            # making before 1.20.0, reintroduced in a smaller place.
            out[UNCHANGED].append({"leg": leg})
        elif _shape(have) != _shape(leg):
            out[CHANGED].append({"leg": leg, "was": have})
        else:
            out[UNCHANGED].append({"leg": leg})

    # SCOPE IS STILL THE MONTH. A paste says nothing about a month it does
    # not mention, and reconciling outside its own months would let a
    # partial paste delete a month it never referred to.
    #
    # Departed legs are now offered too, but flagged, so the page can hold
    # them back from the default. See the docstring.
    for fid, leg in current_by_id.items():
        if fid in paste_by_id:
            continue
        if leg.date.strftime("%Y-%m") not in scope:
            continue          # different month; this paste says nothing
        out[REMOVED].append({"leg": leg, "flown": _departed(leg, now)})

    for key in out:
        out[key].sort(key=lambda e: (e["leg"].date,
                                     e["leg"].dep_time_local))
    return out


def month_labels(months: Set[str]) -> str:
    """"August 2026", or "August–September 2026" for a paste that spans."""
    if not months:
        return ""
    parsed = sorted(datetime.strptime(m, "%Y-%m") for m in months)
    if len(parsed) == 1:
        return parsed[0].strftime("%B %Y")
    if parsed[0].year == parsed[-1].year:
        return f"{parsed[0].strftime('%B')}–{parsed[-1].strftime('%B %Y')}"
    return f"{parsed[0].strftime('%B %Y')}–{parsed[-1].strftime('%B %Y')}"


# ---------------------------------------------------------------------------
# THE PLAN (1.30.0) — one timeline instead of four lists
#
# WHAT WAS WRONG WITH THE OLD REVIEW PAGE
# ---------------------------------------
# It showed the same flight TWICE, in two places, with two different
# controls, and only one of them did anything.
#
# The top of the page had four read-only summaries — added, changed, no
# longer on the line, already flown. The bottom had a COLLAPSED section
# called "Trip breaks & full list", and that was the actual form: the
# hidden fields that get imported, the X that drops a leg, and the trip
# break markers all lived in there. So a pilot looking at a flight under
# "Being added" and wanting to drop it had to know to open a collapsed
# section, find the same flight a second time, and use a different
# control on it. The one they were looking at was a picture.
#
# Two consequences beyond the confusion:
#
#   * TRIP BREAKS WERE INVISIBLE while reading the diff, which is the
#     only moment their placement can be judged. They were shut inside
#     the section nobody opened.
#   * A RETIMED LEG COULD NOT BE DECLINED. The diff said "times changed",
#     the pilot approved the import, and the new times were applied
#     because the leg was in the paste. There was no way to say "no, keep
#     what I have" short of editing the text before pasting it.
#
# WHAT THIS RETURNS
# -----------------
# ONE list, in departure order, holding every leg that this import has an
# opinion about — from the paste and from the roster alike, interleaved,
# so the month reads as a month rather than as four piles. Each row
# carries its own state and its own default, and the template gives each
# row exactly one control.
#
# The six states, and the default for each:
#
#   new         in the paste, not on your roster        -> import      ON
#   retimed     on your roster, and the paste disagrees -> update      ON
#   same        on your roster, paste agrees            -> (no control)
#   flown       on your roster, already departed        -> (frozen)
#   gone        upcoming, and the paste omits it        -> remove      ON
#   gone_flown  already flown, and the paste omits it   -> remove     OFF
#
# THE TWO "GONE" DEFAULTS ARE OPPOSITE, AND THAT IS THE SAFETY MECHANISM,
# not a style choice. It is the 1.20.0/1.21.0 rule, carried over intact.
# `gone` is the paste CONTRADICTING a leg you have not flown yet, so it
# leads with remove. `gone_flown` is the paste being SILENT about
# history — which is the ordinary result of pasting a single trip — so it
# leads with keep. Had flown legs ever defaulted to remove, importing one
# trip would delete a month of history by default.
#
# `flown` legs in the paste are frozen for the 1.22.0 reason: a flown
# leg is NEVER modified by an import, no exceptions. They appear on the
# list anyway rather than being hidden, because a pilot counting his trip
# needs to see them; they simply have nothing to decide.
# ---------------------------------------------------------------------------

NEW = "new"
RETIMED = "retimed"
SAME = "same"
FLOWN = "flown"
GONE = "gone"
GONE_FLOWN = "gone_flown"

# Which states the pilot can actually act on, and which way they lead.
# Declared as data rather than as branches in the template, so the page
# and the confirm step cannot come to disagree about what a row means.
# `default_on` means ONE thing: WILL THIS ROW'S DECISION BE APPLIED.
# For a paste row that means "will its inputs be submitted"; for a roster
# row it means "will the removal be ticked".
#
# SAME and FLOWN are True, and that is a BUG FIX (1.30.1), not a tidy-up.
# They were absent, so `.get(state, False)` made them False — while the
# template still rendered their hidden inputs, because they ARE in the
# paste and the merge does receive them. So the page said "off" about
# rows the browser was submitting, and the review page's trip-break
# counter believed it. See the 1.30.1 entry: a whole-month paste lost
# every trip break after the first already-flown leg.
PLAN_DEFAULT_ON = {NEW: True, RETIMED: True, SAME: True, FLOWN: True,
                   GONE: True, GONE_FLOWN: False}

# The states the pilot can act on at all. `same` and `flown` are on the
# list to be SEEN, not to be decided — a pilot counting his trip needs
# them there, but there is nothing to switch. Kept as data so the
# template, the summary and the page's JavaScript all ask one question
# rather than each carrying their own list of state names.
PLAN_ACTIONABLE = (NEW, RETIMED, GONE, GONE_FLOWN)


def build_plan(pasted: List[FlightLeg], current: List[FlightLeg],
               now: Optional[datetime] = None) -> List[Dict]:
    """Every leg this import touches, in departure order, each with a state.

    Built from `build_diff` rather than beside it: the categorisation
    rules — what counts as changed, which months are in scope, what is
    frozen — are subtle, already correct, and already tested. A second
    implementation of them here is how the page and the merge come to
    disagree, which is the exact failure 1.20.0 was spent on.
    """
    now = now or datetime.now(timezone.utc)
    diff = build_diff(pasted, current, now)
    paste_by_id = {flight_key(l.id): l for l in pasted}
    current_by_id = {flight_key(l.id): l for l in current}

    rows: List[Dict] = []

    def add(leg, state, was=None):
        rows.append({"leg": leg, "state": state, "was": was,
                     "id": flight_key(leg.id),
                     "in_paste": flight_key(leg.id) in paste_by_id,
                     "default_on": PLAN_DEFAULT_ON.get(state, False)})

    for e in diff[ADDED]:
        add(e["leg"], NEW)
    for e in diff[CHANGED]:
        add(e["leg"], RETIMED, was=e.get("was"))
    for e in diff[UNCHANGED]:
        # build_diff folds two different things into UNCHANGED: a leg the
        # paste genuinely matches, and a FLOWN leg the paste restates
        # (which it declines to call "changed", because nothing about a
        # flown leg is editable). They read differently to a pilot — one
        # is "nothing to do", the other is "this already happened" — so
        # they are separated again here, using the roster's copy, which
        # is the one with the real departure time on it.
        have = current_by_id.get(flight_key(e["leg"].id))
        add(e["leg"], FLOWN if (have is not None and _departed(have, now))
            else SAME)
    for e in diff[REMOVED]:
        add(e["leg"], GONE_FLOWN if e.get("flown") else GONE)

    # DEPARTURE ORDER, because that is the order he flies them and the
    # only order in which a trip break means anything. Four separate
    # lists could never show that a removed leg sits in the MIDDLE of a
    # trip you are keeping, which is precisely the case worth seeing.
    rows.sort(key=lambda r: (r["leg"].date, r["leg"].dep_time_local))
    return rows


def plan_summary(rows: List[Dict]) -> Dict[str, int]:
    """How many rows in each state, for the count at the top of the page."""
    out = {s: 0 for s in (NEW, RETIMED, SAME, FLOWN, GONE, GONE_FLOWN)}
    for r in rows:
        out[r["state"]] = out.get(r["state"], 0) + 1
    return out
