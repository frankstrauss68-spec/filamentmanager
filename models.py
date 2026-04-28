from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

db = SQLAlchemy()


class Location(db.Model):
    __tablename__ = "locations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    type = db.Column(db.Enum("shelf", "box", "printer"), nullable=False, default="shelf")
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
