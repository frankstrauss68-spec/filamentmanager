from flask import Blueprint, flash, redirect, render_template, request, url_for
from models import db, Printer, Spool

printers_bp = Blueprint("printers", __name__)


@printers_bp.route("/")
def index():
    printers = Printer.query.order_by(Printer.name).all()
    spools = Spool.query.order_by(Spool.manufacturer, Spool.color).all()
    return render_template("printers.html", printers=printers, spools=spools)


@printers_bp.route("/new", methods=["GET", "POST"])
def new_printer():
    spools = Spool.query.order_by(Spool.manufacturer, Spool.color).all()
    if request.method == "POST":
        errors = _validate(request.form)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("printer_form.html", spools=spools, form=request.form)

        printer = Printer(
            name=request.form["name"].strip(),
            ip=request.form["ip"].strip(),
            api_key=request.form["api_key"].strip(),
            poll_interval=int(request.form.get("poll_interval") or 30),
            spool_id=int(request.form["spool_id"]) if request.form.get("spool_id") else None,
        )
        db.session.add(printer)
        db.session.commit()
        flash(f'Drucker „{printer.name}" angelegt. Bitte App neu starten, um den Monitor zu aktivieren.', "success")
        return redirect(url_for("printers.index"))

    return render_template("printer_form.html", spools=spools, form={})


@printers_bp.route("/<int:printer_id>/edit", methods=["GET", "POST"])
def edit_printer(printer_id):
    printer = db.get_or_404(Printer, printer_id)
    spools = Spool.query.order_by(Spool.manufacturer, Spool.color).all()

    if request.method == "POST":
        errors = _validate(request.form, printer_id=printer_id)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("printer_form.html", spools=spools, form=request.form, printer=printer)

        printer.name = request.form["name"].strip()
        printer.ip = request.form["ip"].strip()
        printer.api_key = request.form["api_key"].strip()
        printer.poll_interval = int(request.form.get("poll_interval") or 30)
        printer.spool_id = int(request.form["spool_id"]) if request.form.get("spool_id") else None
        db.session.commit()
        flash("Drucker aktualisiert.", "success")
        return redirect(url_for("printers.index"))

    return render_template("printer_form.html", spools=spools, form=printer, printer=printer)


@printers_bp.route("/<int:printer_id>/delete", methods=["POST"])
def delete_printer(printer_id):
    printer = db.get_or_404(Printer, printer_id)
    db.session.delete(printer)
    db.session.commit()
    flash("Drucker gelöscht.", "info")
    return redirect(url_for("printers.index"))


@printers_bp.route("/<int:printer_id>/load-spool", methods=["POST"])
def load_spool(printer_id):
    printer = db.get_or_404(Printer, printer_id)
    if printer.last_state in ("PRINTING", "PAUSED"):
        flash(f'„{printer.name}" druckt gerade — Spule kann nicht gewechselt werden.', "warning")
        return redirect(url_for("spools.index"))
    spool_id = request.form.get("spool_id")
    printer.spool_id = int(spool_id) if spool_id else None
    db.session.commit()
    return redirect(url_for("spools.index"))


def _validate(form, printer_id=None):
    errors = []
    name = (form.get("name") or "").strip()
    ip = (form.get("ip") or "").strip()
    api_key = (form.get("api_key") or "").strip()
    if not name:
        errors.append("Name ist erforderlich.")
    if not ip:
        errors.append("IP-Adresse ist erforderlich.")
    if not api_key:
        errors.append("API-Key ist erforderlich.")
    if name:
        existing = Printer.query.filter(Printer.name == name).first()
        if existing and existing.id != printer_id:
            errors.append("Drucker-Name bereits vergeben.")
    return errors
