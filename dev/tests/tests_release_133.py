"""1.33.0: the retention leak, the security pass, and the owner's reports.

Every check here guards something that failed (or could fail) SILENTLY:

  * Flown flights were deleted after 30 days while everything said 365.
    Nothing errored; the calendar just got shorter.
  * The radar route froze every other request while it fetched a tile.
  * The login limiter believed any visitor who claimed a new address.
  * A password change left every other device signed in for a year.
  * The admin inbox printed `<span class="alert">` as text.
  * The progress bar stayed full on every landed flight forever.

Run: python dev/tests/tests_release_133.py
"""
import os as _os
import sys as _sys

_TEST_DIR = _os.path.abspath(_os.path.dirname(__file__))
PROJECT_ROOT = _os.path.dirname(_os.path.dirname(_TEST_DIR))
_sys.path.insert(0, PROJECT_ROOT)

import inspect
import os
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone

os.environ.setdefault("PT_DB_FILE", os.path.join(tempfile.mkdtemp(), "r133.db"))
os.environ["TRACK_POLLER_ENABLED"] = "0"

from fastapi.testclient import TestClient                       # noqa: E402
from starlette.requests import Request                          # noqa: E402

import app.main as m                                            # noqa: E402
from app import flights, ratelimit, view, radar_proxy           # noqa: E402
from app.airports import enrich_leg                             # noqa: E402
from app.auth import (add_share_code, create_user, get_user_by_share_code,  # noqa: E402
                      share_codes_for)
from app.db import get_connection, init_db                      # noqa: E402
from app.flights import get_flight, replace_schedule, write     # noqa: E402
from app.models import FlightLeg                                # noqa: E402

UTC = timezone.utc
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          (f"  [{detail}]" if detail and not cond else ""))


def read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def make_leg(num, on, origin="DFW", dest="OKC"):
    leg = FlightLeg(id=f"{on.isoformat()}-{num}-{origin}-{dest}", date=on,
                    flight_number=str(num), origin=origin, destination=dest,
                    dep_time_local=time(14, 0), arr_time_local=time(15, 10))
    enrich_leg(leg)
    return leg


def fake_request(peer, headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw,
                    "client": (peer, 1234), "query_string": b""})


# ---------------------------------------------------------------- retention
def test_retention(uid):
    print("\nRetention: a year means a year")
    now = datetime.now(UTC)
    flown = make_leg(5101, (now - timedelta(days=40)).date())
    ancient = make_leg(5102, (now - timedelta(days=400)).date())
    replace_schedule(uid, [flown, ancient])
    closed_at = now - timedelta(days=40)
    for leg in (flown, ancient):
        # The exact stamp 1.32.0 wrote on every closed leg: closed + 30 days.
        write(leg.id, always={"closed": 1, "closed_at": closed_at.isoformat(),
                              "closed_by": "airline",
                              "purge_after": (closed_at + timedelta(days=30)).isoformat()})
    flights.purge_old(now)
    check("THE BUG: a leg flown 40 days ago survives the purge",
          get_flight(flown.id) is not None)
    check("...a leg past the year is still purged", get_flight(ancient.id) is None)

    keep = make_leg(5103, (now - timedelta(days=4000)).date())
    replace_schedule(uid, [flown, keep])
    saved = flights.RETENTION_DAYS
    try:
        flights.RETENTION_DAYS = 0
        removed = flights.purge_old(now)
        check("PT_RETENTION_DAYS=0 keeps everything, as documented",
              removed == 0 and get_flight(keep.id) is not None, str(removed))
    finally:
        flights.RETENTION_DAYS = saved
    from app.track import prune_old_positions
    check("...and keeps every track point too", prune_old_positions(0) == 0)
    check("closure no longer stamps a 30-day purge",
          "KEEP_DAYS = 30" not in read("app/closure.py"))


