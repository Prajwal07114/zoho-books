"""Customer Credit Note and CreditNoteLine Models."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CreditNote(Base):
    __tablename__ = "credit_notes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["app.contacts.organization_id", "app.contacts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "invoice_id"],
            ["app.invoices.organization_id", "app.invoices.id"],
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
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    credit_note_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    credit_note_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    state: Mapped[str] = mapped_column(String, default="draft", nullable=False)  # draft, issued, void
    net_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    issue_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    lines: Mapped[List["CreditNoteLine"]] = relationship(
        "CreditNoteLine",
        back_populates="credit_note",
        cascade="all, delete-orphan",
        primaryjoin="and_(CreditNote.organization_id==CreditNoteLine.organization_id, CreditNote.id==CreditNoteLine.credit_note_id)",
        order_by="CreditNoteLine.line_no",
    )

    @property
    def refunded_amount(self) -> Decimal:
        return self.allocated_amount

    @refunded_amount.setter
    def refunded_amount(self, value: Decimal) -> None:
        self.allocated_amount = value



class CreditNoteLine(Base):
    __tablename__ = "credit_note_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "credit_note_id"],
            ["app.credit_notes.organization_id", "app.credit_notes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "income_account_id"],
            ["app.accounts.organization_id", "app.accounts.id"],
            ondelete="RESTRICT",
        ),
        {"schema": "app"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    credit_note_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    line_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(1), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    tax_rate_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    income_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    credit_note: Mapped["CreditNote"] = relationship(
        "CreditNote",
        back_populates="lines",
        primaryjoin="and_(CreditNoteLine.organization_id==CreditNote.organization_id, CreditNoteLine.credit_note_id==CreditNote.id)",
    )
