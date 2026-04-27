"""
PrusaLink background monitor.

Polls the printer every POLL_INTERVAL seconds and:
- Creates a PrintJob when printing starts
- Closes the PrintJob and optionally deducts filament from the assigned
  spool when printing finishes or is stopped
"""

import logging
import os
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
    """Extract filament_g and filament_mm from a key=value text block."""
    filament_g = None
    filament_mm = None
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
    """
    Parse a BGcode binary file.

    File layout:
        magic   : 6 bytes  ("BGCODE")
        version : 1 byte
        blocks  : repeated until EOF
            type             : uint16 LE
            compression      : uint16 LE  (0=none, 1=deflate/zlib)
            uncompressed_size: uint32 LE
            compressed_size  : uint32 LE  (only when compression != 0)
            checksum         : uint32 LE  (CRC32, skipped)
            data             : compressed_size bytes

    Metadata blocks can be type 2 (SlicerMetadata), 3 (PrinterMetadata),
    or 4 (per user spec). We scan all three and return the first hit.
    """
    MAGIC = b"BGCODE"
    if not data.startswith(MAGIC):
        return None, None

    pos = len(MAGIC) + 1  # skip magic + version byte
    best_g = None
    best_mm = None

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
            pos += 4  # skip checksum

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
            if g is not None and best_g is None:
                best_g = g
            if mm is not None and best_mm is None:
                best_mm = mm
            if best_g is not None and best_mm is not None:
                break
        except struct.error:
            break
        except zlib.error as exc:
            logger.debug("BGcode zlib error in block type=%d: %s", block_type, exc)

    return best_g, best_mm


def _parse_gcode(data: bytes):
    text = data.decode("utf-8", errors="replace")
    return _parse_kv_block(text, prefix="; ")


