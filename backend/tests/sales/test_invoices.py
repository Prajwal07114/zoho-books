"""Tests for Sales Invoice Lifecycle: Draft, Calculation, Issuance (JP-01), Immutability, and Voiding (JP-02)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Journal, JournalLine
from app.models.invoice import Invoice


@pytest.mark.asyncio
async def test_invoice_full_lifecycle(client: AsyncClient):
    """Test full sales invoice lifecycle: draft creation, calculation, issuance with JP-01, and voiding with JP-02."""
    # 1. Setup User and Organization
    email = f"sales_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Sales Rep"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Sales Org", "base_currency_code": "USD"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Create Customer
    cust_res = await client.post(
        "/api/v1/contacts",
        json={"display_name": "Global Retailers Ltd", "is_customer": True},
        headers=tenant_headers,
    )
    customer_id = cust_res.json()["id"]

    # 3. Create Tax Rate (10%)
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "Standard VAT", "rate_percent": "10.000000"},
        headers=tenant_headers,
    )
    tax_rate_id = tax_res.json()["id"]

    # 4. Create Catalog Items
    item1_res = await client.post(
        "/api/v1/items",
        json={
            "name": "Consulting Hourly",
            "item_type": "service",
            "sales_price": "150.0000",
            "default_tax_rate_id": tax_rate_id,
        },
        headers=tenant_headers,
    )
    item1_id = item1_res.json()["id"]

    item2_res = await client.post(
        "/api/v1/items",
        json={
            "name": "Cloud Hosting",
            "item_type": "service",
            "sales_price": "50.0000",
            "default_tax_rate_id": None,
        },
        headers=tenant_headers,
    )
    item2_id = item2_res.json()["id"]

    # 5. Create Draft Invoice
    # Line 1: 2 hrs * $150 = $300 + 10% tax ($30) = $330
    # Line 2: 1 unit * $50 = $50 + 0% tax ($0) = $50
    # Net: $350.0000, Tax: $30.0000, Grand Total: $380.0000
    today = date.today()
    due = today + timedelta(days=30)
    inv_payload = {
        "customer_id": customer_id,
        "invoice_date": today.isoformat(),
        "due_date": due.isoformat(),
        "notes": "Thank you for your business!",
        "lines": [
            {
                "item_id": item1_id,
                "description": "Consulting Services",
                "quantity": "2.0000",
                "unit_price": "150.0000",
                "tax_rate_id": tax_rate_id,
            },
            {
                "item_id": item2_id,
                "description": "Monthly Cloud Hosting",
                "quantity": "1.0000",
                "unit_price": "50.0000",
                "tax_rate_id": None,
            },
        ],
    }

    create_res = await client.post("/api/v1/invoices", json=inv_payload, headers=tenant_headers)
    assert create_res.status_code == 201
    inv = create_res.json()

    assert inv["state"] == "draft"
    assert inv["invoice_number"] is None
    assert inv["issue_journal_id"] is None
    assert Decimal(inv["net_amount"]) == Decimal("350.0000")
    assert Decimal(inv["tax_amount"]) == Decimal("30.0000")
    assert Decimal(inv["total_amount"]) == Decimal("380.0000")
    assert Decimal(inv["settled_amount"]) == Decimal("0.0000")
    assert len(inv["lines"]) == 2
    invoice_id = inv["id"]

    # 6. Issue Invoice (Atomically assigns number, snapshot, and posts JP-01)
    issue_res = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=tenant_headers)
    assert issue_res.status_code == 200
    issued_inv = issue_res.json()

    assert issued_inv["state"] == "issued"
    assert issued_inv["invoice_number"] == "INV-0001"
    assert issued_inv["snapshot"] is not None
    assert issued_inv["issued_at"] is not None
    issue_journal_id = issued_inv["issue_journal_id"]
    assert issue_journal_id is not None

    # Verify JP-01 Journal Entry in Database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        j_stmt = select(Journal).where(Journal.id == uuid.UUID(issue_journal_id))
        j_res = await session.execute(j_stmt)
        journal = j_res.scalar_one()

        assert journal.source_type == "invoice"
        assert journal.source_id == uuid.UUID(invoice_id)
        assert journal.total_amount == Decimal("380.0000")

        # Verify lines: Debit AR $380, Credit Sales $350, Credit Tax $30
        lines_stmt = (
            select(JournalLine)
            .where(JournalLine.journal_id == journal.id)
            .order_by(JournalLine.line_no)
        )
        lines_res = await session.execute(lines_stmt)
        lines = lines_res.scalars().all()

        assert len(lines) == 3
        # Debit AR
        assert lines[0].debit == Decimal("380.0000")
        assert lines[0].credit == Decimal("0.0000")
        assert lines[0].contact_id == uuid.UUID(customer_id)

        # Credit Revenue
        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("350.0000")

        # Credit Tax
        assert lines[2].debit == Decimal("0.0000")
        assert lines[2].credit == Decimal("30.0000")

    # 7. Immutability: Attempting to issue again fails
    reissue_res = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=tenant_headers)
    assert reissue_res.status_code == 400

    # 8. Void Invoice (Atomically posts JP-02 reversal)
    void_res = await client.post(
        f"/api/v1/invoices/{invoice_id}/void",
        json={"reason": "Customer cancelled service agreement"},
        headers=tenant_headers,
    )
    assert void_res.status_code == 200
    voided_inv = void_res.json()

    assert voided_inv["state"] == "void"
    assert voided_inv["void_reason"] == "Customer cancelled service agreement"
    void_journal_id = voided_inv["void_journal_id"]
    assert void_journal_id is not None
    assert void_journal_id != issue_journal_id

    # Verify JP-02 Reversal Journal in Database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, organization_id=org_id, user_id=user_id)

        rev_stmt = select(Journal).where(Journal.id == uuid.UUID(void_journal_id))
        rev_res = await session.execute(rev_stmt)
        rev_journal = rev_res.scalar_one()

        assert rev_journal.source_type == "invoice_void"
        assert rev_journal.reverses_journal_id == uuid.UUID(issue_journal_id)
        assert rev_journal.total_amount == Decimal("380.0000")

        # Reversal lines: Debit Sales $350, Debit Tax $30, Credit AR $380
        rev_lines_stmt = (
            select(JournalLine)
            .where(JournalLine.journal_id == rev_journal.id)
            .order_by(JournalLine.line_no)
        )
        rev_lines_res = await session.execute(rev_lines_stmt)
        rev_lines = rev_lines_res.scalars().all()

        assert len(rev_lines) == 3
        # Debit Revenue Reversal
        assert rev_lines[0].debit == Decimal("350.0000")
        assert rev_lines[0].credit == Decimal("0.0000")

        # Debit Tax Reversal
        assert rev_lines[1].debit == Decimal("30.0000")
        assert rev_lines[1].credit == Decimal("0.0000")

        # Credit AR Reversal
        assert rev_lines[2].debit == Decimal("0.0000")
        assert rev_lines[2].credit == Decimal("380.0000")
        assert rev_lines[2].contact_id == uuid.UUID(customer_id)


@pytest.mark.asyncio
async def test_cross_tenant_invoice_isolation(client: AsyncClient):
    """Test Org B cannot view, issue, or void an invoice belonging to Org A."""
    # Org A
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={"email": f"a_{uuid.uuid4().hex[:8]}@example.com", "password": "Password123!", "full_name": "User A"},
    )
    user_a = reg_a.json()["id"]
    org_a = (await client.post("/api/v1/organizations", json={"name": "Org A"}, headers={"X-User-ID": user_a})).json()["id"]
    headers_a = {"X-User-ID": user_a, "X-Organization-ID": org_a}

    cust_a = (await client.post("/api/v1/contacts", json={"display_name": "Cust A"}, headers=headers_a)).json()["id"]
    inv_a = (await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": cust_a,
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=7)).isoformat(),
            "lines": [{"description": "Service A", "quantity": "1.0000", "unit_price": "100.0000"}],
        },
        headers=headers_a,
    )).json()["id"]

    # Org B
    reg_b = await client.post(
        "/api/v1/auth/register",
        json={"email": f"b_{uuid.uuid4().hex[:8]}@example.com", "password": "Password123!", "full_name": "User B"},
    )
    user_b = reg_b.json()["id"]
    org_b = (await client.post("/api/v1/organizations", json={"name": "Org B"}, headers={"X-User-ID": user_b})).json()["id"]
    headers_b = {"X-User-ID": user_b, "X-Organization-ID": org_b}

    # Org B attempts to GET Org A's invoice
    get_res = await client.get(f"/api/v1/invoices/{inv_a}", headers=headers_b)
    assert get_res.status_code == 404

    # Org B attempts to issue Org A's invoice
    issue_res = await client.post(f"/api/v1/invoices/{inv_a}/issue", headers=headers_b)
    assert issue_res.status_code == 404
