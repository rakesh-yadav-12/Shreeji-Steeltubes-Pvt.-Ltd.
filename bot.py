"""Conversation flow:  Start -> Hi/Welcome -> Home -> Existing user?
      Yes -> Customer Details Form (or About Us)          No -> About Us -> Type of Pipe -> Godown -> Brands -> Layers -> Customer Details Form
      Customer Details Form (customer + representative details) -> Enquiry Form -> Submit -> Confirmation
Web chat shows real FORMS; WhatsApp asks the same fields one by one.   Catalog & Contacts stay available from Home."""
import os, re, json
from urllib.parse import quote_plus, quote
from datetime import datetime, timedelta
import options as OPT
import about_fetch
from db import database, get_state, set_state, next_no, now_ist, norm
from whatsapp import send_text, send_document, send_image, send_video

PRODUCTS = ["MS ERW Pipe", "GI ERW Pipe", "Square Hollow Section", "Rectangular Hollow Section",
            "Large Dia ERW Pipes", "Spiral Welded Pipes", "Seamless Pipe", "GP Pipe (Pre-Galvanized)"]
CATALOG = ["Company Catalogue", "Technical Datasheet / Size-Thickness Range / Weight Chart",
           "Distribution Certificate", "Awards & Achievements", "📄 Documents"]
CAT_KEYS = ["catalogue", "datasheet", "certificate", "awards", "documents"]
GREET = ("hi",)            # only when the customer types Hi
HERE = os.path.dirname(os.path.abspath(__file__))

def _digits(x): return re.sub(r"\D", "", str(x or ""))

def company_phones():
    """[(department, person, 10-digit number)] for EVERY department - options.COMPANY_PHONES, else Admin -> Contacts."""
    rows = [(r[0], (r[2] if len(r) > 2 else ""), r[1]) for r in OPT.COMPANY_PHONES] or \
           [(r.get("department", "Call"), r.get("name"), r.get("phone")) for r in database().contacts.find()]
    out = []
    for dept, who, num in rows:
        d = _digits(num)[-10:]
        who = "" if who in (None, "", "-", "Update in admin panel") else who
        if len(d) == 10 and (dept, d) not in [(x[0], x[2]) for x in out]: out.append((dept, who, d))
    return out

def _clean(v):
    v = str(v or "").strip()
    return "" if v in ("-", "--") or v.lower() == "update in admin panel" else v

def contact_rows():
    """Admin -> Contacts as a flat list grouped department by department (first-added department first)."""
    groups = {}
    for r in database().contacts.find().sort("_id", 1):
        groups.setdefault((_clean(r.get("department")) or "OTHER").upper(), []).append(r)
    out = []
    for dept, people in groups.items():
        for r in people:
            d = _digits(r.get("phone"))[-10:]
            out.append({"dept": dept, "department": dept, "name": _clean(r.get("name")), "desig": _clean(r.get("designation")),
                        "designation": _clean(r.get("designation")), "phone": _clean(r.get("phone")), "direct": _clean(r.get("direct_line")),
                        "email": _clean(r.get("email")), "tel": ("tel:+91" + d) if len(d) == 10 else ""})
    return out

def call_lines(calls):
    return "\n".join(f"*{dept}*{' – ' + who if who else ''}: +91 {n[:5]} {n[5:]}" for dept, who, n in calls)

def call_links(calls):
    """One entry per department: (Department, 'tel:+91..', person) - the web chat shows them inside ONE 'Call' tab."""
    return [(dept, "tel:+91" + n, who) for dept, who, n in calls]

def visit_card(to, phone):
    """Advert-style 'Visit Us' card: address + Google Maps link. (Department call numbers are only in Contact Details.)"""
    maps = OPT.MAPS_LINK or "https://www.google.com/maps/search/?api=1&query=" + quote_plus(getattr(OPT, "COMPANY_MAP_QUERY", "") or OPT.COMPANY_ADDRESS)
    head = f"📍 *Visit Us*\n{OPT.COMPANY_ADDRESS}"
    if phone is None:       # web chat: Map button
        return send_text(to, head, links=[("🗺️ Open in Google Maps", maps)])
    return send_text(to, head + f"\n\n🗺️ Find us on map:\n{maps}")

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")      # drop files in catalog/<catalogue|datasheet|certificate|awards>/
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")

def catalog_docs(cat):
    """[(url_path, filename, title)] = files in catalog/<cat>/ folder  +  files uploaded in Admin -> Documents (only if the file really exists)."""
    out, seen = [], set()
    folder = os.path.join(CATALOG_DIR, cat)
    if os.path.isdir(folder):
        for fn in sorted(os.listdir(folder)):
            if fn.startswith(".") or not os.path.isfile(os.path.join(folder, fn)) or fn.lower().endswith((".txt", ".md")): continue
            out.append((f"/catalog-files/{cat}/{quote(fn)}", fn, os.path.splitext(fn)[0].replace("_", " ").replace("-", " ").strip())); seen.add(fn)
    for d in database().documents.find({"category": cat}):
        fn = d.get("filename", "")
        if fn and fn not in seen and os.path.isfile(os.path.join(UPLOAD_DIR, fn)):
            out.append((f"/files/{quote(fn)}", fn, d.get("title") or fn)); seen.add(fn)
    return out

