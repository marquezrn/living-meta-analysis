"""Durable application records and source-independent scientific payloads."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import Settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def identity() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    github_id: Mapped[str] = mapped_column(String(64), unique=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    protocol: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_metadata_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_extraction: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synthesis_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_metadata_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovery_checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    discovery_report: Mapped[dict] = mapped_column(JSON, default=dict)
    synthesis: Mapped[dict] = mapped_column(JSON, default=dict)
    synthesis_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    benchmark: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))


class Invitation(Base):
    __tablename__ = "invitations"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(600))
    doi: Mapped[str | None] = mapped_column(String(250), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="uploaded")
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    inventory: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    budget_microusd: Mapped[int] = mapped_column(Integer)
    spent_microusd: Mapped[int] = mapped_column(Integer, default=0)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    document_ids: Mapped[list] = mapped_column(JSON)
    protocol_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Wallet(Base):
    __tablename__ = "budget_wallets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    limit_microusd: Mapped[int] = mapped_column(Integer)
    spent_microusd: Mapped[int] = mapped_column(Integer, default=0)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)


class Reservation(Base):
    __tablename__ = "budget_reservations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    model: Mapped[str] = mapped_column(String(100))
    reserved_microusd: Mapped[int] = mapped_column(Integer)
    actual_microusd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="held")
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExperimentRecord(Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("document_id", "experiment_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    experiment_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    source_status: Mapped[str] = mapped_column(String(30), default="active")


class MeasurementRecord(Base):
    __tablename__ = "measurements"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    payload: Mapped[dict] = mapped_column(JSON)


class PublicationRecord(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("project_id", "canonical_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    canonical_id: Mapped[str] = mapped_column(String(300))
    doi: Mapped[str | None] = mapped_column(String(250), nullable=True)
    state: Mapped[str] = mapped_column(String(40), default="pending_extraction")
    payload: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=identity)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_database(settings: Settings):
    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    connect_args = {}
    if url.startswith("sqlite"):
        settings.private_directory.mkdir(parents=True, exist_ok=True)
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
    if url.endswith(":memory:"):
        from sqlalchemy.pool import StaticPool
        engine.dispose()
        engine = create_engine(url, connect_args=connect_args, poolclass=StaticPool)
    return engine, sessionmaker(engine, expire_on_commit=False)


def initialize_database(engine):
    Base.metadata.create_all(engine)
