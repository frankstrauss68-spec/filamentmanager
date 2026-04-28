import os
from flask import Flask, redirect, url_for
from dotenv import load_dotenv
from models import db

load_dotenv()


def create_app():
    app = Flask(__name__)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")

    db_user = os.environ.get("DB_USER", "root")
    db_password = os.environ.get("DB_PASSWORD", "")
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "3306")
    db_name = os.environ.get("DB_NAME", "filamentmanager")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    )
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config["UPLOAD_FOLDER"] = os.path.join(basedir, "static", "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    app.config["ALLOWED_EXTENSIONS"] = {"png", "jpg", "jpeg", "webp"}

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    from routes.spools import spools_bp
    from routes.locations import locations_bp
    from routes.usage import usage_bp
    from routes.printers import printers_bp

    app.register_blueprint(spools_bp, url_prefix="/spools")
    app.register_blueprint(locations_bp, url_prefix="/locations")
    app.register_blueprint(usage_bp, url_prefix="/usage")
    app.register_blueprint(printers_bp, url_prefix="/printers")

    @app.route("/")
    def index():
        return redirect(url_for("spools.index"))

    with app.app_context():
        import monitor.models_extension  # noqa: F401
        db.create_all()
        _migrate_db()

    from monitor.prusalink import start_monitor
    start_monitor(app)

    return app


def _migrate_db():
    """Idempotent schema migrations for columns added after initial deploy."""
    with db.engine.connect() as conn:
        def existing_cols(table):
            r = conn.execute(db.text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS"
                " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ), {"t": table})
            return {row[0] for row in r}

        # spools table
        spool_cols = existing_cols("spools")
        spool_ddl = {
            "variant": "ALTER TABLE spools ADD COLUMN variant VARCHAR(50) DEFAULT NULL",
            "opened":  "ALTER TABLE spools ADD COLUMN opened BOOLEAN NOT NULL DEFAULT FALSE",
        }
        for col, ddl in spool_ddl.items():
            if col not in spool_cols:
                conn.execute(db.text(ddl))

        # locations: migrate 'printer' type → 'shelf', then narrow enum
        conn.execute(db.text("UPDATE locations SET type='shelf' WHERE type='printer'"))
        conn.execute(db.text(
            "ALTER TABLE locations MODIFY type ENUM('shelf','box') NOT NULL DEFAULT 'shelf'"
        ))

        # print_jobs table
        pj_cols = existing_cols("print_jobs")
        if "printer_id" not in pj_cols:
            conn.execute(db.text(
                "ALTER TABLE print_jobs ADD COLUMN printer_id INT DEFAULT NULL"
            ))

        conn.commit()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=False)