def ask_catalog(to, phone, prefix=""):
    if phone is None:
        return send_text(to, prefix + "*Catalog & Technical Details*\nChoose one 👇",
                         chips=[(c, str(n)) for n, c in enumerate(CATALOG, 1)] + [HOME], ph="Tap an option above…")
    return send_text(to, prefix + menu_list("*Catalog & Technical Details*", CATALOG))

def ask_doclist(key, to, phone, prefix=""):
    """'Documents' section: lists every document the admin has added (Admin panel -> Documents -> category 'Documents')."""
    docs = catalog_docs("documents")
    if not docs:
        set_state(key, "CATALOG", {})
        return ask_catalog(to, phone, prefix + "ℹ️ No documents have been added yet. Our team will add them soon.\n\n")
    title = prefix + "*Documents*\nTap the document you want to receive:"
    if phone is None:
        return send_text(to, title, chips=[(t_, str(n)) for n, (_, _, t_) in enumerate(docs, 1)] + [("⬅ Back", "back"), HOME],
                         ph="Tap a document above…")
    return send_text(to, prefix + menu_list("*Documents*", [t_ for _, _, t_ in docs]).replace("0 = main menu", "back = catalog menu • 0 = main menu"))

def menu_list(title, items):
    return title + "\n\n" + "\n".join(f"{i+1}. {x}" for i, x in enumerate(items)) + "\n\n_Reply with a number. 0 = main menu_"

def contact_line(doc):
    ph = str(doc.get("phone") or "")
    return f"Our team will call you shortly on *{fmt_phone(ph)}*." if len(ph) >= 10 and ph != "-" else "Our team will contact you shortly."

def fmt_phone(n):
    n = str(n)
    return f"+91 {n[2:7]} {n[7:]}" if len(n) == 12 and n.startswith("91") else "+" + n



FORM_TAG = "@@FORM@@"
HOME = ("🏠 Main menu", "menu")
HOME_ITEMS = [("🏠 Home", "u:home")]                       # only Home after the welcome message
USER_ITEMS = [("✅ Yes, I'm an existing user", "u:exist"), ("🆕 No, I'm a new user", "u:new")]   # shown inside Home
import contextvars
KEY = contextvars.ContextVar("key", default="")          # conversation key of the message being handled
ABOUT_RE = re.compile(r"\babout\s*-?\s*us\b|^(about|aboutus|about company|company|company profile|company info)$")
GST_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

def base_url(phone): return os.getenv("PUBLIC_URL", "").rstrip("/") if phone else ""      # WhatsApp needs public links

# ------------------------------------------------------------------ Home
def welcome(to, phone, cust=None, greet=False):
    name = (cust or {}).get("name")
    head = (f"Welcome back, *{name}*! 🙏" if name else "Welcome to *Shreeji Steeltubes Pvt Ltd* 🙏\n_House of Quality Steel Tubes_")
    return nav(to, phone, head, HOME_ITEMS, home=False, image="/static/logo.png" if greet and phone is None else None)

def ask_existing(to, phone, cust):
    who = f", *{cust['name']}*" if cust and cust.get("name") else ""
    text = f"Welcome back{who}! 🙏\nWhat would you like to do?"
    if phone is None:
        return send_text(to, text, chips=[("📝 Customer Details Form", "details"), ("ℹ️ About Us", "about"), HOME])
    return send_text(to, text + "\n\n1. Customer Details Form\n2. About Us\n\n_0 = main menu_")

# ------------------------------------------------------------------ New-user information steps
def info_step(to, phone, i):
    steps = OPT.FLOW_INFO; st = steps[i]; last = i == len(steps) - 1
    text = st["text"]
    if st.get("list"):
        text += "\n" + "\n".join("• " + x for x in (PRODUCTS if st["list"] == "PRODUCTS" else OPT.BRANDS))
    imgs = [OPT.INTRO_IMAGES[k - 1] for k in st.get("images", []) if 0 < k <= len(OPT.INTRO_IMAGES)]
    vid = ""
    if st.get("video") and OPT.INTRO_VIDEO and (OPT.INTRO_VIDEO.startswith("http") or os.path.exists(os.path.join(HERE, OPT.INTRO_VIDEO.lstrip("/")))):
        vid = OPT.INTRO_VIDEO
    nxt = ("📝 Fill Customer Details ➡", "next") if last else ("Next ➡", "next")
    chips = [nxt] + ([] if last else [("⏭ Skip to Details Form", "skip")]) + ([("⬅ Back", "back")] if i else [])
    if phone is None:
        kw = dict(gallery=[{"img": u, "cap": c} for u, c in imgs], poster=imgs[0][0]) if imgs else {}
        if vid: kw["video"] = vid
        return send_text(to, text, chips=chips, progress=[i + 1, len(steps), st["title"]], ph="Tap Next ➡ to continue…", **kw)
    b = base_url(phone); url = lambda u: u if u.startswith("http") else b + u
    for u, c in imgs: send_image(to, url(u), c)
    if vid: send_video(to, url(vid), "Shreeji SteelTubes – Company Introduction")
    return send_text(to, f"*{st['title']}* ({i + 1}/{len(steps)})\n{text}\n\n_Reply *next* to continue • *skip* = go to the form • *back*_")

