"""Modelos de base de datos (SQLAlchemy) y sesión."""
import datetime as dt
from sqlalchemy import (create_engine, Column, Integer, String, Boolean,
                        DateTime, ForeignKey, Text, Index)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from .config import DB_PATH

Base = declarative_base()
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def now():
    return dt.datetime.utcnow()


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    age = Column(Integer, default=10)
    emoji = Column(String, default="🧒")
    preset = Column(String, default="nino")
    paused_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now)
    devices = relationship("Device", back_populates="profile")
    rules = relationship("Rule", back_populates="profile", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    mac = Column(String, unique=True, index=True)
    ip = Column(String, index=True)
    hostname = Column(String, default="")
    vendor = Column(String, default="")
    label = Column(String, default="")
    ignored = Column(Boolean, default=False)
    is_new = Column(Boolean, default=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    first_seen = Column(DateTime, default=now)
    last_seen = Column(DateTime, default=now)
    profile = relationship("Profile", back_populates="devices")


class Rule(Base):
    """Regla de bloqueo por categoría, dominio o franja de sueño (scope=bedtime)."""
    __tablename__ = "rules"
    id = Column(Integer, primary_key=True)
    profile_id = Column(Integer, ForeignKey("profiles.id"))
    scope = Column(String)        # "category" | "domain" | "bedtime"
    value = Column(String)        # nombre de categoría, dominio, o "all" para bedtime
    action = Column(String)       # "allow" | "block"
    schedule_json = Column(Text, nullable=True)  # {"days":[0..6],"from":"20:00","to":"07:00"}
    profile = relationship("Profile", back_populates="rules")


class Query(Base):
    __tablename__ = "queries"
    id = Column(Integer, primary_key=True)
    ts = Column(DateTime, default=now, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), index=True, nullable=True)
    domain = Column(String, index=True)
    category = Column(String, index=True)
    action = Column(String)
    upstream_ms = Column(Integer, default=0)


Index("ix_queries_device_ts", Query.device_id, Query.ts)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text)


class AlertConfig(Base):
    __tablename__ = "alerts_config"
    id = Column(Integer, primary_key=True)
    channel = Column(String)      # "telegram" | "email"
    target = Column(String)       # chat_id o correo
    extra = Column(Text, default="")
    triggers = Column(Text, default="adult,gambling,new_device,evasion")
    enabled = Column(Boolean, default=True)


def init_db():
    Base.metadata.create_all(engine)


def get_setting(db, key, default=None):
    row = db.get(Setting, key)
    return row.value if row else default


def set_setting(db, key, value):
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    db.commit()
