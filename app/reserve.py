"""Reserve days.

WHY A SEPARATE TABLE (1.29.0).
=============================

Reserve is not in FFDO, so it is typed in by hand — which makes it the
only schedule data in this app a person can lose. Importing a schedule
calls replace_schedule(), and replace_schedule means what it says: it
clears the roster and writes the pasted legs. Anything hand-entered
living in `flights` would be destroyed by the next paste.

So reserve days do not live in `flights`. They live here, in a table
replace_schedule has never heard of and never touches.

That is the whole design, and it is worth being explicit about why it was
chosen over the alternative. The other option was a flag on a flight row
plus code in the import path to notice hand-entered rows and carry them
across. That works right up until someone adds a second import path, or
refactors the merge, or adds a bulk delete — and then it quietly stops
working and a pilot loses a month of reserve. Here, surviving an import
is not a behaviour anybody has to remember to preserve. It is a
consequence of the data being somewhere else.

"OVERWRITTEN BY AN ASSIGNED FLIGHT" IS A DISPLAY RULE, NOT A DELETE.
====================================================================

When a flight is assigned to a reserve day, the day should read as a
flying day. The obvious implementation is to delete the reserve row on
import. This does not do that, and the difference matters:

Reserve assignments get dropped. Trips get rescheduled and re-imported.
If the assignment is deleted from the next paste and the reserve row was
already gone, the day comes back EMPTY — and the pilot is still on
reserve that day. The app would have quietly thrown away something true
because something else was temporarily true.

Instead the row stays and is hidden while flights exist on that date.
Assignment lands, the day shows the flight. Assignment disappears, the
day is on reserve again, which is what reserve means. Nothing needs
cleaning up, and the only thing that removes a reserve day is a person
removing it.
"""

from datetime import date
from typing import Dict, Iterable, Optional, Set

from .db import get_connection


def ensure_table(conn) -> None:
    """Create the reserve table. Called from init_db.

    SCHEMA_VERSION is deliberately NOT bumped for this. The version guard
    exists to stop an older build opening a newer database, and this is a
    purely additive table that an older build simply will not query.
    Bumping would turn a harmless rollback into a refusal to start, which
    is a worse failure than the one it would be protecting against.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reserve_days (
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            note TEXT,
            created_at TEXT,
            PRIMARY KEY (user_id, date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reserve_user_date "
        "ON reserve_days(user_id, date)"
    )


def set_day(user_id: int, day: date, on: bool, note: str = "") -> bool:
    """Mark or unmark a reserve day. Returns the resulting state.

    Idempotent in both directions, so a double-tap or a retried request
    cannot leave the day in a state the person did not ask for.
    """
    conn = get_connection()
    try:
        if on:
            conn.execute(
                "INSERT INTO reserve_days (user_id, date, note, created_at) "
                "VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(user_id, date) DO UPDATE SET note = excluded.note",
                (user_id, day.isoformat(), (note or "").strip()[:200]),
            )
        else:
            conn.execute(
                "DELETE FROM reserve_days WHERE user_id = ? AND date = ?",
                (user_id, day.isoformat()),
            )
        conn.commit()
    finally:
        conn.close()
    return on


def days_in_range(user_id: int, start: date, end: date) -> Dict[str, str]:
    """Reserve days between start and end inclusive, as {iso: note}.

    Dates are stored as ISO strings, which sort the same lexically as
    they do chronologically, so BETWEEN works without parsing anything.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT date, COALESCE(note, '') AS note FROM reserve_days "
            "WHERE user_id = ? AND date BETWEEN ? AND ? ORDER BY date",
            (user_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return {r["date"]: r["note"] for r in rows}


def visible_days(user_id: int, start: date, end: date,
                 dates_with_flights: Iterable[date]) -> Set[str]:
    """Reserve days that should actually be SHOWN in a range.

    A day carrying flights is a flying day: the reserve row stays in the
    table but is suppressed here. See the module docstring for why this
    is hiding rather than deleting.
    """
    flying = {d.isoformat() for d in dates_with_flights}
    return {iso for iso in days_in_range(user_id, start, end) if iso not in flying}


def count_for_user(user_id: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM reserve_days WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return int(row["n"]) if row else 0


def apply_changes(user_id: int, add, remove) -> dict:
    """Apply a batch of reserve changes in one transaction.

    The picker collects edits and sends them together, so this must be
    all-or-nothing: a half-applied batch would leave the pilot looking at
    a calendar that disagrees with what they just saved, with no way to
    tell which half took.

    Adds win over removes if a date somehow appears in both. That should
    not happen from the UI, but the alternative is deciding it by
    dictionary order, and silently dropping a day someone asked for is
    the worse of the two failures.
    """
    add = {d.isoformat() if hasattr(d, "isoformat") else str(d) for d in add}
    remove = {d.isoformat() if hasattr(d, "isoformat") else str(d) for d in remove} - add

    conn = get_connection()
    try:
        with conn:  # commits on success, rolls back if anything raises
            for iso in sorted(add):
                conn.execute(
                    "INSERT INTO reserve_days (user_id, date, note, created_at) "
                    "VALUES (?, ?, '', datetime('now')) "
                    "ON CONFLICT(user_id, date) DO NOTHING",
                    (user_id, iso),
                )
            for iso in sorted(remove):
                conn.execute(
                    "DELETE FROM reserve_days WHERE user_id = ? AND date = ?",
                    (user_id, iso),
                )
    finally:
        conn.close()
    return {"added": len(add), "removed": len(remove)}
