"""Customer login / sign-up / forgot-password (separate from the Admin login).

Flow: QR scan -> "/" -> not signed in? -> /auth (Login | Sign Up | Forgot password) -> back to the chatbot home page.
Stored in MongoDB collection `users`:  name, email, phone, company (optional), password_hash, created, last_login.
Forgot password: a signed, 60-minute, one-time link is e-mailed through Brevo (same mail setup as the enquiry mails).
"""
import os, re, logging, threading
from bson import ObjectId
from flask import Blueprint, request, session, redirect, render_template, jsonify
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash
import brevo
from db import database, now_ist, norm

bp = Blueprint("auth", __name__)

MIN_PASSWORD = 8
RESET_MINUTES = 60
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

_too_many = None      # injected from app.py (shared in-memory rate limiter)
_client_ip = None


# ---------------------------------------------------------------- helpers
def current_user():
    """Logged-in customer document, or None (also signs out a deleted user)."""
    uid = session.get("user_id")
    if not uid: return None
    try: u = database().users.find_one({"_id": ObjectId(uid)})
    except Exception: u = None
    if not u:
        session.pop("user_id", None); session.pop("user_name", None)
    return u


def mobile10(raw):
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"): d = d[2:]
    elif len(d) == 11 and d.startswith("0"): d = d[1:]
    return d if re.fullmatch(r"[6-9]\d{9}", d) else ""


def _clean(v, n=120):
    return " ".join(str(v or "").split())[:n]


def _site_url():
    return (os.getenv("PUBLIC_URL") or request.url_root).rstrip("/")


def _serializer():
    return URLSafeTimedSerializer(os.getenv("SECRET_KEY", "dev-secret"), salt="srj-customer-reset")


def _login_session(u):
    session.permanent = True
    session["user_id"] = str(u["_id"]); session["user_name"] = u.get("name", "")


def _fail(errors, code=400):
    return jsonify(ok=False, errors=errors), code


def _limited(bucket, limit, seconds):
    return _too_many and (_too_many(bucket + ":" + _client_ip(), limit, seconds))


def _check_password(pw, pw2):
    if len(pw or "") < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters"
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        return "Use letters and numbers in the password"
    if pw2 is not None and pw != pw2:
        return "Passwords do not match"
    return ""


# ---------------------------------------------------------------- pages
@bp.get("/auth")
def auth_page():
    if current_user(): return redirect("/")
    view = request.args.get("v", "login")
    r = render_template("auth.html", view=view if view in ("login", "signup", "forgot") else "login",
                        token="", min_pw=MIN_PASSWORD)
    resp = app_response(r); return resp


@bp.get("/auth/reset/<token>")
def reset_page(token):
    try:
        data = _serializer().loads(token, max_age=RESET_MINUTES * 60)
        u = database().users.find_one({"_id": ObjectId(data["uid"])})
        if not u or u["password_hash"][-10:] != data["f"]: raise BadSignature("used")
        ok = True
    except (BadSignature, SignatureExpired, Exception):
        ok = False
    return app_response(render_template("auth.html", view="reset" if ok else "expired", token=token if ok else "", min_pw=MIN_PASSWORD))


def app_response(html):
    from flask import make_response
    r = make_response(html); r.headers["Cache-Control"] = "no-store, max-age=0"; return r


@bp.get("/auth/logout")
def logout():
    session.pop("user_id", None); session.pop("user_name", None)
    return redirect("/auth")


# ---------------------------------------------------------------- API
@bp.post("/auth/signup")
def signup():
    if _limited("signup", 8, 600): return _fail({"_": "Too many attempts. Please wait a few minutes."}, 429)
    b = request.get_json(silent=True) or {}
    name, email = _clean(b.get("name"), 80), _clean(b.get("email"), 120).lower()
    company, phone = _clean(b.get("company"), 120), mobile10(b.get("phone"))
    pw, pw2 = str(b.get("password") or ""), str(b.get("password2") or "")
    errs = {}
    if len(name) < 2: errs["name"] = "Please enter your full name"
    if not EMAIL_RE.match(email): errs["email"] = "Enter a valid email address"
    if not phone: errs["phone"] = "Enter a valid 10-digit mobile number"
    e = _check_password(pw, pw2)
    if e: errs["password"] = e
    if errs: return _fail(errs)
    U = database().users
    if U.find_one({"email": email}): errs["email"] = "This email is already registered. Please login."
    if U.find_one({"phone": norm(phone)}): errs["phone"] = "This mobile number is already registered. Please login."
    if errs: return _fail(errs, 409)
    doc = {"name": name, "email": email, "phone": norm(phone), "company": company,
           "password_hash": generate_password_hash(pw), "created": now_ist(), "last_login": now_ist()}
    try: doc["_id"] = U.insert_one(doc).inserted_id
    except Exception:                          # unique index: two signups at the same moment
        return _fail({"email": "This email or mobile number is already registered. Please login."}, 409)
    _login_session(doc)
    return jsonify(ok=True, redirect="/")


