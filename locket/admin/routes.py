import json
import os
import time

from flask import current_app, jsonify, redirect, render_template, request, session

import requests

from .. import db
from .. import proxies as proxy_pool
from .. import site_settings
from ..rotator import AccountRotator
from ..tokens import tokens_store
from . import bp
from .auth import admin_required, check_credentials, is_admin_logged_in


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_admin_logged_in():
            return redirect("/admin/")
        return render_template("admin_login.html", error=None)

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    if not check_credentials(username, password):
        return render_template("admin_login.html", error="Invalid username or password"), 401
    session.clear()
    session["admin"] = True
    return redirect("/admin/")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/admin/login")


@bp.route("/")
@admin_required
def dashboard():
    return render_template("admin.html")


# ---- accounts ----


@bp.route("/api/accounts", methods=["GET"])
@admin_required
def accounts_list():
    rotator = current_app.rotator
    if rotator is None:
        return jsonify({"success": False, "error": "rotator not initialized"}), 500
    return jsonify({"success": True, "accounts": rotator.list_accounts()})


@bp.route("/api/accounts", methods=["POST"])
@admin_required
def accounts_add():
    rotator = current_app.rotator
    if rotator is None:
        return jsonify({"success": False, "error": "rotator not initialized"}), 500

    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify({"success": False, "error": "email and password are required"}), 400

    ok, err = rotator.test_login(email, password)
    if not ok:
        return jsonify({"success": False, "error": f"Login failed: {err}"}), 400

    slot_id = rotator.add(email, password)
    current_app.queue_manager.add_worker(slot_id)
    return jsonify({"success": True, "id": slot_id, "email": email})


@bp.route("/api/accounts/<slot_id>", methods=["DELETE"])
@admin_required
def accounts_remove(slot_id):
    rotator = current_app.rotator
    if rotator is None:
        return jsonify({"success": False, "error": "rotator not initialized"}), 500
    if not rotator.has(slot_id):
        return jsonify({"success": False, "error": "not found"}), 404
    if rotator.size() <= 1:
        return jsonify({"success": False, "error": "must keep at least 1 account"}), 400
    current_app.queue_manager.remove_worker(slot_id)
    rotator.remove(slot_id)
    return jsonify({"success": True})


@bp.route("/api/accounts/test", methods=["POST"])
@admin_required
def accounts_test():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        return jsonify({"success": False, "error": "email and password are required"}), 400
    ok, err = AccountRotator.test_login(email, password)
    return jsonify({"success": ok, "error": err})


# ---- tokens ----


@bp.route("/api/tokens", methods=["GET"])
@admin_required
def tokens_list():
    return jsonify({"success": True, "tokens": tokens_store.list()})


@bp.route("/api/tokens", methods=["POST"])
@admin_required
def tokens_add():
    body = request.get_json(silent=True) or {}
    payload = body.get("payload")
    if payload is None:
        # Allow raw JSON in a "raw" string field (UI textarea convenience).
        raw = body.get("raw")
        if not raw:
            return jsonify({"success": False, "error": "missing payload"}), 400
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            return jsonify({"success": False, "error": f"Invalid JSON: {e}"}), 400
    try:
        tokens_store.add(payload)
    except (ValueError, OSError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True})


@bp.route("/api/tokens/<int:index>", methods=["DELETE"])
@admin_required
def tokens_remove(index):
    try:
        tokens_store.remove(index)
    except IndexError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    return jsonify({"success": True})


# ---- queue ----


# ---- popup + maintenance ----


@bp.route("/api/popup", methods=["GET"])
@admin_required
def popup_get():
    return jsonify({"success": True, "popup": site_settings.get_popup()})


@bp.route("/api/popup", methods=["PUT"])
@admin_required
def popup_set():
    body = request.get_json(silent=True) or {}
    saved = site_settings.set_popup(body)
    return jsonify({"success": True, "popup": saved})


@bp.route("/api/maintenance", methods=["GET"])
@admin_required
def maintenance_get():
    return jsonify({"success": True, "maintenance": site_settings.get_maintenance()})


