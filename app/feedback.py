"""Bug reports and feedback, and the admin inbox that receives them.

WHY ITS OWN TABLE, NOT A COLUMN ON `users` (1.30.0)
===================================================

The obvious shape is a column on the `users` row. It does not work, for
the same reason `share_codes` needed its own table in 1.23.0: a column
holds ONE value. A pilot who reports a bug in August and another in
September would overwrite the first, and the one thing a bug inbox must
never do is lose a report because a second one arrived.

A report is also not a fact about a person. It is an event, with its own
time, its own app version and its own state — and the same person
generates many of them. That is a row, not a field.

The same argument rules out reusing `debug_events`, which is the nearest
existing table. That one is a RING BUFFER: it self-trims at 20,000 rows
and its retention is deliberately NOT tied to `PT_RETENTION_DAYS`,
because diagnostics are disposable. A bug report is not disposable — it
is the only copy of something a human took the trouble to write down,
and a busy night of polling must not be able to push it out of the
buffer.

WHAT IS CAPTURED, AND WHY EACH FIELD EARNS ITS PLACE
====================================================

Every field here answers a question that was previously answered by
asking the reporter, usually days later when they had forgotten:

    app_version   WHICH BUILD. The single most valuable field. Half the
                  bugs in this app's history were a phone running the
                  previous release's markup (see the cache note in
                  main._no_stale_html), and that is indistinguishable
                  from a real bug until you know the version.
    page          WHERE they were. Captured from the referring page, not
                  typed, because nobody describes a URL accurately.
    user_agent    WHICH PHONE. iOS Safari and Android Chrome disagree
                  about enough here — date inputs, share sheets, the
                  install prompt — that "it looks wrong" needs a device.
    who           WHICH PERSON, including a viewer. See below.

VIEWERS CAN FILE REPORTS, AND THAT IS THE POINT
================================================

The README's OPEN list has carried this for several releases: "Only the
pilot ever tests the pilot's app." The settings tab sent every viewer to
a login screen for an unknown number of releases, and it was found
because a family member happened to mention it in conversation. There
was no route from "the person using the app" to "the person who can fix
it" other than that conversation.

So a viewer files reports too. They have no account, so there is no
user_id to hang the row on; instead the row records the PILOT they are
watching (so it reaches the right inbox) and the invite NAME they logged
in under, which is what the pilot actually knows them by. `is_viewer`
keeps the two kinds apart, because "my wife says the map is blank" and
"I say the map is blank" are different reports about different builds on
different phones.

STATUS IS THREE VALUES AND NO MORE
===================================

new / open / done. An inbox needs to distinguish "I have not looked at
this" from "I have looked and it is real" from "handled". A fourth state
("won't fix", "duplicate") is a taxonomy, and a taxonomy on a one-person
project is overhead that gets filled in wrongly and then trusted. Delete
what you do not want to keep.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from .db import get_connection

# What a reporter picks. Deliberately three, deliberately plain words:
# a form that asks for a severity rating gets a severity rating that
# means nothing, because the person filling it in has no basis for
# comparison and every report is urgent to the person filing it.
KINDS = {
    "bug": "Something is broken",
    "idea": "Idea or request",
    "other": "Something else",
}

STATUSES = ("new", "open", "done")

# Hard ceilings, enforced on the way IN rather than trusted from the
# form. A maxlength attribute is a hint to a browser, not a constraint on
# a request, and this table is reachable by anyone who can log in.
MAX_MESSAGE = 4000
MAX_UA = 300
MAX_PAGE = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_table(conn) -> None:
    """Create the feedback table. Called from init_db.

    SCHEMA_VERSION is deliberately NOT bumped, for the reason recorded in
    reserve.ensure_table: the guard exists to stop an OLDER build opening
    a NEWER database, and a purely additive table that an older build
    never queries is harmless to roll back past. Bumping would turn a
    harmless rollback into a refusal to start.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at   TEXT NOT NULL,
            -- WHOSE INBOX. For a pilot's own report this is themselves;
            -- for a viewer it is the pilot they are watching. Either way
            -- it is the account this report belongs to, which is what
            -- makes the inbox query one indexed lookup.
            user_id      INTEGER,
            -- Denormalised ON PURPOSE. A username can change (settings
            -- has allowed that since 1.25.0) and an account can be
            -- deleted, and a report that suddenly says "user 4" or
            -- nothing at all has lost the part a human reads. This is a
            -- record of who said it AT THE TIME.
            reporter     TEXT,
            is_viewer    INTEGER NOT NULL DEFAULT 0,
            kind         TEXT NOT NULL DEFAULT 'bug',
            message      TEXT NOT NULL,
            page         TEXT,
            app_version  TEXT,
            user_agent   TEXT,
            status       TEXT NOT NULL DEFAULT 'new',
            admin_note   TEXT,
            handled_at   TEXT,
            handled_by   TEXT
        )
        """
    )
    # Newest first is the only order this is ever read in, and the unread
    # badge counts on status. Both are covered here.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_new "
                 "ON feedback(status, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_user "
                 "ON feedback(user_id, id DESC)")


def submit(*, user_id: Optional[int], reporter: str, is_viewer: bool,
           kind: str, message: str, page: str = "", app_version: str = "",
           user_agent: str = "") -> Optional[int]:
    """Record one report. Returns its id, or None if there was nothing to say.

    NEVER RAISES INTO THE REQUEST. A failure to store feedback must not
    produce a 500 on a page someone reached because something was already
    wrong — that turns "I could not report the bug" into a second bug,
    and the person reporting is by definition already having a bad time.
    The same reasoning debuglog.py is built on.
    """
    text = (message or "").strip()
    if not text:
        return None
    if kind not in KINDS:
        kind = "bug"
    try:
        conn = get_connection()
        try:
            ensure_table(conn)
            cur = conn.execute(
                "INSERT INTO feedback (created_at, user_id, reporter, "
                "is_viewer, kind, message, page, app_version, user_agent, "
                "status) VALUES (?,?,?,?,?,?,?,?,?,'new')",
                (_now(), user_id, (reporter or "").strip()[:60],
                 1 if is_viewer else 0, kind, text[:MAX_MESSAGE],
                 (page or "").strip()[:MAX_PAGE],
                 (app_version or "").strip()[:20],
                 (user_agent or "").strip()[:MAX_UA]))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as e:                       # pragma: no cover
        print(f"[feedback] could not store report: {e}")
        return None


def _row(r) -> Dict:
    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "date_label": (r["created_at"] or "")[:16].replace("T", " "),
        "user_id": r["user_id"],
        "reporter": r["reporter"] or "—",
        "is_viewer": bool(r["is_viewer"]),
        "kind": r["kind"],
        "kind_label": KINDS.get(r["kind"], r["kind"]),
        "message": r["message"] or "",
        "page": r["page"] or "",
        "app_version": r["app_version"] or "",
        "user_agent": r["user_agent"] or "",
        "status": r["status"],
        "admin_note": r["admin_note"] or "",
        "handled_at": r["handled_at"] or "",
        "handled_by": r["handled_by"] or "",
    }


def recent(limit: int = 50, status: str = "", user_id: Optional[int] = None
           ) -> List[Dict]:
    """The inbox. Newest first.

    `user_id` is NOT applied for an admin — an admin runs the install and
    sees every report on it, including those filed against another
    pilot's account, because a bug in the tracker is a bug in the tracker
    whoever hit it. The parameter exists for the pilot-facing "your own
    reports" list, which is a different question.
    """
    where, args = [], []
    if status in STATUSES:
        where.append("status = ?")
        args.append(status)
    if user_id is not None:
        where.append("user_id = ?")
        args.append(user_id)
    sql = "SELECT * FROM feedback"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    try:
        conn = get_connection()
        try:
            ensure_table(conn)
            return [_row(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()
    except Exception as e:                       # pragma: no cover
        print(f"[feedback] could not read reports: {e}")
        return []


def counts() -> Dict[str, int]:
    """How many in each state, for the badge on the admin row.

    Always returns every key, so a template can read `counts.new` without
    a default and a fresh install shows 0 rather than a blank.
    """
    out = {s: 0 for s in STATUSES}
    out["total"] = 0
    try:
        conn = get_connection()
        try:
            ensure_table(conn)
            for r in conn.execute(
                    "SELECT status, COUNT(*) c FROM feedback GROUP BY status"):
                if r["status"] in out:
                    out[r["status"]] = r["c"]
                out["total"] += r["c"]
        finally:
            conn.close()
    except Exception as e:                       # pragma: no cover
        print(f"[feedback] could not count reports: {e}")
    return out


def set_status(report_id: int, status: str, by: str = "") -> bool:
    """Move a report between new / open / done.

    `handled_at` is stamped only on the way to `done`, and CLEARED on the
    way back out. A report reopened in March should not still claim it
    was handled in January — that is a stale fact presented as a current
    one, which is the failure mode this whole app is careful about.
    """
    if status not in STATUSES:
        return False
    try:
        conn = get_connection()
        try:
            ensure_table(conn)
            if status == "done":
                conn.execute(
                    "UPDATE feedback SET status = ?, handled_at = ?, "
                    "handled_by = ? WHERE id = ?",
                    (status, _now(), (by or "").strip()[:60], int(report_id)))
            else:
                conn.execute(
                    "UPDATE feedback SET status = ?, handled_at = NULL, "
                    "handled_by = NULL WHERE id = ?", (status, int(report_id)))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:                       # pragma: no cover
        print(f"[feedback] could not update report {report_id}: {e}")
        return False


def delete(report_id: int) -> bool:
    try:
        conn = get_connection()
        try:
            ensure_table(conn)
            conn.execute("DELETE FROM feedback WHERE id = ?", (int(report_id),))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:                       # pragma: no cover
        print(f"[feedback] could not delete report {report_id}: {e}")
        return False


def as_text(rows: List[Dict]) -> str:
    """The inbox as a plain-text file.

    Same reasoning as the decision log's download: the useful thing to do
    with a bug report is paste it somewhere else — into a message, into a
    session with whoever is fixing it — and selecting text out of a
    scrolling panel on a phone is not a way to do that.
    """
    out = []
    for r in rows:
        who = f"{r['reporter']}{' (viewer)' if r['is_viewer'] else ''}"
        out.append(f"#{r['id']}  {r['date_label']}  [{r['status']}]  "
                   f"{r['kind_label']}")
        out.append(f"  from: {who}")
        out.append(f"  build: {r['app_version'] or '?'}    "
                   f"page: {r['page'] or '?'}")
        if r["user_agent"]:
            out.append(f"  device: {r['user_agent']}")
        out.append("")
        for line in r["message"].splitlines() or [""]:
            out.append(f"  {line}")
        if r["admin_note"]:
            out.append(f"  note: {r['admin_note']}")
        out.append("-" * 60)
    return "\n".join(out) + "\n"
