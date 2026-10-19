"""Milestone 4 Tests: Supplier Payments, Multi-Bill Allocation, Over-Allocation Prevention, and Reversals (JP-09 & JP-10)."""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import JournalLine
from app.models.bill import Bill


@pytest.mark.asyncio
async def test_supplier_payment_allocation_and_reversal(client: AsyncClient):
    """Test partial/full bill payment settlement, over-allocation prevention, and reversal."""
    # 1. Setup User and Org
    email = f"spay_user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Treasury Manager"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Treasury Org", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Create Vendor
    vend = (await client.post(
        "/api/v1/vendors",
        json={"display_name": "Global Hardware Wholesale", "tax_identifier": "GHW-9988"},
        headers=tenant_headers,
    )).json()
    vendor_id = vend["id"]

    # 3. Create and Post Bill 1: $500.0000
    bill1 = (await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vendor_id,
            "vendor_invoice_number": "V-INV-001",
            "bill_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "lines": [{"description": "SSD Drives", "quantity": "5.0000", "unit_price": "100.0000"}],
        },
        headers=tenant_headers,
    )).json()
    bill1_id = bill1["id"]
    await client.post(f"/api/v1/bills/{bill1_id}/post", headers=tenant_headers)

    # 4. Create and Post Bill 2: $300.0000
    bill2 = (await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vendor_id,
            "vendor_invoice_number": "V-INV-002",
            "bill_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "lines": [{"description": "RAM Modules", "quantity": "3.0000", "unit_price": "100.0000"}],
        },
        headers=tenant_headers,
    )).json()
    bill2_id = bill2["id"]
    await client.post(f"/api/v1/bills/{bill2_id}/post", headers=tenant_headers)

    # 5. Over-Allocation Rejection Test: allocating $600 to Bill 1 ($500 max)
    bad_alloc = await client.post(
        "/api/v1/supplier-payments",
        json={
            "vendor_id": vendor_id,
            "payment_date": date.today().isoformat(),
            "amount": "600.0000",
            "allocations": [{"bill_id": bill1_id, "amount": "600.0000"}],
        },
        headers=tenant_headers,
    )
    assert bad_alloc.status_code == 400
    assert "exceeds outstanding bill balance" in bad_alloc.json()["detail"]

    # 6. Multi-Bill Partial & Full Settlement:
    # Payment $600: $400 to Bill 1 (leaving $100 unpaid), $200 to Bill 2 (leaving $100 unpaid)
    pay_payload = {
        "vendor_id": vendor_id,
        "payment_date": date.today().isoformat(),
        "amount": "600.0000",
        "method": "bank_transfer",
        "reference": "WIRE-449102",
        "allocations": [
            {"bill_id": bill1_id, "amount": "400.0000"},
            {"bill_id": bill2_id, "amount": "200.0000"},
        ],
    }
    pay_res = await client.post("/api/v1/supplier-payments", json=pay_payload, headers=tenant_headers)
    assert pay_res.status_code == 201
    payment = pay_res.json()
    assert payment["state"] == "active"
    assert Decimal(payment["amount"]) == Decimal("600.0000")
    assert Decimal(payment["allocated_amount"]) == Decimal("600.0000")
    assert len(payment["allocations"]) == 2
    payment_id = payment["id"]
    journal_id = payment["journal_id"]

    # 7. Verify Bill Outstanding Balances Updated in DB
    b1_check = (await client.get(f"/api/v1/bills/{bill1_id}", headers=tenant_headers)).json()
    assert Decimal(b1_check["paid_amount"]) == Decimal("400.0000")

    b2_check = (await client.get(f"/api/v1/bills/{bill2_id}", headers=tenant_headers)).json()
    assert Decimal(b2_check["paid_amount"]) == Decimal("200.0000")

    # 8. Verify JP-09 Journal (Debit AP $600, Credit Bank $600)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = (
            select(JournalLine)
            .where(JournalLine.organization_id == uuid.UUID(org_id), JournalLine.journal_id == uuid.UUID(journal_id))
            .order_by(JournalLine.line_no)
        )
        res = await session.execute(stmt)
        lines = list(res.scalars().all())

        assert len(lines) == 2
        assert lines[0].debit == Decimal("600.0000")
        assert lines[0].credit == Decimal("0.0000")
        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("600.0000")

    # 9. Reverse Supplier Payment (JP-10)
    rev_res = await client.post(
        f"/api/v1/supplier-payments/{payment_id}/reverse",
        json={"reason": "Payment wire recalled by bank"},
        headers=tenant_headers,
    )
    assert rev_res.status_code == 200
    rev_pay = rev_res.json()
    assert rev_pay["state"] == "reversed"
    assert rev_pay["reversal_journal_id"] is not None

    # 10. Verify Bill Balances Restored to $0 paid
    b1_restored = (await client.get(f"/api/v1/bills/{bill1_id}", headers=tenant_headers)).json()
    assert Decimal(b1_restored["paid_amount"]) == Decimal("0.0000")

    b2_restored = (await client.get(f"/api/v1/bills/{bill2_id}", headers=tenant_headers)).json()
    assert Decimal(b2_restored["paid_amount"]) == Decimal("0.0000")
