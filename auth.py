"""Customer login / sign-up / forgot-password (separate from the Admin login).

Flow: QR scan -> "/" -> not signed in? -> /auth (Login | Sign Up | Forgot password) -> back to the chatbot home page.
Stored in MongoDB collection `users`:  name, email, phone, company (optional), password_hash, created, last_login.
Forgot password: a signed, 60-minute, one-time link is e-mailed through Brevo (same mail setup as the enquiry mails).
"""
import os, re, secrets, logging, threading
from urllib.parse import urlencode
import requests
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


# Social login (OAuth 2.0). A button works as soon as its keys are in .env - see SOCIAL_LOGIN_SETUP.md
PROVIDERS = {
    "google": dict(label="Google", id_env="GOOGLE_CLIENT_ID", secret_env="GOOGLE_CLIENT_SECRET",
                   auth="https://accounts.google.com/o/oauth2/v2/auth", token="https://oauth2.googleapis.com/token",
                   info="https://openidconnect.googleapis.com/v1/userinfo", scope="openid email profile",
                   extra={"prompt": "select_account"}),
    "microsoft": dict(label="Microsoft", id_env="MICROSOFT_CLIENT_ID", secret_env="MICROSOFT_CLIENT_SECRET",
                      auth="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                      token="https://login.microsoftonline.com/common/oauth2/v2.0/token",
                      info="https://graph.microsoft.com/oidc/userinfo", scope="openid email profile", extra={}),
    "facebook": dict(label="Facebook", id_env="FACEBOOK_APP_ID", secret_env="FACEBOOK_APP_SECRET",
                     auth="https://www.facebook.com/v19.0/dialog/oauth", token="https://graph.facebook.com/v19.0/oauth/access_token",
                     info="https://graph.facebook.com/me?fields=id,name,email", scope="public_profile,email", extra={}),
    "linkedin": dict(label="LinkedIn", id_env="LINKEDIN_CLIENT_ID", secret_env="LINKEDIN_CLIENT_SECRET",
                     auth="https://www.linkedin.com/oauth/v2/authorization", token="https://www.linkedin.com/oauth/v2/accessToken",
                     info="https://api.linkedin.com/v2/userinfo", scope="openid profile email", extra={}),
}
ERRORS = {"social_off": "This social login is not set up yet. Please use email & password.",
          "social_fail": "Social login failed. Please try again or use email & password.",
          "denied": "Login was cancelled.",
          "social_email": "This email is already registered with a password. Please login with your password (or use Forgot password)."}


def configured(p):
    c = PROVIDERS[p]; return bool(os.getenv(c["id_env"]) and os.getenv(c["secret_env"]))


# ---------------------------------------------------------------- helpers
def current_user(complete_only=False):
    """Logged-in customer document, or None (also signs out a deleted user).
    complete_only=True -> a social-login user who has not added a contact number yet counts as 'not ready'."""
    uid = session.get("user_id")
    if not uid: return None
    try: u = database().users.find_one({"_id": ObjectId(uid)})
    except Exception: u = None
    if not u:
        session.pop("user_id", None); session.pop("user_name", None)
    if u and complete_only and not u.get("phone"): return None
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
def _page(view, token="", **kw):
    return app_response(render_template("auth.html", view=view, token=token, min_pw=MIN_PASSWORD,
                                        providers={p: configured(p) for p in PROVIDERS}, **kw))


@bp.get("/auth")
def auth_page():
    u = current_user()
    if u and u.get("phone"): return redirect("/")
    if u:                                  # signed in with Google / Facebook ... but no contact number yet
        return _page("complete", user_name=u.get("name", ""), user_email=u.get("email", ""))
    view = request.args.get("v", "login")
    return _page(view if view in ("login", "signup", "forgot") else "login", err=ERRORS.get(request.args.get("e"), ""))


@bp.get("/auth/reset/<token>")
def reset_page(token):
    try:
        data = _serializer().loads(token, max_age=RESET_MINUTES * 60)
        u = database().users.find_one({"_id": ObjectId(data["uid"])})
        if not u or u["password_hash"][-10:] != data["f"]: raise BadSignature("used")
        ok = True
    except (BadSignature, SignatureExpired, Exception):
        ok = False
    return _page("reset" if ok else "expired", token if ok else "")


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
    doc = {"name": name, "email": email, "phone": norm(phone), "company": company, "provider": "password",
           "password_hash": generate_password_hash(pw), "created": now_ist(), "last_login": now_ist()}
    try: doc["_id"] = U.insert_one(doc).inserted_id
    except Exception as ex:
        if "duplicate" in str(ex).lower() or "E11000" in str(ex):          # unique index: two signups at the same moment
            return _fail({"email": "This email or mobile number is already registered. Please login."}, 409)
        logging.exception("signup: could not save user")
        return _fail({"_": "Could not save your account right now. Please try again in a moment."}, 500)
    if not U.find_one({"_id": doc["_id"]}):                                 # read it back: never say 'done' unless it is really stored
        logging.error("signup: user %s not found after insert", email)
        return _fail({"_": "Could not save your account right now. Please try again in a moment."}, 500)
    logging.info("new website user saved: %s (%s)", email, doc["_id"])
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
    if u and not u.get("password_hash"):
        return _fail({"_": "This account was created with " + (PROVIDERS.get(u.get("provider"), {}).get("label") or "social login")
                           + ". Use that button above, or tap Forgot password to set a password."}, 401)
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


