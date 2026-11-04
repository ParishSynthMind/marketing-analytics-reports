"""Storage through SQLAlchemy, so the same code runs on SQLite (default) or Postgres.

Every table is keyed by day (plus page / query / campaign where relevant) and upserted,
so re-pulling an overlapping window corrects late-arriving numbers instead of duplicating them.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Store naive UTC, hand back aware UTC. SQLite drops tzinfo otherwise."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16))           # ga4 / gsc / meta
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / ok / empty / failed
    error: Mapped[str] = mapped_column(Text, nullable=True)
    window_start: Mapped[date] = mapped_column(Date, nullable=True)
    window_end: Mapped[date] = mapped_column(Date, nullable=True)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    api_calls: Mapped[int] = mapped_column(Integer, default=0)


class GA4Daily(Base):
    __tablename__ = "ga4_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    sessions: Mapped[int] = mapped_column(Integer, default=0)
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    new_users: Mapped[int] = mapped_column(Integer, default=0)
    engaged_sessions: Mapped[int] = mapped_column(Integer, default=0)
    key_events: Mapped[float] = mapped_column(Float, default=0.0)


class GA4WeeklyUsers(Base):
    """Users de-duplicated across a whole ISO week (summing daily users would double count returning visitors)."""
    __tablename__ = "ga4_weekly_users"
    iso_week: Mapped[str] = mapped_column(String(8), primary_key=True)  # GA4 isoYearIsoWeek, e.g. "202643"
    total_users: Mapped[int] = mapped_column(Integer, default=0)


class GA4PageDaily(Base):
    __tablename__ = "ga4_pages_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    page_path: Mapped[str] = mapped_column(String(700), primary_key=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    sessions: Mapped[int] = mapped_column(Integer, default=0)


class GSCDaily(Base):
    __tablename__ = "gsc_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    clicks: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[float] = mapped_column(Float, default=0.0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float] = mapped_column(Float, default=0.0)


class GSCQueryDaily(Base):
    __tablename__ = "gsc_queries_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    query: Mapped[str] = mapped_column(String(700), primary_key=True)
    clicks: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float] = mapped_column(Float, default=0.0)


class MetaDaily(Base):
    __tablename__ = "meta_daily"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_name: Mapped[str] = mapped_column(String(400), nullable=True)
    spend: Mapped[float] = mapped_column(Float, default=0.0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    conversions: Mapped[float] = mapped_column(Float, default=0.0)
    conversion_value: Mapped[float] = mapped_column(Float, default=0.0)
    purchase_roas: Mapped[float] = mapped_column(Float, nullable=True)  # as reported by Meta, kept for reference


TABLES = {"ga4_daily": GA4Daily, "ga4_weekly_users": GA4WeeklyUsers, "ga4_pages_daily": GA4PageDaily,
          "gsc_daily": GSCDaily, "gsc_queries_daily": GSCQueryDaily, "meta_daily": MetaDaily}


def make_session_factory(url: str) -> sessionmaker:
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def last_run(session: Session, source: str, ok_only: bool = False):
    q = select(SyncRun).where(SyncRun.source == source).order_by(SyncRun.id.desc())
    if ok_only:
        q = q.where(SyncRun.status == "ok")
    return session.scalars(q.limit(1)).first()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
