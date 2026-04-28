from datetime import datetime
from models import db


class PrintJob(db.Model):
    __tablename__ = "print_jobs"

    id = db.Column(db.Integer, primary_key=True)
    printer_job_id = db.Column(db.Integer, nullable=True, index=True)
    printer_id = db.Column(db.Integer, db.ForeignKey("printers.id", ondelete="SET NULL"), nullable=True)
    filename = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    duration_minutes = db.Column(db.Integer, nullable=True)
    filament_total_mm = db.Column(db.Float, nullable=True)
    filament_total_g = db.Column(db.Float, nullable=True)
    final_state = db.Column(db.String(20), nullable=True)
    spool_id = db.Column(db.Integer, db.ForeignKey("spools.id", ondelete="SET NULL"), nullable=True)

    def __repr__(self):
        return f"<PrintJob #{self.id} {self.display_name} {self.final_state}>"
