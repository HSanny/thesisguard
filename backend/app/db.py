from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Float, DateTime, Text, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    price: Mapped[float] = mapped_column(Float)
    funding_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    open_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="binance_futures")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


class AlertRecord(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    severity: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    explanation: Mapped[str] = mapped_column(Text)
    evidence_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
