"""Pre-chat visitor details - replaces the old Login / Sign-up / Social-login system.

Flow: QR scan -> "/" -> no visitor details yet this session? -> /auth (one small form:
Name, Contact number, Company name) -> saved -> straight into the chatbot home page.

Stored in MongoDB collection `users` (kept so the existing Admin > Website Users page,
its CSV export and delete action keep working unchanged): name, phone, company, created, last_login.
"""
import re
import logging
from flask import Blueprint, request, session, redirect, render_template, jsonify, make_response
from bson import ObjectId
from pymongo.errors import DuplicateKeyError, PyMongoError
from db import database, now_ist, norm

bp = Blueprint("auth", __name__)

_too_many = None      # injected from app.py (shared in-memory rate limiter)
_client_ip = None


# ---------------------------------------------------------------- helpers
def mobile10(raw):
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"): d = d[2:]
    elif len(d) == 11 and d.startswith("0"): d = d[1:]
    return d if re.fullmatch(r"[6-9]\d{9}", d) else ""


def _clean(v, n=120):
    return " ".join(str(v or "").split())[:n]


def _limited(bucket, limit, seconds):
    return _too_many and _client_ip and _too_many(bucket + ":" + _client_ip(), limit, seconds)


def current_user(complete_only=False):
    """Signed-in visitor document, or None (also clears a stale / deleted session).
    complete_only is kept only so existing call-sites (app.py) do not need to change -
    a visitor recorded through /auth always has a name + phone, so there is no 'incomplete' state anymore."""
    uid = session.get("user_id")
    if not uid: return None
    try: u = database().users.find_one({"_id": ObjectId(uid)})
    except Exception: u = None
    if not u:
        session.pop("user_id", None); session.pop("user_name", None)
    return u


def _login_session(u):
    session.permanent = True
    session["user_id"] = str(u["_id"]); session["user_name"] = u.get("name", "")


def _fail(errors, code=400):
    return jsonify(ok=False, errors=errors), code


def app_response(html):
    r = make_response(html); r.headers["Cache-Control"] = "no-store, max-age=0"; return r


# ---------------------------------------------------------------- pages
@bp.get("/auth")
def auth_page():
    if current_user(): return redirect("/")      # already entered their details this session
    return app_response(render_template("auth.html"))


@bp.get("/auth/logout")
def logout():        # "Not you? / Change details" - lets the same browser enter a different name / number
    session.pop("user_id", None); session.pop("user_name", None)
    return redirect("/auth")


# ---------------------------------------------------------------- API
@bp.post("/auth/enter")
def enter():
    if _limited("auth-enter", 20, 600): return _fail({"_": "Too many attempts. Please wait a few minutes."}, 429)
    b = request.get_json(silent=True) or {}
    name = _clean(b.get("name"), 80)
    phone = mobile10(b.get("phone"))
    company = _clean(b.get("company"), 120)
    errors = {}
    if not name: errors["name"] = "Please enter your name"
    if not phone: errors["phone"] = "Enter a valid 10-digit mobile number"
    if errors: return _fail(errors)
    try:
        U = database().users
        u = U.find_one({"phone": norm(phone)})
        if u:       # same mobile number visiting again -> keep one record, just refresh their details
            U.update_one({"_id": u["_id"]}, {"$set": {"name": name, "company": company or u.get("company", ""), "last_login": now_ist()}})
            u = U.find_one({"_id": u["_id"]})
        else:
            doc = {"name": name, "phone": norm(phone), "company": company, "email": "",
                   "provider": "chat", "created": now_ist(), "last_login": now_ist()}
            try:
                doc["_id"] = U.insert_one(doc).inserted_id
                u = doc
            except DuplicateKeyError:
                # Same mobile number was saved a split-second earlier by another request
                # (e.g. a double-tap on "Start Chat", or two tabs/devices with the same
                # number at the same instant). That is not a real failure - just use the
                # record that already exists instead of showing an error.
                u = U.find_one({"phone": norm(phone)})
                if u:
                    U.update_one({"_id": u["_id"]}, {"$set": {"name": name, "company": company or u.get("company", ""), "last_login": now_ist()}})
                    u = U.find_one({"_id": u["_id"]})
                else:
                    raise
    except PyMongoError:
        logging.exception("auth/enter: database error")
        return _fail({"_": "We couldn't save your details right now. Please try again in a moment."}, 503)
    except Exception:
        logging.exception("auth/enter: unexpected error")
        return _fail({"_": "Something went wrong. Please try again."}, 500)
    _login_session(u)
    return jsonify(ok=True, redirect="/")


# ---------------------------------------------------------------- wiring
def register(app, too_many, client_ip):
    global _too_many, _client_ip
    _too_many, _client_ip = too_many, client_ip
    app.register_blueprint(bp)
    try:
        U = database().users
        info = U.index_information().get("phone_1")
        if info and "partialFilterExpression" not in info: U.drop_index("phone_1")
        U.create_index("phone", unique=True, partialFilterExpression={"phone": {"$type": "string"}})
    except Exception as ex:
        print("users index:", ex)
    try:
        # Leftover from the old email-based login system: a plain UNIQUE index on "email".
        # This new /auth form does not collect an email any more, so every visitor is saved
        # with email:"" - the first one saves fine, then that same plain unique index makes
        # EVERY following visitor's save fail with "E11000 duplicate key error ... email_1
        # dup key: { email: "" }". Replace it with a partial index that only enforces
        # uniqueness when an email actually has a real, non-empty value.
        U = database().users
        info = U.index_information().get("email_1")
        if info and "partialFilterExpression" not in info: U.drop_index("email_1")
        U.create_index("email", unique=True, partialFilterExpression={"email": {"$type": "string", "$gt": ""}})
    except Exception as ex:
        print("users email index:", ex)
