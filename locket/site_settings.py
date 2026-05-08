"""Site-wide toggleable settings (popup notice + maintenance mode).

Two JSON blobs stored in the `site_settings` key/value table:

- `popup`      → {enabled, title, message, icon, button_text}
- `maintenance`→ {enabled, end_at (ISO+TZ), title, message,
                  contacts:[{role,name,url}], allow_admin}

Reads are cheap and uncached — SQLite + tiny rows. Public endpoint hits
this on every page load.
"""

import json
import threading
import time

from . import db


POPUP_KEY = "popup"
MAINT_KEY = "maintenance"
THEME_KEY = "theme"
LAYOUT_KEY = "layout"

THEMES = ("gold", "aurora", "sunset", "mono")
LAYOUTS = ("stacked", "split", "spotlight")

_DEFAULTS = {
    POPUP_KEY: {
        "enabled": False,
        "title": "Thông báo",
        "message": "",
        "icon": "info",
    },
    THEME_KEY: {"name": "gold"},
    LAYOUT_KEY: {"name": "stacked"},
    MAINT_KEY: {
        "enabled": False,
        "end_at": "",
        "start_at": "",
        "title": "Bảo Trì Máy Chủ",
        "message": (
            "Hệ thống đang được nâng cấp để mang đến trải nghiệm tốt hơn. "
            "Vui lòng quay lại sau khi bảo trì hoàn tất."
        ),
        "notice": (
            "Máy chủ đang trong quá trình bảo trì định kỳ, không phải gặp sự "
            "cố hay sập máy chủ. Toàn bộ dữ liệu của bạn vẫn được bảo toàn an "
            "toàn. Hệ thống sẽ hoạt động bình thường trở lại sau khi bảo trì "
            "hoàn tất. Cảm ơn bạn đã kiên nhẫn chờ đợi!"
        ),
        "contacts": [
            {"role": "Founder", "name": "nguyenthanhson.dev",
             "url": "https://nguyenthanhson.dev"},
            {"role": "Founder", "name": "maihuybao.dev",
             "url": "https://maihuybao.dev"},
        ],
        "allow_admin": True,
    },
}

_lock = threading.Lock()


def _read(key):
    row = db.get_conn().execute(
        "SELECT value FROM site_settings WHERE key=?", (key,)
    ).fetchone()
    if row is None:
        return dict(_DEFAULTS[key])
    try:
        merged = dict(_DEFAULTS[key])
        merged.update(json.loads(row["value"]))
        return merged
    except (ValueError, TypeError):
        return dict(_DEFAULTS[key])


def _write(key, value):
    payload = json.dumps(value)
    db.get_conn().execute(
        "INSERT INTO site_settings (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, payload, time.time()),
    )


def get_popup():
    with _lock:
        return _read(POPUP_KEY)


def set_popup(value):
    cur = get_popup()
    cur.update({k: v for k, v in (value or {}).items() if k in _DEFAULTS[POPUP_KEY]})
    cur["enabled"] = bool(cur.get("enabled"))
    if cur["icon"] not in ("info", "success", "warning", "error", "question"):
        cur["icon"] = "info"
    with _lock:
        _write(POPUP_KEY, cur)
    return cur


def get_maintenance():
    with _lock:
        return _read(MAINT_KEY)


def set_maintenance(value):
    cur = get_maintenance()
    allowed = set(_DEFAULTS[MAINT_KEY].keys())
    for k, v in (value or {}).items():
        if k in allowed:
            cur[k] = v
    cur["enabled"] = bool(cur.get("enabled"))
    cur["allow_admin"] = bool(cur.get("allow_admin", True))
    if not isinstance(cur.get("contacts"), list):
        cur["contacts"] = list(_DEFAULTS[MAINT_KEY]["contacts"])
    with _lock:
        _write(MAINT_KEY, cur)
    return cur


def get_theme():
    with _lock:
        v = _read(THEME_KEY)
    name = v.get("name") if isinstance(v, dict) else None
    if name not in THEMES:
        name = "gold"
    return {"name": name}


def set_theme(value):
    name = (value or {}).get("name")
    if name not in THEMES:
        raise ValueError(f"Unknown theme '{name}'. Allowed: {', '.join(THEMES)}")
    with _lock:
        _write(THEME_KEY, {"name": name})
    return {"name": name}


def get_layout():
    with _lock:
        v = _read(LAYOUT_KEY)
    name = v.get("name") if isinstance(v, dict) else None
    if name not in LAYOUTS:
        name = "stacked"
    return {"name": name}


def set_layout(value):
    name = (value or {}).get("name")
    if name not in LAYOUTS:
        raise ValueError(f"Unknown layout '{name}'. Allowed: {', '.join(LAYOUTS)}")
    with _lock:
        _write(LAYOUT_KEY, {"name": name})
    return {"name": name}


def public_view():
    """Trimmed payload safe to expose to anonymous clients."""
    return {
        "popup": get_popup(),
        "maintenance": get_maintenance(),
        "theme": get_theme(),
        "layout": get_layout(),
    }