# -------------------------------------------------------------------- radar
def test_radar(client_pilot, anon):
    print("\nRadar: off the event loop, signed-in only, whitelisted")
    check("the route is a plain def, so it runs in a worker thread",
          not inspect.iscoroutinefunction(m.radar_wms))
    r = anon.get("/radar/wms?bbox=1,2,3,4&width=256&height=256")
    check("a stranger gets 401", r.status_code == 401, str(r.status_code))

    seen = {}
    real = radar_proxy.get_tile
    try:
        radar_proxy.get_tile = lambda q, lvl: seen.update(q=q, lvl=lvl) or b"\x89PNG"
        r = client_pilot.get("/radar/wms?service=WMS&request=GetMap&layers=nexrad-n0q"
                             "&width=99999&height=256&bbox=1,2,3,4&min=3&evil=1")
    finally:
        radar_proxy.get_tile = real
    check("a signed-in page still gets its tile", r.status_code == 200, str(r.status_code))
    q = seen.get("q", {})
    check("unknown parameters are not forwarded", "evil" not in q, str(q))
    check("tile size is clamped", q.get("width") == "512", str(q.get("width")))
    check("the threshold still reaches the filter", seen.get("lvl") == 3)


# ---------------------------------------------------------------- rate limit
def test_client_ip():
    print("\nRate limiter: who is asking")
    forged = fake_request("8.8.8.8", {"CF-Connecting-IP": "1.2.3.4"})
    check("a forged Cloudflare header from the internet is ignored",
          ratelimit.get_client_ip(forged) == "8.8.8.8",
          ratelimit.get_client_ip(forged))
    tunnel = fake_request("172.18.0.5", {"CF-Connecting-IP": "198.51.100.7"})
    check("...but believed from the tunnel on the private network",
          ratelimit.get_client_ip(tunnel) == "198.51.100.7")
    xff = fake_request("8.8.8.8", {"X-Forwarded-For": "9.9.9.9"})
    check("X-Forwarded-For from the internet is ignored too",
          ratelimit.get_client_ip(xff) == "8.8.8.8")


def test_share_codes(uid):
    print("\nShare codes: longer, one system")
    ratelimit.reset_all()
    code = add_share_code(uid, "Test")
    check("a new code is 8 digits", len(code) == 8 and code.isdigit(), code)
    legacy = share_codes_for(uid)[0]["code"]
    conn = get_connection()
    try:
        conn.execute("UPDATE share_codes SET code = '04512' WHERE code = ?", (legacy,))
        conn.execute("UPDATE users SET share_code = '04512' WHERE id = ?", (uid,))
        conn.commit()
    finally:
        conn.close()
    check("a 5-digit code from before still resolves, same lookup",
          get_user_by_share_code("04512") is not None)
    c = TestClient(m.app)
    r = c.post("/login/code", data={"code": code[:4] + " " + code[4:]},
               follow_redirects=False)
    check("an 8-digit code with a space pasted in logs in", r.status_code == 303,
          str(r.status_code))
    c2 = TestClient(m.app)
    r = c2.post("/login/code", data={"code": "04512"}, follow_redirects=False)
    check("...and so does the old 5-digit one", r.status_code == 303)
    check("the login box accepts either length",
          'maxlength="5"' not in read("templates/login.html"))

    ratelimit.reset_all()
    for _ in range(m.CODE_FAILS_PER_HOUR):
        ratelimit.note_failure("share_code")
    c3 = TestClient(m.app)
    r = c3.post("/login/code", data={"code": code}, follow_redirects=False)
    check("too many wrong codes, from anyone, pauses code sign-in",
          r.status_code == 429, str(r.status_code))
    ratelimit.reset_all()


