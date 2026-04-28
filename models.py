from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

db = SQLAlchemy()


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    type = db.Column(db.Enum("shelf", "box"), nullable=False, default="shelf")
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    spools = db.relationship("Spool", backref="location", lazy=True, foreign_keys="Spool.location_id")

    def __repr__(self):
        return f"<Location {self.name}>"


class Spool(db.Model):
    __tablename__ = "spools"

    id = db.Column(db.Integer, primary_key=True)
    manufacturer = db.Column(db.String(100), nullable=False)
    material = db.Column(
        db.Enum("PLA", "PETG", "ABS", "ASA", "TPU", "Nylon", "PC", "Other"),
        nullable=False,
    )
    color = db.Column(db.String(100), nullable=False)
    color_hex = db.Column(db.String(7), nullable=True)
    total_weight_g = db.Column(db.Integer, nullable=False)
    remaining_g = db.Column(db.Integer, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    photo_filename = db.Column(db.String(255), nullable=True)
    variant = db.Column(db.String(50), nullable=True)
    opened = db.Column(db.Boolean, nullable=False, default=False)
    location_id = db.Column(db.Integer, db.ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    usage_logs = db.relationship("UsageLog", backref="spool", lazy=True, cascade="all, delete-orphan")

    @property
    def remaining_percent(self):
        if self.total_weight_g == 0:
            return 0
        return round(self.remaining_g / self.total_weight_g * 100)

    @property
    def progress_color(self):
        pct = self.remaining_percent
        if pct > 50:
            return "success"
        if pct > 20:
            return "warning"
        return "danger"

    def __repr__(self):
        return f"<Spool {self.manufacturer} {self.material} {self.color}>"


class UsageLog(db.Model):
    __tablename__ = "usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    spool_id = db.Column(db.Integer, db.ForeignKey("spools.id", ondelete="CASCADE"), nullable=False)
    used_g = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255), nullable=True)
    logged_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<UsageLog spool={self.spool_id} used={self.used_g}g>"


class Printer(db.Model):
    __tablename__ = "printers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    ip = db.Column(db.String(100), nullable=False)
    api_key = db.Column(db.String(255), nullable=False)
    poll_interval = db.Column(db.Integer, nullable=False, default=30)
    spool_id = db.Column(db.Integer, db.ForeignKey("spools.id", ondelete="SET NULL"), nullable=True)

    # Status-Cache — wird vom Monitor-Thread befüllt
    last_state = db.Column(db.String(20), nullable=True)
    last_polled_at = db.Column(db.DateTime, nullable=True)
    last_filename = db.Column(db.String(255), nullable=True)
    last_progress = db.Column(db.Float, nullable=True)
    last_time_remaining = db.Column(db.Integer, nullable=True)  # Sekunden

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    spool = db.relationship("Spool", foreign_keys=[spool_id],
                            backref=db.backref("assigned_printer", uselist=False))

    @property
    def state_label(self):
        return {
            "IDLE": "Bereit",
            "PRINTING": "Druckt",
            "PAUSED": "Pausiert",
            "FINISHED": "Fertig",
            "STOPPED": "Gestoppt",
            "ERROR": "Fehler",
        }.get(self.last_state or "", "Unbekannt")

    @property
    def state_badge(self):
        return {
            "IDLE": "success",
            "PRINTING": "primary",
            "PAUSED": "warning",
            "FINISHED": "success",
            "STOPPED": "danger",
            "ERROR": "danger",
        }.get(self.last_state or "", "secondary")

    @property
    def time_remaining_fmt(self):
        if not self.last_time_remaining:
            return None
        h, m = divmod(self.last_time_remaining // 60, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"

    def __repr__(self):
        return f"<Printer {self.name} {self.ip}>"


@event.listens_for(UsageLog, "after_insert")
def decrement_remaining(mapper, connection, target):
    connection.execute(
        db.text(
            "UPDATE spools SET remaining_g = GREATEST(0, remaining_g - :used),"
            " opened = TRUE WHERE id = :sid"
        ),
        {"used": target.used_g, "sid": target.spool_id},
    )


@event.listens_for(UsageLog, "after_delete")
def restore_remaining(mapper, connection, target):
    connection.execute(
        db.text(
            "UPDATE spools SET remaining_g = LEAST(total_weight_g, remaining_g + :used) WHERE id = :sid"
        ),
        {"used": target.used_g, "sid": target.spool_id},
    )
