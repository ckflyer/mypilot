"""Reserve days: survive re-import, yield to flights, persist otherwise.

Reserve is the only schedule data in this app that is typed in by hand,
which makes it the only data a person can actually lose. These tests
cover the three promises made about it.
"""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PT_DB_FILE", tempfile.mkdtemp() + "/reserve.db")

from app.db import init_db
from app.auth import create_user
from app.parser import parse_schedule_text
from app.flights import replace_schedule
from app import reserve

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if not ok else ""))


init_db()
_n = [0]


def new_pilot():
    _n[0] += 1
    return create_user(f"rsv{_n[0]}", "pw-not-used")


def test_a_reserve_day_survives_reimporting_the_schedule():
    """The whole reason reserve lives in its own table.

    replace_schedule() clears the roster and writes the pasted legs.
    Anything hand-entered inside `flights` would be gone. This asserts
    the separation actually holds end to end, not just in principle.
    """
    uid = new_pilot()
    d = date(2026, 9, 14)
    reserve.set_day(uid, d, True)
    check("the day is marked", reserve.days_in_range(uid, d, d) != {})

    replace_schedule(uid, parse_schedule_text(
        "09/20/2026 1001 DFW 0800 OKC 0900"))
    check("...and is still there after an import",
          d.isoformat() in reserve.days_in_range(uid, d, d))

    # Twice, because "survives one import" and "survives every import"
    # are different claims and only the second one is useful.
    replace_schedule(uid, parse_schedule_text(
        "09/21/2026 1002 OKC 0800 DFW 0900"))
    check("...and after a second, different import",
          d.isoformat() in reserve.days_in_range(uid, d, d))


def test_an_assigned_flight_hides_reserve_without_destroying_it():
    """Assignment wins on the day, but does not erase the fact.

    If the row were deleted on import, a reassignment that later moved
    the trip would leave the day EMPTY rather than back on reserve — the
    app would have thrown away something true because something else was
    briefly true.
    """
    uid = new_pilot()
    d = date(2026, 9, 14)
    reserve.set_day(uid, d, True)

    flying = [d]
    check("a day with flights does not show as reserve",
          reserve.visible_days(uid, d, d, flying) == set())
    check("...but the row is still in the table",
          d.isoformat() in reserve.days_in_range(uid, d, d))
    check("...so it comes back when the assignment goes away",
          reserve.visible_days(uid, d, d, []) == {d.isoformat()})


def test_only_a_person_removes_a_reserve_day():
    uid = new_pilot()
    d = date(2026, 9, 14)
    reserve.set_day(uid, d, True)
    reserve.set_day(uid, d, False)
    check("unmarking removes it", reserve.days_in_range(uid, d, d) == {})
    check("...and visible_days agrees",
          reserve.visible_days(uid, d, d, []) == set())


def test_marking_is_idempotent():
    """A double-tap or a retried request must not corrupt the day."""
    uid = new_pilot()
    d = date(2026, 9, 14)
    for _ in range(3):
        reserve.set_day(uid, d, True)
    check("marking three times leaves one row", reserve.count_for_user(uid) == 1)
    for _ in range(3):
        reserve.set_day(uid, d, False)
    check("unmarking three times leaves none", reserve.count_for_user(uid) == 0)


def test_reserve_days_are_private_to_a_pilot():
    a, b = new_pilot(), new_pilot()
    d = date(2026, 9, 14)
    reserve.set_day(a, d, True)
    check("one pilot's reserve is not another's",
          reserve.days_in_range(b, d, d) == {})


def test_the_range_query_covers_only_what_was_asked_for():
    """The calendar grid pads with days from neighbouring months, so the
    range is wider than the month and the bounds have to be right."""
    uid = new_pilot()
    for day in (date(2026, 8, 31), date(2026, 9, 15), date(2026, 10, 1)):
        reserve.set_day(uid, day, True)
    got = reserve.days_in_range(uid, date(2026, 9, 1), date(2026, 9, 30))
    check("only September comes back", set(got) == {"2026-09-15"}, str(sorted(got)))
    wide = reserve.days_in_range(uid, date(2026, 8, 30), date(2026, 10, 4))
    check("a padded grid range picks up the neighbours", len(wide) == 3, str(sorted(wide)))


def test_a_batch_of_changes_is_all_or_nothing():
    """The picker sends one batch, so a half-applied save would leave the
    pilot looking at a calendar that disagrees with what they just did,
    with no way to tell which half took."""
    uid = new_pilot()
    d = date(2026, 9, 1)
    res = reserve.apply_changes(uid, [date(2026, 9, 2), date(2026, 9, 3)], [])
    check("adds are counted", res == {"added": 2, "removed": 0}, str(res))
    check("...and are in the table", reserve.count_for_user(uid) == 2)

    res = reserve.apply_changes(uid, [date(2026, 9, 4)], [date(2026, 9, 2)])
    check("a mixed batch adds and removes together",
          res == {"added": 1, "removed": 1}, str(res))
    got = set(reserve.days_in_range(uid, d, date(2026, 9, 30)))
    check("...leaving exactly the right days", got == {"2026-09-03", "2026-09-04"},
          str(sorted(got)))


def test_adding_a_day_twice_in_one_batch_is_harmless():
    uid = new_pilot()
    d = date(2026, 9, 5)
    reserve.apply_changes(uid, [d], [])
    res = reserve.apply_changes(uid, [d], [])
    check("re-adding an existing day does not duplicate it",
          reserve.count_for_user(uid) == 1, str(res))


def test_a_date_in_both_lists_is_kept_not_dropped():
    """Should not happen from the UI. If it does, the safe reading is
    that the pilot asked for the day — silently dropping one they asked
    for is the worse of the two failures."""
    uid = new_pilot()
    d = date(2026, 9, 6)
    reserve.apply_changes(uid, [d], [d])
    check("add wins over remove", reserve.count_for_user(uid) == 1)


def test_a_month_of_only_reserve_still_counts_as_a_month():
    """The calendar used to build its month list from flights alone.

    A month holding nothing but reserve was therefore treated as empty:
    saved in the database, listed by all_dates, and unreachable in the
    UI. This asserts the source the calendar reads actually contains it.
    """
    uid = new_pilot()
    reserve.apply_changes(uid, [date(2026, 11, 3), date(2026, 11, 4)], [])
    months = {(int(i[:4]), int(i[5:7])) for i in reserve.all_dates(uid)}
    check("a reserve-only month is discoverable", (2026, 11) in months, str(months))
    check("...and every reserve date is listed", len(reserve.all_dates(uid)) == 2)


def main():
    test_a_month_of_only_reserve_still_counts_as_a_month()
    test_a_batch_of_changes_is_all_or_nothing()
    test_adding_a_day_twice_in_one_batch_is_harmless()
    test_a_date_in_both_lists_is_kept_not_dropped()
    test_a_reserve_day_survives_reimporting_the_schedule()
    test_an_assigned_flight_hides_reserve_without_destroying_it()
    test_only_a_person_removes_a_reserve_day()
    test_marking_is_idempotent()
    test_reserve_days_are_private_to_a_pilot()
    test_the_range_query_covers_only_what_was_asked_for()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
