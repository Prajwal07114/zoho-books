"""Milestone 7 Tests: General Ledger, Profit & Loss, Balance Sheet, AP & AR Subledger Reconciliation, and Empty Data."""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_empty_financial_reports(client: AsyncClient):
    """Test that all reports return empty/zeroed balanced structures cleanly when no transactions exist."""
    email = f"empty_reports_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Auditor"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Blank Entity", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 1. Trial Balance on empty dataset
    tb_res = await client.get("/api/v1/reports/trial-balance", headers=tenant_headers)
    assert tb_res.status_code == 200
    assert tb_res.json()["is_balanced"] is True
    assert Decimal(tb_res.json()["total_debits"]) == Decimal("0.0000")

    # 2. General Ledger on empty dataset
    gl_res = await client.get("/api/v1/reports/general-ledger", headers=tenant_headers)
    assert gl_res.status_code == 200
    gl = gl_res.json()
    assert len(gl["accounts"]) > 0
    assert all(len(a["lines"]) == 0 for a in gl["accounts"])

    # 3. Profit and Loss on empty dataset
    pl_res = await client.get("/api/v1/reports/profit-and-loss", headers=tenant_headers)
    assert pl_res.status_code == 200
    pl = pl_res.json()
    assert Decimal(pl["total_income"]) == Decimal("0.0000")
    assert Decimal(pl["total_expense"]) == Decimal("0.0000")
    assert Decimal(pl["net_profit"]) == Decimal("0.0000")

    # 4. Balance Sheet on empty dataset
    bs_res = await client.get("/api/v1/reports/balance-sheet", headers=tenant_headers)
    assert bs_res.status_code == 200
    bs = bs_res.json()
    assert bs["is_balanced"] is True
    assert Decimal(bs["total_assets"]) == Decimal("0.0000")
    assert Decimal(bs["total_liabilities_and_equity"]) == Decimal("0.0000")

    # 5. Accounts Payable on empty dataset
    ap_res = await client.get("/api/v1/reports/ap-summary", headers=tenant_headers)
    assert ap_res.status_code == 200
    ap = ap_res.json()
    assert ap["is_reconciled"] is True
    assert Decimal(ap["total_outstanding"]) == Decimal("0.0000")
    assert Decimal(ap["gl_control_account_balance"]) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_full_accounting_cycle_financial_reports_and_reconciliation(client: AsyncClient):
    """Test full cycle: sales invoice, credit note, customer payment, purchase bill, supplier payment, and direct expense.
    Verify that General Ledger, Profit & Loss, Balance Sheet, AP and AR subledgers are all 100% reconciled and balanced.
    """
    # 1. Setup User and Org
    email = f"reports_fin_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Chief Accountant"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Comprehensive Financials Inc", "base_currency_code": "USD"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Setup Master Data
    cust = (await client.post(
        "/api/v1/contacts",
        json={"display_name": "Acme Client Co", "is_customer": True},
        headers=tenant_headers,
    )).json()
    customer_id = cust["id"]

    vend = (await client.post(
        "/api/v1/vendors",
        json={"display_name": "Apex Vendor Corp"},
        headers=tenant_headers,
    )).json()
    vendor_id = vend["id"]

    today = date.today()

    # 3. Opening Balance: Bank $20,000 (Equity offset $20,000)
    # Get Bank Account
    coa = (await client.get("/api/v1/accounts", headers=tenant_headers)).json()
    bank_acc = next(a for a in coa if a["system_key"] == "bank")
    expense_acc = next(a for a in coa if a["system_key"] == "general_expense")

    await client.post(
        "/api/v1/opening-balances",
        json={
            "conversion_date": (today - timedelta(days=60)).isoformat(),
            "lines": [{"account_id": bank_acc["id"], "debit": "20000.0000", "credit": "0.0000"}],
        },
        headers=tenant_headers,
    )

    # 4. Sales Transaction: Invoice $1000, Credit Note $200, Customer Payment $500
    inv = (await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer_id,
            "invoice_date": (today - timedelta(days=40)).isoformat(),
            "due_date": (today - timedelta(days=10)).isoformat(),
            "lines": [{"description": "Software Subscription", "quantity": "1.0000", "unit_price": "1000.0000"}],
        },
        headers=tenant_headers,
    )).json()
    inv_id = inv["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=tenant_headers)

    # Credit Note $200
    cn = (await client.post(
        "/api/v1/credit-notes",
        json={
            "invoice_id": inv_id,
            "credit_note_date": (today - timedelta(days=30)).isoformat(),
            "reason": "Discount credit",
            "lines": [{"description": "Discount", "quantity": "1.0000", "unit_price": "200.0000"}],
        },
        headers=tenant_headers,
    )).json()
    await client.post(f"/api/v1/credit-notes/{cn['id']}/issue", headers=tenant_headers)

    # Customer Payment $500
    await client.post(
        "/api/v1/payments",
        json={
            "customer_id": customer_id,
            "payment_date": (today - timedelta(days=20)).isoformat(),
            "amount": "500.0000",
            "allocations": [{"invoice_id": inv_id, "amount": "500.0000"}],
        },
        headers=tenant_headers,
    )
    # Remaining AR: $1000 - $200 (credit) - $500 (payment) = $300 open balance!

    # 5. Purchase Transaction: Bill $800, Supplier Payment $300
    bill = (await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vendor_id,
            "vendor_invoice_number": "APEX-2026-09",
            "bill_date": (today - timedelta(days=25)).isoformat(),
            "due_date": (today + timedelta(days=5)).isoformat(),
            "lines": [{"description": "Hardware Support", "quantity": "1.0000", "unit_price": "800.0000"}],
        },
        headers=tenant_headers,
    )).json()
    bill_id = bill["id"]
    await client.post(f"/api/v1/bills/{bill_id}/post", headers=tenant_headers)

    # Supplier Payment $300
    await client.post(
        "/api/v1/supplier-payments",
        json={
            "vendor_id": vendor_id,
            "payment_date": (today - timedelta(days=15)).isoformat(),
            "amount": "300.0000",
            "allocations": [{"bill_id": bill_id, "amount": "300.0000"}],
        },
        headers=tenant_headers,
    )
    # Remaining AP: $800 - $300 = $500 open balance!

    # 6. Direct Expense: $150
    await client.post(
        "/api/v1/expenses",
        json={
            "expense_date": (today - timedelta(days=5)).isoformat(),
            "expense_account_id": expense_acc["id"],
            "payment_account_id": bank_acc["id"],
            "net_amount": "150.0000",
            "description": "Internet Connectivity",
        },
        headers=tenant_headers,
    )

    # 7. VERIFY ACCOUNTS RECEIVABLE RECONCILIATION
    ar_res = await client.get("/api/v1/reports/ar-summary", headers=tenant_headers)
    assert ar_res.status_code == 200
    ar = ar_res.json()
    assert ar["is_reconciled"] is True
    # Subledger total == GL AR balance == $300
    assert Decimal(ar["total_outstanding"]) == Decimal("300.0000")
    assert Decimal(ar["gl_control_account_balance"]) == Decimal("300.0000")
    assert len(ar["customers"]) == 1
    assert Decimal(ar["customers"][0]["outstanding_balance"]) == Decimal("300.0000")

    # 8. VERIFY ACCOUNTS PAYABLE RECONCILIATION
    ap_res = await client.get("/api/v1/reports/ap-summary", headers=tenant_headers)
    assert ap_res.status_code == 200
    ap = ap_res.json()
    assert ap["is_reconciled"] is True
    # Subledger total == GL AP balance == $500
    assert Decimal(ap["total_outstanding"]) == Decimal("500.0000")
    assert Decimal(ap["gl_control_account_balance"]) == Decimal("500.0000")
    assert len(ap["vendors"]) == 1
    assert Decimal(ap["vendors"][0]["outstanding_balance"]) == Decimal("500.0000")

    # 9. VERIFY PROFIT AND LOSS
    # Revenue: $1000 - $200 (credit note) = $800 Net Revenue
    # Expenses: $800 (bill) + $150 (direct expense) = $950 Total Expense
    # Net Profit: $800 - $950 = -$150.0000 Net Loss
    pl_res = await client.get("/api/v1/reports/profit-and-loss", headers=tenant_headers)
    assert pl_res.status_code == 200
    pl = pl_res.json()
    assert Decimal(pl["total_income"]) == Decimal("800.0000")
    assert Decimal(pl["total_expense"]) == Decimal("950.0000")
    assert Decimal(pl["net_profit"]) == Decimal("-150.0000")

    # 10. VERIFY BALANCE SHEET
    # Assets:
    #   Bank: $20,000 (OB) + $500 (Customer Pay) - $300 (Supplier Pay) - $150 (Expense) = $20,050
    #   AR: $300
    #   Total Assets: $20,050 + $300 = $20,350.0000
    # Liabilities:
    #   AP: $500
    #   Total Liabilities: $500.0000
    # Equity:
    #   Opening Balance Equity: $20,000
    #   Current Earnings (P&L): -$150
    #   Total Equity: $19,850.0000
    # Total Liabilities + Equity = $500 + $19,850 = $20,350.0000
    # Assets ($20,350) == Liabilities & Equity ($20,350) -> IS BALANCED!
    bs_res = await client.get("/api/v1/reports/balance-sheet", headers=tenant_headers)
    assert bs_res.status_code == 200
    bs = bs_res.json()
    assert bs["is_balanced"] is True
    assert Decimal(bs["total_assets"]) == Decimal("20350.0000")
    assert Decimal(bs["total_liabilities"]) == Decimal("500.0000")
    assert Decimal(bs["current_year_earnings"]) == Decimal("-150.0000")
    assert Decimal(bs["total_liabilities_and_equity"]) == Decimal("20350.0000")

    # 11. VERIFY GENERAL LEDGER BY ACCOUNT & DATE
    gl_res = await client.get(f"/api/v1/reports/general-ledger?account_id={bank_acc['id']}", headers=tenant_headers)
    assert gl_res.status_code == 200
    gl = gl_res.json()
    bank_gl = gl["accounts"][0]
    assert bank_gl["code"] == "1020"
    assert Decimal(bank_gl["closing_balance"]) == Decimal("20050.0000")
    assert len(bank_gl["lines"]) >= 3  # payment receipt, supplier payment, expense
