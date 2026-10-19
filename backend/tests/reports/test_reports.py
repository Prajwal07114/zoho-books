"""Tests for Accounting Reports: Trial Balance Equality, AR Sub-ledger Reconciliation, and Multi-Transaction Aggregations."""

import uuid
from datetime import date, timedelta
from decimal import Decimal
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_trial_balance_empty_dataset(client: AsyncClient):
    """Test Trial Balance on an empty organization returns zeroed totals and balances without errors."""
    email = f"empty_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Audit Empty"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Empty Org"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    tb_res = await client.get("/api/v1/reports/trial-balance", headers=headers)
    assert tb_res.status_code == 200
    tb = tb_res.json()

    assert Decimal(tb["total_debits"]) == Decimal("0.0000")
    assert Decimal(tb["total_credits"]) == Decimal("0.0000")
    assert tb["is_balanced"] is True
    assert len(tb["accounts"]) == 11

    ar_res = await client.get("/api/v1/reports/ar-summary", headers=headers)
    assert ar_res.status_code == 200
    ar = ar_res.json()
    assert Decimal(ar["total_outstanding"]) == Decimal("0.0000")
    assert Decimal(ar["gl_control_account_balance"]) == Decimal("0.0000")
    assert ar["is_reconciled"] is True
    assert len(ar["customers"]) == 0


@pytest.mark.asyncio
async def test_trial_balance_and_ar_reconciliation(client: AsyncClient):
    """Test real-time trial balance equality and AR sub-ledger reconciliation across multiple invoices, partial payments, and voids."""
    # 1. Setup User & Organization
    email = f"cfo_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Chief Financial Officer"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Grand Enterprises", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Setup 2 Customers
    cust_a = (await client.post("/api/v1/contacts", json={"display_name": "Customer Alpha", "is_customer": True}, headers=headers)).json()["id"]
    cust_b = (await client.post("/api/v1/contacts", json={"display_name": "Customer Beta", "is_customer": True}, headers=headers)).json()["id"]

    today = date.today()

    # 3. Create & Issue Invoice 1 (Customer A: $1,000.00, overdue by 10 days)
    inv1_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": cust_a,
            "invoice_date": (today - timedelta(days=20)).isoformat(),
            "due_date": (today - timedelta(days=10)).isoformat(),
            "lines": [{"description": "Advisory Services", "quantity": "10.0000", "unit_price": "100.0000"}],
        },
        headers=headers,
    )
    inv1_id = inv1_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv1_id}/issue", headers=headers)

    # 4. Create & Issue Invoice 2 (Customer B: $600.00, current/not overdue)
    inv2_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": cust_b,
            "invoice_date": today.isoformat(),
            "due_date": (today + timedelta(days=15)).isoformat(),
            "lines": [{"description": "Implementation Support", "quantity": "3.0000", "unit_price": "200.0000"}],
        },
        headers=headers,
    )
    inv2_id = inv2_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv2_id}/issue", headers=headers)

    # 5. Check Initial Trial Balance & AR Report
    tb1 = (await client.get("/api/v1/reports/trial-balance", headers=headers)).json()
    assert tb1["is_balanced"] is True
    assert Decimal(tb1["total_debits"]) == Decimal("1600.0000")
    assert Decimal(tb1["total_credits"]) == Decimal("1600.0000")

    ar1 = (await client.get("/api/v1/reports/ar-summary", headers=headers)).json()
    assert Decimal(ar1["total_outstanding"]) == Decimal("1600.0000")
    assert Decimal(ar1["gl_control_account_balance"]) == Decimal("1600.0000")
    assert ar1["is_reconciled"] is True
    assert len(ar1["customers"]) == 2

    # Check Aging buckets
    cust_a_ar = next(c for c in ar1["customers"] if c["customer_id"] == cust_a)
    assert Decimal(cust_a_ar["overdue_1_30"]) == Decimal("1000.0000")
    assert Decimal(cust_a_ar["current_amount"]) == Decimal("0.0000")

    cust_b_ar = next(c for c in ar1["customers"] if c["customer_id"] == cust_b)
    assert Decimal(cust_b_ar["current_amount"]) == Decimal("600.0000")
    assert Decimal(cust_b_ar["overdue_1_30"]) == Decimal("0.0000")

    # 6. Record Partial Payment of $400.00 on Invoice 1 (Customer A)
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": cust_a,
            "payment_date": today.isoformat(),
            "amount": "400.0000",
            "allocations": [{"invoice_id": inv1_id, "amount": "400.0000"}],
        },
        headers=headers,
    )
    assert pay_res.status_code == 201

    # Check Reconciled State After Partial Payment
    tb2 = (await client.get("/api/v1/reports/trial-balance", headers=headers)).json()
    assert tb2["is_balanced"] is True
    # Debits: AR ($1600 - $400 = $1200) + Bank ($400) = $1600 debit; Sales Revenue = $1600 credit
    # Total debits in trial balance: initial AR debits ($1600) + Bank debit ($400) = $2000 debits,
    # Credits: Sales ($1600) + AR payment credit ($400) = $2000 credits
    assert Decimal(tb2["total_debits"]) == Decimal("2000.0000")
    assert Decimal(tb2["total_credits"]) == Decimal("2000.0000")

    ar2 = (await client.get("/api/v1/reports/ar-summary", headers=headers)).json()
    assert Decimal(ar2["total_outstanding"]) == Decimal("1200.0000")  # ($600 for A + $600 for B)
    assert Decimal(ar2["gl_control_account_balance"]) == Decimal("1200.0000")
    assert ar2["is_reconciled"] is True

    # 7. Void Invoice 2 (Customer B)
    void_res = await client.post(
        f"/api/v1/invoices/{inv2_id}/void",
        json={"reason": "Client revoked contract before work commenced"},
        headers=headers,
    )
    assert void_res.status_code == 200

    # Check Reconciled State After Void Reversal
    tb3 = (await client.get("/api/v1/reports/trial-balance", headers=headers)).json()
    assert tb3["is_balanced"] is True

    ar3 = (await client.get("/api/v1/reports/ar-summary", headers=headers)).json()
    assert Decimal(ar3["total_outstanding"]) == Decimal("600.0000")  # Only Customer A remains with $600
    assert Decimal(ar3["gl_control_account_balance"]) == Decimal("600.0000")
    assert ar3["is_reconciled"] is True
    assert len(ar3["customers"]) == 1
    assert ar3["customers"][0]["customer_id"] == cust_a
    assert Decimal(ar3["customers"][0]["outstanding_balance"]) == Decimal("600.0000")
