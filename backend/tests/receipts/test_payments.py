"""Tests for Customer Payments: Receipts, Allocations, Settlement Rollups, Atomic JP-03/JP-04, and Receivables."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Journal, JournalLine
from app.models.invoice import Invoice
from app.models.payment import Payment


@pytest.mark.asyncio
async def test_record_partial_and_full_payment(client: AsyncClient):
    """Test recording partial payment followed by final settlement payment with automatic rollups."""
    # 1. Setup User & Organization
    email = f"pay_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Cashier"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Payment Test Org", "base_currency_code": "USD"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Setup Customer
    cust_res = await client.post(
        "/api/v1/contacts",
        json={"display_name": "Beta Enterprises", "is_customer": True},
        headers=tenant_headers,
    )
    customer_id = cust_res.json()["id"]

    # 3. Create & Issue Invoice for $500.00
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=15)).isoformat(),
            "lines": [
                {
                    "description": "Enterprise Software License",
                    "quantity": "1.0000",
                    "unit_price": "500.0000",
                }
            ],
        },
        headers=tenant_headers,
    )
    invoice_id = inv_res.json()["id"]

    # Issue Invoice (JP-01 posts: Debit AR $500, Credit Sales $500)
    issue_res = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=tenant_headers)
    assert issue_res.status_code == 200
    issued_inv = issue_res.json()
    assert Decimal(issued_inv["total_amount"]) == Decimal("500.0000")
    assert Decimal(issued_inv["settled_amount"]) == Decimal("0.0000")

    # 4. Record Partial Payment of $200.00
    pay1_payload = {
        "customer_id": customer_id,
        "payment_date": date.today().isoformat(),
        "amount": "200.0000",
        "method": "bank_transfer",
        "reference": "WIRE-001",
        "allocations": [
            {
                "invoice_id": invoice_id,
                "amount": "200.0000",
            }
        ],
    }
    pay1_res = await client.post("/api/v1/payments", json=pay1_payload, headers=tenant_headers)
    assert pay1_res.status_code == 201
    pay1 = pay1_res.json()

    assert pay1["state"] == "active"
    assert Decimal(pay1["amount"]) == Decimal("200.0000")
    assert Decimal(pay1["allocated_amount"]) == Decimal("200.0000")
    pay1_journal_id = pay1["journal_id"]
    assert pay1_journal_id is not None

    # Check Invoice settled_amount rollup
    inv1_check = await client.get(f"/api/v1/invoices/{invoice_id}", headers=tenant_headers)
    assert Decimal(inv1_check.json()["settled_amount"]) == Decimal("200.0000")

    # Verify JP-03 Journal for Payment 1
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        j_stmt = select(Journal).where(Journal.id == uuid.UUID(pay1_journal_id))
        journal = (await session.execute(j_stmt)).scalar_one()

        assert journal.source_type == "payment"
        assert journal.total_amount == Decimal("200.0000")

        lines_stmt = select(JournalLine).where(JournalLine.journal_id == journal.id).order_by(JournalLine.line_no)
        lines = (await session.execute(lines_stmt)).scalars().all()

        assert len(lines) == 2
        # Debit Bank $200
        assert lines[0].debit == Decimal("200.0000")
        assert lines[0].credit == Decimal("0.0000")
        # Credit AR $200
        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("200.0000")
        assert lines[1].contact_id == uuid.UUID(customer_id)

    # 5. Record Remaining Payment of $300.00 (Full Settlement)
    pay2_payload = {
        "customer_id": customer_id,
        "payment_date": date.today().isoformat(),
        "amount": "300.0000",
        "method": "bank_transfer",
        "reference": "WIRE-002",
        "allocations": [
            {
                "invoice_id": invoice_id,
                "amount": "300.0000",
            }
        ],
    }
    pay2_res = await client.post("/api/v1/payments", json=pay2_payload, headers=tenant_headers)
    assert pay2_res.status_code == 201

    # Check Invoice settled_amount is now fully settled ($500.00)
    inv2_check = await client.get(f"/api/v1/invoices/{invoice_id}", headers=tenant_headers)
    assert Decimal(inv2_check.json()["settled_amount"]) == Decimal("500.0000")


@pytest.mark.asyncio
async def test_overpayment_allocation_rejected(client: AsyncClient):
    """Test allocating more than the outstanding receivable fails validation."""
    email = f"over_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Auditor"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Overpay Org"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    cust_res = await client.post(
        "/api/v1/contacts",
        json={"display_name": "Gamma Corp", "is_customer": True},
        headers=tenant_headers,
    )
    customer_id = cust_res.json()["id"]

    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=10)).isoformat(),
            "lines": [{"description": "Item 1", "quantity": "1.0000", "unit_price": "100.0000"}],
        },
        headers=tenant_headers,
    )
    invoice_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=tenant_headers)

    # Attempt to allocate $150 to a $100 invoice
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": customer_id,
            "payment_date": date.today().isoformat(),
            "amount": "150.0000",
            "allocations": [{"invoice_id": invoice_id, "amount": "150.0000"}],
        },
        headers=tenant_headers,
    )
    assert pay_res.status_code == 400
    assert "exceeds outstanding" in pay_res.json()["detail"]


@pytest.mark.asyncio
async def test_payment_reversal_lifecycle(client: AsyncClient):
    """Test reversing a payment receipt unallocates invoice and emits JP-04."""
    email = f"rev_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Treasurer"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Reversal Org"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    cust_res = await client.post(
        "/api/v1/contacts",
        json={"display_name": "Delta LLC", "is_customer": True},
        headers=tenant_headers,
    )
    customer_id = cust_res.json()["id"]

    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=5)).isoformat(),
            "lines": [{"description": "Consulting", "quantity": "1.0000", "unit_price": "250.0000"}],
        },
        headers=tenant_headers,
    )
    invoice_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=tenant_headers)

    # Pay $250
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": customer_id,
            "payment_date": date.today().isoformat(),
            "amount": "250.0000",
            "allocations": [{"invoice_id": invoice_id, "amount": "250.0000"}],
        },
        headers=tenant_headers,
    )
    payment_id = pay_res.json()["id"]
    original_journal_id = pay_res.json()["journal_id"]

    # Verify Invoice settled is 250
    inv_check1 = await client.get(f"/api/v1/invoices/{invoice_id}", headers=tenant_headers)
    assert Decimal(inv_check1.json()["settled_amount"]) == Decimal("250.0000")

    # Reverse the Payment
    rev_res = await client.post(
        f"/api/v1/payments/{payment_id}/reverse",
        json={"reason": "Check bounced (insufficient funds)"},
        headers=tenant_headers,
    )
    assert rev_res.status_code == 200
    reversed_pay = rev_res.json()

    assert reversed_pay["state"] == "reversed"
    assert reversed_pay["reversal_reason"] == "Check bounced (insufficient funds)"
    reversal_journal_id = reversed_pay["reversal_journal_id"]
    assert reversal_journal_id is not None
    assert reversal_journal_id != original_journal_id

    # Verify Invoice settled_amount restored to 0!
    inv_check2 = await client.get(f"/api/v1/invoices/{invoice_id}", headers=tenant_headers)
    assert Decimal(inv_check2.json()["settled_amount"]) == Decimal("0.0000")

    # Verify JP-04 Reversal Journal in Database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        j_stmt = select(Journal).where(Journal.id == uuid.UUID(reversal_journal_id))
        rev_journal = (await session.execute(j_stmt)).scalar_one()

        assert rev_journal.source_type == "payment_reversal"
        assert rev_journal.reverses_journal_id == uuid.UUID(original_journal_id)
        assert rev_journal.total_amount == Decimal("250.0000")

        lines_stmt = select(JournalLine).where(JournalLine.journal_id == rev_journal.id).order_by(JournalLine.line_no)
        lines = (await session.execute(lines_stmt)).scalars().all()

        assert len(lines) == 2
        # Debit AR $250
        assert lines[0].debit == Decimal("250.0000")
        assert lines[0].credit == Decimal("0.0000")
        assert lines[0].contact_id == uuid.UUID(customer_id)

        # Credit Bank $250
        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("250.0000")

    # Attempting to reverse again fails
    rev_again = await client.post(
        f"/api/v1/payments/{payment_id}/reverse",
        json={"reason": "Second reversal attempt"},
        headers=tenant_headers,
    )
    assert rev_again.status_code == 400
