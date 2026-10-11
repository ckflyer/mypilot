"""Lightweight in-memory rate limiting for auth endpoints (login, register,
password reset, share codes, feedback). Not distributed — fine for a
single-process deployment, resets on restart.

TWO LAYERS (1.33.0):

  * PER VISITOR — the original limiter. Stops one device hammering a form.
  * INSTALL-WIDE, failures only — `note_failure` / `global_failures_ok`.
    Exists for the share codes. A per-visitor limit does nothing against
    someone spreading guesses across many addresses, and a five-digit code
    is only 100,000 possibilities. Counting WRONG answers across everyone
    caps how fast the whole internet together can guess, while a family
    member who types the right code is never counted at all.
"""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import Request

_buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_failures: Dict[str, Deque[float]] = defaultdict(deque)
_lock = threading.Lock()

# Set PT_TRUST_PROXY_HEADERS=0 to ignore forwarding headers entirely, or =1
# to trust them from anywhere (only if every request really comes through a
# proxy you control). Default "auto": trust them only from a private address.
_TRUST = os.environ.get("PT_TRUST_PROXY_HEADERS", "auto").strip().lower()


def _peer_is_private(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # "testclient" and similar non-addresses: local by definition.
        return True
    return ip.is_private or ip.is_loopback


def get_client_ip(request: Request) -> str:
    """Who is this, for rate-limiting purposes?

    WHY THE HEADERS ARE NO LONGER TRUSTED BLINDLY (1.33.0). Cloudflare puts
    the real visitor's address in CF-Connecting-IP, and without reading it
    every request would appear to come from the tunnel and share one limit.
    But anybody can SEND that header. Read unconditionally, a script could
    claim a new address on every request and never be limited at all —
    which turned "15 tries per 10 minutes" into unlimited guesses at a
    five-digit share code.

    So the header is believed only when the connection itself comes from a
    private address: the cloudflared container or a reverse proxy on the
    same box or LAN. A connection arriving straight from the internet is
    judged by its own address, whatever it claims.
    """
    peer = request.client.host if request.client else "unknown"
    trust = (_TRUST in ("1", "true", "yes")) or (
        _TRUST not in ("0", "false", "no") and _peer_is_private(peer))
    if trust:
        cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf_ip:
            return cf_ip
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return peer


def check_rate_limit(request: Request, bucket: str, max_attempts: int, window_seconds: int) -> bool:
    """Returns True if this request should proceed, False if the caller
    has exceeded max_attempts within window_seconds and should be refused."""
    ip = get_client_ip(request)
    key = (bucket, ip)
    now = time.time()
    with _lock:
        dq = _buckets[key]
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        if len(dq) >= max_attempts:
            return False
        dq.append(now)
        # Keep memory bounded: buckets for visitors long gone are dropped.
        if len(_buckets) > 5000:
            for k in [k for k, v in _buckets.items() if not v]:
                _buckets.pop(k, None)
        return True


def note_failure(bucket: str) -> None:
    """Record one WRONG answer, install-wide."""
    with _lock:
        _failures[bucket].append(time.time())


def global_failures_ok(bucket: str, max_failures: int, window_seconds: int) -> bool:
    """False once everyone together has failed too often in the window."""
    now = time.time()
    with _lock:
        dq = _failures[bucket]
        while dq and now - dq[0] > window_seconds:
            dq.popleft()
        return len(dq) < max_failures


def reset_all() -> None:
    """For tests."""
    with _lock:
        _buckets.clear()
        _failures.clear()
