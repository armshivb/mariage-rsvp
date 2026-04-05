import os
import csv
import io
import secrets
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from pathlib import Path

BASE_DIR = Path(__file__).parent
from datetime import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db, init_db
from models import Guest, Setting

# ── Configuration ──────────────────────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "mariage2026")
COUPLE_NAMES   = os.environ.get("COUPLE_NAMES",   "Keke & Lucie")
WEDDING_DATE   = os.environ.get("WEDDING_DATE",   "Samedi 5 Septembre 2026")
WEDDING_PLACE  = os.environ.get("WEDDING_PLACE",  "Cayenne, Guyane")

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.on_event("startup")
def startup():
    init_db()

# ── Contexte commun aux templates ──────────────────────────────────────────────
def ctx(request: Request, **kwargs):
    return {
        "request": request,
        "couple": COUPLE_NAMES,
        "date": WEDDING_DATE,
        "place": WEDDING_PLACE,
        **kwargs,
    }

# ── Page d'accueil / saisie du code ────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", ctx(request))

@app.post("/", response_class=HTMLResponse)
def index_post(request: Request, code: str = Form(...), db: Session = Depends(get_db)):
    guest = db.query(Guest).filter(Guest.code == code.strip().upper()).first()
    if not guest:
        return templates.TemplateResponse("index.html", ctx(request, error="Code invalide. Vérifiez votre invitation."))
    return RedirectResponse(f"/rsvp/{code.strip().upper()}", status_code=302)

# ── Page RSVP de l'invité ──────────────────────────────────────────────────────
@app.get("/rsvp/{code}", response_class=HTMLResponse)
def rsvp_get(code: str, request: Request, db: Session = Depends(get_db)):
    guest = db.query(Guest).filter(Guest.code == code.upper()).first()
    if not guest:
        return RedirectResponse("/")
    return templates.TemplateResponse("rsvp.html", ctx(request, guest=guest))

@app.post("/rsvp/{code}", response_class=HTMLResponse)
def rsvp_post(
    code: str,
    request: Request,
    response: str = Form(...),
    plus_one: int = Form(0),
    message: str = Form(""),
    db: Session = Depends(get_db),
):
    guest = db.query(Guest).filter(Guest.code == code.upper()).first()
    if not guest:
        return RedirectResponse("/")
    guest.response  = response   # "yes" ou "no"
    guest.plus_one  = max(0, plus_one)
    guest.message   = message.strip()
    guest.updated_at = datetime.utcnow()
    db.commit()
    return templates.TemplateResponse("merci.html", ctx(request, guest=guest))

# ── Admin ──────────────────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
def admin_login(request: Request):
    return templates.TemplateResponse("admin_login.html", ctx(request))

