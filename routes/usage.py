from flask import Blueprint, jsonify, request
from models import db, Spool, UsageLog

usage_bp = Blueprint("usage", __name__)


@usage_bp.route("/log", methods=["POST"])
def log_usage():
    data = request.get_json(silent=True) or {}
    spool_id = data.get("spool_id")
    used_g = data.get("used_g")
    note = (data.get("note") or "").strip() or None

    if not spool_id or used_g is None:
        return jsonify({"error": "spool_id und used_g sind erforderlich"}), 400

    try:
        used_g = int(used_g)
    except (TypeError, ValueError):
        return jsonify({"error": "used_g muss eine Zahl sein"}), 400

    if used_g <= 0:
        return jsonify({"error": "used_g muss größer 0 sein"}), 400

    spool = db.get_or_404(Spool, spool_id)

    if used_g > spool.remaining_g:
        return jsonify({"error": f"Nicht genug Restmenge ({spool.remaining_g}g verfügbar)"}), 400

    log = UsageLog(spool_id=spool.id, used_g=used_g, note=note)
    db.session.add(log)
    db.session.commit()

    db.session.refresh(spool)
    return jsonify(
        {
            "log_id": log.id,
            "remaining_g": spool.remaining_g,
            "remaining_percent": spool.remaining_percent,
            "logged_at": log.logged_at.strftime("%d.%m.%Y %H:%M"),
            "note": log.note or "",
            "used_g": log.used_g,
        }
    )


@usage_bp.route("/<int:log_id>/delete", methods=["POST"])
def delete_log(log_id):
    log = db.get_or_404(UsageLog, log_id)
    spool_id = log.spool_id
    db.session.delete(log)
    db.session.commit()

    spool = db.get_or_404(Spool, spool_id)
    db.session.refresh(spool)
    return jsonify(
        {
            "remaining_g": spool.remaining_g,
            "remaining_percent": spool.remaining_percent,
        }
    )