# ------------------------------------------------------------------ Forms (schema used for the web form AND the WhatsApp question-by-question flow)
def F(name, label, type="text", req=False, ph="", options=None, val="", **kw):
    d = dict(name=name, label=label, type=type, req=req, ph=ph, val=val or ""); d.update(kw)
    if options: d["options"] = options
    return d

def customer_form(v=None, errs=None):
    v, errs = v or {}, errs or {}
    sec1 = [F("name", "Customer Name", req=True, ph="Full name"),
            F("company", "Company / Firm Name", req=True, ph="Firm name"),
            F("gst", "GST Number", ph="e.g. 27ABCDE1234F1Z5", upper=True, maxlen=15),
            F("uc_code", "UC Code", ph="Your UC code (if any)"),
            F("portal_access", "Portal Access", "select", options=["Yes", "No"])]
    sec2 = []
    for k, lab in OPT.REPRESENTATIVES:
        sec2 += [F(f"{k}_name", f"{lab} – Name", ph="Name"), F(f"{k}_mobile", f"{lab} – Mobile No.", "tel", ph="10-digit mobile", maxlen=14)]
    for s in (sec1, sec2):
        for f in s: f["val"] = v.get(f["name"], f["val"]); f["err"] = errs.get(f["name"], "")
    return {"id": "customer", "title": "📝 Customer Details Form", "submit": "Save & Continue ➡",
            "note": "Please fill in your details. Fields marked * are required.",
            "sections": [{"title": "Customer Details", "fields": sec1},
                         {"title": "Representative Details  (add at least one mobile number)", "fields": sec2}]}

def enquiry_form(v=None, errs=None):
    v, errs = v or {}, errs or {}
    f = [F("product", "Product", "select", True, options=PRODUCTS, ph="Select product"),
         F("size", "Size / NB / OD", req=True, ph="e.g. 50 NB or 60.3 mm OD"),
         F("thickness", "Thickness / Schedule", req=True, ph="e.g. 3.2 mm or Sch 40"),
         F("length", "Length", req=True, ph="e.g. 6 m / 12 m / Random"),
         F("qty", "Quantity", req=True, ph="e.g. 5 MT / 500 pcs"),
         F("grade", "Grade", ph="Select or type", lists=OPT.GRADES, depends="product"),
         F("standard", "Standard", ph="Select or type", lists=OPT.STANDARDS, depends="product"),
         F("brand", "Brand Preference", "select", options=list(OPT.BRANDS) + ["Other"], ph="Select brand"),
         F("delivery_date", "Delivery Date", "date"),
         F("remarks", "Remarks / Special requirement", "textarea", ph="Anything else we should know?")]
    for x in f: x["val"] = v.get(x["name"], x["val"]); x["err"] = errs.get(x["name"], "")
    return {"id": "enquiry", "title": "📋 Enquiry Form", "submit": "Submit Enquiry ✅",
            "note": "Tell us what you need. Fields marked * are required.",
            "sections": [{"title": "Product Requirement", "fields": f}]}

def job_form(v=None, errs=None):
    v, errs = v or {}, errs or {}
    f = [F("name", "Full Name", req=True, ph="Your name"), F("phone", "Mobile No.", "tel", True, ph="10-digit mobile", maxlen=14),
         F("email", "Email", ph="you@example.com"), F("position", "Position applying for", "select", True, options=OPT.JOB_POSITIONS, ph="Select position"),
         F("qualification", "Qualification", ph="e.g. B.Com"), F("experience", "Experience", ph="e.g. 3 years"),
         F("city", "Current City", ph="City"), F("note", "About you / resume link", "textarea", ph="Short note or Google Drive link to your resume")]
    for x in f: x["val"] = v.get(x["name"], ""); x["err"] = errs.get(x["name"], "")
    return {"id": "job", "title": "💼 Job Application Form", "submit": "Submit Application ✅", "note": "Fields marked * are required.", "sections": [{"title": "Application", "fields": f}]}

def vendor_form(v=None, errs=None):
    v, errs = v or {}, errs or {}
    f = [F("company", "Company / Firm Name", req=True, ph="Firm name"), F("name", "Contact Person", req=True, ph="Name"),
         F("phone", "Mobile No.", "tel", True, ph="10-digit mobile", maxlen=14), F("email", "Email", ph="you@example.com"),
         F("gst", "GST Number", ph="e.g. 27ABCDE1234F1Z5", upper=True, maxlen=15), F("city", "City / Address", ph="City"),
         F("supplies", "Products / Services you supply", "textarea", True, ph="e.g. MS pipes, transport, coating"), F("years", "Years in business", ph="e.g. 10")]
    for x in f: x["val"] = v.get(x["name"], ""); x["err"] = errs.get(x["name"], "")
    return {"id": "vendor", "title": "🤝 Vendor Registration", "submit": "Register ✅", "note": "Fields marked * are required.", "sections": [{"title": "Vendor Details", "fields": f}]}

