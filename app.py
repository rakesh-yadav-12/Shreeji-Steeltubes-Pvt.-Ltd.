import os, io, csv, uuid
from functools import wraps
from dotenv import load_dotenv
load_dotenv()
from bson import ObjectId
from flask import (Flask, request, session, redirect, url_for, render_template, flash,
                   send_from_directory, abort, Response, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import qrcode
import db, bot, whatsapp, pdfgen, about_fetch
from db import database, norm, ser

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
UPLOADS = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOADS, exist_ok=True)
db.init(os.getenv("ADMIN_PHONE", "9930988307"), os.getenv("ADMIN_PASSWORD", "admin123"), os.getenv("ADMIN_NAME", "Admin"))

def site_url():
    return (os.getenv("PUBLIC_URL") or request.url_root).rstrip("/")

def qr_target():
    return os.getenv("QR_URL") or site_url() + "/"

# ---------- public: chatbot home (opened by QR scan) ----------
@app.after_request
def _no_cache(r):          # phones must always get the latest chat page / replies (no stale cached copy)
    if request.path in ("/", "/chat/send"): r.headers["Cache-Control"] = "no-store, max-age=0"
    return r

@app.route("/")
def home():
    return render_template("home.html")

@app.get("/products/data")
def products_data():       # product details for the Home page popup (same data the chat used to show)
    import options as OPT
    out = {k: {"label": v[0], "img": OPT.H + v[1], "title": v[2], "rows": [list(r) for r in v[3]], "desc": v[4]}
           for k, v in OPT.PRODUCT_INFO.items()}
    return jsonify(out)

@app.get("/about/data")
def about_data():          # About Us details read live from https://www.shreejisteeltubes.com/about-us/ (cached, falls back to built-in copy)
    r = jsonify(about_fetch.get_about(force=request.args.get("refresh") == "1" and bool(session.get("admin_id"))))
    r.headers["Cache-Control"] = "no-store, max-age=0"; return r

@app.post("/chat/send")
def chat_send():
    sid = session.setdefault("sid", uuid.uuid4().hex)
    box = []; tok = whatsapp.collector.set(box)
    try: bot.handle("web:" + sid, (request.get_json(silent=True) or {}).get("text", "hi"))
    finally: whatsapp.collector.reset(tok)
    return jsonify(replies=box)

@app.route("/qr.png")
def qr_png():
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    q.add_data(qr_target()); q.make(fit=True)
    buf = io.BytesIO(); q.make_image(fill_color="#2E3F96", back_color="white").save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/catalog-files/<cat>/<path:name>")   # public: files kept in the project's catalog/<category>/ folders
def catalog_files(cat, name):
    return send_from_directory(os.path.join(os.path.dirname(__file__), "catalog", secure_filename(cat)), name,
                               as_attachment=bool(request.args.get("dl")))

@app.route("/files/<path:name>")   # public: chat / WhatsApp download catalog files from here
def files(name): return send_from_directory(UPLOADS, name, as_attachment=bool(request.args.get("dl")))

# ---------- WhatsApp webhook (optional) ----------
@app.get("/webhook")
def verify():
    if request.args.get("hub.verify_token") == os.getenv("WA_VERIFY_TOKEN"):
        return request.args.get("hub.challenge", ""), 200
    return "forbidden", 403

@app.post("/webhook")
def incoming():
    try:
        for e in (request.get_json(silent=True) or {}).get("entry", []):
            for ch in e.get("changes", []):
                for m in ch["value"].get("messages", []):
                    phone = norm(m["from"])
                    text = m.get("text", {}).get("body", "") if m["type"] == "text" else "menu"
                    bot.handle("wa:" + phone, text, phone=phone)
    except Exception as ex:
        print("webhook error:", ex)
    return "ok", 200

# ---------- admin auth ----------
def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        if "admin_id" not in session: return redirect(url_for("admin_login"))
        return f(*a, **k)
    return w

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        a = database().admins.find_one({"phone": norm(request.form["phone"]), "active": True})
        if a and check_password_hash(a["password_hash"], request.form["password"]):
            session.update(admin_id=str(a["_id"]), admin_name=a["name"])
            return redirect(url_for("admin_enquiries"))
        flash("Invalid contact number or password")
    return render_template("login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None); session.pop("admin_name", None); return redirect(url_for("home"))

@app.route("/admin")
@admin_required
def admin_index(): return redirect(url_for("admin_enquiries"))

# ---------- admin: enquiries (view, PDF, CSV) ----------
def find_enquiries():
    f = {}
    st, s = request.args.get("status", ""), request.args.get("q", "").strip()
    if st: f["status"] = st
    if s:
        import re; rx = {"$regex": re.escape(s), "$options": "i"}
        f["$or"] = [{k: rx} for k in ("name", "phone", "company", "gst", "product", "size")]
    return list(database().enquiries.find(f).sort("no", -1))

@app.route("/admin/enquiries")
@admin_required
def admin_enquiries():
    return render_template("admin_enquiries.html", enq=find_enquiries(),
                           status=request.args.get("status", ""), q=request.args.get("q", ""))

@app.post("/admin/enquiries/<int:no>/status")
@admin_required
def set_status(no):
    database().enquiries.update_one({"no": no}, {"$set": {"status": request.form["status"]}})
    return redirect(request.referrer or url_for("admin_enquiries"))

@app.post("/admin/enquiries/<int:no>/delete")
@admin_required
def delete_enquiry(no):
    database().enquiries.delete_one({"no": no}); return redirect(url_for("admin_enquiries"))

@app.route("/admin/enquiries/export.csv")
@admin_required
def export_csv():
    rows = find_enquiries()
    cols = ["no", "created", "name", "company", "gst", "uc_code", "portal_access", "reps", "phone", "product", "size", "thickness", "length", "grade", "standard",
            "brand", "qty", "delivery_date", "remarks", "status", "source"]
    out = io.StringIO(); w = csv.writer(out); w.writerow(cols)
    for e in rows:
        w.writerow([e["created"].strftime("%Y-%m-%d %H:%M") if c == "created" and e.get("created") else e.get(c, "") for c in cols])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=enquiries.csv"})

@app.route("/admin/enquiries/export.pdf")
@admin_required
def export_pdf():
    return Response(pdfgen.listing(find_enquiries()), mimetype="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=enquiries.pdf"})

