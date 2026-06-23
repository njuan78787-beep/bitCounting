"""Motor de decisión: dada una consulta, decide permitir o bloquear.

Orden de precedencia:
  1. Pausa activa del perfil → bloquea todo.
  2. Hora de dormir (scope="bedtime") con horario activo → bloquea todo.
  3. Regla de dominio explícita (allow gana a block del mismo alcance).
  4. Regla de categoría (con horario si aplica).
  5. Por defecto: permitir.
"""
import datetime as dt
import json


def _schedule_active(schedule_json, when: dt.datetime) -> bool:
    """True si la franja horaria está activa ahora."""
    if not schedule_json:
        return True
    try:
        s = json.loads(schedule_json)
    except Exception:
        return True
    days = s.get("days")
    if days and when.weekday() not in days:
        return False
    f, t = s.get("from"), s.get("to")
    if not f or not t:
        return True
    cur = when.hour * 60 + when.minute
    fh, fm = map(int, f.split(":"))
    th, tm = map(int, t.split(":"))
    start, end = fh * 60 + fm, th * 60 + tm
    if start <= end:
        return start <= cur < end
    # Franja que cruza medianoche (ej. 22:00 → 07:00)
    return cur >= start or cur < end


def decide(profile, category: str, domain: str, when: dt.datetime = None):
    """Devuelve (allowed: bool, reason: str)."""
    when = when or dt.datetime.utcnow()
    if profile is None:
        return True, "sin_perfil"

    # 1. Pausa inmediata
    if profile.paused_until and profile.paused_until > when:
        return False, "pausa"

    # 2. Hora de dormir (bloquea todo internet del hijo)
    for r in profile.rules:
        if r.scope == "bedtime" and r.action == "block":
            if _schedule_active(r.schedule_json, when):
                return False, "hora_de_dormir"

    # 3. Reglas de dominio explícitas
    domain = (domain or "").lower().strip(".")
    domain_allow = domain_block = False
    cat_block = False
    for r in profile.rules:
        if r.scope == "domain":
            rv = r.value.lower().strip(".")
            if domain == rv or domain.endswith("." + rv):
                if r.action == "allow":
                    domain_allow = True
                elif r.action == "block":
                    domain_block = True
        elif r.scope == "category" and r.value == category and r.action == "block":
            if _schedule_active(r.schedule_json, when):
                cat_block = True

    if domain_allow:
        return True, "lista_blanca"
    if domain_block:
        return False, "dominio_bloqueado"

    # 4. Categoría
    if cat_block:
        return False, f"categoria:{category}"

    return True, "permitido"