def get_form(fid, v=None, errs=None):
    return {"customer": customer_form, "enquiry": enquiry_form, "job": job_form, "vendor": vendor_form}[fid](v, errs)

def simple_submit(key, to, phone, fid, vals):
    """Job application / vendor registration: validate, save in MongoDB (collections jobs / vendors)."""
    form = get_form(fid); c = {f["name"]: _c(vals.get(f["name"]), 500 if f["type"] == "textarea" else 120) for f in flat(form)}; errs = {}
    for f in flat(form):
        if f["req"] and not c[f["name"]]: errs[f["name"]] = "This field is required"
    c["phone"] = mobile10(c["phone"]) or c["phone"]
    if c["phone"] and not mobile10(c["phone"]): errs["phone"] = "Enter a valid 10-digit mobile number"
    if c.get("gst"):
        c["gst"] = c["gst"].upper().replace(" ", "")
        if not GST_RE.match(c["gst"]): errs["gst"] = "Invalid GST number"
    if errs: return errs
    no = next_no(fid); c.update(no=no, source="whatsapp" if phone else "web", created=now_ist(), status="New")
    database()["jobs" if fid == "job" else "vendors"].insert_one(c)
    n = os.getenv("NOTIFY_NUMBER")
    if n: send_text(n, f"New {'job application' if fid == 'job' else 'vendor registration'} #{no}: {c['name']} {c['phone']}")
    set_state(key, "DONE", {})
    msg = "✅ *Application submitted!* Our HR team will contact you if your profile matches." if fid == "job" else "✅ *Vendor registration received!* Our team will get in touch with you."
    items = [("📋 Enquiry Form", "s:enquiry"), ("📞 Contact Us", "s:contact")] if fid == "job" else [("📋 Request For Quote", "s:enquiry"), ("⬅ Supply Chain", "s:supply")]
    nav(to, phone, msg, items)
    return None

def flat(form): return [f for s in form["sections"] for f in s["fields"]]

def cust_values(cust):
    v = {k: (cust or {}).get(k, "") for k in ("name", "company", "gst", "uc_code", "portal_access")}
    for k, _ in OPT.REPRESENTATIVES:
        r = ((cust or {}).get("reps") or {}).get(k, {})
        v[f"{k}_name"], v[f"{k}_mobile"] = r.get("name", ""), r.get("mobile", "")
    return v

def _c(x, n=120): return re.sub(r"\s+", " ", str(x or "")).strip()[:n]

def mobile10(x):
    d = _digits(x)
    return d[-10:] if len(d) >= 10 else ""

def validate_customer(vals, phone):
    c, errs = {k: _c(vals.get(k)) for k in customer_form_keys()}, {}
    if len(c["name"]) < 2: errs["name"] = "Please enter the customer name"
    if not c["company"]: errs["company"] = "Please enter the company / firm name"
    c["gst"] = c["gst"].upper().replace(" ", "")
    if c["gst"] and not GST_RE.match(c["gst"]): errs["gst"] = "Invalid GST number (15 characters, e.g. 27ABCDE1234F1Z5)"
    reps, first = {}, ""
    for k, _ in OPT.REPRESENTATIVES:
        nm, raw = c[f"{k}_name"], c[f"{k}_mobile"]
        mb = mobile10(raw)
        if raw and not mb: errs[f"{k}_mobile"] = "Enter a valid 10-digit mobile number"
        if nm or mb: reps[k] = {"name": nm, "mobile": mb}
        first = first or mb
    if not first and not phone and not [e for e in errs if e.endswith("_mobile")]:
        errs[f"{OPT.REPRESENTATIVES[0][0]}_mobile"] = "Add at least one mobile number so our team can reach you"
    c["reps"] = reps; c["_first"] = first
    return c, errs

def customer_form_keys():
    return ["name", "company", "gst", "uc_code", "portal_access"] + [f"{k}_{x}" for k, _ in OPT.REPRESENTATIVES for x in ("name", "mobile")]

def validate_enquiry(vals):
    keys = [f["name"] for f in flat(enquiry_form())]
    c, errs = {k: _c(vals.get(k), 500 if k == "remarks" else 120) for k in keys}, {}
    if c["product"] not in PRODUCTS: errs["product"] = "Please select a product"
    for k, lab in (("size", "size"), ("thickness", "thickness / schedule"), ("length", "length"), ("qty", "quantity")):
        if not c[k]: errs[k] = f"Please enter the {lab}"
    if c["delivery_date"]:
        d = parse_date(c["delivery_date"])
        if not d: errs["delivery_date"] = "Invalid date"
        elif d < now_ist().date(): errs["delivery_date"] = "Date can't be in the past"
        else: c["delivery_date"] = d.strftime("%d-%m-%Y")
    return c, errs

