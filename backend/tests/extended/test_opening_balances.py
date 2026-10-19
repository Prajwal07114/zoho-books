"""Milestone 6 Tests: Opening Balance Batch Recording, Equity Offset (3030), and Duplicate Prevention."""

from datetime import date
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.accounting import Account, JournalLine


@pytest.mark.asyncio
async def test_opening_balance_equity_offset_and_duplicate_prevention(client: AsyncClient):
    """Test opening balance batch posting, automatic 3030 balancing, and duplicate prevention."""
    # 1. Setup User and Org
    email = f"ob_user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Migration Specialist"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Migration NewCo", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Get Bank Account (1020) and Opening Balance Equity (3030)
    async with AsyncSessionLocal() as session:
        await set_tenant_context(session, uuid.UUID(org_id))
        stmt = select(Account).where(Account.organization_id == uuid.UUID(org_id))
        res = await session.execute(stmt)
        accounts = {a.system_key: a for a in res.scalars().all()}
        bank_id = str(accounts["bank"].id)
        equity_id = str(accounts["opening_balance_equity"].id)

    # 3. Post Unbalanced User Input: Debit Bank $15,000 (Equity offset of $15,000 automatically generated)
    ob_payload = {
        "conversion_date": "2026-01-01",
        "notes": "Historical migration from legacy accounting software",
        "lines": [
            {
                "account_id": bank_id,
                "debit": "15000.0000",
                "credit": "0.0000",
                "description": "Opening Bank Balance",
            }
        ],
    }
    ob_res = await client.post("/api/v1/opening-balances", json=ob_payload, headers=tenant_headers)
    assert ob_res.status_code == 201
    batch = ob_res.json()
    assert batch["status"] == "posted"
    assert batch["journal_id"] is not None
    journal_id = batch["journal_id"]

    # 4. Verify Double-Entry Journal in Database
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
        # Line 1: Debit Bank $15,000
        assert lines[0].account_id == uuid.UUID(bank_id)
        assert lines[0].debit == Decimal("15000.0000")
        assert lines[0].credit == Decimal("0.0000")
        # Line 2: Credit Opening Balance Equity (3030) $15,000
        assert lines[1].account_id == uuid.UUID(equity_id)
        assert lines[1].debit == Decimal("0.0000")
        assert lines[1].credit == Decimal("15000.0000")

    # 5. Verify Trial Balance is in Perfect Mathematical Balance
    tb_res = await client.get("/api/v1/reports/trial-balance?as_of_date=2026-01-01", headers=tenant_headers)
    assert tb_res.status_code == 200
    tb = tb_res.json()
    assert tb["is_balanced"] is True
    assert Decimal(tb["total_debits"]) == Decimal("15000.0000")
    assert Decimal(tb["total_credits"]) == Decimal("15000.0000")

    # 6. Duplicate Import Prevention: Second batch rejected with 400
    dup_res = await client.post("/api/v1/opening-balances", json=ob_payload, headers=tenant_headers)
    assert dup_res.status_code == 400
    assert "already exists" in dup_res.json()["detail"]
