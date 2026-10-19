"""Milestone 1 Tests: Vendor Master Data, Validation, Lifecycle, and Multi-Tenant Isolation."""

import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_vendor_lifecycle_and_validation(client: AsyncClient):
    """Test vendor creation, retrieval, update, soft-deactivation, and validation."""
    # 1. Setup User and Organization
    email = f"vendor_owner_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Vendor Admin"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Procurement Org", "base_currency_code": "USD"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 2. Validation: Empty display name rejected
    bad_res = await client.post(
        "/api/v1/vendors",
        json={"display_name": "", "email": "supplier@acme.com"},
        headers=tenant_headers,
    )
    assert bad_res.status_code == 422

    # 3. Validation: Invalid email format rejected
    bad_email_res = await client.post(
        "/api/v1/vendors",
        json={"display_name": "Acme Corp", "email": "not-an-email"},
        headers=tenant_headers,
    )
    assert bad_email_res.status_code == 422

    # 4. Valid Vendor Creation
    vendor_payload = {
        "display_name": "Acme Supplies Inc",
        "email": "billing@acmesupplies.com",
        "phone": "+1-555-0199",
        "tax_identifier": "US-EIN-987654321",
        "payment_terms_days": 45,
        "billing_address": {
            "street": "123 Industrial Parkway",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "USA",
        },
    }
    create_res = await client.post(
        "/api/v1/vendors",
        json=vendor_payload,
        headers=tenant_headers,
    )
    assert create_res.status_code == 201
    vendor = create_res.json()
    assert vendor["display_name"] == "Acme Supplies Inc"
    assert vendor["is_vendor"] is True
    assert vendor["is_customer"] is False
    assert vendor["tax_identifier"] == "US-EIN-987654321"
    assert vendor["payment_terms_days"] == 45
    assert vendor["status"] == "active"
    assert vendor["billing_address"]["city"] == "Austin"
    vendor_id = vendor["id"]

    # 5. Fetch Vendor by ID
    get_res = await client.get(f"/api/v1/vendors/{vendor_id}", headers=tenant_headers)
    assert get_res.status_code == 200
    assert get_res.json()["id"] == vendor_id

    # 6. Update Vendor
    patch_res = await client.patch(
        f"/api/v1/vendors/{vendor_id}",
        json={"payment_terms_days": 60, "phone": "+1-555-0200"},
        headers=tenant_headers,
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["payment_terms_days"] == 60
    assert patch_res.json()["phone"] == "+1-555-0200"

    # 7. List Vendors
    list_res = await client.get("/api/v1/vendors", headers=tenant_headers)
    assert list_res.status_code == 200
    vendors = list_res.json()
    assert len(vendors) >= 1
    assert any(v["id"] == vendor_id for v in vendors)

    # 8. Soft-Deactivation
    deact_res = await client.post(f"/api/v1/vendors/{vendor_id}/deactivate", headers=tenant_headers)
    assert deact_res.status_code == 200
    assert deact_res.json()["status"] == "inactive"

    # 9. Verify historical vendor record is preserved
    hist_res = await client.get(f"/api/v1/vendors/{vendor_id}", headers=tenant_headers)
    assert hist_res.status_code == 200
    assert hist_res.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_vendor_cross_tenant_isolation(client: AsyncClient):
    """Test strict tenant isolation prevents cross-tenant access to vendors."""
    # Setup Tenant A
    user_a = (await client.post(
        "/api/v1/auth/register",
        json={"email": f"tenanta_{uuid.uuid4().hex[:8]}@example.com", "password": "Password123!", "full_name": "Tenant A"},
    )).json()["id"]
    org_a = (await client.post(
        "/api/v1/organizations",
        json={"name": "Org A", "base_currency_code": "USD"},
        headers={"X-User-ID": user_a},
    )).json()["id"]
    headers_a = {"X-User-ID": user_a, "X-Organization-ID": org_a}

    # Setup Tenant B
    user_b = (await client.post(
        "/api/v1/auth/register",
        json={"email": f"tenantb_{uuid.uuid4().hex[:8]}@example.com", "password": "Password123!", "full_name": "Tenant B"},
    )).json()["id"]
    org_b = (await client.post(
        "/api/v1/organizations",
        json={"name": "Org B", "base_currency_code": "USD"},
        headers={"X-User-ID": user_b},
    )).json()["id"]
    headers_b = {"X-User-ID": user_b, "X-Organization-ID": org_b}

    # Create Vendor in Tenant A
    v_a = (await client.post(
        "/api/v1/vendors",
        json={"display_name": "Confidential Supplier A"},
        headers=headers_a,
    )).json()

    # Tenant B attempts to read Tenant A's vendor -> 404
    get_res = await client.get(f"/api/v1/vendors/{v_a['id']}", headers=headers_b)
    assert get_res.status_code == 404

    # Tenant B attempts to update Tenant A's vendor -> 404
    patch_res = await client.patch(
        f"/api/v1/vendors/{v_a['id']}",
        json={"display_name": "Hacked Supplier"},
        headers=headers_b,
    )
    assert patch_res.status_code == 404

    # Tenant B's vendor listing does not show Tenant A's vendor
    list_b = (await client.get("/api/v1/vendors", headers=headers_b)).json()
    assert all(v["id"] != v_a["id"] for v in list_b)
