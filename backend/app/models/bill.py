"""Purchase Bill (Supplier Invoice) and Line Models."""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
import uuid
from sqlalchemy import Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Bill(Base):
    __tablename__ = "bills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "vendor_id"],
            ["app.contacts.organization_id", "app.contacts.id"],
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
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    bill_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    vendor_invoice_number: Mapped[str] = mapped_column(String, nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    state: Mapped[str] = mapped_column(String, default="draft", nullable=False)  # draft, posted, void
    net_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    settled_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    post_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
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

    lines: Mapped[List["BillLine"]] = relationship(
        "BillLine",
        back_populates="bill",
        cascade="all, delete-orphan",
        primaryjoin="and_(Bill.organization_id==BillLine.organization_id, Bill.id==BillLine.bill_id)",
        order_by="BillLine.line_no",
    )

    @property
    def paid_amount(self) -> Decimal:
        return self.settled_amount

    @paid_amount.setter
    def paid_amount(self, value: Decimal) -> None:
        self.settled_amount = value

    @property
    def posting_journal_id(self) -> Optional[uuid.UUID]:
        return self.post_journal_id

    @posting_journal_id.setter
    def posting_journal_id(self, value: Optional[uuid.UUID]) -> None:
        self.post_journal_id = value



class BillLine(Base):
    __tablename__ = "bill_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "bill_id"],
            ["app.bills.organization_id", "app.bills.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "expense_account_id"],
            ["app.accounts.organization_id", "app.accounts.id"],
            ondelete="RESTRICT",
        ),
        {"schema": "app"},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    bill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    line_no: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(1), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    tax_rate_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(19, 4), default=Decimal(0), nullable=False)
    expense_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        server_default=text("now()"),
        nullable=False,
    )

    bill: Mapped["Bill"] = relationship(
        "Bill",
        back_populates="lines",
        primaryjoin="and_(BillLine.organization_id==Bill.organization_id, BillLine.bill_id==Bill.id)",
    )
