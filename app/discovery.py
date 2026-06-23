"""Descubrimiento de dispositivos de la red leyendo la tabla ARP del sistema."""
import re
import subprocess
import datetime as dt

from .models import SessionLocal, Device

OUI = {
    "F0:18:98": "Apple", "A4:83:E7": "Apple", "DC:A6:32": "Raspberry Pi",
    "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi", "3C:5A:B4": "Google",
    "F4:F5:E8": "Google", "44:65:0D": "Amazon", "FC:65:DE": "Amazon",
    "00:1A:11": "Google", "C8:2A:14": "Apple", "98:01:A7": "Apple",
    "5C:CF:7F": "Espressif", "AC:DE:48": "Samsung", "78:BD:BC": "Samsung",
    "00:12:FB": "Samsung", "B4:E6:2D": "Espressif", "DC:A2:CB": "Apple",
}


def vendor_for(mac: str) -> str:
    if not mac:
        return ""
    prefix = mac.upper()[0:8]
    return OUI.get(prefix, "")


def read_arp():
    entries = []
    try:
        out = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            m = re.match(r"(\S+).*lladdr\s+([0-9a-fA-F:]{17})", line)
            if m:
                entries.append((m.group(1), m.group(2).upper()))
    except Exception:
        pass
    if not entries:
        try:
            with open("/proc/net/arp") as f:
                for line in f.readlines()[1:]:
                    cols = line.split()
                    if len(cols) >= 4 and cols[3] != "00:00:00:00:00:00":
                        entries.append((cols[0], cols[3].upper()))
        except Exception:
            pass
    return entries


def discover():
    db = SessionLocal()
    new_count = 0
    try:
        for ip, mac in read_arp():
            dev = db.query(Device).filter(Device.mac == mac).first()
            if dev:
                dev.ip = ip
                dev.last_seen = dt.datetime.utcnow()
            else:
                dev = Device(mac=mac, ip=ip, vendor=vendor_for(mac), is_new=True)
                db.add(dev)
                new_count += 1
        db.commit()
    finally:
        db.close()
    return new_count


def upsert_by_ip(ip: str):
    db = SessionLocal()
    try:
        dev = db.query(Device).filter(Device.ip == ip).order_by(Device.last_seen.desc()).first()
        if not dev:
            for aip, mac in read_arp():
                if aip == ip:
                    dev = db.query(Device).filter(Device.mac == mac).first()
                    if not dev:
                        dev = Device(mac=mac, ip=ip, vendor=vendor_for(mac), is_new=True)
                        db.add(dev)
                    break
        if not dev:
            dev = Device(mac=f"ip:{ip}", ip=ip, is_new=True)
            db.add(dev)
        dev.last_seen = dt.datetime.utcnow()
        db.commit()
        return dev.id, dev.profile_id, dev.ignored
    finally:
        db.close()
