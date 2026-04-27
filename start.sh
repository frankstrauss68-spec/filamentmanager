#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/.venv"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "[Fehler] Keine .env-Datei gefunden. Bitte .env.example kopieren und ausfüllen:"
  echo "  cp .env.example .env && nano .env"
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "[Setup] Erstelle Python-Virtualenv..."
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --upgrade pip --quiet
  "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet
  echo "[Setup] Fertig."
fi

source "$VENV_DIR/bin/activate"

LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "[Start] FilamentManager läuft auf http://${LAN_IP:-localhost}:5000"
echo "        Zum Beenden: Strg+C"

python3 "$APP_DIR/app.py"
