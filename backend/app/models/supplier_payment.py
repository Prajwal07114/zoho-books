"""Supplier Payment and Bill Allocation Models."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "vendor_id"],
            ["app.contacts.organization_id", "app.contacts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "payment_account_id"],
            ["app.accounts.organization_id", "app.accounts.id"],
            ondelete="RESTRICT",
        ),
        {"schema": "app"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("app.organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payment_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    method: Mapped[str] = mapped_column(String, default="bank_transfer", nullable=False)
    payment_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active, reversed
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    journal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reversal_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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

    allocations: Mapped[List["SupplierPaymentAllocation"]] = relationship(
        "SupplierPaymentAllocation",
        back_populates="payment",
        cascade="all, delete-orphan",
        primaryjoin="and_(SupplierPayment.organization_id==SupplierPaymentAllocation.organization_id, SupplierPayment.id==SupplierPaymentAllocation.payment_id)",
    )


class SupplierPaymentAllocation(Base):
    __tablename__ = "supplier_payment_allocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "payment_id"],
            ["app.supplier_payments.organization_id", "app.supplier_payments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "bill_id"],
            ["app.bills.organization_id", "app.bills.id"],
            ondelete="CASCADE",
        ),
        {"schema": "app"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)  # active, reversed
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped["SupplierPayment"] = relationship(
        "SupplierPayment",
        back_populates="allocations",
        primaryjoin="and_(SupplierPaymentAllocation.organization_id==SupplierPayment.organization_id, SupplierPaymentAllocation.payment_id==SupplierPayment.id)",
    )
