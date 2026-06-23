"""KidSafe — backend principal (FastAPI).

API REST, WebSocket feed en vivo, panel web y tareas de fondo.
"""
import os
import json
import secrets
import asyncio
import datetime as dt
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import bcrypt as _bcrypt
from sqlalchemy import func

from . import config, dns_server, alerts
from .models import (init_db, SessionLocal, Profile, Device, Rule, Query,
                     AlertConfig, get_setting, set_setting)
from .classifier import CATEGORIES, classify, set_manual, stats as classifier_stats
from .presets import PRESETS, apply_preset
from .discovery import discover


def _hash_pw(pw: str) -> str:
    return _bcrypt.hashpw(pw.encode()[:72], _bcrypt.gensalt()).decode()


def _verify_pw(pw: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except Exception:
        return False


app = FastAPI(title="KidSafe")


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_auth(authorization: str = Header(default="")):
    token = authorization.replace("Bearer ", "").strip()
    db = SessionLocal()
    try:
        real = get_setting(db, "session_token")
    finally:
        db.close()
    if not real or token != real:
        raise HTTPException(status_code=401, detail="No autorizado")
    return True


# ----------------------------- Feed en vivo -----------------------------

class FeedHub:
    def __init__(self):
        self.clients = set()
        self.loop = None

    def register_loop(self, loop):
        self.loop = loop

    async def connect(self, ws):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws):
        self.clients.discard(ws)

    def push(self, event):
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(event), self.loop)

    async def _broadcast(self, event):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


hub = FeedHub()


def on_dns_event(event):
    hub.push(event)
    if event["action"] == "blocked" and event["category"] in ("adult", "gambling", "evasion"):
        pname = "Un dispositivo"
        if event.get("profile_id"):
            db = SessionLocal()
            try:
                p = db.get(Profile, event["profile_id"])
                if p:
                    pname = p.name
            finally:
                db.close()
        alerts.dispatch(event["category"], pname, event["domain"])


# ----------------------------- Setup / Auth -----------------------------

@app.get("/api/setup/status")
def setup_status(db=Depends(db_session)):
    has_pw = get_setting(db, "admin_password") is not None
    n_profiles = db.query(Profile).count()
    n_assigned = db.query(Device).filter(Device.profile_id.isnot(None)).count()
    return {"configured": has_pw, "profiles": n_profiles, "assigned_devices": n_assigned}


@app.post("/api/setup/password")
def set_password(payload: dict, db=Depends(db_session)):
    if get_setting(db, "admin_password"):
        raise HTTPException(400, "La contraseña ya fue configurada")
    pw = payload.get("password", "")
    if len(pw) < 4:
        raise HTTPException(400, "La contraseña debe tener al menos 4 caracteres")
    set_setting(db, "admin_password", _hash_pw(pw))
    token = secrets.token_hex(24)
    set_setting(db, "session_token", token)
    return {"token": token}


@app.post("/api/auth/login")
def login(payload: dict, db=Depends(db_session)):
    stored = get_setting(db, "admin_password")
    if not stored or not _verify_pw(payload.get("password", ""), stored):
        raise HTTPException(401, "Contraseña incorrecta")
    token = secrets.token_hex(24)
    set_setting(db, "session_token", token)
    return {"token": token}


# ----------------------------- Categorías -----------------------------

@app.get("/api/categories")
def list_categories(_=Depends(require_auth)):
    return [{"key": k, **v} for k, v in CATEGORIES.items()]


# ----------------------------- Dispositivos -----------------------------

@app.get("/api/devices")
def list_devices(_=Depends(require_auth), db=Depends(db_session)):
    out = []
    for d in db.query(Device).order_by(Device.last_seen.desc()).all():
        out.append({
            "id": d.id, "mac": d.mac, "ip": d.ip,
            "hostname": d.hostname, "vendor": d.vendor,
            "label": d.label or d.hostname or d.vendor or d.ip,
            "ignored": d.ignored, "is_new": d.is_new,
            "profile_id": d.profile_id,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None,
        })
    return out


@app.post("/api/devices/discover")
def run_discover(_=Depends(require_auth)):
    n = discover()
    dns_server.refresh_cache(force=True)
    return {"new_devices": n}


