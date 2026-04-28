"""
PrusaLink background monitor.

Loads all Printer records from the DB at startup and spawns one thread
per printer. Each thread polls its printer every poll_interval seconds,
updates Printer.last_* status fields, and handles PrintJob lifecycle.
"""

import logging
import struct
import threading
import time
import zlib
from datetime import datetime

import requests
from requests.auth import HTTPDigestAuth

logger = logging.getLogger(__name__)

ACTIVE_STATES = {"PRINTING", "PAUSED"}
TERMINAL_STATES = {"FINISHED", "STOPPED", "ERROR"}


# ---------------------------------------------------------------------------
# BGcode / GCode metadata parsing
# ---------------------------------------------------------------------------

def _parse_kv_block(text, prefix=""):
    filament_g = filament_mm = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if prefix and not line.startswith(prefix):
            continue
        line = line[len(prefix):]
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        try:
            if "filament used [g]" in key:
                filament_g = float(val)
            elif "filament used [mm]" in key:
                filament_mm = float(val)
        except ValueError:
            pass
    return filament_g, filament_mm


def _parse_bgcode(data: bytes):
    if not data.startswith(b"BGCODE"):
        return None, None
    pos = 7  # magic (6) + version (1)
    best_g = best_mm = None
    while pos < len(data):
        try:
            if pos + 8 > len(data):
                break
            block_type, compression = struct.unpack_from("<HH", data, pos)
            pos += 4
            uncompressed_size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if compression != 0:
                if pos + 4 > len(data):
                    break
                compressed_size = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            else:
                compressed_size = uncompressed_size
            pos += 4  # checksum
            block_bytes = data[pos: pos + compressed_size]
            pos += compressed_size
            if block_type not in (2, 3, 4):
                continue
            if compression == 1:
                text = zlib.decompress(block_bytes).decode("utf-8", errors="replace")
            elif compression == 0:
                text = block_bytes.decode("utf-8", errors="replace")
            else:
                continue
            g, mm = _parse_kv_block(text)
            best_g = best_g if best_g is not None else g
            best_mm = best_mm if best_mm is not None else mm
            if best_g is not None and best_mm is not None:
                break
        except (struct.error, zlib.error) as exc:
            logger.debug("BGcode block parse error: %s", exc)
            break
    return best_g, best_mm


