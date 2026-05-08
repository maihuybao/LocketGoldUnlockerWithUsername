"""Pool of Locket accounts keyed by stable slot_id (uuid).

Persistence lives in SQLite (`db.accounts` table). The rotator caches Auth +
LocketAPI instances in memory keyed by slot_id — those are runtime-only and
must be rebuilt after a restart.

Workers bind to a slot_id for life and call `get(slot_id)` to obtain that
slot's LocketAPI; `refresh(slot_id)` re-runs login after a 401. Mutators
(`add`, `remove`) write to the DB and update the in-memory cache atomically
under `self._lock`.

Falls back to a single account derived from EMAIL/PASSWORD env vars when the
DB is empty AND no legacy accounts.json was migrated.
"""

import os
import threading
import time
import uuid

from . import db
from .locket_auth import Auth
from .locket_api import LocketAPI


class _Slot:
    __slots__ = ("email", "password", "auth", "api", "token_at")

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.auth = Auth(email, password)
        self.api = None
        self.token_at = 0.0  # epoch when current token was minted


class AccountRotator:
    def __init__(self):
        db.init()
        self._lock = threading.Lock()
        self._slots = {}  # slot_id -> _Slot
        self._order = []  # slot_id list, sorted by added_at

        rows = list(db.get_conn().execute(
            "SELECT slot_id, email, password FROM accounts ORDER BY added_at ASC, slot_id ASC"
        ))

        if not rows:
            self._seed_from_env()
            rows = list(db.get_conn().execute(
                "SELECT slot_id, email, password FROM accounts ORDER BY added_at ASC, slot_id ASC"
            ))

        for r in rows:
            self._slots[r["slot_id"]] = _Slot(r["email"], r["password"])
            self._order.append(r["slot_id"])

        if not self._slots:
            print(
                "AccountRotator: 0 accounts configured. App will start but restore "
                "endpoints will return 503 until an account is added via /admin."
            )
            return

        # Eagerly initialize the first slot so startup fails loudly on bad creds.
        try:
            self._init_slot_locked(self._order[0])
        except Exception as e:
            print(f"AccountRotator: warning, first slot init failed: {e}")

        print(f"AccountRotator: loaded {len(self._slots)} account(s)")

        # Background thread: proactively refresh tokens before they hit the
        # 1h Firebase TTL. Daemon thread, never joined.
        self._stop_refresher = threading.Event()
        self._refresher = threading.Thread(
            target=self._refresher_loop,
            daemon=True,
            name="rotator-token-refresher",
        )
        self._refresher.start()

    def ensure_fresh(self, slot_id):
        """Return the slot's LocketAPI, refreshing the token first if it's
        older than TOKEN_TTL_SEC. Used by sync endpoints right before they
        hit getUserByUsername so the call always carries a fresh token."""
        with self._lock:
            if slot_id not in self._slots:
                raise KeyError(f"Unknown slot_id: {slot_id}")
            slot = self._slots[slot_id]
            stale = (time.time() - slot.token_at) >= self.TOKEN_TTL_SEC or slot.api is None
        if stale:
            print(f"AccountRotator: ensure_fresh refreshing {slot_id[:8]}")
            api = self.refresh(slot_id)
            if api is not None:
                return api
        with self._lock:
            return self._slots[slot_id].api

    def _refresher_loop(self):
        """Wake every minute; refresh any slot whose token is older than TTL."""
        while not self._stop_refresher.wait(self.REFRESHER_INTERVAL_SEC):
            try:
                slot_ids = self.list_ids()
                now = time.time()
                for sid in slot_ids:
                    try:
                        with self._lock:
                            if sid not in self._slots:
                                continue
                            age = now - self._slots[sid].token_at
                            stale = age >= self.TOKEN_TTL_SEC and self._slots[sid].api is not None
                        if stale:
                            print(f"AccountRotator: token age {int(age)}s on {sid[:8]} — refreshing")
                            self.refresh(sid)
                    except Exception as e:
                        print(f"AccountRotator: refresher error on {sid}: {e}")
            except Exception as e:
                print(f"AccountRotator: refresher loop error: {e}")

    def _seed_from_env(self):
        email = os.getenv("EMAIL")
        password = os.getenv("PASSWORD")
        if not email or not password:
            return
        slot_id = str(uuid.uuid4())
        db.get_conn().execute(
            "INSERT INTO accounts (slot_id, email, password, added_at) VALUES (?,?,?,?)",
            (slot_id, email, password, time.time()),
        )
        print(f"AccountRotator: seeded one account from EMAIL env var")

    # Firebase id tokens technically last ~1 hour, but Locket's edge starts
    # 502-ing on tokens that are even slightly stale during their incidents.
    # Keep tokens fresh: anything older than 5 min is refreshed proactively,
    # and the background loop ticks every 60s.
    TOKEN_TTL_SEC = 5 * 60
    REFRESHER_INTERVAL_SEC = 60

    def _init_slot_locked(self, slot_id):
        """Caller must hold self._lock."""
        slot = self._slots[slot_id]
        token = slot.auth.get_token()
        slot.api = LocketAPI(token)
        slot.token_at = time.time()

    # --- Read API ---

    def size(self):
        with self._lock:
            return len(self._slots)

    def list_ids(self):
        with self._lock:
            return list(self._order)

    def list_accounts(self):
        """Admin view: [{id, email}] in insertion order. Password never exposed."""
        with self._lock:
            return [{"id": sid, "email": self._slots[sid].email} for sid in self._order]

    def has(self, slot_id):
        with self._lock:
            return slot_id in self._slots

    def email(self, slot_id):
        with self._lock:
            return self._slots[slot_id].email

    def get(self, slot_id):
        """Return the LocketAPI bound to one slot, lazy-initializing on first use."""
        with self._lock:
            if slot_id not in self._slots:
                raise KeyError(f"Unknown slot_id: {slot_id}")
            slot = self._slots[slot_id]
            if slot.api is None:
                self._init_slot_locked(slot_id)
            return slot.api

    # --- Mutators ---

    def add(self, email, password):
        """Append a new account, persist, return the new slot_id."""
        slot_id = str(uuid.uuid4())
        with self._lock:
            db.get_conn().execute(
                "INSERT INTO accounts (slot_id, email, password, added_at) VALUES (?,?,?,?)",
                (slot_id, email, password, time.time()),
            )
            self._slots[slot_id] = _Slot(email, password)
            self._order.append(slot_id)
        print(f"AccountRotator: added slot {slot_id} ({email})")
        return slot_id

    def remove(self, slot_id):
        """Remove an account, persist. Returns True if removed."""
        with self._lock:
            if slot_id not in self._slots:
                return False
            email = self._slots[slot_id].email
            db.get_conn().execute("DELETE FROM accounts WHERE slot_id = ?", (slot_id,))
            del self._slots[slot_id]
            self._order.remove(slot_id)
        print(f"AccountRotator: removed slot {slot_id} ({email})")
        return True

    def refresh(self, slot_id):
        """Force a fresh login for one slot (after a 401)."""
        with self._lock:
            if slot_id not in self._slots:
                return None
            slot = self._slots[slot_id]
            print(f"AccountRotator: refreshing token for slot {slot_id} ({slot.email})")
            try:
                new_token = slot.auth.create_token()
                slot.api = LocketAPI(new_token)
                slot.token_at = time.time()
                return slot.api
            except Exception as e:
                print(f"AccountRotator: refresh failed for slot {slot_id}: {e}")
                return None

    @staticmethod
    def test_login(email, password):
        """Validate creds without touching the pool. Returns (ok, error)."""
        try:
            Auth(email, password).create_token()
            return True, None
        except Exception as e:
            return False, str(e)
