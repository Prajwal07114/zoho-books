"""Expense SQLAlchemy Model for Direct Business Disbursements."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
import uuid
from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "expense_account_id"],
            ["app.accounts.organization_id", "app.accounts.id"],
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
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    expense_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payment_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    tax_rate_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    vendor_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, default="posted", nullable=False)  # posted, void
    journal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    void_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
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

    @property
    def net_amount(self) -> Decimal:
        return self.amount

    @net_amount.setter
    def net_amount(self, value: Decimal) -> None:
        self.amount = value

    @property
    def total_amount(self) -> Decimal:
        return self.amount + (self.tax_amount or Decimal("0.0000"))

    @property
    def payment_status(self) -> str:
        return self.state

    @payment_status.setter
    def payment_status(self, value: str) -> None:
        self.state = value

    @property
    def posting_journal_id(self) -> uuid.UUID:
        return self.journal_id

    @posting_journal_id.setter
    def posting_journal_id(self, value: uuid.UUID) -> None:
        self.journal_id = value

