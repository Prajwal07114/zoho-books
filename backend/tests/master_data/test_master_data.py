"""Comprehensive Master Data & Multi-Tenant Authorization Tests."""

import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_user_registration_and_login(client: AsyncClient):
    """Test user registration, duplicate prevention, and login."""
    unique_email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    payload = {
        "email": unique_email,
        "password": "StrongPassword123!",
        "full_name": "Test Founder",
    }

    # 1. Register
    reg_res = await client.post("/api/v1/auth/register", json=payload)
    assert reg_res.status_code == 201
    user_data = reg_res.json()
    assert user_data["email"] == unique_email
    assert user_data["full_name"] == "Test Founder"
    assert "id" in user_data

    # 2. Duplicate registration rejected
    dup_res = await client.post("/api/v1/auth/register", json=payload)
    assert dup_res.status_code == 409

    # 3. Login
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": unique_email, "password": "StrongPassword123!"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    token = token_data["token"]
    assert token == user_data["id"]

    # 4. Auth Me
    me_res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 200
    assert me_res.json()["id"] == user_data["id"]


@pytest.mark.asyncio
async def test_organization_creation_and_coa_seeding(client: AsyncClient):
    """Test organization creation automatically seeds standard Chart of Accounts."""
    # Register owner
    email = f"owner_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Org Owner"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    # Create Organization
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Acme Accounting", "base_currency_code": "USD"},
        headers=auth_headers,
    )
    assert org_res.status_code == 201
    org = org_res.json()
    org_id = org["id"]
    assert org["name"] == "Acme Accounting"

    # List Chart of Accounts
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}
    acc_res = await client.get("/api/v1/accounts", headers=tenant_headers)
    assert acc_res.status_code == 200
    accounts = acc_res.json()

    # Verify standard 11 accounts are present
    assert len(accounts) == 11
    system_keys = {acc["system_key"] for acc in accounts}
    expected_keys = {
        "cash",
        "bank",
        "accounts_receivable",
        "accounts_payable",
        "tax_payable",
        "owners_equity",
        "retained_earnings",
        "opening_balance_equity",
        "sales_income",
        "general_expense",
        "rounding",
    }
    assert expected_keys.issubset(system_keys)

    # Verify normal balance types
    ar_acc = next(a for a in accounts if a["system_key"] == "accounts_receivable")
    assert ar_acc["account_type"] == "asset"
    assert ar_acc["normal_balance"] == "debit"

    sales_acc = next(a for a in accounts if a["system_key"] == "sales_income")
    assert sales_acc["account_type"] == "income"
    assert sales_acc["normal_balance"] == "credit"


@pytest.mark.asyncio
async def test_customer_and_item_master_data(client: AsyncClient):
    """Test customer, tax rate, and product/service item creation."""
    # Setup user & org
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Catalog Manager"},
    )
    user_id = reg.json()["id"]
    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": "Catalog Enterprise"},
        headers={"X-User-ID": user_id},
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # 1. Create Tax Rate
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "Standard VAT", "rate_percent": "10.00"},
        headers=tenant_headers,
    )
    assert tax_res.status_code == 201
    tax = tax_res.json()
    assert float(tax["rate_percent"]) == 10.0
    tax_id = tax["id"]

    # 2. Create Customer
    cust_res = await client.post(
        "/api/v1/contacts",
        json={
            "display_name": "Global Trade Corp",
            "email": "billing@globaltrade.com",
            "phone": "+1-555-0199",
            "is_customer": True,
        },
        headers=tenant_headers,
    )
    assert cust_res.status_code == 201
    cust = cust_res.json()
    assert cust["display_name"] == "Global Trade Corp"

    # 3. Create Service Item
    item_res = await client.post(
        "/api/v1/items",
        json={
            "name": "Hourly Consulting",
            "item_type": "service",
            "sku": "SRV-CONS-01",
            "sales_price": "175.50",
            "default_tax_rate_id": tax_id,
        },
        headers=tenant_headers,
    )
    assert item_res.status_code == 201
    item = item_res.json()
    assert item["name"] == "Hourly Consulting"
    assert float(item["sales_price"]) == 175.50
    assert item["default_tax_rate_id"] == tax_id

    # 4. List Items and Contacts
    list_items = await client.get("/api/v1/items", headers=tenant_headers)
    assert len(list_items.json()) == 1

    list_contacts = await client.get("/api/v1/contacts?is_customer=true", headers=tenant_headers)
    assert len(list_contacts.json()) == 1


@pytest.mark.asyncio
async def test_cross_tenant_isolation_master_data(client: AsyncClient):
    """Verify Tenant A and Tenant B cannot access or list each other's data."""
    # Org A
    reg_a = await client.post(
        "/api/v1/auth/register",
        json={"email": f"a_{uuid.uuid4().hex[:6]}@example.com", "password": "Password123!", "full_name": "User A"},
    )
    user_a = reg_a.json()["id"]
    org_a_res = await client.post("/api/v1/organizations", json={"name": "Org A"}, headers={"X-User-ID": user_a})
    org_a = org_a_res.json()["id"]
    headers_a = {"X-User-ID": user_a, "X-Organization-ID": org_a}

    # Org B
    reg_b = await client.post(
        "/api/v1/auth/register",
        json={"email": f"b_{uuid.uuid4().hex[:6]}@example.com", "password": "Password123!", "full_name": "User B"},
    )
    user_b = reg_b.json()["id"]
    org_b_res = await client.post("/api/v1/organizations", json={"name": "Org B"}, headers={"X-User-ID": user_b})
    org_b = org_b_res.json()["id"]
    headers_b = {"X-User-ID": user_b, "X-Organization-ID": org_b}

    # Org A creates Customer A
    await client.post(
        "/api/v1/contacts",
        json={"display_name": "Customer Alpha", "is_customer": True},
        headers=headers_a,
    )

    # Org B creates Customer B
    await client.post(
        "/api/v1/contacts",
        json={"display_name": "Customer Beta", "is_customer": True},
        headers=headers_b,
    )

    # 1. Org A only sees Customer Alpha
    res_a = await client.get("/api/v1/contacts", headers=headers_a)
    contacts_a = res_a.json()
    assert len(contacts_a) == 1
    assert contacts_a[0]["display_name"] == "Customer Alpha"

    # 2. Org B only sees Customer Beta
    res_b = await client.get("/api/v1/contacts", headers=headers_b)
    contacts_b = res_b.json()
    assert len(contacts_b) == 1
    assert contacts_b[0]["display_name"] == "Customer Beta"

    # 3. User A attempts to access Org B using Org B's ID -> Must be REJECTED 403 Forbidden!
    attack_headers = {"X-User-ID": user_a, "X-Organization-ID": org_b}
    attack_res = await client.get("/api/v1/contacts", headers=attack_headers)
    assert attack_res.status_code == 403
    assert "not an active member" in attack_res.json()["detail"].lower()