def _extract_filament_meta(data: bytes):
    if data[:6] == b"BGCODE":
        return _parse_bgcode(data)
    return _parse_gcode(data)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class PrusaLinkMonitor:
    def __init__(self, app, ip: str, api_key: str, poll_interval: int = 30):
        self.app = app
        self.base_url = f"http://{ip}"
        self.auth = HTTPDigestAuth("maker", api_key)
        self.poll_interval = poll_interval

        self._prev_state: str | None = None
        self._current_job_db_id: int | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="prusalink-monitor"
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self):
        logger.info("PrusaLink monitor thread started")
        while True:
            try:
                self._poll()
            except Exception:
                logger.exception("Unhandled error in monitor poll")
            time.sleep(self.poll_interval)

    def _poll(self):
        status = self._get("/api/v1/status")
        if status is None:
            return

        state = (
            (status.get("printer") or {}).get("state") or "UNKNOWN"
        ).upper()

        job_data = None
        if state in ACTIVE_STATES | TERMINAL_STATES:
            job_data = self._get_job()

        prev = self._prev_state

        if state == "PRINTING" and prev not in ACTIVE_STATES:
            self._on_print_start(job_data)

        elif prev in ACTIVE_STATES and state in TERMINAL_STATES:
            progress = (job_data or {}).get("progress", 100)
            self._on_print_finish(state, job_data, progress)

        self._prev_state = state

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    def _get(self, path: str):
        try:
            r = requests.get(
                self.base_url + path, auth=self.auth, timeout=10
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("PrusaLink %s failed: %s", path, exc)
            return None

    def _get_job(self):
        try:
            r = requests.get(
                self.base_url + "/api/v1/job", auth=self.auth, timeout=10
            )
            if r.status_code == 204:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as exc:
            logger.warning("PrusaLink /api/v1/job failed: %s", exc)
            return None

    def _download_file(self, url: str) -> bytes | None:
        if url.startswith("/"):
            url = self.base_url + url
        try:
            r = requests.get(
                url, auth=self.auth, timeout=60, stream=True
            )
            r.raise_for_status()
            return r.content
        except requests.exceptions.RequestException as exc:
            logger.warning("File download failed (%s): %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _on_print_start(self, job_data: dict | None):
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
                    download_url = (file_info.get("refs") or {}).get("download")
                    if download_url:
                        raw = self._download_file(download_url)
                        if raw:
                            filament_g, filament_mm = _extract_filament_meta(raw)

            job = PrintJob(
                printer_job_id=printer_job_id,
                filename=filename,
                display_name=display_name,
                filament_total_g=filament_g,
                filament_total_mm=filament_mm,
            )
            db.session.add(job)
            db.session.commit()
            self._current_job_db_id = job.id
            logger.info(
                "Print started: %s (printer_job_id=%s, filament_g=%s)",
                display_name,
                printer_job_id,
                filament_g,
            )

    def _on_print_finish(
        self, final_state: str, job_data: dict | None, progress: float
    ):
        with self.app.app_context():
            from monitor.models_extension import PrintJob
            from models import db, Spool, Location, UsageLog

            job = (
                db.session.get(PrintJob, self._current_job_db_id)
                if self._current_job_db_id
                else None
            )

            # Compute actual filament used
            meta = ((job_data or {}).get("file") or {}).get("meta") or {}
            total_g = (job.filament_total_g if job else None) or meta.get(
                "filament_used_g"
            )
            total_mm = (job.filament_total_mm if job else None) or meta.get(
                "filament_used_mm"
            )

            if total_g is not None:
                if final_state == "FINISHED":
                    used_g = total_g
                    used_mm = total_mm
                else:  # STOPPED — prorate by progress
                    factor = max(0.0, min(float(progress), 100.0)) / 100.0
                    used_g = round(total_g * factor, 2)
                    used_mm = round(total_mm * factor, 2) if total_mm is not None else None
            else:
                used_g = used_mm = None

            now = datetime.utcnow()

            if job:
                job.finished_at = now
                job.final_state = final_state
                if job.started_at:
                    job.duration_minutes = max(
                        0, int((now - job.started_at).total_seconds() / 60)
                    )
                if used_g is not None:
                    job.filament_total_g = used_g
                if used_mm is not None:
                    job.filament_total_mm = used_mm

            # Auto-deduct from spool at a printer location
            deducted_spool_id = None
            if used_g and used_g > 0:
                spool = self._find_printer_spool(db, Location, Spool)
                if spool:
                    used_g_int = max(1, round(used_g))
                    if used_g_int <= spool.remaining_g:
                        note = "PrusaLink: {} ({})".format(
                            (job.display_name if job else None) or "Unbekannt",
                            final_state,
                        )
                        log_entry = UsageLog(
                            spool_id=spool.id, used_g=used_g_int, note=note
                        )
                        db.session.add(log_entry)
                        deducted_spool_id = spool.id
                        logger.info(
                            "Auto-deducted %dg from spool #%d (%s %s)",
                            used_g_int,
                            spool.id,
                            spool.manufacturer,
                            spool.color,
                        )
                    else:
                        logger.warning(
                            "Not enough filament on spool #%d (%dg remaining, %dg needed)",
                            spool.id,
                            spool.remaining_g,
                            used_g_int,
                        )

            if job:
                job.spool_id = deducted_spool_id

            db.session.commit()
            self._current_job_db_id = None
            logger.info(
                "Print finished (%s): used_g=%s, spool_deducted=%s",
                final_state,
                used_g,
                deducted_spool_id,
            )

    @staticmethod
    def _find_printer_spool(db, Location, Spool):
        """Return the first spool assigned to a printer-type location."""
        printer_locs = Location.query.filter_by(type="printer").all()
        for loc in printer_locs:
            spool = Spool.query.filter_by(location_id=loc.id).first()
            if spool:
                return spool
        return None


# ---------------------------------------------------------------------------
# Public entry point called from app.py
# ---------------------------------------------------------------------------

def start_monitor(app):
    ip = os.environ.get("PRINTER_IP", "").strip()
    api_key = os.environ.get("PRINTER_API_KEY", "").strip()
    poll_interval = int(os.environ.get("POLL_INTERVAL", "30"))

    if not ip or not api_key:
        app.logger.info(
            "PrusaLink monitor disabled (set PRINTER_IP and PRINTER_API_KEY to enable)"
        )
        return

    monitor = PrusaLinkMonitor(app, ip, api_key, poll_interval)
    monitor.start()
    app.logger.info(
        "PrusaLink monitor started: polling http://%s every %ds", ip, poll_interval
    )
