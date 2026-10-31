"""SQLAlchemy access to the Prisma-owned tables. This module does not migrate."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # users is owned by Prisma. Keep the column, not a mapped FK, so SQLAlchemy
    # does not require a User table in this metadata.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String, default="template")
    template_id: Mapped[str | None] = mapped_column(String, nullable=True)
    material: Mapped[str | None] = mapped_column(String, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    peak_force_n: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_von_mises_pa: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_plastic_strain: Mapped[float | None] = mapped_column(Float, nullable=True)
    energy_balance_error_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    s3_prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SimMetric(Base):
    __tablename__ = "sim_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    sim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("simulations.id"))
    name: Mapped[str] = mapped_column(String)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    simulation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("simulations.id"))
    kind: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    s3_key: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)


def make_session_factory(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def wait_for_db(engine, attempts: int = 30) -> None:
    import time

    last = None
    for _ in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1)
    raise RuntimeError(f"database unavailable: {last}")


class PostgresSink:
    def __init__(self, factory):
        self.factory = factory

    def _session(self) -> Session:
        return self.factory()

    def update(self, simulation_id: str, **fields) -> None:
        with self._session() as session:
            row = session.get(Simulation, uuid.UUID(simulation_id))
            if row is None:
                return
            for key, value in fields.items():
                setattr(row, key, value)
            if fields.get("status") in {"running_starter", "meshing", "running_engine"} and row.started_at is None:
                row.started_at = utcnow()
            if fields.get("status") in {"succeeded", "failed", "cancelled"}:
                row.finished_at = utcnow()
            session.commit()

    def save_results(
        self,
        simulation_id: str,
        summary: dict,
        artifacts: list[dict],
        s3_prefix: str,
        metric_rows: list[dict] | None = None,
    ) -> None:
        with self._session() as session:
            row = session.get(Simulation, uuid.UUID(simulation_id))
            if row is None:
                return
            row.summary = summary
            row.peak_force_n = summary.get("peak_force_N")
            row.max_von_mises_pa = summary.get("max_von_mises_Pa")
            row.max_plastic_strain = summary.get("max_plastic_strain")
            balance = summary.get("energy_balance_ok")
            row.energy_balance_error_pct = None if balance is None else (0.0 if balance else 100.0)
            row.s3_prefix = s3_prefix
            session.execute(delete(SimMetric).where(SimMetric.sim_id == row.id))
            for item in metric_rows or []:
                session.add(
                    SimMetric(
                        id=uuid.uuid4(),
                        sim_id=row.id,
                        name=item["name"],
                        value=item["value"],
                        unit=item["unit"] or "",
                    )
                )
            session.execute(delete(Artifact).where(Artifact.simulation_id == row.id))
            for art in artifacts:
                session.add(
                    Artifact(
                        id=uuid.uuid4(),
                        simulation_id=row.id,
                        kind=art["kind"],
                        filename=art["filename"],
                        s3_key=art["s3_key"],
                        content_type=art["content_type"],
                        size_bytes=int(art["size_bytes"]),
                    )
                )
            session.commit()

    def get(self, simulation_id: str, user_id: str) -> Simulation | None:
        with self._session() as session:
            row = session.get(Simulation, uuid.UUID(simulation_id))
            if row is None or str(row.user_id) != user_id:
                return None
            session.expunge(row)
            return row

    def list_for_user(self, user_id: str, limit: int = 50) -> list[Simulation]:
        with self._session() as session:
            stmt = (
                select(Simulation)
                .where(Simulation.user_id == uuid.UUID(user_id))
                .order_by(Simulation.created_at.desc())
                .limit(limit)
            )
            rows = list(session.scalars(stmt))
            for row in rows:
                session.expunge(row)
            return rows
