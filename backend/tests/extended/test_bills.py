"""Milestone 2 Tests: Purchase Bills Lifecycle, Tax, Posting (JP-07), Duplicate Detection, Voiding (JP-08), and Period Lock."""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Journal, JournalLine
from app.models.organization import OrganizationSettings


@pytest.mark.asyncio
async def test_purchase_bill_lifecycle_and_accounting(client: AsyncClient):
    """Test draft creation, line calculations, posting (JP-07), duplicate detection, and voiding (JP-08)."""
    # 1. Setup User and Organization
    email = f"bill_user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Purchasing Officer"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Logistics Corp", "base_currency_code": "USD"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Create Vendor
    vend_res = await client.post(
        "/api/v1/vendors",
        json={"display_name": "ServerRack Suppliers Ltd", "tax_identifier": "US-TAX-12345"},
        headers=tenant_headers,
    )
    assert vend_res.status_code == 201
    vendor_id = vend_res.json()["id"]

    # 3. Create Tax Rate (10%)
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "Standard Input VAT", "rate_percent": "10.000000"},
        headers=tenant_headers,
    )
    tax_rate_id = tax_res.json()["id"]

    # 4. Create Draft Bill
    # Line 1: 5 units * $100 = $500 + 10% tax ($50) = $550
    # Line 2: 2 units * $250 = $500 + 0% tax ($0) = $500
    # Net: $1000.0000, Tax: $50.0000, Total: $1050.0000
    today = date.today()
    due = today + timedelta(days=30)
    bill_payload = {
        "vendor_id": vendor_id,
        "vendor_invoice_number": "INV-SRV-2026-001",
        "bill_date": today.isoformat(),
        "due_date": due.isoformat(),
        "notes": "Data center hardware upgrades",
        "lines": [
            {
                "description": "Server Hardware Units",
                "quantity": "5.0000",
                "unit_price": "100.0000",
                "tax_rate_id": tax_rate_id,
            },
            {
                "description": "Network Cables & Accessories",
                "quantity": "2.0000",
                "unit_price": "250.0000",
                "tax_rate_id": None,
            },
        ],
    }

    create_res = await client.post("/api/v1/bills", json=bill_payload, headers=tenant_headers)
    assert create_res.status_code == 201
    bill = create_res.json()
    assert bill["state"] == "draft"
    assert Decimal(bill["net_amount"]) == Decimal("1000.0000")
    assert Decimal(bill["tax_amount"]) == Decimal("50.0000")
    assert Decimal(bill["total_amount"]) == Decimal("1050.0000")
    assert Decimal(bill["paid_amount"]) == Decimal("0.0000")
    assert len(bill["lines"]) == 2
    bill_id = bill["id"]

    # 5. Duplicate Vendor Invoice Number Detection
    dup_res = await client.post("/api/v1/bills", json=bill_payload, headers=tenant_headers)
    assert dup_res.status_code == 400
    assert "already exists for this vendor" in dup_res.json()["detail"]

    # 6. Post Bill to GL (JP-07)
    post_res = await client.post(f"/api/v1/bills/{bill_id}/post", headers=tenant_headers)
    assert post_res.status_code == 200
    posted_bill = post_res.json()
    assert posted_bill["state"] == "posted"
    assert posted_bill["bill_number"].startswith("BILL-")
    assert posted_bill["posting_journal_id"] is not None

    journal_id = posted_bill["posting_journal_id"]

    # 7. Verify Journal Lines in Database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = (
            select(JournalLine)
            .where(JournalLine.organization_id == uuid.UUID(org_id), JournalLine.journal_id == uuid.UUID(journal_id))
            .order_by(JournalLine.line_no)
        )
        res = await session.execute(stmt)
        lines = list(res.scalars().all())

        # Debits: Line 1 ($500), Line 2 ($500), Tax ($50) -> Total Debits $1050
        # Credits: AP ($1050)
        total_deb = sum(l.debit for l in lines)
        total_cred = sum(l.credit for l in lines)
        assert total_deb == Decimal("1050.0000")
        assert total_cred == Decimal("1050.0000")
        assert total_deb == total_cred

    # 8. Cannot Post Again (Idempotency / State Invariant)
    repost_res = await client.post(f"/api/v1/bills/{bill_id}/post", headers=tenant_headers)
    assert repost_res.status_code == 400

    # 9. Void Bill (JP-08)
    void_res = await client.post(
        f"/api/v1/bills/{bill_id}/void",
        json={"reason": "Hardware order cancelled by vendor"},
        headers=tenant_headers,
    )
    assert void_res.status_code == 200
    voided_bill = void_res.json()
    assert voided_bill["state"] == "void"
    assert voided_bill["void_journal_id"] is not None

    # 10. Verify Reversal Journal Lines & Reason
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        rev_id = uuid.UUID(voided_bill["void_journal_id"])
        j_stmt = select(Journal).where(Journal.id == rev_id)
        j_res = await session.execute(j_stmt)
        rev_j = j_res.scalar_one()
        assert rev_j.reason == "Hardware order cancelled by vendor"

        stmt = select(JournalLine).where(JournalLine.journal_id == rev_id)

        res = await session.execute(stmt)
        rev_lines = list(res.scalars().all())

        rev_deb = sum(l.debit for l in rev_lines)
        rev_cred = sum(l.credit for l in rev_lines)
        assert rev_deb == Decimal("1050.0000")
        assert rev_cred == Decimal("1050.0000")
        # Line 1 of JP-08 is debit to AP
        assert rev_lines[0].debit == Decimal("1050.0000")


@pytest.mark.asyncio
async def test_purchase_bill_period_lock_enforcement(client: AsyncClient):
    """Test that posting a bill dated on or before the organization lock date is rejected."""
    # Setup User and Org
    email = f"bill_lock_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Controller"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Lockdown Org", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # Set Organization Lock Date to 2026-03-31
    lock_date = date(2026, 3, 31)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = select(OrganizationSettings).where(OrganizationSettings.organization_id == uuid.UUID(org_id))
        res = await session.execute(stmt)
        settings = res.scalar_one()
        settings.lock_date = lock_date
        await session.commit()

    # Create Vendor
    vend = (await client.post(
        "/api/v1/vendors",
        json={"display_name": "Closed Period Vendor"},
        headers=tenant_headers,
    )).json()

    # Create Bill dated 2026-03-15 (within closed period)
    bill = (await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vend["id"],
            "bill_date": "2026-03-15",
            "due_date": "2026-04-15",
            "lines": [
                {"description": "Old Supplies", "quantity": "1.0000", "unit_price": "200.0000"}
            ],
        },
        headers=tenant_headers,
    )).json()

    # Attempt to Post -> Should be rejected with PeriodLockedError (status 400)
    post_res = await client.post(f"/api/v1/bills/{bill['id']}/post", headers=tenant_headers)
    assert post_res.status_code == 400
    assert "Accounting period is closed" in post_res.json()["detail"]
