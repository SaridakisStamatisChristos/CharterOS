from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from charteros.infrastructure.db.base import Base


class ApiRateLimitWindowRow(Base):
    __tablename__ = "api_rate_limit_windows"
    __table_args__ = (
        CheckConstraint("request_count > 0", name="ck_api_rate_limit_windows_count"),
    )

    budget: Mapped[str] = mapped_column(String(32), primary_key=True)
    identity_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
