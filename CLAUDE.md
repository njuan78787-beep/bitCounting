# CLAUDE.md — Guía del proyecto KidSafe para Claude Code

Este archivo orienta a Claude Code al trabajar en este repositorio. Léelo antes de tocar código.

## Qué es

KidSafe es un sistema de control parental que funciona como **servidor DNS del hogar**.
Registra, clasifica y bloquea los dominios que visita cada dispositivo de los hijos, y lo
muestra en un panel web en español para padres no técnicos.

**Fase 1 + Fase 2 implementadas.** El DNS resuelve lo permitido, bloquea lo prohibido,
registra todo y alimenta el panel. La Fase 2 añade horarios por categoría, hora de dormir
y alertas por email. No empieces de cero: extiende.

## Cómo correr y probar

```bash
# Local, sin Docker, en puerto DNS alto (sin admin):
KIDSAFE_DNS_PORT=5300 ./run-local.sh
# Panel: http://localhost:8080

# Probar el DNS de verdad:
dig @127.0.0.1 -p 5300 youtube.com    # resuelve (permitido)
dig @127.0.0.1 -p 5300 pornhub.com    # 0.0.0.0 (bloqueado por preset)

# Docker (producción):
docker compose up -d --build
```

Para probar el bloqueo necesitas un dispositivo asignado a un perfil cuya IP coincida
con el origen de la consulta. En local, las consultas vienen de `127.0.0.1`: crea un
Device con `ip="127.0.0.1"` y `profile_id` asignado.

## Arquitectura (no la rompas)

- **`app/dns_server.py`** es el corazón y el componente más crítico. Debe seguir resolviendo
  aunque la web falle. Usa un caché en memoria (`refresh_cache`) refrescado cada ~4s.
- **`app/rules.py`** decide permitir/bloquear. Precedencia: pausa > hora de dormir
  (scope=bedtime) > regla de dominio (allow gana) > regla de categoría (con horario)
  > permitir por defecto. Funciones puras y testeables: no metas acceso a BD aquí.
- **`app/classifier.py`** mapea dominio → categoría probando el dominio y sus padres.
- **`app/main.py`** expone la API REST + WebSocket y sirve `static/index.html`.
- **`static/index.html`** es TODO el frontend: SPA en vanilla JS + Tailwind por CDN,
  sin paso de build. Mantén ese principio (cero build).
- **`app/models.py`** SQLite vía SQLAlchemy. `scope="bedtime"` es el tipo de regla
  que bloquea todo el internet del hijo en una franja horaria.

## Convenciones

- Todo el texto de cara al usuario va en **español** y sin jerga.
- Di "dispositivos", "bloqueado", "categorías". Nunca "DNS", "MAC", "NXDOMAIN".
- Cambios que afectan reglas/dispositivos/perfiles deben llamar `dns_server.refresh_cache(force=True)`.
- Auth: header `Authorization: Bearer <token>`; el WebSocket usa `?token=`.

## Estado por fase (PRD)

**Hecho (Fase 1):** servidor DNS con logging por dispositivo, descubrimiento, perfiles
por hijo, clasificación, bloqueo por categoría y dominio, presets por edad, panel completo,
wizard de configuración, pausa, reporte natural básico, Docker.

**Hecho (Fase 2):**
- Horarios por categoría en la UI (reloj junto a cada categoría bloqueada).
- "Hora de dormir": bloquea todo el internet en una franja configurable por días y hora.
- Alertas por email/SMTP desde el panel (además de Telegram).

**Por construir (Fase 3):**
- Detección de evasión real (DoH/VPN desde la red).
- Reportes semanales automáticos por Telegram/email.
- Ampliar listas con blocklists públicas (Hagezi, blocklistproject).
- Reclasificación manual de dominios desde la UI.

## Al terminar un cambio

1. Verifica que el DNS sigue resolviendo y bloqueando.
2. Verifica el panel: setup → crear hijo → asignar dispositivo → ver actividad.
3. No introduzcas un paso de build en el frontend ni dependencias de nube.
