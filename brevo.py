"""All outgoing e-mail of the bot goes through this file: Brevo (ex-Sendinblue) transactional API over HTTPS.

Why: SMTP (ports 25/465/587) is blocked on Render's free plan, so the old smtplib code could not reach the sales person
from the live site. The Brevo API uses normal HTTPS (port 443) and works everywhere.

Settings (Render -> Environment, or .env locally):
  BREVO_API_KEY     -> Brevo dashboard -> SMTP & API -> API Keys -> Generate (starts with  xkeysib-)
  MAIL_FROM_EMAIL   -> sender address. MUST be a verified sender in Brevo (Senders, domains & dedicated IPs -> Senders)
  MAIL_FROM_NAME    -> display name (optional, default "Shreeji SteelTubes")

Usage:   ok, note = brevo.send(["a@x.com"], "Subject", text="plain text")   or   html_body="<p>...</p>"
Returns (ok, note) and never raises - the callers save `note` on the enquiry / job so it is visible in the admin panel.
"""
import os, time
from html import escape
import requests

API_URL = "https://api.brevo.com/v3/smtp/email"


def api_key():
    return (os.getenv("BREVO_API_KEY") or "").strip()


def from_email():
    return (os.getenv("MAIL_FROM_EMAIL") or "").strip()


def from_name():
    return (os.getenv("MAIL_FROM_NAME") or "Shreeji SteelTubes").strip()


def ready():
    """True when the API key and the sender address are both set."""
    return bool(api_key() and from_email())


def _text_to_html(text):
    # Brevo accepts ONE body type per request, so plain-text mails are sent as simple HTML that keeps the line breaks.
    return ("<div style=\"font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;white-space:pre-wrap;"
            "word-break:break-word\">" + escape(text or "") + "</div>")


def send(to, subject, text=None, html_body=None, reply_to=None, tags=None):
    """to = one address or a list of addresses. Give html_body (preferred) or text. Returns (ok, note)."""
    if not ready():
        return False, "Brevo not set up: BREVO_API_KEY and MAIL_FROM_EMAIL are required"
    if isinstance(to, str): to = [to]
    to = [str(e).strip() for e in (to or []) if e and str(e).strip()]
    if not to: return False, "no recipient"
    body = html_body if html_body else _text_to_html(text)
    payload = {"sender": {"name": from_name(), "email": from_email()},
               "to": [{"email": e} for e in to],
               "subject": subject, "htmlContent": body}
    if reply_to: payload["replyTo"] = {"email": str(reply_to).strip()}
    if tags: payload["tags"] = list(tags)
    headers = {"accept": "application/json", "content-type": "application/json", "api-key": api_key()}
    last = ""
    for attempt in (1, 2):                                   # one quick retry for a network blip / Brevo 5xx
        try:
            r = requests.post(API_URL, json=payload, headers=headers, timeout=20)
            if r.status_code in (200, 201, 202):
                try: mid = (r.json() or {}).get("messageId", "")
                except Exception: mid = ""
                return True, f"sent to {', '.join(to)}" + (f" (brevo id {mid})" if mid else "")
            try: msg = (r.json() or {}).get("message") or r.text
            except Exception: msg = r.text
            last = f"Brevo error {r.status_code}: {str(msg)[:300]}"
            if r.status_code < 500: break                    # 4xx = wrong key / unverified sender / bad address -> retry is useless
        except requests.RequestException as e:
            last = f"Brevo request failed: {e}"
        if attempt == 1: time.sleep(1.5)
    return False, last
