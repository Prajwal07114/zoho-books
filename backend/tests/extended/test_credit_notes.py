"""Milestone 5 Tests: Sales Credit Notes Lifecycle, Over-Crediting Prevention, Posting (JP-05), and Reversals (JP-06)."""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import JournalLine
from app.models.invoice import Invoice


@pytest.mark.asyncio
async def test_credit_note_lifecycle_and_accounting(client: AsyncClient):
    """Test credit note draft creation, over-crediting prevention, JP-05 posting, and JP-06 void reversal."""
    # 1. Setup User and Org
    email = f"cn_user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Credit Officer"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Retail Commercial Org", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Create Customer & Tax Rate (10%)
    cust = (await client.post(
        "/api/v1/contacts",
        json={"display_name": "Valued Enterprise Client", "is_customer": True},
        headers=tenant_headers,
    )).json()
    customer_id = cust["id"]

    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "Sales VAT", "rate_percent": "10.000000"},
        headers=tenant_headers,
    )
    tax_rate_id = tax_res.json()["id"]

    # 3. Create and Issue Sales Invoice: $1000 Net + 10% tax ($100) = $1100 Grand Total
    today = date.today()
    inv = (await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": today.isoformat(),
            "due_date": (today + timedelta(days=30)).isoformat(),
            "lines": [
                {
                    "description": "Enterprise Software Licenses",
                    "quantity": "10.0000",
                    "unit_price": "100.0000",
                    "tax_rate_id": tax_rate_id,
                }
            ],
        },
        headers=tenant_headers,
    )).json()
    inv_id = inv["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=tenant_headers)

    # 4. Over-Crediting Rejection: Attempting to credit $1500 (invoice total is $1100)
    bad_cn = await client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": inv_id,
            "credit_note_date": today.isoformat(),
            "reason": "Customer over-billing compensation",
            "lines": [
                {
                    "description": "Excess credit requested",
                    "quantity": "15.0000",
                    "unit_price": "100.0000",
                    "tax_rate_id": None,
                }
            ],
        },
        headers=tenant_headers,
    )
    assert bad_cn.status_code == 400
    assert "exceeds maximum available credit" in bad_cn.json()["detail"]

    # 5. Create Valid Credit Note: Return 2 units ($200 net + 10% tax ($20) = $220 total)
    cn_payload = {
        "invoice_id": inv_id,
        "credit_note_date": today.isoformat(),
        "reason": "Return of 2 defective license seats",
        "lines": [
            {
                "description": "License seat return",
                "quantity": "2.0000",
                "unit_price": "100.0000",
                "tax_rate_id": tax_rate_id,
            }
        ],
    }
    create_res = await client.post("/api/v1/credit-notes", json=cn_payload, headers=tenant_headers)
    assert create_res.status_code == 201
    credit_note = create_res.json()
    assert credit_note["state"] == "draft"
    assert Decimal(credit_note["net_amount"]) == Decimal("200.0000")
    assert Decimal(credit_note["tax_amount"]) == Decimal("20.0000")
    assert Decimal(credit_note["total_amount"]) == Decimal("220.0000")
    cn_id = credit_note["id"]

    # 6. Issue Credit Note (JP-05)
    issue_res = await client.post(f"/api/v1/credit-notes/{cn_id}/issue", headers=tenant_headers)
    assert issue_res.status_code == 200
    issued_cn = issue_res.json()
    assert issued_cn["state"] == "issued"
    assert issued_cn["credit_note_number"].startswith("CN-")
    assert issued_cn["issue_journal_id"] is not None
    journal_id = issued_cn["issue_journal_id"]

    # 7. Verify Invoice credit_noted_amount Rollup Updated
    inv_check = (await client.get(f"/api/v1/invoices/{inv_id}", headers=tenant_headers)).json()
    assert Decimal(inv_check["credit_noted_amount"]) == Decimal("220.0000")

    # 8. Verify JP-05 Journal Lines in Database (Debit Revenue $200, Debit Tax $20, Credit AR $220)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = (
            select(JournalLine)
            .where(JournalLine.organization_id == uuid.UUID(org_id), JournalLine.journal_id == uuid.UUID(journal_id))
            .order_by(JournalLine.line_no)
        )
        res = await session.execute(stmt)
        lines = list(res.scalars().all())

        total_deb = sum(l.debit for l in lines)
        total_cred = sum(l.credit for l in lines)
        assert total_deb == Decimal("220.0000")
        assert total_cred == Decimal("220.0000")
        assert lines[0].debit == Decimal("200.0000")  # Sales revenue reduction
        assert lines[1].debit == Decimal("20.0000")   # Tax liability reduction
        assert lines[2].credit == Decimal("220.0000") # Accounts receivable reduction

    # 9. Void Credit Note (JP-06)
    void_res = await client.post(
        f"/api/v1/credit-notes/{cn_id}/void",
        json={"reason": "Customer decided to keep all license seats"},
        headers=tenant_headers,
    )
    assert void_res.status_code == 200
    voided_cn = void_res.json()
    assert voided_cn["state"] == "void"
    assert voided_cn["void_journal_id"] is not None

    # 10. Verify Invoice credit_noted_amount Restored to $0
    inv_restored = (await client.get(f"/api/v1/invoices/{inv_id}", headers=tenant_headers)).json()
    assert Decimal(inv_restored["credit_noted_amount"]) == Decimal("0.0000")