# ------------------------------------------------------------------ sessions
def test_sessions():
    print("\nSessions: a password change signs out other devices")
    create_user("epochpilot", "first-password")
    a, b = TestClient(m.app), TestClient(m.app)
    for c in (a, b):
        c.post("/login/pilot", data={"username": "epochpilot",
                                     "password": "first-password"})
    check("both devices are signed in",
          a.get("/flights", follow_redirects=False).status_code == 200
          and b.get("/flights", follow_redirects=False).status_code == 200)
    a.post("/settings/password", data={"current_password": "first-password",
                                       "new_password": "second-password",
                                       "confirm_password": "second-password"})
    check("the device that changed it stays in",
          a.get("/flights", follow_redirects=False).status_code == 200)
    r = b.get("/flights", follow_redirects=False)
    check("THE OTHER DEVICE IS SIGNED OUT", r.status_code == 303
          and r.headers.get("location") == "/login", str(r.status_code))
    check("an unknown username still pays for a hash (no timing tell)",
          "_DUMMY_HASH" in inspect.getsource(m.login_pilot))


def test_usernames():
    print("\nUsernames: one rule everywhere")
    ratelimit.reset_all()
    create_user("Dave", "password123")
    c = TestClient(m.app)
    r = c.post("/register", data={"username": "dave", "password": "password123",
                                  "confirm_password": "password123"})
    check("'dave' cannot register beside 'Dave'", "already taken" in r.text)
    r = c.post("/register", data={"username": "a<b>c", "password": "password123",
                                  "confirm_password": "password123"})
    check("markup characters are refused in a username", "3 to 40 characters" in r.text)
    ratelimit.reset_all()


