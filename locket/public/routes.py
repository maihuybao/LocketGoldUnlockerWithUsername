import os
from datetime import datetime, timezone

from flask import (
    current_app, jsonify, render_template, request, send_file, session,
)

from .. import db, site_settings
from . import bp


def _mobileconfig_path():
    static_dir = os.path.join(current_app.root_path, "static")
    return os.path.join(static_dir, "locket.mobileconfig")


def _mask_username(name):
    if not name:
        return "—"
    s = str(name)
    return s[0] + "*" * min(4, max(0, len(s) - 1))


def _no_accounts_response():
    return jsonify({
        "success": False,
        "msg": "Chưa có tài khoản Locket nào. Admin hãy thêm qua /admin.",
    }), 503


def _maintenance_active():
    m = site_settings.get_maintenance()
    if not m.get("enabled"):
        return None
    if m.get("allow_admin", True) and session.get("admin"):
        return None
    return m


def _maintenance_json_response():
    m = _maintenance_active()
    if m is None:
        return None
    return jsonify({
        "success": False,
        "maintenance": True,
        "msg": m.get("message") or "Hệ thống đang bảo trì.",
        "end_at": m.get("end_at") or None,
    }), 503


@bp.route("/")
def index():
    m = _maintenance_active()
    if m is not None:
        return render_template("maintenance.html", settings=m), 503
    theme = site_settings.get_theme().get("name", "gold")
    layout = site_settings.get_layout().get("name", "stacked")
    return render_template("index.html", theme=theme, layout=layout)


@bp.route("/api/mobileconfig", methods=["GET"])
def mobileconfig_download():
    """Serve the mobileconfig with the exact headers iOS needs to trigger the
    'Install Profile' system dialog (instead of saving as a regular download).

    - Content-Type: application/x-apple-aspen-config — required by iOS Safari.
    - Content-Disposition: inline — keeps Safari from offering "Save to Files".
    - No-cache — admins can re-upload and clients see the new version.
    """
    path = _mobileconfig_path()
    if not os.path.exists(path):
        return jsonify({"success": False, "msg": "Profile not configured"}), 404
    resp = send_file(
        path,
        mimetype="application/x-apple-aspen-config",
        as_attachment=False,
        download_name="locket.mobileconfig",
    )
    resp.headers["Content-Disposition"] = 'inline; filename="locket.mobileconfig"'
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@bp.route("/api/site-settings", methods=["GET"])
def site_settings_public():
    payload = site_settings.public_view()
    # Whether the current visitor is actually under maintenance (after admin
    # bypass). FE uses this to decide whether to redirect to the maintenance
    # page — `maintenance.enabled` alone would loop admins.
    payload["maintenance_active"] = _maintenance_active() is not None
    return jsonify({"success": True, **payload})


@bp.route("/api/get-user-info", methods=["POST"])
def get_user_info():
    blocked = _maintenance_json_response()
    if blocked is not None:
        return blocked
    rotator = current_app.rotator
    qm = current_app.queue_manager
    if rotator is None or rotator.size() == 0:
        return _no_accounts_response()

    data = request.json or {}
    username = data.get("username")
    if not username:
        return jsonify({"success": False, "msg": "Username is required"}), 400

    try:
        print(f"Looking up user: {username}")
        account_info = qm.call_round_robin("getUserByUsername", username)

        if not account_info or "result" not in account_info:
            return jsonify({"success": False, "msg": "User not found or API error"}), 404

        user_data = account_info.get("result", {}).get("data")
        if not user_data:
            return jsonify({"success": False, "msg": "User data not found"}), 404

        return jsonify({
            "success": True,
            "data": {
                "uid": user_data.get("uid"),
                "username": user_data.get("username"),
                "first_name": user_data.get("first_name", ""),
                "last_name": user_data.get("last_name", ""),
                "profile_picture_url": user_data.get("profile_picture_url", ""),
            },
        })

    except Exception as e:
        print(f"Error in get user info: {e}")
        return jsonify({"success": False, "msg": f"An error occurred: {str(e)}"}), 500


@bp.route("/api/restore", methods=["POST"])
def restore_purchase():
    """Add a request to the queue. Returns client_id for polling."""
    blocked = _maintenance_json_response()
    if blocked is not None:
        return blocked
    rotator = current_app.rotator
    qm = current_app.queue_manager
    if rotator is None or rotator.size() == 0:
        return _no_accounts_response()

    data = request.json or {}
    username = data.get("username")
    if not username:
        return jsonify({"success": False, "msg": "Username is required"}), 400

    try:
        client_id = qm.add_to_queue(username)
        if client_id is None:
            return jsonify({"success": False, "msg": "Queue is full, please try again later."}), 503

        status = qm.get_status(client_id)
        return jsonify({
            "success": True,
            "client_id": client_id,
            "position": status["position"],
            "total_queue": status["total_queue"],
            "estimated_time": status["estimated_time"],
        })
    except Exception as e:
        print(f"Error adding to queue: {e}")
        return jsonify({"success": False, "msg": f"An error occurred: {str(e)}"}), 500


@bp.route("/api/recent-history", methods=["GET"])
def recent_history():
    """Public-safe recent history. Username is masked (a**** style); slot_id
    and error details are stripped. Returns up to 30 newest entries."""
    cutoff = __import__("time").time() - 24 * 3600
    rows = db.get_conn().execute(
        "SELECT username, status, duration, completed_at "
        "FROM recent_log WHERE completed_at >= ? "
        "ORDER BY id DESC LIMIT 30",
        (cutoff,),
    ).fetchall()
    items = []
    for r in rows:
        completed_at = None
        if r["completed_at"] is not None:
            completed_at = datetime.fromtimestamp(
                r["completed_at"], tz=timezone.utc
            ).isoformat()
        items.append({
            "username": _mask_username(r["username"]),
            "status": r["status"],
            "duration": r["duration"],
            "completed_at": completed_at,
        })
    return jsonify({"success": True, "items": items})


@bp.route("/api/mobileconfig/history", methods=["GET"])
def mobileconfig_history_public():
    """Public-safe profile update history. Filenames are stripped (admins only
    see those); we expose action + size + signed flag + timestamp so users
    know when the profile was last refreshed."""
    rows = db.get_conn().execute(
        "SELECT action, size, signed, created_at "
        "FROM mobileconfig_history ORDER BY id DESC LIMIT 10"
    ).fetchall()
    items = [
        {
            "action": r["action"],
            "size": r["size"],
            "signed": bool(r["signed"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return jsonify({"success": True, "items": items})


@bp.route("/api/queue/global-status", methods=["GET"])
def global_queue_status():
    """Aggregate queue stats — no client_id required."""
    return jsonify({"success": True, **current_app.queue_manager.get_global_status()})


@bp.route("/api/queue/status", methods=["POST"])
def queue_status():
    """Per-client polling endpoint. Returns success even on `not_found` so
    the frontend can recover instead of treating it as fatal."""
    data = request.json or {}
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"success": False, "msg": "client_id is required"}), 400
    return jsonify({"success": True, **current_app.queue_manager.get_status(client_id)})