@app.route("/admin/enquiries/<int:no>.pdf")
@admin_required
def enquiry_pdf(no):
    e = database().enquiries.find_one({"no": no}) or abort(404)
    return Response(pdfgen.single(e), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=enquiry_{no}.pdf"})

# ---------- admin: customers (everyone who started a chat: name, contact, location) ----------
@app.route("/admin/customers")
@admin_required
def admin_customers():
    q, f = request.args.get("q", "").strip(), {}
    if q:
        import re; rx = {"$regex": re.escape(q), "$options": "i"}
        f["$or"] = [{k: rx} for k in ("name", "phone", "company", "gst", "uc_code")]
    rows = list(database().customers.find(f).sort("created", -1))
    for c in rows: c["enq_count"] = database().enquiries.count_documents({"phone": c["phone"]})
    return render_template("admin_customers.html", rows=rows, q=q)

# ---------- admin: job applications + vendor registrations (from the chatbot) ----------
def _list_page(col, title):
    rows = list(database()[col].find().sort("no", -1))
    return render_template("admin_list.html", rows=rows, title=title, col=col)

@app.route("/admin/jobs")
@admin_required
def admin_jobs(): return _list_page("jobs", "Job Applications")

@app.route("/admin/vendors")
@admin_required
def admin_vendors(): return _list_page("vendors", "Vendor Registrations")

# ---------- admin: contacts, catalog files, QR, admins ----------
@app.route("/admin/contacts", methods=["GET", "POST"])
@admin_required
def admin_contacts():
    if request.method == "POST":
        f = request.form
        if f.get("delete_id"):
            database().contacts.delete_one({"_id": ObjectId(f["delete_id"])})
            flash("Deleted")
        else:
            d = {k: f.get(k, "").strip() for k in ("department", "name", "designation", "phone", "email", "direct_line")}
            d["department"] = d["department"].upper()
            if f.get("id"): database().contacts.update_one({"_id": ObjectId(f["id"])}, {"$set": d})
            else: database().contacts.insert_one(d)
            flash("Saved")
    groups = {}                                    # department -> contacts (department-wise view)
    for r in ser(database().contacts.find().sort("_id", 1)):
        groups.setdefault((r.get("department") or "OTHER").strip().upper(), []).append(r)
    return render_template("admin_contacts.html", groups=groups, depts=list(groups.keys()))

@app.route("/admin/documents", methods=["GET", "POST"])
@admin_required
def admin_docs():
    if request.method == "POST":
        f = request.files["file"]; name = secure_filename(f.filename)
        if name:
            f.save(os.path.join(UPLOADS, name))
            database().documents.insert_one({"category": request.form["category"], "title": request.form["title"], "filename": name})
            flash("Uploaded")
    return render_template("admin_docs.html", docs=ser(database().documents.find().sort("_id", -1)))

@app.post("/admin/documents/<id>/delete")
@admin_required
def delete_doc(id):
    database().documents.delete_one({"_id": ObjectId(id)}); return redirect(url_for("admin_docs"))

@app.route("/admin/qr")
@admin_required
def admin_qr():
    return render_template("admin_qr.html", target=qr_target())

@app.route("/admin/admins", methods=["GET", "POST"])
@admin_required
def admin_admins():
    a = database().admins
    if request.method == "POST":
        if request.form.get("action") == "password":
            a.update_one({"_id": ObjectId(session["admin_id"])},
                         {"$set": {"password_hash": generate_password_hash(request.form["password"])}})
            flash("Your password was changed")
        else:
            try:
                a.insert_one({"name": request.form["name"], "phone": norm(request.form["phone"]),
                              "password_hash": generate_password_hash(request.form["password"]),
                              "active": True, "created": db.now_ist()})
                flash("Admin added")
            except Exception: flash("This contact number already exists")
    return render_template("admin_admins.html", admins=ser(a.find()), me=session["admin_id"])

@app.post("/admin/admins/<id>/toggle")
@admin_required
def toggle_admin(id):
    if id != session["admin_id"]:
        a = database().admins.find_one({"_id": ObjectId(id)})
        database().admins.update_one({"_id": a["_id"]}, {"$set": {"active": not a["active"]}})
    return redirect(url_for("admin_admins"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
