"""Alertas por Telegram y email (SMTP). Mensajes en lenguaje natural."""
import json
import smtplib
import datetime as dt
from email.mime.text import MIMEText

import requests

from .models import SessionLocal, AlertConfig

_recent = {}
_COOLDOWN = 600  # 10 min entre alertas del mismo tipo


def _should_send(key):
    now = dt.datetime.utcnow().timestamp()
    last = _recent.get(key, 0)
    if now - last < _COOLDOWN:
        return False
    _recent[key] = now
    return True


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, json={"chat_id": chat_id, "text": text,
                                 "parse_mode": "HTML"}, timeout=8)
        return True
    except Exception:
        return False


def send_email(cfg_extra, target, subject, body):
    try:
        c = json.loads(cfg_extra or "{}")
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = c.get("from", c.get("user", "kidsafe@local"))
        msg["To"] = target
        s = smtplib.SMTP(c["host"], int(c.get("port", 587)), timeout=10)
        s.starttls()
        s.login(c["user"], c["password"])
        s.send_message(msg)
        s.quit()
        return True
    except Exception:
        return False


def dispatch(event_type, profile_name, detail):
    key = f"{event_type}:{profile_name}"
    if not _should_send(key):
        return
    db = SessionLocal()
    try:
        configs = db.query(AlertConfig).filter(AlertConfig.enabled == True).all()  # noqa
        text = _format(event_type, profile_name, detail)
        plain = _format_plain(event_type, profile_name, detail)
        for cfg in configs:
            if event_type not in (cfg.triggers or ""):
                continue
            if cfg.channel == "telegram":
                extra = json.loads(cfg.extra or "{}")
                send_telegram(extra.get("token", ""), cfg.target, text)
            elif cfg.channel == "email":
                send_email(cfg.extra, cfg.target, "KidSafe — alerta", plain)
    finally:
        db.close()


def _format(event_type, profile_name, detail):
    mapping = {
        "adult":      f"⚠️ <b>{profile_name}</b> intentó entrar a contenido adulto: {detail}. Fue bloqueado.",
        "gambling":   f"⚠️ <b>{profile_name}</b> intentó entrar a una página de apuestas: {detail}. Fue bloqueado.",
        "new_device": f"📱 Nuevo dispositivo en tu red: {detail}. Ábrelo en KidSafe para asignarlo.",
        "evasion":    f"🕵️ <b>{profile_name}</b> parece estar usando una VPN o DNS diferente ({detail}).",
    }
    return mapping.get(event_type, f"KidSafe: {event_type} — {detail}")


def _format_plain(event_type, profile_name, detail):
    mapping = {
        "adult":      f"{profile_name} intentó entrar a contenido adulto: {detail}. Fue bloqueado.",
        "gambling":   f"{profile_name} intentó entrar a una página de apuestas: {detail}. Fue bloqueado.",
        "new_device": f"Nuevo dispositivo en tu red: {detail}. Ábrelo en KidSafe para asignarlo.",
        "evasion":    f"{profile_name} parece estar usando una VPN o DNS diferente ({detail}).",
    }
    return mapping.get(event_type, f"KidSafe: {event_type} — {detail}")
