import os
import uuid
from io import BytesIO

import qrcode
import qrcode.constants
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from models import db, Location, Spool, UsageLog

spools_bp = Blueprint("spools", __name__)

MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "Nylon", "PC", "Other"]


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]
    )


def save_photo(file, spool_id):
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{spool_id}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)

    img = Image.open(file.stream)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    img.thumbnail((800, 800), Image.LANCZOS)
    img.save(path, "JPEG", quality=85)
    return filename


def delete_photo(filename):
    if filename:
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(path):
            os.remove(path)


@spools_bp.route("/")
def index():
    material_filter = request.args.get("material", "")
    location_filter = request.args.get("location_id", "")
    variant_filter = request.args.get("variant", "").strip()
    search = request.args.get("q", "").strip()
    replacement_of = request.args.get("replacement_of", "").strip()

    query = Spool.query

    if replacement_of:
        ref = db.session.get(Spool, int(replacement_of))
        if ref:
            query = query.filter(
                Spool.manufacturer.ilike(ref.manufacturer),
                Spool.material == ref.material,
                Spool.color.ilike(ref.color),
                db.func.lower(db.func.ifnull(Spool.variant, ""))
                == (ref.variant or "").lower(),
                Spool.opened == False,  # noqa: E712
                Spool.id != ref.id,
            )
    else:
        if material_filter:
            query = query.filter(Spool.material == material_filter)
        if variant_filter:
            query = query.filter(Spool.variant.ilike(variant_filter))
        if location_filter:
            if location_filter == "none":
                query = query.filter(Spool.location_id.is_(None))
            else:
                query = query.filter(Spool.location_id == int(location_filter))
        if search:
            like = f"%{search}%"
            query = query.filter(
                db.or_(
                    Spool.manufacturer.ilike(like),
                    Spool.color.ilike(like),
                    Spool.notes.ilike(like),
                )
            )

    spools = query.order_by(Spool.manufacturer, Spool.color).all()
    locations = Location.query.order_by(Location.name).all()

    # Collect distinct variants for filter dropdown
    all_variants = sorted(
        {s.variant for s in Spool.query.with_entities(Spool.variant).all() if s.variant},
        key=str.lower,
    )

    # Build replacement count map: opened spool id → count of matching unopened spools
    all_spools = spools if replacement_of else Spool.query.all()
    unopened_index: dict[tuple, int] = {}
    for s in Spool.query.filter_by(opened=False).all():
        key = (
            s.manufacturer.lower(),
            s.material,
            (s.color or "").lower(),
            (s.variant or "").lower(),
        )
        unopened_index[key] = unopened_index.get(key, 0) + 1

    replacements: dict[int, int] = {}
    for spool in spools:
        if spool.opened:
            key = (
                spool.manufacturer.lower(),
                spool.material,
                (spool.color or "").lower(),
                (spool.variant or "").lower(),
            )
            count = unopened_index.get(key, 0)
            if count > 0:
                replacements[spool.id] = count

    all_spools = Spool.query.order_by(Spool.manufacturer, Spool.color).all()

    from models import Printer
    from monitor.models_extension import PrintJob
    printer_widgets = []
    for p in Printer.query.order_by(Printer.name).all():
        estimated_remaining = None
        if p.last_state == "PRINTING" and p.spool and p.last_progress is not None:
            current_job = PrintJob.query.filter_by(
                printer_id=p.id, finished_at=None
            ).order_by(PrintJob.started_at.desc()).first()
            if current_job and current_job.filament_total_g:
                estimated_remaining = max(
                    0, round(p.spool.remaining_g - current_job.filament_total_g)
                )
        printer_widgets.append({"printer": p, "estimated_remaining": estimated_remaining})

    return render_template(
        "index.html",
        spools=spools,
        locations=locations,
        materials=MATERIALS,
        all_variants=all_variants,
        material_filter=material_filter,
        variant_filter=variant_filter,
        location_filter=location_filter,
        search=search,
        replacement_of=replacement_of,
        replacements=replacements,
        printer_widgets=printer_widgets,
        all_spools=all_spools,
    )


