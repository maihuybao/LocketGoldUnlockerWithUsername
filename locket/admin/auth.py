"""Session-based admin auth.

Single password from ADMIN_PASSWORD env. After login, session["admin"] = True
and Flask's session cookie (HttpOnly, SameSite=Lax) is set. The decorator
distinguishes HTML and JSON callers: HTML pages get a redirect to /admin/login,
JSON endpoints get a 401 so the frontend can show its own error.
"""

import os
from functools import wraps

from flask import jsonify, redirect, request, session


def is_admin_logged_in():
    return bool(session.get("admin"))


def check_credentials(username, password):
    expected_user = os.getenv("ADMIN_USERNAME")
    expected_pass = os.getenv("ADMIN_PASSWORD")
    if not expected_user or not expected_pass:
        return False
    return username == expected_user and password == expected_pass


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if is_admin_logged_in():
            return view(*args, **kwargs)
        if request.path.startswith("/admin/api/"):
            return jsonify({"success": False, "error": "unauthorized"}), 401
        return redirect("/admin/login")

    return wrapped