def parse_date(t):
    t = str(t).strip().replace(" ", "")
    for fm in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y", "%Y-%m-%d"):
        try: return datetime.strptime(t, fm).date()
        except ValueError: pass
    return None

def reps_text(reps):
    lab = dict(OPT.REPRESENTATIVES)
    return "; ".join(f"{lab[k]}: {r.get('name') or '-'} ({r.get('mobile') or '-'})" for k, r in (reps or {}).items() if k in lab)

def summary_rows(doc):
    rows = [("Company", "company"), ("Product", "product"), ("Size / NB / OD", "size"), ("Thickness / Schedule", "thickness"),
            ("Length", "length"), ("Grade", "grade"), ("Standard", "standard"), ("Brand Preference", "brand"),
            ("Quantity", "qty"), ("Delivery Date", "delivery_date"), ("Remarks", "remarks")]
    return [[a, doc.get(k) if doc.get(k) not in (None, "", "-") else "Not specified"] for a, k in rows]

def summary(doc):
    return "\n".join(f"• {a}: *{v}*" for a, v in summary_rows(doc))

# ------------------------------------------------------------------ show forms
def show_customer_form(key, to, phone, cust, prefix="", vals=None, errs=None):
    if phone is None:
        set_state(key, "CFORM", {})
        return send_text(to, prefix + "Please fill the form below 👇", form=customer_form(vals or cust_values(cust), errs), ph="Fill the form above…")
    return wa_start(key, to, phone, "customer", cust, prefix)

def show_enquiry_form(key, to, phone, cust, prefix="", vals=None, errs=None):
    if phone is not None and vals: return wa_start(key, to, phone, "enquiry", cust, prefix, vals)
    if phone is None:
        set_state(key, "ENQ", {})
        return send_text(to, prefix + "Please fill the *Enquiry Form* below 👇", form=enquiry_form(vals, errs), ph="Fill the form above…")
    return wa_start(key, to, phone, "enquiry", cust, prefix)

def show_form(fid, key, to, phone, cust, prefix="", vals=None, errs=None):
    if fid == "customer": return show_customer_form(key, to, phone, cust, prefix, vals, errs)
    if fid == "enquiry": return show_enquiry_form(key, to, phone, cust, prefix, vals, errs)
    if phone is None:
        set_state(key, "XFORM", {"fid": fid})
        return send_text(to, prefix + "Please fill the form below 👇", form=get_form(fid, vals, errs), ph="Fill the form above…")
    return wa_start(key, to, phone, fid, cust, prefix)

# ------------------------------------------------------------------ submit
def submit(key, to, phone, fid, vals, cust):
    """Validate + save.  Returns errs dict (nothing saved) or None (saved and replies sent)."""
    if fid in ("job", "vendor"): return simple_submit(key, to, phone, fid, vals)
    if fid == "customer":
        c, errs = validate_customer(vals, phone)
        if errs: return errs
        ph = phone or ("91" + c["_first"] if c["_first"] else "-")
        database().customers.update_one({"_id": key},
            {"$set": {"name": c["name"], "company": c["company"], "gst": c["gst"], "uc_code": c["uc_code"],
                      "portal_access": c["portal_access"], "reps": c["reps"], "phone": ph, "location": "-",
                      "source": "whatsapp" if phone else "web"}, "$setOnInsert": {"created": now_ist()}}, upsert=True)
        cust = database().customers.find_one({"_id": key})
        show_enquiry_form(key, to, phone, cust, prefix=f"✅ Thanks, *{c['name']}*! Your details are saved.\n\n")
        return None
    if not cust: return {"product": "Please fill the Customer Details Form first"}
    c, errs = validate_enquiry(vals)
    if errs: return errs
    no = next_no()
    doc = {"no": no, "source": "whatsapp" if phone else "web", "status": "New", "created": now_ist(), "name": cust["name"],
           "phone": cust.get("phone", "-"), "customer_location": "-", "location": "-", "company": cust.get("company", ""),
           "gst": cust.get("gst", ""), "uc_code": cust.get("uc_code", ""), "portal_access": cust.get("portal_access", ""),
           "reps": reps_text(cust.get("reps"))}
    for k in ("product", "size", "thickness", "length", "qty", "grade", "standard", "brand", "delivery_date", "remarks"): doc[k] = c.get(k) or "-"
    database().enquiries.insert_one(doc)
    set_state(key, "DONE", {})
    n = os.getenv("NOTIFY_NUMBER")
    if n: send_text(n, f"New enquiry #{no}: {doc['name']} ({doc['company']}) {doc['phone']} – {doc['product']}, {doc['size']}, qty {doc['qty']}")
    chips = [("📞 Contact Us", "s:contact"), ("📝 New Enquiry", "enquiry"), HOME]
    if phone is None:
        send_text(to, f"✅ *Enquiry #{no} submitted!*\nThanks, *{doc['name']}*.",
                  card={"title": f"📋 Enquiry #{no} – Summary", "rows": summary_rows(doc)})
        send_text(to, contact_line(doc), chips=chips, ph="Tap an option above…")
    else:
        send_text(to, f"✅ *Enquiry #{no} submitted!*\nThanks, *{doc['name']}*.\n\n📋 *Summary*\n{summary(doc)}\n\n{contact_line(doc)}\n\n_Send *enquiry* for a new enquiry • *menu* for the main menu._")
    return None

