from flask import Blueprint, jsonify, render_template, request
from models import db, Location, Spool

locations_bp = Blueprint("locations", __name__)


@locations_bp.route("/")
def index():
    locations = (
        db.session.query(Location, db.func.count(Spool.id).label("spool_count"))
        .outerjoin(Spool, Spool.location_id == Location.id)
        .group_by(Location.id)
        .order_by(Location.name)
        .all()
    )
    return render_template("locations.html", locations=locations)


@locations_bp.route("/new", methods=["POST"])
def create_location():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    loc_type = data.get("type", "shelf")
    description = (data.get("description") or "").strip() or None

    if not name:
        return jsonify({"error": "Name ist erforderlich"}), 400
    if loc_type not in ("shelf", "box", "printer"):
        return jsonify({"error": "Ungültiger Typ"}), 400
    if Location.query.filter_by(name=name).first():
        return jsonify({"error": "Name bereits vergeben"}), 409

    loc = Location(name=name, type=loc_type, description=description)
    db.session.add(loc)
    db.session.commit()
    return jsonify({"id": loc.id, "name": loc.name, "type": loc.type, "description": loc.description}), 201


@locations_bp.route("/<int:loc_id>/edit", methods=["POST"])
def update_location(loc_id):
    loc = db.get_or_404(Location, loc_id)
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    loc_type = data.get("type", loc.type)
    description = (data.get("description") or "").strip() or None

    if not name:
        return jsonify({"error": "Name ist erforderlich"}), 400
    if loc_type not in ("shelf", "box", "printer"):
        return jsonify({"error": "Ungültiger Typ"}), 400

    existing = Location.query.filter(Location.name == name, Location.id != loc_id).first()
    if existing:
        return jsonify({"error": "Name bereits vergeben"}), 409

    loc.name = name
    loc.type = loc_type
    loc.description = description
    db.session.commit()
    return jsonify({"id": loc.id, "name": loc.name, "type": loc.type, "description": loc.description})


@locations_bp.route("/<int:loc_id>/delete", methods=["POST"])
def delete_location(loc_id):
    loc = db.get_or_404(Location, loc_id)
    spool_count = Spool.query.filter_by(location_id=loc_id).count()
    if spool_count > 0:
        return jsonify({"error": f"Bitte zuerst die {spool_count} Spule(n) umlagern"}), 409
    db.session.delete(loc)
    db.session.commit()
    return jsonify({"ok": True})
