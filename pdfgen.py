import io, os
from xml.sax.saxutils import escape
from PIL import Image as PILImage
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

LOGO = os.path.join(os.path.dirname(__file__), "static", "logo.png")
BLUE, ORANGE = colors.HexColor("#2E3F96"), colors.HexColor("#EE6A24")
SS = getSampleStyleSheet()

def _logo(width):
    w, h = PILImage.open(LOGO).size
    return Image(LOGO, width=width, height=width * h / w)

def _p(text, size=8, bold=False, color=colors.black):
    st = ParagraphStyle("x", parent=SS["Normal"], fontSize=size, leading=size + 2,
                        fontName="Helvetica-Bold" if bold else "Helvetica", textColor=color)
    return Paragraph(escape(str(text if text not in (None, "") else "-")), st)

def _d(e):
    c = e.get("created"); return c.strftime("%d-%m-%Y %H:%M") if c else "-"

def single(e):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=30, bottomMargin=30)
    rows = [("Enquiry No.", f"#{e['no']}"), ("Date", _d(e)), ("Customer Name", e["name"]),
            ("Company / Firm", e.get("company")), ("GST Number", e.get("gst")), ("UC Code", e.get("uc_code")), ("Portal Access", e.get("portal_access")), ("Contact No.", e["phone"]), ("Representatives", e.get("reps")), ("Product", e["product"]), ("Size / NB / OD", e["size"]),
            ("Thickness / Schedule", e["thickness"]), ("Length", e["length"]), ("Grade", e["grade"]),
            ("Standard", e["standard"]), ("Brand preference", e["brand"]), ("Quantity", e["qty"]),
            ("Required delivery date", e["delivery_date"]), ("Remarks", e.get("remarks")),
            ("Source", e.get("source", "-")), ("Status", e.get("status", "-"))]
    t = Table([[_p(a, 10, True, BLUE), _p(b, 10)] for a, b in rows], colWidths=[170, 340])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .5, colors.lightgrey),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF1FB")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    doc.build([_logo(230), Spacer(1, 12), _p("PRODUCT ENQUIRY", 16, True, ORANGE), Spacer(1, 10), t])
    return buf.getvalue()

def listing(rows, title="Product Enquiries"):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    heads = ["#", "Date", "Name", "Contact", "Company", "Product", "Size", "Thk/Sch", "Length", "Grade", "Std",
             "Brand", "Qty", "Remarks", "Del. date", "Status"]
    widths = [22, 54, 58, 62, 40, 74, 52, 48, 44, 44, 44, 44, 44, 62, 56, 40]
    data = [[_p(h, 7, True, colors.white) for h in heads]]
    for e in rows:
        vals = [e["no"], _d(e), e["name"], e["phone"], e.get("company"), e["product"], e["size"], e["thickness"], e["length"],
                e["grade"], e["standard"], e["brand"], e["qty"], e.get("remarks"), e["delivery_date"], e["status"]]
        data.append([_p(v, 7) for v in vals])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BLUE), ("GRID", (0, 0), (-1, -1), .4, colors.lightgrey),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FC")]),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    head = Table([[_logo(150), _p(f"{title}  ({len(rows)})", 14, True, ORANGE)]], colWidths=[170, 500])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    doc.build([head, Spacer(1, 8), t])
    return buf.getvalue()