# ------------------------------------------------------------------- headers
def test_headers(client_pilot):
    print("\nSecurity headers")
    r = client_pilot.get("/login")
    for h in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
              "Permissions-Policy"):
        check(f"{h} is set", h in r.headers)
    check("no HSTS over plain http", "Strict-Transport-Security" not in r.headers)
    ratelimit.reset_all()
    c = TestClient(m.app)
    r = c.post("/login/code", data={"code": "00000000"},
               headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    check("HSTS over https", "Strict-Transport-Security" in r.headers)
    create_user("securepilot", "password123")
    r = c.post("/login/pilot", data={"username": "securepilot", "password": "password123"},
               headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    cookie = r.headers.get("set-cookie", "")
    check("the login cookie is marked Secure over https",
          "pt_session=" in cookie and "secure" in cookie.lower(), cookie[:80])
    check("...and HttpOnly", "httponly" in cookie.lower())


def test_api_401(anon):
    print("\nAPI says 401 when signed out")
    for path in ("/api/v1/current", "/api/v1/selected", "/api/v1/leg/x"):
        r = anon.get(path)
        check(f"{path} -> 401", r.status_code == 401, str(r.status_code))
    v = read("templates/viewer.html")
    check("the tracker goes to sign-in on a 401", "r.status === 401" in v)
    check("the sign-in page drops the offline flight cache",
          "mypilot-api" in read("templates/login.html"))


def test_hardening_misc():
    print("\nMisc hardening")
    req = read("requirements.txt")
    lines = [l for l in req.splitlines() if l.strip() and not l.startswith("#")]
    check("every dependency is pinned exactly", all("==" in l for l in lines),
          str([l for l in lines if "==" not in l]))
    src = read("app/main.py")
    check("no deprecated startup hooks remain",
          not re.search(r"^@app\.on_event", src, flags=re.M))
    check("startup runs through lifespan", "lifespan=_lifespan" in src)
    check("feedback is rate limited", '"feedback", max_attempts' in src)
    check("last-seen writes are throttled",
          "last_seen_at < ?" in read("app/auth.py"))


# ------------------------------------------------------- the owner's reports
def test_progress_hold():
    print("\nReport #8: the bar after landing")
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def row(**kw):
        return dict({"progress_pct": 64.0}, **kw)
    sp = view._shown_progress
    check("in flight: the measured percentage", sp(row(), False, False, now) == 64.0)
    landed = (now - timedelta(minutes=10)).isoformat()
    check("10 min after touchdown: 100",
          sp(row(on_observed=landed, closed_at=landed), True, False, now) == 100.0)
    old = (now - timedelta(minutes=45)).isoformat()
    check("45 min after touchdown: no bar",
          sp(row(on_observed=old, closed_at=now.isoformat()), True, False, now) is None)
    check("the hold runs from TOUCHDOWN, not from a late close",
          sp(row(on_actual_api=old, closed_at=(now - timedelta(minutes=1)).isoformat()),
             True, False, now) is None)
    check("a cancelled leg never gets a bar",
          sp(row(closed_at=landed), True, True, now) is None)


def test_tail_number():
    print("\nReport #11: the tail number")
    v = read("templates/viewer.html")
    check("the flight details carry an Aircraft row", 'id="row-aircraft"' in v)
    check("...it is not inside the live ADS-B box any more",
          '<div class="aircraft-line">' not in v and "'<div class=\"aircraft-line\">'" not in v)
    check("...switching flights can clear it, a poll cannot",
          "__legSwitch: true" in v and "data.__legSwitch" in v)
    check("ADS-B text is escaped before it reaches the page",
          "escHtml(data.live.icao24)" in v)


def test_admin_inbox_and_share(client_pilot):
    print("\nReports #9 and #10: admin page")
    from app import feedback
    feedback.submit(user_id=1, reporter="x", is_viewer=False, kind="bug",
                    message="hello", page="/", app_version="t", user_agent="t")
    r = client_pilot.get("/admin")
    check("the admin page renders", r.status_code == 200, str(r.status_code))
    check("THE BUG: no escaped span in the inbox header",
          "&lt;span" not in r.text)
    check("...the count is real markup", 'class="grow-alert"' in r.text)
    # Moved to Settings in 1.33.1 so EVERYONE can share it (owner's call).
    check("the share button is no longer admin-only", 'id="share-register"' not in r.text)
    r = client_pilot.get("/settings")
    check("a pilot sees Invite a pilot on Settings", 'id="share-register"' in r.text)
    viewer = TestClient(m.app)
    code = add_share_code(1, "v")
    viewer.post("/login/code", data={"code": code})
    r = viewer.get("/settings")
    check("...and so does a family viewer",
          r.status_code == 200 and 'id="share-register"' in r.text, str(r.status_code))
    top = r.text.split('class="topbar"', 1)[1].split("<h1>", 1)[0]
    check("it sits in the top bar, beside log out (1.33.2)",
          'id="share-register"' in top and "logout-btn" in top)


def test_tracker_ui():
    print("\nTracker: slide, gestures, the bar above the tabs")
    v = read("templates/viewer.html")
    check("the panel slide is PLAYED, not transitioned (1.33.3)",
          "panel.animate([{ transform: from }, { transform: to }]" in v)
    check("...and focus waits for the slide to finish",
          "}, SLIDE_MS + 20);" in v)
    check("the sheet sits lower: 48vh (owner, 1.33.3)", "--sheet-peek: 48vh" in v)
    check("a drag that starts on the list locks the map",
          "map.dragging.disable()" in v and "OWNERS = '#sheet, #detail-panel, .tabbar'" in v)
    sheet = v.split("    .sheet {", 1)[1].split("}", 1)[0]
    check("THE WHITE BAR: the sheet reserves no solid band at the bottom",
          "padding-bottom" not in sheet, sheet)
    body = v.split("    .sheet-body {", 1)[1].split("}", 1)[0]
    check("...the room for the tab pill is scroll room inside the list",
          "64px" in body and "env(safe-area-inset-bottom" in body)


def main():
    init_db()
    uid = create_user("pilot133", "password133")
    client_pilot = TestClient(m.app)
    r = client_pilot.post("/login/pilot", data={"username": "pilot133",
                                                "password": "password133"},
                          follow_redirects=False)
    check("setup: the pilot logs in", r.status_code == 303, str(r.status_code))
    anon = TestClient(m.app)

    test_retention(uid)
    test_radar(client_pilot, anon)
    test_client_ip()
    test_share_codes(uid)
    test_sessions()
    test_usernames()
    test_headers(client_pilot)
    test_api_401(anon)
    test_hardening_misc()
    test_progress_hold()
    test_tail_number()
    test_admin_inbox_and_share(client_pilot)
    test_tracker_ui()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
