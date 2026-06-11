#!/usr/bin/env python3
"""
Solar Bridge — backup manager.

Creates local backups (with rotation) and sends a cloud copy via Telegram
(sendDocument) and/or e-mail attachment using the credentials already in
config.ini. Run by hand, from the dashboard, or nightly via solar-backup.timer:

    /opt/solar-bridge/venv/bin/python /opt/solar-bridge/backup_manager.py
"""

import configparser
import smtplib
import sys
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

BASE        = Path(__file__).parent
BACKUP_DIR  = BASE / "backups"
SETTINGS    = ["config.ini", "automation.json", "energy.json"]
DB_FILE     = "solar_bridge.db"


def _cfg():
    c = configparser.ConfigParser(interpolation=None)
    c.read(BASE / "config.ini", encoding="utf-8")
    return c

def _get(c, s, k, d=""):
    return c.get(s, k, fallback=d).split("#")[0].strip()

def _get_bool(c, s, k, d=True):
    v = _get(c, s, k, "")
    if v == "":
        return d
    return v.lower() in ("1", "true", "yes", "on")


# ── Create / rotate ──────────────────────────────────────────────────────────

def create_backup(include_history: bool = True) -> Path:
    """Write a timestamped zip into backups/ and return its path."""
    BACKUP_DIR.mkdir(exist_ok=True)
    name = f"solar-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
    path = BACKUP_DIR / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in SETTINGS:
            p = BASE / fn
            if p.exists():
                z.write(p, fn)
        if include_history and (BASE / DB_FILE).exists():
            z.write(BASE / DB_FILE, DB_FILE)
        z.writestr("MANIFEST.txt",
                   f"Solar Bridge backup\ncreated: {datetime.now().isoformat()}\n"
                   f"history_included: {include_history}\n")
    return path


def create_settings_zip() -> Path:
    """Small settings-only zip (a few KB) — used for the cloud copy so it
    always fits Telegram's 50 MB bot limit regardless of history size."""
    BACKUP_DIR.mkdir(exist_ok=True)
    path = BACKUP_DIR / f"solar-settings-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in SETTINGS:
            p = BASE / fn
            if p.exists():
                z.write(p, fn)
        z.writestr("MANIFEST.txt",
                   f"Solar Bridge settings backup\ncreated: {datetime.now().isoformat()}\n")
    return path


def rotate(keep: int = 14):
    """Keep only the newest `keep` of each backup kind."""
    if not BACKUP_DIR.exists():
        return
    for pattern in ("solar-backup-*.zip", "solar-settings-*.zip"):
        files = sorted(BACKUP_DIR.glob(pattern), reverse=True)
        for old in files[keep:]:
            try: old.unlink()
            except OSError: pass


# ── Cloud copies ─────────────────────────────────────────────────────────────

def send_telegram(path: Path):
    """Send a file to the configured Telegram chat. Returns (ok, detail)."""
    c = _cfg()
    token = _get(c, "telegram", "token")
    chat  = _get(c, "telegram", "chat_id")
    if not (_get_bool(c, "telegram", "enabled", False) and token and chat):
        return False, "Telegram not configured/enabled"
    if requests is None:
        return False, "python 'requests' missing"
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > 49:
        return False, f"file too big for Telegram ({size_mb:.0f} MB > 49 MB)"
    try:
        with open(path, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat,
                      "caption": f"☁️ Solar Bridge backup · {path.name} · {size_mb:.1f} MB"},
                files={"document": (path.name, f)},
                timeout=180)
        ok = r.ok and r.json().get("ok", False)
        return ok, "sent" if ok else r.text[:200]
    except Exception as e:
        return False, str(e)


def send_email(path: Path):
    """E-mail the backup as an attachment. Returns (ok, detail)."""
    c = _cfg()
    if not _get_bool(c, "email", "enabled", False):
        return False, "e-mail not enabled"
    host = _get(c, "email", "smtp_host", "smtp.gmail.com")
    port = int(_get(c, "email", "smtp_port", "587") or 587)
    user = _get(c, "email", "username")
    pwd  = _get(c, "email", "password")
    from_addr = _get(c, "email", "from_addr") or user
    to_addrs  = [a.strip() for a in _get(c, "email", "to_addr").split(",") if a.strip()]
    if not (user and pwd and to_addrs):
        return False, "e-mail credentials incomplete"
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Solar Bridge backup {path.name}"
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg.set_content("Automatic Solar Bridge backup attached.\n")
        msg.add_attachment(path.read_bytes(), maintype="application",
                           subtype="zip", filename=path.name)
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True, "sent"
    except Exception as e:
        return False, str(e)


# ── CLI entry (used by solar-backup.timer) ───────────────────────────────────

def run_auto():
    c = _cfg()
    include = _get_bool(c, "backup", "include_history", True)
    keep    = int(_get(c, "backup", "keep", "14") or 14)

    local = create_backup(include)
    print(f"local: {local.name} ({local.stat().st_size // 1024} KB)")

    if _get_bool(c, "backup", "to_telegram", True):
        cloud = create_settings_zip()
        ok, detail = send_telegram(cloud)
        print(f"telegram: {detail}")
    if _get_bool(c, "backup", "to_email", False):
        ok, detail = send_email(local)
        print(f"email: {detail}")

    rotate(keep)
    print("rotation done")


if __name__ == "__main__":
    run_auto()
