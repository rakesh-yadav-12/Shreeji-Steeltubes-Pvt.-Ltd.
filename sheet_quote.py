"""Automatic price/stock reply to the CUSTOMER, fetched live from a Google Sheet, right after they submit a
product enquiry (web or WhatsApp). Mirrors the pattern of mailer.py / confirm.py: runs in a background thread so
the customer's chat never waits on it, and the result is saved on the enquiry (field 'sheet_quote').

.env settings (see ENV_ADDITIONS.txt):
  GOOGLE_SHEET_ID              -> the long id in the sheet's URL  (…/spreadsheets/d/<THIS PART>/edit)
  GOOGLE_SHEET_TAB             -> worksheet/tab name (default: first tab)
  GOOGLE_SERVICE_ACCOUNT_FILE  -> path to the service-account JSON key file (default: service_account.json)
  SHEET_QUOTE=0                -> switch this feature off entirely
  SHEET_CACHE_MINUTES=10       -> how long the sheet is cached in memory before re-reading it

Expected sheet columns (any order, header row required, case-insensitive; extra columns are ignored):
  Product | Size | Thickness | Price | Unit | Stock

(The customer price mail is switched off in send() below; mails of the bot are sent with the Brevo API - see brevo.py.)
"""
import os, re, threading, time
import brevo
from db import database, now_ist

_CACHE = {"rows": None, "at": 0}


# ---------------------------------------------------------------- sheet read (cached)
def _client():
    import gspread
    from google.oauth2.service_account import Credentials
    keyfile = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(keyfile, scopes=scopes)
    return gspread.authorize(creds)


def _read_sheet():
    """Returns a list of dicts, one per sheet row (header row used as keys, lower-cased)."""
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID not set in .env")
    gc = _client()
    sh = gc.open_by_key(sheet_id)
    tab = (os.getenv("GOOGLE_SHEET_TAB") or "").split("#", 1)[0].strip()   # tolerate an accidental trailing "# comment" in .env
    ws = sh.worksheet(tab) if tab else sh.sheet1
    rows = ws.get_all_records()                      # list[dict], keys = header row
    return [{str(k).strip().lower(): str(v).strip() for k, v in r.items()} for r in rows]


def rows(force=False):
    """Cached read of the sheet - avoids hitting the Google API on every enquiry."""
    ttl = int(os.getenv("SHEET_CACHE_MINUTES", "10")) * 60
    if force or _CACHE["rows"] is None or (time.time() - _CACHE["at"]) > ttl:
        _CACHE["rows"] = _read_sheet()
        _CACHE["at"] = time.time()
    return _CACHE["rows"]


# ---------------------------------------------------------------- matching
def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def find_matches(product, size, thickness=None):
    """Best-effort match: product + size required; thickness narrows it further if the sheet has that column
    and the enquiry has one. Returns a list of matching row-dicts (can be empty)."""
    p, s, t = _norm(product), _norm(size), _norm(thickness)
    out = []
    for r in rows():
        rp, rs = _norm(r.get("product")), _norm(r.get("size"))
        if not rp or not rs: continue
        if p and p not in rp and rp not in p: continue
        if s and s != rs: continue
        if t and r.get("thickness"):
            rt = _norm(r.get("thickness"))
            if rt and rt != t: continue
        out.append(r)
    return out


# ---------------------------------------------------------------- email
def enabled():
    return bool(os.getenv("GOOGLE_SHEET_ID")) and os.getenv("SHEET_QUOTE", "1") != "0" and brevo.ready()


def _row_block(r):
    price = r.get("price", "-"); unit = r.get("unit", ""); stock = r.get("stock", "-")
    return (f"Product   : {r.get('product', '-')}\nSize      : {r.get('size', '-')}"
            + (f"  |  Thickness: {r.get('thickness')}" if r.get("thickness") else "") +
            f"\nPrice     : {price}{(' / ' + unit) if unit else ''}\nStock     : {stock}\n")


def _body(doc, matches):
    head = f"Dear {doc.get('name', '-')},\n\nThank you for your enquiry #{doc['no']} with Shreeji SteelTubes Pvt. Ltd.\n\n"
    if matches:
        body = "Here is the latest price & stock for your requirement:\n\n" + "\n".join(_row_block(r) for r in matches)
    else:
        body = (f"Your requirement:\nProduct : {doc.get('product', '-')}\nSize    : {doc.get('size', '-')}\n\n"
                "Our team is checking the current price & stock for this and will get back to you shortly.")
    return head + body + "\n\n- Shreeji SteelTubes Pvt. Ltd. (automatic mail)"


def send(doc, matches):
    # DISABLED by owner's request: the customer must never get an automatic price/stock mail.
    # Only the sales-designated admin mail (see sales_quote.sales_emails()) should receive the enquiry.
    return False, "price mail to customer is turned off (admin setting)"


def _job(doc):
    try:
        matches = find_matches(doc.get("product"), doc.get("size"), doc.get("thickness"))
        ok, note = send(doc, matches)
        if ok: note += f" ({len(matches)} row(s) matched in sheet)" if matches else " (0 rows matched in sheet - generic fallback mail sent)"
        st = "sent" if ok else "failed"
    except Exception as e:
        ok, note, st = False, str(e), "error"
    try:
        database().enquiries.update_one({"no": doc["no"]}, {"$set": {
            "sheet_quote": st, "sheet_quote_note": note[:400], "sheet_quote_at": now_ist()}})
    except Exception as e:
        print("sheet_quote log error:", e)


def start(doc):
    """Called right after an enquiry is saved. Returns 'queued' | 'off' | 'no-email' (also saved on the enquiry)."""
    if not enabled(): st = "off"
    elif not (doc.get("email") or "").strip(): st = "no-email"
    else: st = "queued"
    database().enquiries.update_one({"no": doc["no"]}, {"$set": {"sheet_quote": st}})
    if st == "queued": threading.Thread(target=_job, args=(dict(doc),), daemon=True).start()
    return st