def on_form(key, to, phone, payload, state, cust):
    """Web form submitted: '@@FORM@@{"id":..., "values":{...}}'"""
    try: p = json.loads(payload[:20000]); fid, vals = p["id"], dict(p["values"])
    except Exception: return welcome(to, phone, cust)
    if fid not in ("customer", "enquiry", "job", "vendor"): return welcome(to, phone, cust)
    errs = submit(key, to, phone, fid, vals, cust)
    if errs:
        msg = "⚠️ Please correct the highlighted field(s) and submit again."
        return show_form(fid, key, to, phone, cust, prefix=msg + "\n\n", vals=vals, errs=errs)

# ------------------------------------------------------------------ WhatsApp: same forms, question by question
def wa_opts(f, vals):
    if f.get("options"): return f["options"]
    if f.get("lists"):
        L = f["lists"]; return L.get(vals.get(f.get("depends", ""))) or L["default"]
    return []

def wa_start(key, to, phone, fid, cust, prefix="", vals0=None):
    form = get_form(fid, cust_values(cust) if fid == "customer" else (vals0 or {}))
    vals = {f["name"]: f["val"] for f in flat(form)}
    set_state(key, "WAFORM", {"fid": fid, "i": 0, "vals": vals})
    return wa_ask(to, form, 0, vals, prefix)

def wa_ask(to, form, i, vals, prefix="", err=""):
    fl = flat(form); f = fl[i]; opts = wa_opts(f, vals)
    q = f"{prefix}{err}*{form['title']}*  ({i + 1}/{len(fl)})\n\n*{f['label']}*" + (" *(required)*" if f["req"] else " _(optional)_")
    if f["type"] == "date": q += "\n_Type a date as DD-MM-YYYY_"
    if opts: q += "\n" + "\n".join(f"{n}. {o}" for n, o in enumerate(opts, 1)) + ("\n_Reply with a number" + (" or type your own_" if f.get("lists") else "_") )
    elif f.get("ph"): q += f"\n_{f['ph']}_"
    cur = vals.get(f["name"])
    if cur: q += f"\n_Saved: {cur} – send *ok* to keep it_"
    if not f["req"]: q += "\n_*skip* to leave blank_"
    return send_text(to, q)

def wa_input(key, to, phone, t, data, cust):
    fid, i, vals = data["fid"], data["i"], data["vals"]
    form = get_form(fid, vals)
    fl = flat(form); f = fl[i]; low = t.lower(); opts = wa_opts(f, vals)
    if low == "skip" and not f["req"]: val = ""
    elif low == "ok" and vals.get(f["name"]): val = vals[f["name"]]
    elif opts and t.isdigit() and 1 <= int(t) <= len(opts): val = opts[int(t) - 1]
    elif f.get("options") and f["type"] == "select": return wa_ask(to, form, i, vals, err="⚠️ Please reply with a number from the list.\n\n")
    else: val = t
    vals[f["name"]] = val
    if i + 1 < len(fl):
        data["i"] = i + 1; set_state(key, "WAFORM", data)
        return wa_ask(to, form, i + 1, vals)
    errs = submit(key, to, phone, fid, vals, cust)
    if not errs: return
    j = next(n for n, x in enumerate(fl) if x["name"] in errs)       # ask the first wrong field again
    data["i"] = j; set_state(key, "WAFORM", data)
    return wa_ask(to, form, j, vals, err=f"⚠️ {errs[fl[j]['name']]}\n\n")

# ------------------------------------------------------------------ NEW FLOW: navigation (web = tap chips, WhatsApp = numbered list)
def nav(to, phone, text, items, home=True, imgs=None, card=None, image=None):
    """Send `text` (+ images / table) followed by tappable options.  items = [(label, token)].  Numbers 1..n pick items on WhatsApp."""
    items = list(items) + ([HOME] if home else [])
    set_state(KEY.get(), "NAV", {"menu": [t for _, t in items]})
    if phone is None:
        kw = {}
        if imgs: kw["gallery"] = [{"img": u, "cap": c} for u, c in imgs]; kw["poster"] = imgs[0][0]
        if card: kw["card"] = card
        if image: kw["image"] = image
        return send_text(to, text, chips=items, ph="Tap an option above…", **kw)
    b = base_url(phone)
    for u, c in (imgs or [])[:4]: send_image(to, b + u, c)
    if card: text += "\n\n" + "\n".join(f"- {a}: *{v}*" for a, v in card["rows"])
    return send_text(to, text + "\n\n" + "\n".join(f"{n}. {l}" for n, (l, _) in enumerate(items, 1)) + "\n\n_Reply with a number • 0 = main menu_")

TOUR = ["about", "products", "resource", "supply", "services", "jobs", "enquiry", "contact"]
SECTIONS = {"about": "ℹ️ About Us", "products": "📦 Products", "resource": "📚 Resource", "supply": "🚚 Supply Chain", "services": "🛠 Services",
            "jobs": "💼 Job Application Form", "enquiry": "📋 Enquiry Form", "contact": "📞 Contact Us"}