def _extract_filament_meta(data: bytes):
    if data[:6] == b"BGCODE":
        return _parse_bgcode(data)
    text = data.decode("utf-8", errors="replace")
    return _parse_kv_block(text, prefix="; ")


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class PrusaLinkMonitor:
    def __init__(self, app, printer_id: int):
        self.app = app
        self.printer_id = printer_id
        self._prev_state: str | None = None
        self._current_job_db_id: int | None = None

    def start(self):
        t = threading.Thread(
            target=self._run, daemon=True,
            name=f"prusalink-{self.printer_id}"
        )
        t.start()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self):
        with self.app.app_context():
            from models import Printer
            printer = db_get(Printer, self.printer_id)
            if not printer:
                return
            logger.info("Monitor started for printer #%d (%s, %s)",
                        self.printer_id, printer.name, printer.ip)
            base_url = f"http://{printer.ip}"
            auth = HTTPDigestAuth("maker", printer.api_key)
            poll_interval = printer.poll_interval

        while True:
            try:
                self._poll(base_url, auth)
            except Exception:
                logger.exception("Unhandled error in monitor for printer #%d", self.printer_id)
            time.sleep(poll_interval)

    def _poll(self, base_url: str, auth):
        status = _get(base_url, "/api/v1/status", auth)
        if status is None:
            return

        state = ((status.get("printer") or {}).get("state") or "UNKNOWN").upper()
        job_data = None
        if state in ACTIVE_STATES | TERMINAL_STATES:
            job_data = _get_job(base_url, auth)

        progress = (job_data or {}).get("progress")
        filename = ((job_data or {}).get("file") or {}).get("name")
        time_remaining = ((status.get("job") or {}).get("time_remaining"))

        # Update status cache in DB
        with self.app.app_context():
            from models import db, Printer
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text(
                    "UPDATE printers SET last_state=:s, last_polled_at=:t,"
                    " last_filename=:f, last_progress=:p, last_time_remaining=:r"
                    " WHERE id=:id"
                ), {
                    "s": state, "t": datetime.utcnow(),
                    "f": filename, "p": progress, "r": time_remaining,
                    "id": self.printer_id,
                })
                conn.commit()

        prev = self._prev_state
        if state == "PRINTING" and prev not in ACTIVE_STATES:
            self._on_print_start(base_url, auth, job_data)
        elif prev in ACTIVE_STATES and state in TERMINAL_STATES:
            self._on_print_finish(state, job_data, progress or 100)

        self._prev_state = state

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _on_print_start(self, base_url, auth, job_data):
        with self.app.app_context():
            from monitor.models_extension import PrintJob
            from models import db

            filename = display_name = None
            printer_job_id = None
            filament_g = filament_mm = None

            if job_data:
                printer_job_id = job_data.get("id")
                file_info = job_data.get("file") or {}
                filename = file_info.get("name")
                display_name = file_info.get("display_name") or filename
                meta = file_info.get("meta") or {}
                filament_g = meta.get("filament_used_g")
                filament_mm = meta.get("filament_used_mm")

                if filament_g is None:
                    dl_url = (file_info.get("refs") or {}).get("download")
                    if dl_url:
                        raw = _download(base_url, dl_url, auth)
                        if raw:
                            filament_g, filament_mm = _extract_filament_meta(raw)

            job = PrintJob(
                printer_id=self.printer_id,
                printer_job_id=printer_job_id,
                filename=filename,
                display_name=display_name,
                filament_total_g=filament_g,
                filament_total_mm=filament_mm,
            )
            db.session.add(job)
            db.session.commit()
            self._current_job_db_id = job.id
            logger.info("Print started on printer #%d: %s", self.printer_id, display_name)

    def _on_print_finish(self, final_state: str, job_data, progress: float):
        with self.app.app_context():
            from monitor.models_extension import PrintJob
            from models import db, Spool, Printer, UsageLog

            job = db.session.get(PrintJob, self._current_job_db_id) \
                if self._current_job_db_id else None
            printer = db.session.get(Printer, self.printer_id)

            meta = ((job_data or {}).get("file") or {}).get("meta") or {}
            total_g = (job.filament_total_g if job else None) or meta.get("filament_used_g")
            total_mm = (job.filament_total_mm if job else None) or meta.get("filament_used_mm")

            if total_g is not None:
                factor = max(0.0, min(float(progress), 100.0)) / 100.0
                used_g = total_g if final_state == "FINISHED" else round(total_g * factor, 2)
                used_mm = total_mm if final_state == "FINISHED" else (
                    round(total_mm * factor, 2) if total_mm is not None else None
                )
            else:
                used_g = used_mm = None

            now = datetime.utcnow()
            if job:
                job.finished_at = now
                job.final_state = final_state
                if job.started_at:
                    job.duration_minutes = max(0, int((now - job.started_at).total_seconds() / 60))
                if used_g is not None:
                    job.filament_total_g = used_g
                if used_mm is not None:
                    job.filament_total_mm = used_mm

            # Auto-deduct from assigned spool
            deducted_spool_id = None
            if used_g and used_g > 0 and printer and printer.spool_id:
                spool = db.session.get(Spool, printer.spool_id)
                if spool:
                    used_g_int = max(1, round(used_g))
                    if used_g_int <= spool.remaining_g:
                        note = "PrusaLink: {} ({})".format(
                            (job.display_name if job else None) or "Unbekannt",
                            final_state,
                        )
                        db.session.add(UsageLog(spool_id=spool.id, used_g=used_g_int, note=note))
                        deducted_spool_id = spool.id
                        logger.info("Auto-deducted %dg from spool #%d", used_g_int, spool.id)
                    else:
                        logger.warning("Spool #%d has only %dg, needed %dg",
                                       spool.id, spool.remaining_g, used_g_int)

            if job:
                job.spool_id = deducted_spool_id
            db.session.commit()
            self._current_job_db_id = None
            logger.info("Print finished (%s) on printer #%d: used_g=%s",
                        final_state, self.printer_id, used_g)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(base_url, path, auth):
    try:
        r = requests.get(base_url + path, auth=auth, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("PrusaLink %s%s failed: %s", base_url, path, exc)
        return None


def _get_job(base_url, auth):
    try:
        r = requests.get(base_url + "/api/v1/job", auth=auth, timeout=10)
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("PrusaLink job fetch failed: %s", exc)
        return None


def _download(base_url, url, auth):
    if url.startswith("/"):
        url = base_url + url
    try:
        r = requests.get(url, auth=auth, timeout=60, stream=True)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException as exc:
        logger.warning("File download failed: %s", exc)
        return None


def db_get(model, pk):
    from models import db
    return db.session.get(model, pk)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def start_monitor(app):
    with app.app_context():
        from models import Printer
        printers = Printer.query.all()

    if not printers:
        app.logger.info("PrusaLink monitor: no printers configured, monitor inactive.")
        return

    for printer in printers:
        monitor = PrusaLinkMonitor(app, printer.id)
        monitor.start()
        app.logger.info(
            "PrusaLink monitor started for '%s' (%s), polling every %ds",
            printer.name, printer.ip, printer.poll_interval
        )
