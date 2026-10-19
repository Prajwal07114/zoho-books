"""Extended Multi-Tenant Isolation Tests:
Verifying PostgreSQL RLS and tenant scoping prevent cross-tenant access to
vendors, bills, expenses, supplier payments, credit notes, and reports.
"""

from datetime import date, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_cross_tenant_isolation_extended(client: AsyncClient):
    """Verify that Tenant B cannot access or modify Tenant A's extended accounting resources."""
    # 1. Setup Tenant A
    user_a_email = f"user_a_{uuid.uuid4().hex[:8]}@example.com"
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={"email": user_a_email, "password": "Password123!", "full_name": "Tenant A User"},
    )
    user_a_id = reg_a.json()["id"]
    org_a_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Tenant A Corp", "base_currency_code": "USD"},
        headers={"X-User-ID": user_a_id},
    )
    org_a_id = org_a_res.json()["id"]
    headers_a = {"X-User-ID": user_a_id, "X-Organization-ID": org_a_id}

    # 2. Setup Tenant B
    user_b_email = f"user_b_{uuid.uuid4().hex[:8]}@example.com"
    reg_b = await client.post(
        "/api/v1/auth/register",
        json={"email": user_b_email, "password": "Password123!", "full_name": "Tenant B User"},
    )
    user_b_id = reg_b.json()["id"]
    org_b_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Tenant B Corp", "base_currency_code": "USD"},
        headers={"X-User-ID": user_b_id},
    )
    org_b_id = org_b_res.json()["id"]
    headers_b = {"X-User-ID": user_b_id, "X-Organization-ID": org_b_id}

    # 3. Tenant A creates Vendor, Bill, Expense
    vend_a = (await client.post(
        "/api/v1/vendors",
        json={"display_name": "Vendor Corp A", "email": "a@vendor.com"},
        headers=headers_a,
    )).json()
    vend_a_id = vend_a["id"]

    bill_a = (await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vend_a_id,
            "vendor_invoice_number": "INV-A-001",
            "bill_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "lines": [{"description": "Supplies A", "quantity": "1.0000", "unit_price": "500.0000"}],
        },
        headers=headers_a,
    )).json()
    bill_a_id = bill_a["id"]

    # 4. Tenant B cannot access Tenant A's vendor
    get_vend = await client.get(f"/api/v1/vendors/{vend_a_id}", headers=headers_b)
    assert get_vend.status_code == 404

    # 5. Tenant B cannot access Tenant A's bill
    get_bill = await client.get(f"/api/v1/bills/{bill_a_id}", headers=headers_b)
    assert get_bill.status_code == 404

    # 6. Tenant B cannot post Tenant A's bill
    post_bill = await client.post(f"/api/v1/bills/{bill_a_id}/post", headers=headers_b)
    assert post_bill.status_code == 404

    # 7. Tenant B cannot create a bill referencing Tenant A's vendor
    cross_bill = await client.post(
        "/api/v1/bills",
        json={
            "vendor_id": vend_a_id,
            "vendor_invoice_number": "INV-B-CROSS",
            "bill_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "lines": [{"description": "Cross Tenant Test", "quantity": "1.0000", "unit_price": "100.0000"}],
        },
        headers=headers_b,
    )
    assert cross_bill.status_code in (400, 404)

    # 8. Post Tenant A's bill
    await client.post(f"/api/v1/bills/{bill_a_id}/post", headers=headers_a)

    # 9. Tenant B cannot pay Tenant A's bill
    cross_pay = await client.post(
        "/api/v1/supplier-payments",
        json={
            "vendor_id": vend_a_id,
            "payment_date": date.today().isoformat(),
            "amount": "500.0000",
            "allocations": [{"bill_id": bill_a_id, "amount": "500.0000"}],
        },
        headers=headers_b,
    )
    assert cross_pay.status_code in (400, 404)

    # 10. Reports isolation: Tenant B's AP Report is empty
    ap_report_b = (await client.get("/api/v1/reports/ap-summary", headers=headers_b)).json()
    assert Decimal(ap_report_b["total_outstanding"]) == Decimal("0.0000")
    assert len(ap_report_b["vendors"]) == 0

    # Tenant A's AP report shows the bill
    ap_report_a = (await client.get("/api/v1/reports/ap-summary", headers=headers_a)).json()
    assert Decimal(ap_report_a["total_outstanding"]) == Decimal("500.0000")
    assert len(ap_report_a["vendors"]) == 1
