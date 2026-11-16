"""Email alerts via SMTP (Gmail App Password etc.).

Brevo SMTP setup. Env vars (same names as on Render):
  SMTP_HOST  (default smtp-relay.brevo.com)
  SMTP_PORT  (default 2525 - Render free plan blocks 25/465/587; 2525 is open. 465 = SSL)
  SMTP_USER  = Brevo SMTP login (looks like xxxxx@smtp-brevo.com)
  SMTP_PASS  = Brevo SMTP key (NOT your Brevo account password)
  SMTP_FROM  = a sender email verified in Brevo (Senders & IP). Required for Brevo.

Recipients = email of every ACTIVE admin (Admin panel -> Admins).
If no admin has an email saved, falls back to SMTP_FROM / SMTP_USER.
"""
import os, ssl, smtplib, threading
from email.message import EmailMessage


def _cfg():
    user = (os.getenv("SMTP_USER") or "").strip()
    return {
        "host": (os.getenv("SMTP_HOST") or "smtp-relay.brevo.com").strip(),
        "port": int((os.getenv("SMTP_PORT") or "2525").strip()),
        "user": user,
        "pw": (os.getenv("SMTP_PASS") or "").replace(" ", ""),   # Gmail shows app password with spaces
        "from": (os.getenv("SMTP_FROM") or user).strip(),
    }


def admin_emails():
    from db import database
    mails = []
    for a in database().admins.find({"active": True}):
        e = (a.get("email") or "").strip()
        if e and "@" in e and e.lower() not in [m.lower() for m in mails]:
            mails.append(e)
    if not mails:
        c = _cfg()
        fb = c["from"] or c["user"]
        if fb and "@" in fb:
            mails.append(fb)
    return mails


def send_now(subject, body, to=None, reply_to=None):
    """Send synchronously. Returns (ok, message). Never raises."""
    c = _cfg()
    if not c["user"] or not c["pw"]:
        return False, "SMTP_USER / SMTP_PASS not set"
    if not c["from"] or "@" not in c["from"]:
        return False, "SMTP_FROM not set (use a sender email verified in Brevo)"
    to = to or admin_emails()
    if not to:
        return False, "No recipient: add an email for an admin in Admin -> Admins"
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, c["from"] or c["user"], ", ".join(to)
    if reply_to and "@" in reply_to and " " not in reply_to.strip():
        msg["Reply-To"] = reply_to.strip()     # admin can hit Reply to answer the applicant
    msg.set_content(body)
    try:
        if c["port"] == 465:
            s = smtplib.SMTP_SSL(c["host"], c["port"], timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(c["host"], c["port"], timeout=20)
            s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
        with s:
            s.login(c["user"], c["pw"])
            s.send_message(msg)
        print(f"[MAIL] sent '{subject}' -> {to}")
        return True, "Sent to " + ", ".join(to)
    except Exception as e:
        print(f"[MAIL ERROR] {type(e).__name__}: {e}")
        return False, f"{type(e).__name__}: {e}"


def notify(subject, body, reply_to=None):
    """Fire-and-forget (background thread) so the chat/form never waits on SMTP."""
    threading.Thread(target=send_now, args=(subject, body, None, reply_to), daemon=True).start()