@spools_bp.route("/new", methods=["GET", "POST"])
def new_spool():
    locations = Location.query.order_by(Location.name).all()
    if request.method == "POST":
        errors = _validate_form(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("spool_form.html", locations=locations, materials=MATERIALS, form=request.form)

        spool = Spool(
            manufacturer=request.form["manufacturer"].strip(),
            material=request.form["material"],
            color=request.form["color"].strip(),
            color_hex=request.form.get("color_hex", "").strip() or None,
            variant=request.form.get("variant", "").strip() or None,
            total_weight_g=int(request.form["total_weight_g"]),
            remaining_g=int(request.form["total_weight_g"]),
            opened="opened" in request.form,
            notes=request.form.get("notes", "").strip() or None,
            location_id=int(request.form["location_id"]) if request.form.get("location_id") else None,
        )
        db.session.add(spool)
        db.session.flush()

        file = request.files.get("photo")
        if file and file.filename and allowed_file(file.filename):
            spool.photo_filename = save_photo(file, spool.id)

        db.session.commit()
        flash("Spule erfolgreich angelegt.", "success")
        return redirect(url_for("spools.spool_detail", spool_id=spool.id))

    return render_template("spool_form.html", locations=locations, materials=MATERIALS, form={})


@spools_bp.route("/<int:spool_id>")
def spool_detail(spool_id):
    spool = db.get_or_404(Spool, spool_id)
    logs = UsageLog.query.filter_by(spool_id=spool_id).order_by(UsageLog.logged_at.desc()).all()
    replacement_count = 0
    if spool.opened:
        replacement_count = Spool.query.filter(
            Spool.manufacturer.ilike(spool.manufacturer),
            Spool.material == spool.material,
            Spool.color.ilike(spool.color),
            db.func.lower(db.func.ifnull(Spool.variant, "")) == (spool.variant or "").lower(),
            Spool.opened == False,  # noqa: E712
            Spool.id != spool.id,
        ).count()
    return render_template("spool_detail.html", spool=spool, logs=logs, replacement_count=replacement_count)


@spools_bp.route("/<int:spool_id>/edit", methods=["GET", "POST"])
def edit_spool(spool_id):
    spool = db.get_or_404(Spool, spool_id)
    locations = Location.query.order_by(Location.name).all()

    if request.method == "POST":
        errors = _validate_form(request.form, editing=True)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template(
                "spool_form.html", locations=locations, materials=MATERIALS, form=request.form, spool=spool
            )

        spool.manufacturer = request.form["manufacturer"].strip()
        spool.material = request.form["material"]
        spool.color = request.form["color"].strip()
        spool.color_hex = request.form.get("color_hex", "").strip() or None
        spool.variant = request.form.get("variant", "").strip() or None
        spool.total_weight_g = int(request.form["total_weight_g"])
        spool.remaining_g = max(0, min(int(request.form.get("remaining_g", spool.remaining_g)), spool.total_weight_g))
        spool.opened = "opened" in request.form
        spool.notes = request.form.get("notes", "").strip() or None
        spool.location_id = int(request.form["location_id"]) if request.form.get("location_id") else None

        file = request.files.get("photo")
        if file and file.filename and allowed_file(file.filename):
            delete_photo(spool.photo_filename)
            spool.photo_filename = save_photo(file, spool.id)

        db.session.commit()
        flash("Spule aktualisiert.", "success")
        return redirect(url_for("spools.spool_detail", spool_id=spool.id))

    return render_template(
        "spool_form.html", locations=locations, materials=MATERIALS, form=spool, spool=spool
    )


@spools_bp.route("/<int:spool_id>/delete", methods=["POST"])
def delete_spool(spool_id):
    spool = db.get_or_404(Spool, spool_id)
    delete_photo(spool.photo_filename)
    db.session.delete(spool)
    db.session.commit()
    flash("Spule gelöscht.", "info")
    return redirect(url_for("spools.index"))


@spools_bp.route("/<int:spool_id>/clone")
def clone_spool(spool_id):
    src = db.get_or_404(Spool, spool_id)
    prefill = dict(
        manufacturer=src.manufacturer,
        material=src.material,
        color=src.color,
        color_hex=src.color_hex or "",
        variant=src.variant or "",
        total_weight_g=src.total_weight_g,
        notes=src.notes or "",
        location_id=src.location_id,
        opened=False,
    )
    locations = Location.query.order_by(Location.name).all()
    return render_template(
        "spool_form.html",
        locations=locations,
        materials=MATERIALS,
        form=prefill,
        clone_of=src,
    )


@spools_bp.route("/<int:spool_id>/qr.png")
def qr_image(spool_id):
    db.get_or_404(Spool, spool_id)
    url = request.host_url.rstrip("/") + f"/spools/{spool_id}"

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=3600)


@spools_bp.route("/<int:spool_id>/qr-label")
def qr_label(spool_id):
    spool = db.get_or_404(Spool, spool_id)
    return render_template("qr_label.html", spool=spool)


def _validate_form(form, editing=False):
    errors = []
    if not form.get("manufacturer", "").strip():
        errors.append("Hersteller ist erforderlich.")
    if form.get("material") not in MATERIALS:
        errors.append("Ungültiges Material.")
    if not form.get("color", "").strip():
        errors.append("Farbe ist erforderlich.")
    try:
        w = int(form.get("total_weight_g", 0))
        if w <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Gesamtgewicht muss eine positive Zahl sein.")
    return errors