ABOUT_BTNS = {"history": ("📜 Our History", None), "vision": ("🎯 Vision, Mission & Values", None), "mgmt": ("👥 Management", None)}
def about_sub(v):          # WhatsApp About Us texts - read live from the official website (falls back to options.py)
    try:
        d = about_fetch.get_about(); return {"history": about_fetch.wa_history, "vision": about_fetch.wa_vision, "mgmt": about_fetch.wa_mgmt}[v](d)
    except Exception:
        return {"history": OPT.ABOUT_HISTORY, "vision": OPT.ABOUT_VISION, "mgmt": OPT.ABOUT_MGMT}[v]
def about_main():
    try: return about_fetch.wa_main(about_fetch.get_about())
    except Exception: return OPT.ABOUT_TEXT
ABOUT_SUB = {"history": None, "vision": None, "mgmt": None}
def nxt(k): return [(f"Next ➡ {SECTIONS[TOUR[TOUR.index(k) + 1]][2:]}", "s:" + TOUR[TOUR.index(k) + 1])] if k != "contact" else []

def open_enquiry(key, to, phone, cust, product=None):
    vals = {"product": product} if product else None
    if cust: return show_enquiry_form(key, to, phone, cust, vals=vals)
    return show_customer_form(key, to, phone, cust, prefix="Please share your details first 👇\n\n")

def route(key, to, phone, cust, tk):
    """Resolve a navigation token ('s:about', 'p:gi', 'r:cat' ...) - returns True if handled."""
    k, _, v = tk.partition(":")
    if tk == "u:home":      # after the customer has visited Home -> ask Existing / New user
        nav(to, phone, "*Are you an existing user?*", USER_ITEMS, home=False); return True
    if tk == "u:exist": open_enquiry(key, to, phone, cust); return True
    if tk == "u:new":       # new user -> show ONLY the About Us button (details open after the tap)
        nav(to, phone, "Welcome to *Shreeji SteelTubes* 🙏\nTap *About Us* to know more about our company 👇", [("ℹ️ About Us", "s:about")], home=False); return True
    if k == "a" and v in ABOUT_SUB:
        text = about_sub(v)
        back = [(l, "a:" + x) for x, (l, _) in ABOUT_BTNS.items() if x != v]
        nav(to, phone, text, back + [("⬅ About Us", "s:about")] + nxt("about")); return True
    if tk == "s:all": nav(to, phone, "*All sections* 👇", [(l, "s:" + x) for x, l in SECTIONS.items()]); return True
    if k == "s" and v in SECTIONS:
        if v == "about":
            if phone is None:   # web chat: the About Us page (like Home) opens full screen; chat only gets a short note
                # web chat: About Us opens full screen by itself; chat gets ONE button -> Products
                items = [("📦 Products", "s:products")]
                set_state(KEY.get(), "NAV", {"menu": [t for _, t in items]})
                send_text(to, "Tap below to see our products 👇", chips=items, ph="Tap an option above…")
            else:
                nav(to, phone, about_main(), [(l, "a:" + x) for x, (l, _) in ABOUT_BTNS.items()] + nxt(v), imgs=OPT.ABOUT_IMAGES)
        elif v == "products":
            nav(to, phone, "*Our Products*\nChoose a product 👇", [(OPT.PRODUCT_INFO[x][0], "p:" + x) for x in ("gi", "ms", "hollow", "seamless", "gp")] + nxt(v))
        elif v == "resource":
            nav(to, phone, "*Resource*\nChoose one 👇", [("📘 Product Catalogue", "r:cat"), ("🏅 Certificates", "r:cert"), ("✅ Quality Assurance", "r:qa"), ("🖼 Gallery", "r:gal"), ("⬇ Downloads", "r:dl")] + nxt(v))
        elif v == "supply":
            nav(to, phone, "*Supply Chain*\nChoose one 👇", [("🏭 Authorised Dealers & Distributors", "v:dealers"), ("💬 Request For Quote", "v:rfq"), ("🤝 Vendor Registration", "v:vendor"), ("👥 Clients", "v:clients")] + nxt(v))
        elif v == "services":
            nav(to, phone, OPT.SERVICES_TEXT, nxt(v), imgs=[(OPT.H + "services.jpg", "Our Services")])
        elif v == "jobs": show_form("job", key, to, phone, cust)
        elif v == "enquiry": open_enquiry(key, to, phone, cust)
        else:
            rows = contact_rows()
            if phone: send_text(to, "*Contact Details (Department wise)*\n" + "".join(f"\n*{r['department']}*\n" + (r['name'] + "\n" if r['name'] else "") + (f"📞 {r['phone']}\n" if r['phone'] else "") + (f"✉️ {r['email']}\n" if r['email'] else "") for r in rows))
            else: send_text(to, "*Contact Details (Department wise)*", contacts=rows)
            visit_card(to, phone)
            nav(to, phone, "👆 Tap below to continue", [("⬅ All sections", "s:all"), ("📋 Enquiry Form", "s:enquiry")])
        return True
    if k == "p" and phone is None and v in ("gi", "ms", "hollow", "seamless", "gp"):   # web: the product page opens full screen; chat shows the 5 product buttons again
        nav(to, phone, "*Our Products*\nChoose a product 👇", [(OPT.PRODUCT_INFO[x][0], "p:" + x) for x in ("gi", "ms", "hollow", "seamless", "gp")] + nxt("products")); return True
    if k == "p" and v in OPT.PRODUCT_INFO:
        lab, img, title, rows, desc, prod = OPT.PRODUCT_INFO[v]
        items = [("📋 Enquire this product", "q:" + v)]
        if v == "ms": items = [("MS Black Pipe", "p:msblack"), ("Large Diameter Pipe", "p:ldp")] + items
        nav(to, phone, f"*{lab}*\n{desc}", items + [("⬅ Products", "s:products")] + nxt("products"), imgs=[(OPT.H + img, lab)], card={"title": title, "rows": rows}); return True
    if k == "q" and v in OPT.PRODUCT_INFO: open_enquiry(key, to, phone, cust, OPT.PRODUCT_INFO[v][5]); return True
    if k == "r":
        back = [("⬅ Resource", "s:resource")] + nxt("resource")
        if v == "qa": nav(to, phone, OPT.QA_TEXT, back, imgs=[(OPT.H + "awards.jpg", "Quality & Certifications")])
        elif v == "gal":
            for u, fn, t_ in catalog_docs("awards"): send_document(to, base_url(phone) + u, fn, t_)
            nav(to, phone, "*Gallery*" + ("\n_Swipe to see more_" if phone is None else ""), back, imgs=OPT.GALLERY_IMAGES)
        elif v in ("cat", "cert"):
            docs = catalog_docs("catalogue" if v == "cat" else "certificate")
            for u, fn, t_ in docs: send_document(to, base_url(phone) + u, fn, t_)
            nav(to, phone, ("✅ Sent! 👆" if docs else "ℹ️ This will be shared soon."), back)
        elif v == "dl":
            docs = catalog_docs("datasheet") + catalog_docs("documents")
            if not docs: nav(to, phone, "ℹ️ No downloads added yet. Our team will add them soon.", back)
            else: nav(to, phone, "*Downloads*\nTap a file to receive it 👇", [(t_, f"d:{n}") for n, (_, _, t_) in enumerate(docs)] + back)
        return True
    if k == "d" and v.isdigit():
        docs = catalog_docs("datasheet") + catalog_docs("documents")
        if int(v) < len(docs): u, fn, t_ = docs[int(v)]; send_document(to, base_url(phone) + u, fn, t_)
        return route(key, to, phone, cust, "r:dl")
    if k == "v":
        back = [("⬅ Supply Chain", "s:supply")] + nxt("supply")
        if v == "dealers": nav(to, phone, OPT.DEALERS_TEXT, back, imgs=[(OPT.H + "ldp.jpg", "Authorised distributor of leading brands")])
        elif v == "clients": nav(to, phone, OPT.CLIENTS_TEXT, back, imgs=[(OPT.H + "industries.jpg", "Industries we serve")])
        elif v == "rfq": open_enquiry(key, to, phone, cust)
        elif v == "vendor": show_form("vendor", key, to, phone, cust)
        return True
    return False

