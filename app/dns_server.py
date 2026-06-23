"""Servidor DNS de KidSafe.

Recibe consultas, identifica el dispositivo/hijo, decide permitir o bloquear,
registra todo y reenvía al upstream. Mantiene caché en memoria refrescado cada
pocos segundos para no tocar la BD en cada consulta.
"""
import time
import socket
import threading
import datetime as dt

from dnslib import DNSRecord, RR, QTYPE, A, RCODE
from dnslib.server import DNSServer, BaseResolver

from . import config
from .classifier import classify
from .rules import decide
from .models import SessionLocal, Query, Device, Profile

_cache_lock = threading.Lock()
_ip_to_device = {}
_profiles = {}
_last_refresh = 0

live_callback = None


class _ProfileSnap:
    def __init__(self, p):
        self.id = p.id
        self.name = p.name
        self.paused_until = p.paused_until
        self.rules = list(p.rules)


def refresh_cache(force=False):
    global _last_refresh, _ip_to_device, _profiles
    if not force and time.time() - _last_refresh < 4:
        return
    db = SessionLocal()
    try:
        profiles = {}
        for p in db.query(Profile).all():
            _ = p.rules
            profiles[p.id] = _ProfileSnap(p)
        ip_map = {}
        for d in db.query(Device).all():
            ip_map[d.ip] = (d.id, d.ignored, d.profile_id)
        with _cache_lock:
            _profiles = profiles
            _ip_to_device = ip_map
            _last_refresh = time.time()
    finally:
        db.close()


def _resolve_upstream(request, upstream):
    data = request.pack()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(data, (upstream, 53))
        resp, _ = sock.recvfrom(4096)
        return resp
    finally:
        sock.close()


class KidSafeResolver(BaseResolver):
    def resolve(self, request, handler):
        client_ip = handler.client_address[0]
        qname = str(request.q.qname).rstrip(".")
        category = classify(qname)

        refresh_cache()
        with _cache_lock:
            dev = _ip_to_device.get(client_ip)
            profiles = _profiles

        device_id, ignored, profile_id = (dev if dev else (None, False, None))
        profile = profiles.get(profile_id) if profile_id else None

        allowed, reason = decide(profile, category, qname)
        ts = dt.datetime.utcnow()

        if allowed:
            t0 = time.time()
            raw = None
            for up in config.UPSTREAMS:
                try:
                    raw = _resolve_upstream(request, up.strip())
                    break
                except Exception:
                    continue
            elapsed = int((time.time() - t0) * 1000)
            if raw is not None:
                reply = DNSRecord.parse(raw)
            else:
                reply = request.reply()
                reply.header.rcode = RCODE.SERVFAIL
            action = "allowed"
        else:
            reply = request.reply()
            if request.q.qtype == QTYPE.A:
                reply.add_answer(RR(request.q.qname, QTYPE.A, rdata=A(config.SINKHOLE_IP), ttl=60))
            else:
                reply.header.rcode = RCODE.NXDOMAIN
            elapsed = 0
            action = "blocked"

        if not ignored:
            _log_query(ts, device_id, qname, category, action, elapsed)

        if live_callback and not ignored:
            try:
                live_callback({
                    "ts": ts.isoformat(), "device_id": device_id,
                    "domain": qname, "category": category, "action": action,
                    "profile_id": profile_id, "reason": reason,
                })
            except Exception:
                pass

        return reply


def _log_query(ts, device_id, domain, category, action, ms):
    db = SessionLocal()
    try:
        db.add(Query(ts=ts, device_id=device_id, domain=domain,
                     category=category, action=action, upstream_ms=ms))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


_server = None


def start_dns():
    global _server
    refresh_cache(force=True)
    resolver = KidSafeResolver()
    _server = DNSServer(resolver, port=config.DNS_PORT, address=config.DNS_HOST)
    _server.start_thread()
    return _server


def stop_dns():
    if _server:
        _server.stop()
