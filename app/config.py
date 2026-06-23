"""Configuración central de KidSafe. Todo se puede sobreescribir por variables de entorno."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("KIDSAFE_DATA", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "kidsafe.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

DNS_HOST = os.environ.get("KIDSAFE_DNS_HOST", "0.0.0.0")
DNS_PORT = int(os.environ.get("KIDSAFE_DNS_PORT", "53"))
UPSTREAMS = os.environ.get("KIDSAFE_UPSTREAMS", "1.1.1.1,8.8.8.8").split(",")
SINKHOLE_IP = os.environ.get("KIDSAFE_SINKHOLE", "0.0.0.0")

WEB_HOST = os.environ.get("KIDSAFE_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("KIDSAFE_WEB_PORT", "8080"))

RETENTION_DAYS = int(os.environ.get("KIDSAFE_RETENTION_DAYS", "90"))

os.makedirs(DATA_DIR, exist_ok=True)