@bp.route("/api/maintenance", methods=["PUT"])
@admin_required
def maintenance_set():
    body = request.get_json(silent=True) or {}
    saved = site_settings.set_maintenance(body)
    return jsonify({"success": True, "maintenance": saved})


@bp.route("/api/theme", methods=["GET"])
@admin_required
def theme_get():
    return jsonify({
        "success": True,
        "theme": site_settings.get_theme(),
        "available": list(site_settings.THEMES),
    })


@bp.route("/api/theme", methods=["PUT"])
@admin_required
def theme_set():
    body = request.get_json(silent=True) or {}
    try:
        saved = site_settings.set_theme(body)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "theme": saved})


@bp.route("/api/layout", methods=["GET"])
@admin_required
def layout_get():
    return jsonify({
        "success": True,
        "layout": site_settings.get_layout(),
        "available": list(site_settings.LAYOUTS),
    })


@bp.route("/api/layout", methods=["PUT"])
@admin_required
def layout_set():
    body = request.get_json(silent=True) or {}
    try:
        saved = site_settings.set_layout(body)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "layout": saved})


# ---- proxies ----


def _redact(url):
    # Hide password in user:pass@host
    try:
        if "://" not in url:
            return url
        scheme, rest = url.split("://", 1)
        if "@" not in rest:
            return url
        creds, host = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{host}"
        return f"{scheme}://{creds}@{host}"
    except Exception:
        return url


@bp.route("/api/proxies", methods=["GET"])
@admin_required
def proxies_list():
    items = proxy_pool.list_all()
    for it in items:
        it["url_redacted"] = _redact(it["url"])
    return jsonify({
        "success": True,
        "master_enabled": proxy_pool.is_master_on(),
        "items": items,
    })


@bp.route("/api/proxies", methods=["POST"])
@admin_required
def proxies_add():
    body = request.get_json(silent=True) or {}
    raw = body.get("raw") or body.get("url") or ""
    try:
        added = proxy_pool.add_many(raw)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "added": added})


@bp.route("/api/proxies/<int:proxy_id>", methods=["PATCH"])
@admin_required
def proxies_patch(proxy_id):
    body = request.get_json(silent=True) or {}
    if "enabled" in body:
        proxy_pool.set_enabled(proxy_id, bool(body["enabled"]))
    return jsonify({"success": True})


@bp.route("/api/proxies/<int:proxy_id>", methods=["DELETE"])
@admin_required
def proxies_remove(proxy_id):
    proxy_pool.remove(proxy_id)
    return jsonify({"success": True})


@bp.route("/api/proxies/<int:proxy_id>/test", methods=["POST"])
@admin_required
def proxies_test_one(proxy_id):
    rows = [r for r in proxy_pool.list_all() if r["id"] == proxy_id]
    if not rows:
        return jsonify({"success": False, "error": "not found"}), 404
    url = rows[0]["url"]
    try:
        resp = requests.post(
            "https://api.locketcamera.com/getUserByUsername",
            json={"data": {"username": "locket"}},
            proxies={"http": url, "https": url},
            timeout=15,
        )
        ok = resp.status_code < 500
        if ok:
            proxy_pool.mark_ok(proxy_id)
        else:
            proxy_pool.mark_err(proxy_id, f"HTTP {resp.status_code}")
        return jsonify({"success": ok, "status": resp.status_code})
    except Exception as e:
        proxy_pool.mark_err(proxy_id, str(e)[:200])
        return jsonify({"success": False, "error": str(e)}), 502


@bp.route("/api/proxies/master", methods=["PUT"])
@admin_required
def proxies_master():
    body = request.get_json(silent=True) or {}
    proxy_pool.set_master(bool(body.get("enabled")))
    return jsonify({"success": True, "master_enabled": proxy_pool.is_master_on()})


# ---- mobileconfig upload ----


MAX_MOBILECONFIG_BYTES = 5 * 1024 * 1024  # 5 MB ceiling
MOBILECONFIG_HISTORY_LIMIT = 20


def _mobileconfig_path():
    static_dir = os.path.join(current_app.root_path, "static")
    return os.path.join(static_dir, "locket.mobileconfig")


