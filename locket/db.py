"""SQLite-backed persistence for accounts, tokens, queue state, and recent log.

Single source of truth for state that previously lived in three JSON files
(accounts.json, tokens.json, queue_state.json). Connections are thread-local:
each worker thread and Flask request handler gets its own sqlite3 connection,
configured for WAL mode with a 5-second busy timeout. WAL allows many readers
to run alongside one writer; busy_timeout absorbs short write contention.

`init()` is idempotent — call it once on app startup. It creates schema if
absent and one-shot imports legacy JSON files into the DB, renaming them to
*.bak so subsequent restarts do not double-import.
"""

import json
import os
import sqlite3
import threading
import time
import uuid

DB_PATH = os.environ.get("LOCKET_DB", "locket.db")

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def get_conn():
    """Return this thread's sqlite3 connection, creating it on first call."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    slot_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    added_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_accounts_added_at ON accounts(added_at);

CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_requests (
    client_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('waiting','processing','completed','error')),
    result TEXT,
    error TEXT,
    added_at REAL NOT NULL,
    started_at REAL,
    completed_at REAL,
    slot_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status_added ON queue_requests(status, added_at);
CREATE INDEX IF NOT EXISTS idx_queue_completed_at ON queue_requests(completed_at);

CREATE TABLE IF NOT EXISTS processing_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    duration REAL NOT NULL,
    completed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT,
    username TEXT,
    slot_id TEXT,
    status TEXT,
    error TEXT,
    duration REAL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_recent_log_id ON recent_log(id);

CREATE TABLE IF NOT EXISTS site_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS proxies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at REAL NOT NULL,
    last_ok_at REAL,
    last_err_at REAL,
    last_err TEXT
);

CREATE TABLE IF NOT EXISTS mobileconfig_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL CHECK (action IN ('upload','delete')),
    filename TEXT,
    size INTEGER,
    signed INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mc_history_created ON mobileconfig_history(created_at DESC);
"""


def init():
    """Create tables, run one-shot migrations from JSON. Safe to call repeatedly."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = get_conn()
        conn.executescript(SCHEMA)

        # On startup, any rows still marked "processing" are leftovers from a
        # prior run that crashed or restarted. Reset them to waiting so a worker
        # can pick them up again.
        conn.execute(
            "UPDATE queue_requests SET status='waiting', started_at=NULL, slot_id=NULL "
            "WHERE status='processing'"
        )

        _migrate_legacy_files(conn)
        _initialized = True
        print(f"db: initialized at {DB_PATH}")


def _migrate_legacy_files(conn):
    _migrate_accounts(conn)
    _migrate_tokens(conn)
    _migrate_queue_state(conn)


def _table_empty(conn, table):
    row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
    return row is None


def _migrate_accounts(conn):
    path = "accounts.json"
    if not os.path.exists(path):
        return
    if not _table_empty(conn, "accounts"):
        # Already populated — leave the JSON alone, user can clean up manually.
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"db: skipping accounts.json migration: {e}")
        return
    if not isinstance(data, list):
        return
    now = time.time()
    inserted = 0
    for entry in data:
        if not isinstance(entry, dict):
            continue
        email = entry.get("email")
        password = entry.get("password")
        if not email or not password:
            continue
        slot_id = entry.get("id") or str(uuid.uuid4())
        try:
            conn.execute(
                "INSERT INTO accounts (slot_id, email, password, added_at) VALUES (?,?,?,?)",
                (slot_id, email, password, now),
            )
            inserted += 1
        except sqlite3.IntegrityError as e:
            print(f"db: skip duplicate account {email}: {e}")
    if inserted:
        os.rename(path, path + ".bak")
        print(f"db: migrated {inserted} account(s) from {path} (renamed to {path}.bak)")


def _migrate_tokens(conn):
    path = "tokens.json"
    if not os.path.exists(path):
        return
    if not _table_empty(conn, "tokens"):
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"db: skipping tokens.json migration: {e}")
        return
    if not isinstance(data, list):
        return
    now = time.time()
    inserted = 0
    for payload in data:
        if not isinstance(payload, dict):
            continue
        conn.execute(
            "INSERT INTO tokens (payload, added_at) VALUES (?,?)",
            (json.dumps(payload), now),
        )
        inserted += 1
    if inserted:
        os.rename(path, path + ".bak")
        print(f"db: migrated {inserted} token payload(s) from {path}")


def _migrate_queue_state(conn):
    path = "queue_state.json"
    if not os.path.exists(path):
        return
    if not _table_empty(conn, "queue_requests") or not _table_empty(conn, "processing_times"):
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"db: skipping queue_state.json migration: {e}")
        return

    requests = data.get("client_requests", {}) if isinstance(data, dict) else {}
    times = data.get("processing_times", []) if isinstance(data, dict) else []

    inserted_q = 0
    for client_id, r in requests.items():
        if not isinstance(r, dict):
            continue
        # Reset any "processing" status to "waiting" — we just crashed.
        status = r.get("status")
        if status == "processing":
            status = "waiting"
        if status not in ("waiting", "completed", "error"):
            continue
        conn.execute(
            """INSERT INTO queue_requests
               (client_id, username, status, result, error,
                added_at, started_at, completed_at, slot_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                client_id,
                r.get("username", ""),
                status,
                json.dumps(r["result"]) if r.get("result") else None,
                r.get("error"),
                _iso_to_epoch(r.get("added_at")) or time.time(),
                _iso_to_epoch(r.get("started_at")) if status != "waiting" else None,
                _iso_to_epoch(r.get("completed_at")),
                None,
            ),
        )
        inserted_q += 1

    inserted_t = 0
    now = time.time()
    for d in times:
        if isinstance(d, (int, float)):
            conn.execute(
                "INSERT INTO processing_times (duration, completed_at) VALUES (?,?)",
                (float(d), now),
            )
            inserted_t += 1

    if inserted_q or inserted_t:
        os.rename(path, path + ".bak")
        print(
            f"db: migrated {inserted_q} queue request(s) and "
            f"{inserted_t} processing time(s) from {path}"
        )


def _iso_to_epoch(s):
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None