# ------------------------------------------------------------------ main entry
def handle(key, text, phone=None):
    """key = 'web:<session>' or 'wa:<phone>'.  phone = known number (WhatsApp only)."""
    to = phone or key; KEY.set(key)
    raw = (text or "").strip()
    state, data = get_state(key)
    cust = database().customers.find_one({"_id": key})
    if raw.startswith(FORM_TAG): return on_form(key, to, phone, raw[len(FORM_TAG):], state, cust)
    t = raw[:500]; low = t.lower()
    if state != "WAFORM":     # typed words open the same page as the tab/button: "about us" -> About Us page, "home" -> Home page
        w = re.sub(r"[^\w\s-]", "", low).strip()
        if ABOUT_RE.search(w): t = "s:about"
        elif w == "home": t = "u:home"

    if low in ("hi", "hello", "hey", "menu", "0", "start"):
        set_state(key, "MENU", {}); return welcome(to, phone, cust, greet=low in GREET)
    if low == "enquiry": return open_enquiry(key, to, phone, cust)
    if state == "NAV" and t.isdigit() and 1 <= int(t) <= len((data or {}).get("menu", [])): t = data["menu"][int(t) - 1]
    if ":" in t and route(key, to, phone, cust, t): return
    if state == "CFORM": return show_customer_form(key, to, phone, cust, prefix="👆 ")
    if state == "ENQ":   return show_enquiry_form(key, to, phone, cust, prefix="👆 ")
    if state == "XFORM": return show_form(data.get("fid", "job"), key, to, phone, cust, prefix="👆 ")
    if state == "WAFORM": return wa_input(key, to, phone, t, data, cust)
    return welcome(to, phone, cust)
