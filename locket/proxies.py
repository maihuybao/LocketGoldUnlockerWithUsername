"""Proxy pool for outgoing Locket API calls.

Round-robin picks an enabled proxy from the `proxies` table and returns it
in the dict shape `requests` expects. The pool is shared by Auth (Firebase
verifyPassword) and LocketAPI (Locket + RevenueCat) so all upstream calls
flow through the same egress IP per attempt.

Master switch lives in `site_settings` (key=`proxy_master`) → admin can
disable the whole pool with one click; the helpers fall through to direct
connection in that case.

Shorthand parser accepts both:
    http://user:pass@host:port
    user:pass:host:port
    host:port
"""

import threading
import time
from itertools import cycle

from . import db


_lock = threading.Lock()
_iter = None  # itertools.cycle, rebuilt on add/remove/enable


def _build_iter():
    global _iter
    rows = list_enabled()
    _iter = cycle(rows) if rows else None


def _normalize_url(raw):
    s = (raw or "").strip()
    if not s:
        return None
    if "://" in s:
        return s
    parts = s.split(":")
    if len(parts) == 2:
        # host:port
        return f"http://{parts[0]}:{parts[1]}"
    if len(parts) >= 4:
        # user:pass:host:port  (extras after port are appended to password)
        host, port = parts[-2], parts[-1]
        user = parts[0]
        pwd = ":".join(parts[1:-2])
        return f"http://{user}:{pwd}@{host}:{port}"
    return None


def parse_lines(text):
    """Return [normalized_url, ...] from a multi-line paste, skipping blanks
    and comments."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url = _normalize_url(line)
        if url:
            out.append(url)
    return out


def list_all():
    rows = db.get_conn().execute(
        "SELECT id, url, enabled, added_at, last_ok_at, last_err_at, last_err "
        "FROM proxies ORDER BY id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_enabled():
    rows = db.get_conn().execute(
        "SELECT id, url FROM proxies WHERE enabled=1 ORDER BY id ASC"
    ).fetchall()
    return [{"id": r["id"], "url": r["url"]} for r in rows]


def add(raw_url):
    url = _normalize_url(raw_url)
    if not url:
        raise ValueError("Invalid proxy URL")
    db.get_conn().execute(
        "INSERT OR IGNORE INTO proxies (url, enabled, added_at) VALUES (?, 1, ?)",
        (url, time.time()),
    )
    with _lock:
        _build_iter()


def add_many(raw_text):
    urls = parse_lines(raw_text)
    inserted = 0
    conn = db.get_conn()
    for url in urls:
        cur = conn.execute(
            "INSERT OR IGNORE INTO proxies (url, enabled, added_at) VALUES (?, 1, ?)",
            (url, time.time()),
        )
        inserted += cur.rowcount or 0
    if inserted:
        with _lock:
            _build_iter()
    return inserted


def remove(proxy_id):
    db.get_conn().execute("DELETE FROM proxies WHERE id=?", (int(proxy_id),))
    with _lock:
        _build_iter()


def set_enabled(proxy_id, enabled):
    db.get_conn().execute(
        "UPDATE proxies SET enabled=? WHERE id=?",
        (1 if enabled else 0, int(proxy_id)),
    )
    with _lock:
        _build_iter()


def mark_ok(proxy_id):
    db.get_conn().execute(
        "UPDATE proxies SET last_ok_at=?, last_err=NULL WHERE id=?",
        (time.time(), int(proxy_id)),
    )


def mark_err(proxy_id, err):
    db.get_conn().execute(
        "UPDATE proxies SET last_err_at=?, last_err=? WHERE id=?",
        (time.time(), str(err)[:240], int(proxy_id)),
    )


# ---- master switch ----

_MASTER_KEY = "proxy_master"


def is_master_on():
    row = db.get_conn().execute(
        "SELECT value FROM site_settings WHERE key=?", (_MASTER_KEY,)
    ).fetchone()
    if row is None:
        return False
    import json as _json
    try:
        return bool(_json.loads(row["value"]).get("enabled"))
    except (ValueError, TypeError):
        return False


def set_master(enabled):
    import json as _json
    db.get_conn().execute(
        "INSERT INTO site_settings (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (_MASTER_KEY, _json.dumps({"enabled": bool(enabled)}), time.time()),
    )


# ---- runtime accessors used by HTTP layer ----

def next_proxy():
    """Return (id, requests_proxies_dict) or (None, None) when disabled/empty."""
    if not is_master_on():
        return None, None
    with _lock:
        global _iter
        if _iter is None:
            _build_iter()
        if _iter is None:
            return None, None
        try:
            entry = next(_iter)
        except StopIteration:
            return None, None
    url = entry["url"]
    return entry["id"], {"http": url, "https": url}