# ---------------------------------------------------------------- social login (Google / Microsoft / Facebook / LinkedIn)
def _redirect_uri(p): return _site_url() + "/auth/social/" + p + "/callback"


@bp.get("/auth/social/<p>")
def social_start(p):
    if p not in PROVIDERS: return redirect("/auth")
    if not configured(p): return redirect("/auth?e=social_off")
    c = PROVIDERS[p]
    state = secrets.token_urlsafe(24); session["oauth_state"] = state; session["oauth_p"] = p
    q = {"client_id": os.getenv(c["id_env"]), "redirect_uri": _redirect_uri(p), "response_type": "code",
         "scope": c["scope"], "state": state, **c["extra"]}
    return redirect(c["auth"] + "?" + urlencode(q))


def _social_profile(p, code):
    """Exchange the code for the member's profile -> dict(id, name, email, verified) or None."""
    c = PROVIDERS[p]
    tr = requests.post(c["token"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(p),
                                         "client_id": os.getenv(c["id_env"]), "client_secret": os.getenv(c["secret_env"])},
                       headers={"Accept": "application/json"}, timeout=15)
    tok = (tr.json() or {}).get("access_token")
    if not tok: return None
    ir = requests.get(c["info"], headers={"Authorization": "Bearer " + tok}, timeout=15)
    d = ir.json() or {}
    pid = str(d.get("sub") or d.get("id") or "")
    if not pid: return None
    name = _clean(d.get("name") or (str(d.get("given_name", "")) + " " + str(d.get("family_name", ""))), 80)
    email = _clean(d.get("email"), 120).lower()
    verified = p in ("google", "linkedin") and str(d.get("email_verified")).lower() in ("true", "1")   # only these tell us the e-mail is verified
    return {"id": pid, "name": name, "email": email if EMAIL_RE.match(email) else "", "verified": verified}


@bp.get("/auth/social/<p>/callback")
def social_callback(p):
    if p not in PROVIDERS or not configured(p): return redirect("/auth?e=social_off")
    if request.args.get("error"): return redirect("/auth?e=denied")
    saved, sp = session.pop("oauth_state", None), session.pop("oauth_p", None)
    if not saved or sp != p or not secrets.compare_digest(saved, str(request.args.get("state", ""))):     # CSRF check
        return redirect("/auth?e=social_fail")
    return _social_finish(p, request.args.get("code", ""))


def _social_finish(p, code):
    try: prof = _social_profile(p, code)
    except Exception: logging.exception("social login failed (%s)", p); prof = None
    if not prof: return redirect("/auth?e=social_fail")
    U = database().users
    u = U.find_one({"social." + p: prof["id"]})
    if not u and prof["email"]:
        u = U.find_one({"email": prof["email"]})
        if u and not prof["verified"]: return redirect("/auth?e=social_email")      # never attach to someone else's account on an unverified e-mail
        if u: U.update_one({"_id": u["_id"]}, {"$set": {"social." + p: prof["id"]}})
    if not u:
        doc = {"name": prof["name"] or "Customer", "company": "", "provider": p, "social": {p: prof["id"]},
               "password_hash": "", "created": now_ist(), "last_login": now_ist()}
        if prof["email"]: doc["email"] = prof["email"]
        try: doc["_id"] = U.insert_one(doc).inserted_id
        except Exception: logging.exception("social signup could not save"); return redirect("/auth?e=social_fail")
        u = doc
    U.update_one({"_id": u["_id"]}, {"$set": {"last_login": now_ist()}})
    _login_session(u)
    return redirect("/" if u.get("phone") else "/auth")        # no contact number yet -> 'one last step' screen


@bp.post("/auth/complete")
def complete():
    u = current_user()
    if not u: return _fail({"_": "Please login first."}, 401)
    b = request.get_json(silent=True) or {}
    phone, company = mobile10(b.get("phone")), _clean(b.get("company"), 120)
    if not phone: return _fail({"phone": "Enter a valid 10-digit mobile number"})
    other = database().users.find_one({"phone": norm(phone)})
    if other and other["_id"] != u["_id"]: return _fail({"phone": "This mobile number is already registered with another account."}, 409)
    sets = {"phone": norm(phone)}
    if company: sets["company"] = company
    database().users.update_one({"_id": u["_id"]}, {"$set": sets})
    return jsonify(ok=True, redirect="/")


# ---------------------------------------------------------------- wiring
def register(app, too_many, client_ip):
    global _too_many, _client_ip
    _too_many, _client_ip = too_many, client_ip
    app.register_blueprint(bp)
    try:
        U = database().users
        for f in ("email", "phone"):          # unique, but only when the field really has a value (social users may not have a phone yet)
            info = U.index_information().get(f + "_1")
            if info and "partialFilterExpression" not in info: U.drop_index(f + "_1")
            U.create_index(f, unique=True, partialFilterExpression={f: {"$type": "string"}})
    except Exception as ex:
        print("users index:", ex)
