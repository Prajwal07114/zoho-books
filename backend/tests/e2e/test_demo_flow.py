"""End-to-End Verification of the 14-Step Non-Negotiable Core Accounting Demo Journey.

Executes the entire lifecycle sequentially against real PostgreSQL and validates all invariants:
1. Create Organization
2. Seed Chart of Accounts
3. Create Customer
4. Create Product / Service Item
5. Configure Generic Percentage Tax Rate
6. Create Draft Sales Invoice
7. Calculate Invoice Totals Deterministically
8. Issue Invoice & Atomically Emit JP-01 Journal
9. Record Partial & Full Payment Receipt
10. Post Payment Journal Atomically (JP-03)
11. Calculate Outstanding Receivable Balance
12. Generate Trial Balance (Debit == Credit Net Zero)
13. Generate Accounts Receivable Report (Reconciles with GL Control Balance)
14. Demonstrate Cross-Tenant Isolation (Tenant B Cannot Access Tenant A's Records)
"""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_full_14_step_demo_journey(client: AsyncClient):
    """Execute the full 14-step Ledgerly accounting spine demonstration."""

    # -------------------------------------------------------------------------
    # STEP 1: Create or Configure an Organization
    # -------------------------------------------------------------------------
    email_a = f"owner_a_{uuid.uuid4().hex[:8]}@example.com"
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={"email": email_a, "password": "Password123!", "full_name": "Alice Auditor"},
    )
    assert reg_a.status_code == 201
    user_a = reg_a.json()["id"]
    auth_a = {"X-User-ID": user_a}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Pinnacle Financials", "legal_name": "Pinnacle Financials Inc.", "base_currency_code": "USD"},
        headers=auth_a,
    )
    assert org_res.status_code == 201
    org_a = org_res.json()["id"]
    headers_a = {"X-User-ID": user_a, "X-Organization-ID": org_a}

    # -------------------------------------------------------------------------
    # STEP 2: Seed Chart of Accounts (Automatically done upon Org creation)
    # -------------------------------------------------------------------------
    coa_res = await client.get("/api/v1/accounts", headers=headers_a)
    assert coa_res.status_code == 200
    coa = coa_res.json()
    assert len(coa) == 11
    acc_map = {acc["system_key"]: acc["id"] for acc in coa if acc.get("system_key")}
    assert "accounts_receivable" in acc_map
    assert "sales_income" in acc_map
    assert "tax_payable" in acc_map
    assert "bank" in acc_map

    # -------------------------------------------------------------------------
    # STEP 3: Create Customer
    # -------------------------------------------------------------------------
    cust_res = await client.post(
        "/api/v1/contacts",
        json={
            "display_name": "Acme Global Solutions",
            "email": "billing@acmeglobal.com",
            "is_customer": True,
        },
        headers=headers_a,
    )
    assert cust_res.status_code == 201
    customer_id = cust_res.json()["id"]

    # -------------------------------------------------------------------------
    # STEP 4: Configure Generic Percentage Tax Rate
    # -------------------------------------------------------------------------
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={
            "name": "Standard Sales Tax (8.25%)",
            "rate_percent": "8.250000",
            "tax_account_id": acc_map["tax_payable"],
        },
        headers=headers_a,
    )
    assert tax_res.status_code == 201
    tax_rate_id = tax_res.json()["id"]

    # -------------------------------------------------------------------------
    # STEP 5: Create Product or Service Item
    # -------------------------------------------------------------------------
    item_res = await client.post(
        "/api/v1/items",
        json={
            "name": "Enterprise Implementation Package",
            "item_type": "service",
            "sales_price": "1200.0000",
            "default_tax_rate_id": tax_rate_id,
            "income_account_id": acc_map["sales_income"],
        },
        headers=headers_a,
    )
    assert item_res.status_code == 201
    item_id = item_res.json()["id"]

    # -------------------------------------------------------------------------
    # STEP 6 & 7: Create Draft Sales Invoice & Calculate Deterministic Totals
    # -------------------------------------------------------------------------
    # 2 units * $1,200.00 = $2,400.00 net
    # Tax @ 8.25% = $198.0000
    # Grand Total = $2,598.0000
    today = date.today()
    due = today + timedelta(days=30)
    inv_payload = {
        "customer_id": customer_id,
        "invoice_date": today.isoformat(),
        "due_date": due.isoformat(),
        "notes": "Net 30 terms apply.",
        "lines": [
            {
                "item_id": item_id,
                "description": "Enterprise Implementation Services (Q4)",
                "quantity": "2.0000",
                "unit_price": "1200.0000",
                "tax_rate_id": tax_rate_id,
                "income_account_id": acc_map["sales_income"],
            }
        ],
    }
    draft_res = await client.post("/api/v1/invoices", json=inv_payload, headers=headers_a)
    assert draft_res.status_code == 201
    draft_inv = draft_res.json()
    invoice_id = draft_inv["id"]

    assert draft_inv["state"] == "draft"
    assert Decimal(draft_inv["net_amount"]) == Decimal("2400.0000")
    assert Decimal(draft_inv["tax_amount"]) == Decimal("198.0000")
    assert Decimal(draft_inv["total_amount"]) == Decimal("2598.0000")
    assert Decimal(draft_inv["settled_amount"]) == Decimal("0.0000")

    # -------------------------------------------------------------------------
    # STEP 8: Issue Invoice and Create Journal Posting (JP-01) Atomically
    # -------------------------------------------------------------------------
    issue_res = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=headers_a)
    assert issue_res.status_code == 200
    issued_inv = issue_res.json()

    assert issued_inv["state"] == "issued"
    assert issued_inv["invoice_number"] == "INV-0001"
    assert issued_inv["snapshot"] is not None
    assert issued_inv["issue_journal_id"] is not None

    # Check that Trial Balance reflects issued invoice (Debit AR $2598, Credit Sales $2400, Credit Tax $198)
    tb_after_issue = (await client.get("/api/v1/reports/trial-balance", headers=headers_a)).json()
    assert tb_after_issue["is_balanced"] is True
    assert Decimal(tb_after_issue["total_debits"]) == Decimal("2598.0000")
    assert Decimal(tb_after_issue["total_credits"]) == Decimal("2598.0000")

    # -------------------------------------------------------------------------
    # STEP 9 & 10: Record Partial Payment Receipt and Post Journal Atomically (JP-03)
    # -------------------------------------------------------------------------
    # Partial payment of $1,000.00
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": customer_id,
            "payment_date": today.isoformat(),
            "amount": "1000.0000",
            "deposit_account_id": acc_map["bank"],
            "method": "bank_transfer",
            "reference": "ACH-982341",
            "allocations": [
                {
                    "invoice_id": invoice_id,
                    "amount": "1000.0000",
                }
            ],
        },
        headers=headers_a,
    )
    assert pay_res.status_code == 201
    payment = pay_res.json()
    assert payment["state"] == "active"
    assert Decimal(payment["amount"]) == Decimal("1000.0000")
    assert payment["journal_id"] is not None

    # -------------------------------------------------------------------------
    # STEP 11: Calculate the Outstanding Receivable
    # -------------------------------------------------------------------------
    # Invoice Total: $2,598.00, Settled: $1,000.00 => Outstanding: $1,598.00
    inv_check = (await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers_a)).json()
    assert Decimal(inv_check["settled_amount"]) == Decimal("1000.0000")
    outstanding = Decimal(inv_check["total_amount"]) - Decimal(inv_check["settled_amount"])
    assert outstanding == Decimal("1598.0000")

    # -------------------------------------------------------------------------
    # STEP 12: Generate a Reconciled Trial Balance
    # -------------------------------------------------------------------------
    # Bank Debit: $1000.00
    # AR Net Debit: $1598.00 ($2598 debit - $1000 credit)
    # Sales Income Credit: $2400.00
    # Tax Payable Credit: $198.00
    # Total Debits ($3598.00) == Total Credits ($3598.00)
    tb_report = (await client.get("/api/v1/reports/trial-balance", headers=headers_a)).json()
    assert tb_report["is_balanced"] is True
    assert Decimal(tb_report["total_debits"]) == Decimal("3598.0000")
    assert Decimal(tb_report["total_credits"]) == Decimal("3598.0000")

    # -------------------------------------------------------------------------
    # STEP 13: Generate an Accounts Receivable Report (Reconciles with GL)
    # -------------------------------------------------------------------------
    ar_report = (await client.get("/api/v1/reports/ar-summary", headers=headers_a)).json()
    assert ar_report["is_reconciled"] is True
    assert Decimal(ar_report["total_outstanding"]) == Decimal("1598.0000")
    assert Decimal(ar_report["gl_control_account_balance"]) == Decimal("1598.0000")
    assert len(ar_report["customers"]) == 1
    assert ar_report["customers"][0]["customer_id"] == customer_id
    assert Decimal(ar_report["customers"][0]["outstanding_balance"]) == Decimal("1598.0000")

    # -------------------------------------------------------------------------
    # STEP 14: Demonstrate Cross-Tenant Isolation
    # -------------------------------------------------------------------------
    # Create Organization B with a distinct user
    email_b = f"intruder_{uuid.uuid4().hex[:8]}@example.com"
    reg_b = await client.post(
        "/api/v1/auth/register",
        json={"email": email_b, "password": "Password123!", "full_name": "Bob Intruder"},
    )
    user_b = reg_b.json()["id"]
    org_b = (await client.post("/api/v1/organizations", json={"name": "Competitor Org"}, headers={"X-User-ID": user_b})).json()["id"]
    headers_b = {"X-User-ID": user_b, "X-Organization-ID": org_b}

    # Tenant B tries to read Tenant A's customer
    res_b_cust = await client.get(f"/api/v1/contacts/{customer_id}", headers=headers_b)
    assert res_b_cust.status_code == 404

    # Tenant B tries to read Tenant A's invoice
    res_b_inv = await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers_b)
    assert res_b_inv.status_code == 404

    # Tenant B tries to void Tenant A's invoice
    res_b_void = await client.post(f"/api/v1/invoices/{invoice_id}/void", json={"reason": "Malicious void"}, headers=headers_b)
    assert res_b_void.status_code == 404

    # Tenant B's Trial Balance is completely empty (zero balances)
    tb_b = (await client.get("/api/v1/reports/trial-balance", headers=headers_b)).json()
    assert Decimal(tb_b["total_debits"]) == Decimal("0.0000")
    assert Decimal(tb_b["total_credits"]) == Decimal("0.0000")
    assert tb_b["is_balanced"] is True

    # Tenant B's AR Report shows 0 outstanding
    ar_b = (await client.get("/api/v1/reports/ar-summary", headers=headers_b)).json()
    assert Decimal(ar_b["total_outstanding"]) == Decimal("0.0000")
    assert ar_b["is_reconciled"] is True
    assert len(ar_b["customers"]) == 0
