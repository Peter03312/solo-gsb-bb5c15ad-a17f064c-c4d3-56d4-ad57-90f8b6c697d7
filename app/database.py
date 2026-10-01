"""Database engine and idempotent schema initialization."""
from __future__ import annotations

import os

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://railway:railway@db:5432/railway",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


class Base(DeclarativeBase):
    pass


class Line(Base):
    __tablename__ = "lines"
    __table_args__ = (CheckConstraint("id = 1", name="lines_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)

    base_segments: Mapped[list["BaseSegment"]] = relationship(
        back_populates="line",
        cascade="all, delete-orphan",
        order_by="BaseSegment.start_mileage",
    )


class BaseSegment(Base):
    __tablename__ = "base_segments"
    __table_args__ = (
        CheckConstraint("start_mileage >= 0", name="base_segments_start_non_negative"),
        CheckConstraint("end_mileage > start_mileage", name="base_segments_non_empty"),
        CheckConstraint("speed > 0", name="base_segments_speed_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), nullable=False
    )
    start_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    end_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    speed: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)

    line: Mapped[Line] = relationship(back_populates="base_segments")


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (
        CheckConstraint(
            "start_mileage >= 0 AND end_mileage <= line_total_mileage",
            name="commands_mileage_within_line",
        ),
        CheckConstraint("end_mileage > start_mileage", name="commands_non_empty"),
        CheckConstraint("speed > 0", name="commands_speed_positive"),
        CheckConstraint(
            "effective_end > effective_start",
            name="commands_time_interval_positive",
        ),
        CheckConstraint("version >= 0", name="commands_version_non_negative"),
        CheckConstraint(
            "status IN ('draft', 'published', 'cancelled')",
            name="commands_status_valid",
        ),
        CheckConstraint(
            "(status = 'draft') = (published_at IS NULL)",
            name="commands_published_at_matches_status",
        ),
        CheckConstraint(
            "effective_start >= published_at",
            name="commands_no_retroactive_publication",
        ),
        CheckConstraint(
            "cancelled_at IS NULL OR cancelled_at >= published_at",
            name="commands_cancelled_after_publication",
        ),
        Index(
            "ix_commands_active_lookup",
            "status",
            "start_mileage",
            "end_mileage",
            "effective_start",
            "effective_end",
        ),
    )

    number: Mapped[str] = mapped_column(String(128), primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="RESTRICT"), nullable=False
    )
    line_total_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    start_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    end_mileage: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    speed: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    effective_start: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_end: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    published_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


_IMMUTABLE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION reject_line_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION '基础线路录入后不可修改';
END;
$$ LANGUAGE plpgsql;
"""


def initialize_database() -> None:
    """Create extensions, tables, immutable triggers, and the GiST overlap guard."""
    with engine.begin() as connection:
        # btree_gist gives GiST support for the bigint line_id in the exclusion.
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        Base.metadata.create_all(bind=connection)

        connection.execute(text(_IMMUTABLE_TRIGGER_SQL))
        for table in ("lines", "base_segments"):
            connection.execute(text(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}"))
            connection.execute(
                text(
                    f"CREATE TRIGGER {table}_immutable "
                    f"BEFORE UPDATE OR DELETE ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION reject_line_mutation()"
                )
            )

        exists = connection.execute(
            text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'base_segments_no_overlap'"
            )
        ).scalar()
        if exists is None:
            connection.execute(
                text(
                    "ALTER TABLE base_segments ADD CONSTRAINT base_segments_no_overlap "
                    "EXCLUDE USING GIST ("
                    "line_id WITH =, "
                    "int8range("
                    "CAST(start_mileage * 1000000 AS BIGINT), "
                    "CAST(end_mileage * 1000000 AS BIGINT), "
                    "'[)'"
                    ") WITH ="
                    ")"
                )
            )