@bp.post("/auth/login")
def login():
    if _limited("ulogin", 8, 600): return _fail({"_": "Too many attempts. Please wait 10 minutes and try again."}, 429)
    b = request.get_json(silent=True) or {}
    ident, pw = _clean(b.get("login"), 120).lower(), str(b.get("password") or "")
    if not ident or not pw: return _fail({"_": "Enter your email / mobile number and password"})
    U = database().users
    u = U.find_one({"email": ident}) if "@" in ident else (U.find_one({"phone": norm(mobile10(ident))}) if mobile10(ident) else None)
    if not u or not check_password_hash(u["password_hash"], pw):
        return _fail({"_": "Invalid email / mobile number or password"}, 401)
    U.update_one({"_id": u["_id"]}, {"$set": {"last_login": now_ist()}})
    _login_session(u)
    return jsonify(ok=True, redirect="/")


def _mail_reset(u, link):
    try:
        import mailer
        logo = mailer._logo_url()
    except Exception:
        logo = ""
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;border:1px solid #e5e7f5;border-radius:12px;padding:24px">'
            + (f'<img src="{logo}" alt="Shreeji SteelTubes" style="height:46px;margin-bottom:12px"><br>' if logo else "")
            + f'<h2 style="color:#26399e;margin:6px 0">Reset your password</h2>'
              f'<p>Hello {brevo.escape(u.get("name", ""))},</p>'
              f'<p>We received a request to reset your Shreeji SteelTubes password. This link is valid for {RESET_MINUTES} minutes and works only once.</p>'
              f'<p style="margin:22px 0"><a href="{link}" style="background:#f15307;color:#fff;text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:bold">Reset Password</a></p>'
              f'<p style="font-size:12px;color:#696e8a">If the button does not work, copy this link:<br>{link}<br><br>'
              f'Did not ask for this? You can ignore this email - your password stays the same.</p></div>')
    ok, note = brevo.send(u["email"], "Reset your Shreeji SteelTubes password", html_body=html, tags=["password-reset"])
    if not ok:
        logging.warning("password reset mail not sent: %s", note)
        if os.getenv("FLASK_DEBUG", "0") == "1": print("RESET LINK (dev):", link)


@bp.post("/auth/forgot")
def forgot():
    if _limited("uforgot", 5, 600): return _fail({"_": "Too many requests. Please wait a few minutes."}, 429)
    b = request.get_json(silent=True) or {}
    ident = _clean(b.get("login"), 120).lower()
    U = database().users
    u = U.find_one({"email": ident}) if "@" in ident else (U.find_one({"phone": norm(mobile10(ident))}) if mobile10(ident) else None)
    if u:
        tok = _serializer().dumps({"uid": str(u["_id"]), "f": u["password_hash"][-10:]})
        threading.Thread(target=_mail_reset, args=(u, _site_url() + "/auth/reset/" + tok), daemon=True).start()
    # same answer whether the account exists or not (no account guessing)
    return jsonify(ok=True, message="If this account exists, a password reset link has been sent to its registered email address.")


@bp.post("/auth/reset")
def reset():
    if _limited("ureset", 10, 600): return _fail({"_": "Too many attempts. Please wait a few minutes."}, 429)
    b = request.get_json(silent=True) or {}
    pw, pw2 = str(b.get("password") or ""), str(b.get("password2") or "")
    e = _check_password(pw, pw2)
    if e: return _fail({"password": e})
    try:
        data = _serializer().loads(str(b.get("token") or ""), max_age=RESET_MINUTES * 60)
        u = database().users.find_one({"_id": ObjectId(data["uid"])})
        if not u or u["password_hash"][-10:] != data["f"]: raise BadSignature("used")
    except (BadSignature, SignatureExpired, Exception):
        return _fail({"_": "This reset link has expired or was already used. Please request a new one."}, 400)
    database().users.update_one({"_id": u["_id"]}, {"$set": {"password_hash": generate_password_hash(pw), "pw_changed": now_ist()}})
    session.pop("user_id", None); session.pop("user_name", None)
    return jsonify(ok=True, message="Password changed. Please login with your new password.")


# ---------------------------------------------------------------- wiring
def register(app, too_many, client_ip):
    global _too_many, _client_ip
    _too_many, _client_ip = too_many, client_ip
    app.register_blueprint(bp)
    try:
        U = database().users
        U.create_index("email", unique=True); U.create_index("phone", unique=True)
    except Exception as ex:
        print("users index:", ex)
