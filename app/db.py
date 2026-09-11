"""Persistence. SQLite locally, Heroku Postgres in prod — both via DATABASE_URL.

KVKK Art. 6(4): access logging + separate storage are mandatory regardless of legal
basis. Every read of a patient report writes an AuditLog row; identifiable free text is
never stored (only the de-identified complaint) — see app/llm.py.
"""
import datetime as dt
import json
import os

from sqlalchemy import String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

_url = os.environ.get("DATABASE_URL", "sqlite:///hekimyol.db")
# Render/Heroku hand out "postgres://" or "postgresql://"; SQLAlchemy needs the
# dialect+driver form to pick psycopg3 (the driver actually in requirements.txt) —
# plain "postgresql://" defaults to psycopg2, which isn't installed.
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+psycopg://", 1)
elif _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    pathway_slug: Mapped[str] = mapped_column(String(64))
    complaint_deid: Mapped[str] = mapped_column(Text)  # de-identified only
    answers_json: Mapped[str] = mapped_column(Text, default="[]")  # [[node_id, option_idx], ...]
    consent_health_data: Mapped[bool] = mapped_column(default=False)
    consent_store_report: Mapped[bool] = mapped_column(default=False)

    @property
    def answers(self) -> list[list]:
        return json.loads(self.answers_json)

    @answers.setter
    def answers(self, v: list[list]) -> None:
        self.answers_json = json.dumps(v, ensure_ascii=False)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=_now)
    markdown: Mapped[str] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[str] = mapped_column(String(40), default=_now)
    event: Mapped[str] = mapped_column(String(40))  # session_create|consent|report_generate|report_access
    session_id: Mapped[str] = mapped_column(String(36), default="")
    actor: Mapped[str] = mapped_column(String(80), default="patient")
    detail: Mapped[str] = mapped_column(Text, default="")


def init_db() -> None:
    # ponytail: create_all, no migrations. Add alembic when the schema starts churning.
    Base.metadata.create_all(engine)


def log(db, event: str, session_id: str = "", actor: str = "patient", detail: str = "") -> None:
    db.add(AuditLog(event=event, session_id=session_id, actor=actor, detail=detail))
    db.commit()