@app.patch("/api/devices/{device_id}")
def update_device(device_id: int, payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    d = db.get(Device, device_id)
    if not d:
        raise HTTPException(404, "Dispositivo no encontrado")
    if "label" in payload:
        d.label = payload["label"]
    if "profile_id" in payload:
        d.profile_id = payload["profile_id"]
    if "ignored" in payload:
        d.ignored = bool(payload["ignored"])
    d.is_new = False
    db.commit()
    dns_server.refresh_cache(force=True)
    return {"ok": True}


# ----------------------------- Perfiles (hijos) -----------------------------

@app.get("/api/profiles")
def list_profiles(_=Depends(require_auth), db=Depends(db_session)):
    out = []
    for p in db.query(Profile).all():
        blocked = [r.value for r in p.rules if r.scope == "category" and r.action == "block"]
        out.append({
            "id": p.id, "name": p.name, "age": p.age, "emoji": p.emoji,
            "preset": p.preset,
            "paused_until": p.paused_until.isoformat() if p.paused_until else None,
            "blocked_categories": blocked,
            "device_count": db.query(Device).filter(Device.profile_id == p.id).count(),
        })
    return out


@app.post("/api/profiles")
def create_profile(payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    p = Profile(name=payload.get("name", "Hijo"), age=payload.get("age", 10),
                emoji=payload.get("emoji", "🧒"), preset=payload.get("preset", "nino"))
    db.add(p)
    db.commit()
    apply_preset(db, p, p.preset)
    dns_server.refresh_cache(force=True)
    return {"id": p.id}


@app.patch("/api/profiles/{pid}")
def update_profile(pid: int, payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    p = db.get(Profile, pid)
    if not p:
        raise HTTPException(404, "Perfil no encontrado")
    for f in ("name", "age", "emoji"):
        if f in payload:
            setattr(p, f, payload[f])
    db.commit()
    if "preset" in payload and payload["preset"] != p.preset:
        apply_preset(db, p, payload["preset"])
    dns_server.refresh_cache(force=True)
    return {"ok": True}


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: int, _=Depends(require_auth), db=Depends(db_session)):
    p = db.get(Profile, pid)
    if p:
        db.query(Device).filter(Device.profile_id == pid).update({"profile_id": None})
        db.delete(p)
        db.commit()
        dns_server.refresh_cache(force=True)
    return {"ok": True}


@app.post("/api/profiles/{pid}/pause")
def pause_profile(pid: int, minutes: int = 30, _=Depends(require_auth), db=Depends(db_session)):
    p = db.get(Profile, pid)
    if not p:
        raise HTTPException(404, "Perfil no encontrado")
    if minutes <= 0:
        p.paused_until = None
    else:
        p.paused_until = dt.datetime.utcnow() + dt.timedelta(minutes=minutes)
    db.commit()
    dns_server.refresh_cache(force=True)
    return {"paused_until": p.paused_until.isoformat() if p.paused_until else None}


# ----------------------------- Reglas -----------------------------

@app.post("/api/profiles/{pid}/category")
def set_category(pid: int, payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    """Activa/desactiva el bloqueo de una categoría. Acepta horario opcional."""
    cat = payload["category"]
    block = payload.get("block", True)
    schedule = payload.get("schedule")
    existing = db.query(Rule).filter(Rule.profile_id == pid, Rule.scope == "category",
                                     Rule.value == cat).first()
    if block:
        if existing:
            existing.action = "block"
            existing.schedule_json = json.dumps(schedule) if schedule else None
        else:
            db.add(Rule(profile_id=pid, scope="category", value=cat, action="block",
                        schedule_json=json.dumps(schedule) if schedule else None))
    else:
        if existing:
            db.delete(existing)
    db.commit()
    dns_server.refresh_cache(force=True)
    return {"ok": True}


@app.post("/api/profiles/{pid}/domain")
def set_domain_rule(pid: int, payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    """Bloquea o permite un dominio concreto."""
    domain = payload["domain"].lower().strip(".")
    action = payload.get("action", "block")
    db.query(Rule).filter(Rule.profile_id == pid, Rule.scope == "domain",
                          Rule.value == domain).delete()
    db.add(Rule(profile_id=pid, scope="domain", value=domain, action=action))
    db.commit()
    dns_server.refresh_cache(force=True)
    return {"ok": True}


@app.post("/api/profiles/{pid}/bedtime")
def set_bedtime(pid: int, payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    """Configura la hora de dormir: bloquea todo internet del hijo en la franja indicada.
    Enviar schedule=null para desactivar."""
    schedule = payload.get("schedule")
    db.query(Rule).filter(Rule.profile_id == pid, Rule.scope == "bedtime").delete()
    if schedule:
        db.add(Rule(profile_id=pid, scope="bedtime", value="all", action="block",
                    schedule_json=json.dumps(schedule)))
    db.commit()
    dns_server.refresh_cache(force=True)
    return {"ok": True}


@app.get("/api/profiles/{pid}/rules")
def get_rules(pid: int, _=Depends(require_auth), db=Depends(db_session)):
    rows = db.query(Rule).filter(Rule.profile_id == pid).all()
    return [{"id": r.id, "scope": r.scope, "value": r.value, "action": r.action,
             "schedule": json.loads(r.schedule_json) if r.schedule_json else None}
            for r in rows]


@app.delete("/api/rules/{rid}")
def delete_rule(rid: int, _=Depends(require_auth), db=Depends(db_session)):
    r = db.get(Rule, rid)
    if r:
        db.delete(r)
        db.commit()
        dns_server.refresh_cache(force=True)
    return {"ok": True}


# ----------------------------- Estado / Home -----------------------------

def _range_start(rng):
    now = dt.datetime.utcnow()
    if rng == "week":
        return now - dt.timedelta(days=7)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@app.get("/api/state")
def home_state(_=Depends(require_auth), db=Depends(db_session)):
    start = _range_start("today")
    now = dt.datetime.utcnow()
    cards = []
    for p in db.query(Profile).all():
        dev_ids = [d.id for d in db.query(Device).filter(Device.profile_id == p.id).all()]
        base = db.query(Query).filter(Query.ts >= start)
        if dev_ids:
            base = base.filter(Query.device_id.in_(dev_ids))
        else:
            base = base.filter(Query.id < 0)
        total = base.count()
        blocked = base.filter(Query.action == "blocked").count()
        top = (base.filter(Query.action == "allowed")
               .with_entities(Query.domain, func.count().label("c"))
               .group_by(Query.domain).order_by(func.count().desc()).limit(3).all())
        cards.append({
            "id": p.id, "name": p.name, "emoji": p.emoji, "age": p.age,
            "paused": bool(p.paused_until and p.paused_until > now),
            "paused_until": p.paused_until.isoformat() if p.paused_until else None,
            "queries_today": total, "blocked_today": blocked,
            "top_sites": [{"domain": d, "count": c} for d, c in top],
            "device_count": len(dev_ids),
        })
    unassigned_new = db.query(Device).filter(Device.profile_id.is_(None),
                                             Device.ignored == False,  # noqa
                                             Device.is_new == True).count()  # noqa
    return {"children": cards, "new_devices": unassigned_new}


@app.get("/api/profiles/{pid}/activity")
def profile_activity(pid: int, range: str = "today", _=Depends(require_auth), db=Depends(db_session)):
    start = _range_start(range)
    dev_ids = [d.id for d in db.query(Device).filter(Device.profile_id == pid).all()]
    base = db.query(Query).filter(Query.ts >= start)
    base = base.filter(Query.device_id.in_(dev_ids)) if dev_ids else base.filter(Query.id < 0)

    by_cat = (base.with_entities(Query.category, func.count())
              .group_by(Query.category).all())
    top_sites = (base.filter(Query.action == "allowed")
                 .with_entities(Query.domain, func.count())
                 .group_by(Query.domain).order_by(func.count().desc()).limit(15).all())
    recent = base.order_by(Query.ts.desc()).limit(50).all()
    return {
        "by_category": [{"category": c or "other",
                         "label": CATEGORIES.get(c or "other", {}).get("label", c),
                         "color": CATEGORIES.get(c or "other", {}).get("color", "#999"),
                         "count": n} for c, n in by_cat],
        "top_sites": [{"domain": d, "count": n} for d, n in top_sites],
        "recent": [{"ts": q.ts.isoformat(), "domain": q.domain,
                    "category": q.category, "action": q.action} for q in recent],
    }


@app.get("/api/reports/{pid}")
def report(pid: int, range: str = "week", _=Depends(require_auth), db=Depends(db_session)):
    p = db.get(Profile, pid)
    if not p:
        raise HTTPException(404, "Perfil no encontrado")
    start = _range_start(range)
    dev_ids = [d.id for d in db.query(Device).filter(Device.profile_id == pid).all()]
    base = db.query(Query).filter(Query.ts >= start)
    base = base.filter(Query.device_id.in_(dev_ids)) if dev_ids else base.filter(Query.id < 0)
    top = (base.filter(Query.action == "allowed")
           .with_entities(Query.domain, func.count())
           .group_by(Query.domain).order_by(func.count().desc()).limit(3).all())
    blocked = base.filter(Query.action == "blocked").count()
    blocked_adult = base.filter(Query.action == "blocked", Query.category == "adult").count()
    periodo = "esta semana" if range == "week" else "hoy"
    if not top and blocked == 0:
        texto = f"No hay actividad registrada de {p.name} {periodo} todavía."
    else:
        sitios = ", ".join(d for d, _ in top) if top else "ningún sitio destacado"
        texto = f"{periodo.capitalize()}, {p.name} usó sobre todo {sitios}."
        if blocked:
            texto += f" Se bloquearon {blocked} intentos"
            if blocked_adult:
                texto += f", de los cuales {blocked_adult} fueron de contenido adulto"
            texto += "."
    return {"text": texto}


# ----------------------------- Alertas -----------------------------

@app.get("/api/alerts")
def get_alerts(_=Depends(require_auth), db=Depends(db_session)):
    rows = db.query(AlertConfig).all()
    return [{"id": a.id, "channel": a.channel, "target": a.target,
             "triggers": a.triggers, "enabled": a.enabled} for a in rows]


@app.post("/api/alerts")
def add_alert(payload: dict, _=Depends(require_auth), db=Depends(db_session)):
    a = AlertConfig(channel=payload["channel"], target=payload["target"],
                    extra=json.dumps(payload.get("extra", {})),
                    triggers=payload.get("triggers", "adult,gambling,new_device,evasion"),
                    enabled=payload.get("enabled", True))
    db.add(a)
    db.commit()
    return {"id": a.id}


@app.delete("/api/alerts/{aid}")
def del_alert(aid: int, _=Depends(require_auth), db=Depends(db_session)):
    a = db.get(AlertConfig, aid)
    if a:
        db.delete(a)
        db.commit()
    return {"ok": True}


# ----------------------------- WebSocket feed -----------------------------

@app.websocket("/api/feed/live")
async def feed_live(ws: WebSocket):
    token = ws.query_params.get("token", "")
    db = SessionLocal()
    try:
        real = get_setting(db, "session_token")
    finally:
        db.close()
    if token != real:
        await ws.close(code=1008)
        return
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception:
        hub.disconnect(ws)


# ----------------------------- Tareas de fondo -----------------------------

async def background_loop():
    while True:
        try:
            new = discover()
            if new:
                dns_server.refresh_cache(force=True)
                db = SessionLocal()
                try:
                    for d in db.query(Device).filter(Device.is_new == True).all():  # noqa
                        if not d.ignored and d.profile_id is None:
                            alerts.dispatch("new_device", "", d.label or d.vendor or d.ip)
                finally:
                    db.close()
            cutoff = dt.datetime.utcnow() - dt.timedelta(days=config.RETENTION_DAYS)
            db = SessionLocal()
            try:
                db.query(Query).filter(Query.ts < cutoff).delete()
                db.commit()
            finally:
                db.close()
        except Exception:
            pass
        await asyncio.sleep(30)


# ----------------------------- Arranque -----------------------------

@app.on_event("startup")
async def startup():
    init_db()
    hub.register_loop(asyncio.get_event_loop())
    dns_server.live_callback = on_dns_event
    dns_server.start_dns()
    asyncio.create_task(background_loop())


@app.get("/")
def index():
    return FileResponse(os.path.join(config.STATIC_DIR, "index.html"))


if os.path.isdir(config.STATIC_DIR):
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