@app.post("/admin", response_class=HTMLResponse)
def admin_login_post(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse("admin_login.html", ctx(request, error="Mot de passe incorrect."))
    return RedirectResponse("/admin/dashboard", status_code=302)

def get_setting(db: Session, key: str, default: str = "") -> str:
    s = db.query(Setting).filter(Setting.key == key).first()
    return s.value if s else default

def set_setting(db: Session, key: str, value: str):
    s = db.query(Setting).filter(Setting.key == key).first()
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()

@app.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    guests = db.query(Guest).order_by(Guest.nom, Guest.prenom).all()
    yes_count = sum(1 + g.plus_one for g in guests if g.response == "yes")
    no_count  = sum(1 for g in guests if g.response == "no")
    pending   = sum(1 for g in guests if g.response == "pending")
    repondu   = sum(1 for g in guests if g.response != "pending")

    expected_str = get_setting(db, "expected_guests", "0")
    expected = int(expected_str) if expected_str.isdigit() else 0
    taux_reponse = round(repondu / expected * 100) if expected > 0 else None
    taux_presence = round(yes_count / expected * 100) if expected > 0 else None

    return templates.TemplateResponse("admin.html", ctx(
        request,
        guests=guests,
        yes_count=yes_count,
        no_count=no_count,
        pending=pending,
        repondu=repondu,
        expected=expected,
        taux_reponse=taux_reponse,
        taux_presence=taux_presence,
    ))

@app.post("/admin/set-expected")
def set_expected(expected: int = Form(...), db: Session = Depends(get_db)):
    set_setting(db, "expected_guests", str(max(0, expected)))
    return RedirectResponse("/admin/dashboard", status_code=302)

# ── Admin : ajouter des invités ────────────────────────────────────────────────
@app.post("/admin/add-guest")
def add_guest(prenom: str = Form(...), nom: str = Form(...), db: Session = Depends(get_db)):
    code = secrets.token_hex(3).upper()
    while db.query(Guest).filter(Guest.code == code).first():
        code = secrets.token_hex(3).upper()
    guest = Guest(prenom=prenom.strip(), nom=nom.strip(), code=code)
    db.add(guest)
    db.commit()
    return RedirectResponse("/admin/dashboard", status_code=302)

@app.get("/admin/export-pdf")
def export_pdf(db: Session = Depends(get_db)):
    guests = db.query(Guest).order_by(Guest.nom, Guest.prenom).all()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"],
                                 fontSize=20, textColor=colors.HexColor("#2c2416"),
                                 spaceAfter=4)
    sub_style = ParagraphStyle("sub", parent=styles["Normal"],
                               fontSize=11, textColor=colors.HexColor("#7a6a55"),
                               alignment=TA_CENTER, spaceAfter=16)

    elements = []
    elements.append(Paragraph(f"Mariage – {COUPLE_NAMES}", title_style))
    elements.append(Paragraph(WEDDING_DATE, sub_style))

    # Statistiques
    yes_count = sum(1 + g.plus_one for g in guests if g.response == "yes")
    no_count  = sum(1 for g in guests if g.response == "no")
    pending   = sum(1 for g in guests if g.response == "pending")
    stats_text = f"Présents : {yes_count}   |   Absents : {no_count}   |   En attente : {pending}   |   Total invités : {len(guests)}"
    elements.append(Paragraph(stats_text, sub_style))
    elements.append(Spacer(1, 0.4*cm))

    # Tableau
    STATUT = {"yes": "Présent(e) ✓", "no": "Absent(e) ✗", "pending": "En attente…"}
    COULEURS = {"yes": colors.HexColor("#e8f5ea"), "no": colors.HexColor("#fdecea"), "pending": colors.HexColor("#fef3e2")}

    data = [["Prénom", "Nom", "Code", "Statut", "Accompagnants", "Message"]]
    for g in guests:
        data.append([
            g.prenom,
            g.nom,
            g.code,
            STATUT.get(g.response, "–"),
            str(g.plus_one) if g.response == "yes" else "–",
            (g.message[:40] + "…") if g.message and len(g.message) > 40 else (g.message or "–"),
        ])

    col_widths = [3*cm, 3.5*cm, 2.2*cm, 3.2*cm, 3*cm, None]
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style = TableStyle([
        # En-tête
        ("BACKGROUND",   (0,0), (-1,0), colors.HexColor("#b8986a")),
        ("TEXTCOLOR",    (0,0), (-1,0), colors.white),
        ("FONTNAME",     (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,0), 9),
        ("BOTTOMPADDING",(0,0), (-1,0), 8),
        ("TOPPADDING",   (0,0), (-1,0), 8),
        # Corps
        ("FONTSIZE",     (0,1), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, colors.HexColor("#fdfaf5")]),
        ("GRID",         (0,0), (-1,-1), 0.4, colors.HexColor("#ecdfc8")),
        ("VALIGN",       (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,1), (-1,-1), 6),
        ("BOTTOMPADDING",(0,1), (-1,-1), 6),
    ])

    # Couleur par statut sur la colonne Statut
    for i, g in enumerate(guests, start=1):
        style.add("BACKGROUND", (3, i), (3, i), COULEURS.get(g.response, colors.white))

    table.setStyle(style)
    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf",
                             headers={"Content-Disposition": "attachment; filename=invites-mariage.pdf"})

@app.post("/admin/import-csv")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    text = content.decode("utf-8-sig")  # utf-8-sig gère le BOM des fichiers Excel
    reader = csv.reader(io.StringIO(text))
    added = 0
    for row in reader:
        # Ignore les lignes vides et les entêtes
        if not row or row[0].strip().lower() in ("prénom", "prenom", "firstname"):
            continue
        prenom = row[0].strip() if len(row) > 0 else ""
        nom    = row[1].strip() if len(row) > 1 else ""
        if not prenom and not nom:
            continue
        code = secrets.token_hex(3).upper()
        while db.query(Guest).filter(Guest.code == code).first():
            code = secrets.token_hex(3).upper()
        db.add(Guest(prenom=prenom, nom=nom, code=code))
        added += 1
    db.commit()
    return RedirectResponse(f"/admin/dashboard?imported={added}", status_code=302)

@app.post("/admin/delete-guest/{guest_id}")
def delete_guest(guest_id: int, db: Session = Depends(get_db)):
    guest = db.query(Guest).filter(Guest.id == guest_id).first()
    if guest:
        db.delete(guest)
        db.commit()
    return RedirectResponse("/admin/dashboard", status_code=302)