def _record_mobileconfig_history(action, filename=None, size=None, signed=None):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO mobileconfig_history (action, filename, size, signed, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            action,
            filename,
            int(size) if size is not None else None,
            1 if signed else 0,
            time.time(),
        ),
    )
    # Trim to most-recent N rows so the log doesn't grow unbounded.
    conn.execute(
        "DELETE FROM mobileconfig_history WHERE id NOT IN ("
        "SELECT id FROM mobileconfig_history ORDER BY id DESC LIMIT ?)",
        (MOBILECONFIG_HISTORY_LIMIT,),
    )


def _looks_like_mobileconfig(blob):
    """Accept either a plain XML plist or a CMS/PKCS7-signed .mobileconfig.
    Plain plists start with "<?xml". Signed ones are DER bags whose payload
    contains '<plist' somewhere in the first 4KB."""
    if not blob:
        return False
    head = blob[:4096]
    if head.lstrip().startswith(b"<?xml") or b"<plist" in head:
        return True
    # PKCS7 / signed mobileconfig: DER seq starts with 0x30 0x82 (or 0x30 0x80)
    if blob[:1] == b"\x30":
        return b"<plist" in blob[:8192] or b"-//Apple//DTD PLIST" in blob[:8192]
    return False


@bp.route("/api/mobileconfig", methods=["GET"])
@admin_required
def mobileconfig_info():
    path = _mobileconfig_path()
    if not os.path.exists(path):
        return jsonify({"success": True, "exists": False})
    st = os.stat(path)
    with open(path, "rb") as f:
        blob = f.read(8192)
    return jsonify({
        "success": True,
        "exists": True,
        "size": st.st_size,
        "modified_at": st.st_mtime,
        "signed": blob[:1] == b"\x30",
    })


@bp.route("/api/mobileconfig", methods=["POST"])
@admin_required
def mobileconfig_upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify({"success": False, "error": "Missing file"}), 400

    blob = f.read(MAX_MOBILECONFIG_BYTES + 1)
    if len(blob) == 0:
        return jsonify({"success": False, "error": "Empty file"}), 400
    if len(blob) > MAX_MOBILECONFIG_BYTES:
        return jsonify({"success": False, "error": "File too large (max 5 MB)"}), 400
    if not _looks_like_mobileconfig(blob):
        return jsonify({
            "success": False,
            "error": "File doesn't look like a .mobileconfig (no <plist> found)",
        }), 400

    target = _mobileconfig_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "wb") as out:
        out.write(blob)
    os.replace(tmp, target)
    st = os.stat(target)
    signed = blob[:1] == b"\x30"
    _record_mobileconfig_history(
        "upload",
        filename=f.filename,
        size=st.st_size,
        signed=signed,
    )
    return jsonify({
        "success": True,
        "size": st.st_size,
        "modified_at": st.st_mtime,
    })


@bp.route("/api/mobileconfig", methods=["DELETE"])
@admin_required
def mobileconfig_remove():
    path = _mobileconfig_path()
    existed = os.path.exists(path)
    if existed:
        os.remove(path)
        _record_mobileconfig_history("delete")
    return jsonify({"success": True})


@bp.route("/api/mobileconfig/history", methods=["GET"])
@admin_required
def mobileconfig_history():
    rows = db.get_conn().execute(
        "SELECT id, action, filename, size, signed, created_at "
        "FROM mobileconfig_history ORDER BY id DESC LIMIT ?",
        (MOBILECONFIG_HISTORY_LIMIT,),
    ).fetchall()
    items = [
        {
            "id": r["id"],
            "action": r["action"],
            "filename": r["filename"],
            "size": r["size"],
            "signed": bool(r["signed"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return jsonify({"success": True, "items": items})


@bp.route("/api/queue", methods=["GET"])
@admin_required
def queue_snapshot():
    qm = current_app.queue_manager
    rotator = current_app.rotator
    snap = qm.admin_snapshot()
    worker_emails = {}
    for slot_id in list(qm.workers.keys()):
        try:
            worker_emails[slot_id] = rotator.email(slot_id) if rotator else "<no rotator>"
        except KeyError:
            worker_emails[slot_id] = "<removed>"
    return jsonify({"success": True, "workers": worker_emails, **snap})
