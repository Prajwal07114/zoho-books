"""Milestone 3 Tests: Direct Business Expense Recording (JP-11), Account Category Validation, and Reversal (JP-12)."""

from datetime import date
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Account, Journal, JournalLine


@pytest.mark.asyncio
async def test_direct_expense_lifecycle_and_accounting(client: AsyncClient):
    """Test expense recording with generic tax (JP-11), double-entry balance, and reversal (JP-12)."""
    # 1. Setup User and Org
    email = f"exp_user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Office Manager"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Operations Hub", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Get Accounts (Expense: 5010, Bank: 1020, AP: 2010)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = select(Account).where(Account.organization_id == uuid.UUID(org_id))
        res = await session.execute(stmt)
        accounts = {a.system_key: a for a in res.scalars().all()}
        expense_acc_id = str(accounts["general_expense"].id)
        bank_acc_id = str(accounts["bank"].id)
        ap_acc_id = str(accounts["accounts_payable"].id)

    # 3. Create Tax Rate (10%)
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "Input GST", "rate_percent": "10.000000"},
        headers=tenant_headers,
    )
    tax_rate_id = tax_res.json()["id"]

    # 4. Reject Ineligible Account Category (AP is a liability, not an expense)
    bad_res = await client.post(
        "/api/v1/expenses",
        json={
            "expense_date": date.today().isoformat(),
            "expense_account_id": ap_acc_id,
            "payment_account_id": bank_acc_id,
            "net_amount": "100.0000",
            "description": "Invalid expense account",
        },
        headers=tenant_headers,
    )
    assert bad_res.status_code == 400
    assert "not eligible for expense recording" in bad_res.json()["detail"]

    # 5. Record Valid Expense: $200.0000 Net + 10% tax ($20.0000) = $220.0000 Total
    exp_payload = {
        "expense_date": date.today().isoformat(),
        "expense_account_id": expense_acc_id,
        "payment_account_id": bank_acc_id,
        "tax_rate_id": tax_rate_id,
        "net_amount": "200.0000",
        "description": "Office Stationery & Refreshments",
        "reference": "RCPT-88219",
    }
    create_res = await client.post("/api/v1/expenses", json=exp_payload, headers=tenant_headers)
    assert create_res.status_code == 201
    expense = create_res.json()
    assert Decimal(expense["net_amount"]) == Decimal("200.0000")
    assert Decimal(expense["tax_amount"]) == Decimal("20.0000")
    assert Decimal(expense["total_amount"]) == Decimal("220.0000")
    assert expense["payment_status"] == "posted"
    assert expense["posting_journal_id"] is not None
    expense_id = expense["id"]
    journal_id = expense["posting_journal_id"]

    # 6. Verify JP-11 Journal in Database
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = (
            select(JournalLine)
            .where(JournalLine.organization_id == uuid.UUID(org_id), JournalLine.journal_id == uuid.UUID(journal_id))
            .order_by(JournalLine.line_no)
        )
        res = await session.execute(stmt)
        lines = list(res.scalars().all())

        # Debits: Expense ($200), Tax ($20) -> $220
        # Credits: Bank ($220)
        total_deb = sum(l.debit for l in lines)
        total_cred = sum(l.credit for l in lines)
        assert total_deb == Decimal("220.0000")
        assert total_cred == Decimal("220.0000")
        assert lines[0].account_id == uuid.UUID(expense_acc_id)
        assert lines[0].debit == Decimal("200.0000")
        assert lines[2].account_id == uuid.UUID(bank_acc_id)
        assert lines[2].credit == Decimal("220.0000")

    # 7. Void Expense (JP-12)
    void_res = await client.post(
        f"/api/v1/expenses/{expense_id}/void",
        json={"reason": "Incorrect vendor receipt charged"},
        headers=tenant_headers,
    )
    assert void_res.status_code == 200
    voided_exp = void_res.json()
    assert voided_exp["payment_status"] == "void"
    assert voided_exp["void_journal_id"] is not None

    # 8. Verify JP-12 Reversal Journal Lines
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        rev_id = uuid.UUID(voided_exp["void_journal_id"])
        stmt = select(JournalLine).where(JournalLine.journal_id == rev_id).order_by(JournalLine.line_no)
        res = await session.execute(stmt)
        rev_lines = list(res.scalars().all())

        # Line 1: Debit Bank ($220)
        # Line 2: Credit Expense ($200)
        # Line 3: Credit Tax ($20)
        assert rev_lines[0].account_id == uuid.UUID(bank_acc_id)
        assert rev_lines[0].debit == Decimal("220.0000")
        assert rev_lines[1].account_id == uuid.UUID(expense_acc_id)
        assert rev_lines[1].credit == Decimal("200.0000")
        assert sum(l.debit for l in rev_lines) == sum(l.credit for l in rev_lines)
