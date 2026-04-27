# FilamentManager

Lokale Web-App zur Verwaltung von 3D-Drucker-Filamentspulen. Läuft auf Debian Linux.

## Funktionen

- Spulen anlegen, bearbeiten, löschen (Hersteller, Material, Farbe, Foto)
- Restmenge tracken mit Verbrauchsprotokoll
- Lagerorte verwalten (Regal, Box, Drucker)
- QR-Code-Etiketten generieren und drucken

## Installation

### 1. MariaDB-Datenbank einrichten

```sql
CREATE DATABASE filamentmanager CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'filament_user'@'%' IDENTIFIED BY 'sicheres-passwort';
GRANT ALL PRIVILEGES ON filamentmanager.* TO 'filament_user'@'%';
FLUSH PRIVILEGES;
```

### 2. Konfiguration

```bash
cp .env.example .env
nano .env
```

Folgende Werte eintragen:

```
DB_HOST=192.168.x.x      # IP deines MariaDB-Servers
DB_PORT=3306
DB_NAME=filamentmanager
DB_USER=filament_user
DB_PASSWORD=sicheres-passwort
SECRET_KEY=zufaelliger-langer-string
```

### 3. Starten

```bash
bash start.sh
```

Beim ersten Start wird automatisch ein Python-Virtualenv erstellt und alle Abhängigkeiten installiert.

Die App ist dann erreichbar unter:
- Lokal: http://localhost:5000
- Im Heimnetz: http://\<deine-IP\>:5000

## Dauerhaft als Systemdienst einrichten (optional)

```bash
sudo nano /etc/systemd/system/filamentmanager.service
```

```ini
[Unit]
Description=FilamentManager
After=network.target

[Service]
Type=simple
User=DEIN_USER
WorkingDirectory=/pfad/zu/filamentmanager
ExecStart=/pfad/zu/filamentmanager/.venv/bin/python app.py
Restart=on-failure
EnvironmentFile=/pfad/zu/filamentmanager/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now filamentmanager
```

## QR-Code-Etiketten

Die QR-Codes kodieren die LAN-URL der Spule (z.B. `http://192.168.1.50:5000/spools/7`).  
Jedes Handy im Heimnetz kann den Code scannen und landet direkt auf der Detailseite.  
Empfohlen: Etiketten in Chrome/Chromium drucken für beste Qualität.
