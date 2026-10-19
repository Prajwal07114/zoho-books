"""Organization and Membership Models."""

import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = {"schema": "app"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    legal_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    base_currency_code: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    time_zone: Mapped[str] = mapped_column(String, default="UTC", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity.users.id"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    settings: Mapped["OrganizationSettings"] = relationship(
        "OrganizationSettings",
        back_populates="organization",
        uselist=False,
        cascade="all, delete-orphan",
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"
    __table_args__ = {"schema": "app"}

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lock_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    books_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    invoice_prefix: Mapped[str] = mapped_column(String, default="INV-", nullable=False)
    next_invoice_number: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="settings")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = {"schema": "app"}

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("identity.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String, default="owner", nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="memberships")
