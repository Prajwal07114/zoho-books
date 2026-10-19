"""Comprehensive Integration and Unit Tests for Ledgerly Phase 5: Notifications & Automation.

Covers:
- Invoice issuance email with dynamic PDF generation and attachment
- Payment confirmation email with partial/full allocation breakdowns
- Scheduled reminder scanning, balance revalidation, and stage deduplication
- Bounded retries, exponential backoff, dead-letter failure handling, and manual retries
- Row-Level Security (RLS) tenant isolation on notifications
- Webhook signature computation and HMAC-SHA256 verification (n8n integration)
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.models.invoice import Invoice
from app.models.notification import InvoiceReminderHistory, NotificationAttempt, NotificationJob
from app.services.email_provider import _dev_provider
from app.services.webhook_service import WebhookService, clear_dispatched_webhooks, get_dispatched_webhooks


@pytest.fixture(autouse=True)
def clean_dev_provider():
    """Ensure clean dev mailbox and clear failure simulations between tests."""
    _dev_provider.clear()
    clear_dispatched_webhooks()
    yield
    _dev_provider.clear()
    clear_dispatched_webhooks()


async def setup_org_and_customer(client: AsyncClient, org_name: str = "Notification Test Org"):
    """Helper to create user, organization, customer with email, item, and tax rate."""
    email = f"user_{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!", "full_name": "Test User"},
    )
    user_id = reg.json()["id"]
    auth_headers = {"X-User-ID": user_id}

    org_res = await client.post(
        "/api/v1/organizations",
        json={"name": org_name, "base_currency_code": "USD"},
        headers=auth_headers,
    )
    org_id = org_res.json()["id"]
    tenant_headers = {"X-User-ID": user_id, "X-Organization-ID": org_id}

    # Create Customer with email
    cust_res = await client.post(
        "/api/v1/contacts",
        json={
            "display_name": "Acme Commercial Client",
            "email": "billing@acmeclient.com",
            "is_customer": True,
        },
        headers=tenant_headers,
    )
    customer_id = cust_res.json()["id"]

    # Create Tax Rate (10%)
    tax_res = await client.post(
        "/api/v1/tax-rates",
        json={"name": "VAT 10%", "rate_percent": "10.000000"},
        headers=tenant_headers,
    )
    tax_rate_id = tax_res.json()["id"]

    # Create Item
    item_res = await client.post(
        "/api/v1/items",
        json={
            "name": "Platform Subscription",
            "item_type": "service",
            "sales_price": "500.0000",
            "default_tax_rate_id": tax_rate_id,
        },
        headers=tenant_headers,
    )
    item_id = item_res.json()["id"]

    return {
        "user_id": user_id,
        "org_id": org_id,
        "customer_id": customer_id,
        "tax_rate_id": tax_rate_id,
        "item_id": item_id,
        "headers": tenant_headers,
    }


@pytest.mark.asyncio
async def test_invoice_issued_notification_and_pdf_delivery(client: AsyncClient):
    """Verify Feature A: Issuing an invoice atomically queues an outbox job and delivers email with PDF."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]

    # 1. Create Draft Invoice
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=30)).isoformat(),
            "notes": "Payment terms net 30",
            "lines": [
                {
                    "item_id": env["item_id"],
                    "description": "Monthly Cloud Subscription",
                    "quantity": "2.0000",
                    "unit_price": "500.0000",
                    "tax_rate_id": env["tax_rate_id"],
                }
            ],
        },
        headers=headers,
    )
    assert inv_res.status_code == 201
    invoice = inv_res.json()
    invoice_id = invoice["id"]

    # Verify Draft invoices do NOT queue notifications
    notifs_res = await client.get("/api/v1/notifications", headers=headers)
    assert notifs_res.status_code == 200
    assert len(notifs_res.json()) == 0

    # 2. Issue Invoice (JP-01 posted, Outbox job enqueued)
    issue_res = await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=headers)
    assert issue_res.status_code == 200
    issued_inv = issue_res.json()
    assert issued_inv["state"] == "issued"
    invoice_number = issued_inv["invoice_number"]

    # 3. Verify Outbox Job is pending
    notifs_res = await client.get("/api/v1/notifications", headers=headers)
    assert notifs_res.status_code == 200
    jobs = notifs_res.json()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["event_type"] == "invoice_issued"
    assert job["recipient_email"] == "billing@acmeclient.com"
    assert job["status"] == "pending"
    assert job["attempts_count"] == 0
    assert job["idempotency_key"] == f"inv_issued_{invoice_id}"

    # 4. Trigger Worker Batch Processing
    proc_res = await client.post("/api/v1/notifications/process", headers=headers)
    assert proc_res.status_code == 200
    proc_data = proc_res.json()
    assert proc_data["claimed"] == 1
    assert proc_data["succeeded"] == 1
    assert proc_data["failed"] == 0

    # 5. Verify Job is now 'sent'
    detail_res = await client.get(f"/api/v1/notifications/{job['id']}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["status"] == "sent"
    assert detail["sent_at"] is not None
    assert len(detail["attempts"]) == 1
    assert detail["attempts"][0]["status"] == "success"

    # 6. Verify Email was received in Dev Mailbox with valid PDF attachment
    sent_emails = _dev_provider.get_sent_emails()
    assert len(sent_emails) == 1
    email_record = sent_emails[0]
    assert email_record["to"] == "billing@acmeclient.com"
    assert invoice_number in email_record["subject"]
    assert len(email_record["attachments"]) == 1

    pdf_att = email_record["attachments"][0]
    assert pdf_att["filename"] == f"{invoice_number}.pdf"
    assert pdf_att["content_bytes"].startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf_att["content_bytes"]


@pytest.mark.asyncio
async def test_payment_received_notification_flow(client: AsyncClient):
    """Verify Feature B: Recording a payment queues confirmation with allocations breakdown."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]

    # Create and Issue an Invoice for $1,100 ($1000 + $100 tax)
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=15)).isoformat(),
            "lines": [
                {
                    "item_id": env["item_id"],
                    "description": "Enterprise License",
                    "quantity": "2.0000",
                    "unit_price": "500.0000",
                    "tax_rate_id": env["tax_rate_id"],
                }
            ],
        },
        headers=headers,
    )
    invoice_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{invoice_id}/issue", headers=headers)

    # Process issuance email to clear the outbox
    await client.post("/api/v1/notifications/process", headers=headers)

    # Record Partial Payment of $400.00
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": env["customer_id"],
            "payment_date": date.today().isoformat(),
            "amount": "400.0000",
            "method": "bank_transfer",
            "reference": "WIRE-PARTIAL-001",
            "allocations": [{"invoice_id": invoice_id, "amount": "400.0000"}],
        },
        headers=headers,
    )
    assert pay_res.status_code == 201

    # Verify Payment Notification is queued
    notifs_res = await client.get("/api/v1/notifications?event_type=payment_received", headers=headers)
    assert notifs_res.status_code == 200
    pay_jobs = notifs_res.json()
    assert len(pay_jobs) == 1
    pay_job = pay_jobs[0]
    assert pay_job["recipient_email"] == "billing@acmeclient.com"
    assert "WIRE-PARTIAL-001" in pay_job["subject"]

    # Check payload remaining balance is $700.00 ($1100 - $400)
    detail_res = await client.get(f"/api/v1/notifications/{pay_job['id']}", headers=headers)
    detail = detail_res.json()
    allocations = detail["payload_json"]["allocations"]
    assert len(allocations) == 1
    assert Decimal(allocations[0]["amount"]) == Decimal("400.0000")
    assert Decimal(allocations[0]["balance_due"]) == Decimal("700.0000")

    # Process Outbox Batch
    await client.post("/api/v1/notifications/process", headers=headers)

    # Record Final Payment of $700.00
    pay2_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": env["customer_id"],
            "payment_date": date.today().isoformat(),
            "amount": "700.0000",
            "method": "bank_transfer",
            "reference": "WIRE-FINAL-002",
            "allocations": [{"invoice_id": invoice_id, "amount": "700.0000"}],
        },
        headers=headers,
    )
    assert pay2_res.status_code == 201

    # Verify second confirmation job has balance_due == 0
    notifs_res2 = await client.get("/api/v1/notifications?status=pending", headers=headers)
    pay2_job = notifs_res2.json()[0]
    detail2_res = await client.get(f"/api/v1/notifications/{pay2_job['id']}", headers=headers)
    allocations2 = detail2_res.json()["payload_json"]["allocations"]
    assert Decimal(allocations2[0]["balance_due"]) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_scheduled_payment_reminders_and_deduplication(client: AsyncClient):
    """Verify Feature C: Reminder engine scans upcoming, due, and overdue invoices, skipping fully paid ones."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]
    today = date.today()

    # Configure Reminder Policy: 3 days before, on due date, 3 days after
    await client.put(
        "/api/v1/notifications/reminder-policy",
        json={
            "is_enabled": True,
            "days_before_due": 3,
            "remind_on_due_date": True,
            "days_after_due": 3,
            "overdue_interval_days": 7,
            "max_overdue_reminders": 3,
        },
        headers=headers,
    )

    # Invoice 1: Upcoming (Due in exactly 3 days)
    inv1_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": (today + timedelta(days=3)).isoformat(),
            "lines": [{"item_id": env["item_id"], "description": "Consulting A", "quantity": "1.0000", "unit_price": "200.0000"}],
        },
        headers=headers,
    )
    inv1_id = inv1_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv1_id}/issue", headers=headers)

    # Invoice 2: Due Today (Due date = today)
    inv2_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": today.isoformat(),
            "lines": [{"item_id": env["item_id"], "description": "Consulting B", "quantity": "1.0000", "unit_price": "300.0000"}],
        },
        headers=headers,
    )
    inv2_id = inv2_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv2_id}/issue", headers=headers)

    # Invoice 3: Overdue (Due 3 days ago)
    inv3_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": (today - timedelta(days=30)).isoformat(),
            "due_date": (today - timedelta(days=3)).isoformat(),
            "lines": [{"item_id": env["item_id"], "description": "Consulting C", "quantity": "1.0000", "unit_price": "400.0000"}],
        },
        headers=headers,
    )
    inv3_id = inv3_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv3_id}/issue", headers=headers)

    # Invoice 4: Fully Paid Invoice (Due today, but settled)
    inv4_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": today.isoformat(),
            "lines": [{"description": "Non-taxable Consulting D", "quantity": "1.0000", "unit_price": "100.0000"}],
        },
        headers=headers,
    )
    inv4_id = inv4_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv4_id}/issue", headers=headers)

    # Pay Invoice 4 completely ($100.00)
    await client.post(
        "/api/v1/payments",
        json={
            "customer_id": env["customer_id"],
            "payment_date": today.isoformat(),
            "amount": "100.0000",
            "method": "cash",
            "allocations": [{"invoice_id": inv4_id, "amount": "100.0000"}],
        },
        headers=headers,
    )

    # Clear outbox from issuance and payments
    await client.post("/api/v1/notifications/process", headers=headers)

    # Trigger Automated Reminder Scan
    scan_res = await client.post("/api/v1/notifications/run-reminders", headers=headers)
    assert scan_res.status_code == 200
    scan_data = scan_res.json()
    assert scan_data["reminders_queued"] == 3  # Invoices 1, 2, 3 only! Invoice 4 skipped.

    # Verify Reminders in Outbox
    reminders_res = await client.get("/api/v1/notifications?event_type=payment_reminder", headers=headers)
    rem_jobs = reminders_res.json()
    assert len(rem_jobs) == 3

    # CRITICAL: Trigger scan second time immediately -> Must not queue duplicate reminders
    scan2_res = await client.post("/api/v1/notifications/run-reminders", headers=headers)
    assert scan2_res.status_code == 200
    assert scan2_res.json()["reminders_queued"] == 0


@pytest.mark.asyncio
async def test_delivery_retry_backoff_and_manual_retry(client: AsyncClient):
    """Verify Feature D: Transient failures trigger exponential backoff, dead-letter failure, and manual retry."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]

    # Issue an Invoice to create a pending job
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=10)).isoformat(),
            "lines": [{"item_id": env["item_id"], "description": "Consulting R", "quantity": "1.0000", "unit_price": "250.0000"}],
        },
        headers=headers,
    )
    inv_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=headers)

    job_res = await client.get("/api/v1/notifications?status=pending", headers=headers)
    job_id = job_res.json()[0]["id"]

    # Simulate 1 transient network failure
    _dev_provider.simulate_transient_failures(count=1)

    # First Process Attempt -> Should fail transiently and schedule retry
    p1 = await client.post("/api/v1/notifications/process", headers=headers)
    assert p1.json()["claimed"] == 1
    assert p1.json()["succeeded"] == 0

    # Job is still pending with attempts_count = 1
    detail1 = (await client.get(f"/api/v1/notifications/{job_id}", headers=headers)).json()
    assert detail1["status"] == "pending"
    assert detail1["attempts_count"] == 1
    assert len(detail1["attempts"]) == 1
    assert detail1["attempts"][0]["status"] == "failure"

    # Simulate persistent permanent failure
    _dev_provider.simulate_permanent_failure(fail=True)

    # Force remaining attempts to exhaust max_attempts by advancing next_retry_at
    for _ in range(4):
        async with AsyncSessionLocal() as s:
            await set_tenant_context(s, organization_id=env["org_id"])
            await s.execute(
                update(NotificationJob)
                .where(NotificationJob.id == job_id)
                .values(next_retry_at=datetime.utcnow() - timedelta(seconds=1))
            )
            await s.commit()
        await client.post("/api/v1/notifications/process", headers=headers)

    # Job must now be permanently 'failed' (Dead Letter)
    detail_final = (await client.get(f"/api/v1/notifications/{job_id}", headers=headers)).json()
    assert detail_final["status"] == "failed"
    assert detail_final["failed_at"] is not None

    # Clear failure simulation on provider
    _dev_provider.simulate_permanent_failure(fail=False)

    # Test Manual Retry Action
    retry_res = await client.post(f"/api/v1/notifications/{job_id}/retry", headers=headers)
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "pending"

    # Process again -> Should now succeed
    p_success = await client.post("/api/v1/notifications/process", headers=headers)
    assert p_success.json()["succeeded"] == 1

    detail_recovered = (await client.get(f"/api/v1/notifications/{job_id}", headers=headers)).json()
    assert detail_recovered["status"] == "sent"


@pytest.mark.asyncio
async def test_cross_tenant_notification_isolation_rls(client: AsyncClient):
    """Verify Security Requirement: Cross-tenant read, retry, and RLS isolation on notification jobs."""
    org_a = await setup_org_and_customer(client, org_name="Tenant A")
    org_b = await setup_org_and_customer(client, org_name="Tenant B")

    # Issue an invoice in Tenant A to create a notification job
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": org_a["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=10)).isoformat(),
            "lines": [{"item_id": org_a["item_id"], "description": "Consulting T", "quantity": "1.0000", "unit_price": "100.0000"}],
        },
        headers=org_a["headers"],
    )
    inv_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=org_a["headers"])

    # Org A sees the job
    jobs_a = (await client.get("/api/v1/notifications", headers=org_a["headers"])).json()
    assert len(jobs_a) == 1
    job_a_id = jobs_a[0]["id"]

    # Org B queries /notifications -> MUST NOT see Org A's job
    jobs_b = (await client.get("/api/v1/notifications", headers=org_b["headers"])).json()
    assert len(jobs_b) == 0

    # Org B attempts to inspect Org A's job by ID -> 404
    probe_get = await client.get(f"/api/v1/notifications/{job_a_id}", headers=org_b["headers"])
    assert probe_get.status_code == 404

    # Org B attempts to trigger retry on Org A's job -> 404
    probe_retry = await client.post(f"/api/v1/notifications/{job_a_id}/retry", headers=org_b["headers"])
    assert probe_retry.status_code == 404


@pytest.mark.asyncio
async def test_n8n_webhook_hmac_sha256_security():
    """Verify Milestone 7: Outbound Webhook HMAC-SHA256 signature generation and timing-safe verification."""
    secret = "test-secret-key-phase5"
    sample_data = {
        "invoice_id": str(uuid.uuid4()),
        "invoice_number": "INV-0099",
        "total_amount": "1250.0000",
    }
    org_id = uuid.uuid4()

    # Dispatch event
    result = await WebhookService.dispatch_event(
        event_type="invoice.issued",
        organization_id=org_id,
        data=sample_data,
        secret=secret,
    )
    assert result["delivered"] is True
    assert result["signature"].startswith("sha256=")

    # Test signature verification
    import json
    raw_payload = json.dumps(result["payload"], separators=(",", ":"), default=str).encode("utf-8")
    assert WebhookService.verify_signature(raw_payload, result["signature"], secret) is True

    # Test tampered payload fails verification
    tampered_payload = json.dumps({**result["payload"], "data": {"tampered": True}}, separators=(",", ":")).encode("utf-8")
    assert WebhookService.verify_signature(tampered_payload, result["signature"], secret) is False

    # Test wrong secret fails verification
    assert WebhookService.verify_signature(raw_payload, result["signature"], "wrong-secret") is False


@pytest.mark.asyncio
async def test_payment_notification_failure_does_not_affect_accounting(client: AsyncClient):
    """Verify Invariant: Notification failure never rolls back or mutates accounting entries or balances."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]

    # 1. Issue an Invoice for $500
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=15)).isoformat(),
            "lines": [{"description": "Audit Consulting", "quantity": "1.0000", "unit_price": "500.0000"}],
        },
        headers=headers,
    )
    inv_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=headers)
    await client.post("/api/v1/notifications/process", headers=headers)

    # 2. Simulate permanent email provider failure
    _dev_provider.simulate_permanent_failure(fail=True)

    # 3. Record Payment of $200
    pay_res = await client.post(
        "/api/v1/payments",
        json={
            "customer_id": env["customer_id"],
            "payment_date": date.today().isoformat(),
            "amount": "200.0000",
            "method": "bank_transfer",
            "reference": "SAFE-FAIL-001",
            "allocations": [{"invoice_id": inv_id, "amount": "200.0000"}],
        },
        headers=headers,
    )
    assert pay_res.status_code == 201
    payment_data = pay_res.json()
    assert payment_data["state"] == "active"
    assert Decimal(payment_data["allocated_amount"]) == Decimal("200.0000")

    # 4. Process outbox batch -> Notification delivery fails
    p_res = await client.post("/api/v1/notifications/process", headers=headers)
    assert p_res.json()["failed"] == 1 or p_res.json()["succeeded"] == 0

    # 5. Verify authoritative accounting entries remain 100% intact
    # Check invoice settled amount
    inv_check = (await client.get(f"/api/v1/invoices", headers=headers)).json()
    matched_inv = next(i for i in inv_check if i["id"] == inv_id)
    assert Decimal(matched_inv["settled_amount"]) == Decimal("200.0000")

    # Check Trial Balance remains balanced
    tb_res = await client.get("/api/v1/reports/trial-balance", headers=headers)
    assert tb_res.status_code == 200
    assert tb_res.json()["is_balanced"] is True


@pytest.mark.asyncio
async def test_stale_processing_job_recovery(client: AsyncClient):
    """Verify Feature D: Worker crash recovery where jobs stuck in 'processing' are safely reclaimed."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]

    # Issue an Invoice to create a pending job
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": date.today().isoformat(),
            "due_date": (date.today() + timedelta(days=10)).isoformat(),
            "lines": [{"description": "Recovery Service", "quantity": "1.0000", "unit_price": "150.0000"}],
        },
        headers=headers,
    )
    inv_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=headers)

    job_res = await client.get("/api/v1/notifications?status=pending", headers=headers)
    job_id = job_res.json()[0]["id"]

    # Simulate worker crash: job was marked 'processing' 15 minutes ago (older than 300s timeout)
    async with AsyncSessionLocal() as s:
        await set_tenant_context(s, organization_id=env["org_id"])
        await s.execute(
            update(NotificationJob)
            .where(NotificationJob.id == job_id)
            .values(
                status="processing",
                locked_at=datetime.utcnow() - timedelta(minutes=15),
                locked_by="dead-worker-pid-9999",
            )
        )
        await s.commit()

    # Worker runs -> must reclaim the stale processing job and deliver it
    p_res = await client.post("/api/v1/notifications/process", headers=headers)
    assert p_res.json()["claimed"] == 1
    assert p_res.json()["succeeded"] == 1

    detail = (await client.get(f"/api/v1/notifications/{job_id}", headers=headers)).json()
    assert detail["status"] == "sent"


@pytest.mark.asyncio
async def test_draft_and_voided_invoices_skipped_by_reminders(client: AsyncClient):
    """Verify Feature C: Draft and voided invoices are strictly skipped by the reminder engine."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]
    today = date.today()

    # Configure reminder policy
    await client.put(
        "/api/v1/notifications/reminder-policy",
        json={
            "is_enabled": True,
            "days_before_due": 3,
            "remind_on_due_date": True,
            "days_after_due": 3,
            "overdue_interval_days": 7,
            "max_overdue_reminders": 3,
        },
        headers=headers,
    )

    # 1. Draft invoice due today
    await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": today.isoformat(),
            "lines": [{"description": "Draft Line", "quantity": "1.0000", "unit_price": "200.0000"}],
        },
        headers=headers,
    )

    # 2. Voided invoice due today
    inv2 = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": today.isoformat(),
            "lines": [{"description": "Void Line", "quantity": "1.0000", "unit_price": "300.0000"}],
        },
        headers=headers,
    )
    inv2_id = inv2.json()["id"]
    await client.post(f"/api/v1/invoices/{inv2_id}/issue", headers=headers)
    void_res = await client.post(
        f"/api/v1/invoices/{inv2_id}/void",
        json={"reason": "Order cancelled by client"},
        headers=headers,
    )
    assert void_res.status_code == 200

    # Clear outbox from issuance
    await client.post("/api/v1/notifications/process", headers=headers)

    # Run reminder scan -> Draft and Voided invoices must be skipped!
    scan_res = await client.post("/api/v1/notifications/run-reminders", headers=headers)
    assert scan_res.status_code == 200
    assert scan_res.json()["reminders_queued"] == 0


@pytest.mark.asyncio
async def test_reminder_reflects_partial_payment_authoritative_balance(client: AsyncClient):
    """Verify Feature C: Partially paid invoice reminder calculates and displays authoritative remaining balance."""
    env = await setup_org_and_customer(client)
    headers = env["headers"]
    today = date.today()

    # Configure reminder policy
    await client.put(
        "/api/v1/notifications/reminder-policy",
        json={
            "is_enabled": True,
            "days_before_due": 0,
            "remind_on_due_date": True,
            "days_after_due": 3,
            "overdue_interval_days": 7,
            "max_overdue_reminders": 3,
        },
        headers=headers,
    )

    # 1. Issue an Invoice for $800.00 (Due today)
    inv_res = await client.post(
        "/api/v1/invoices",
        json={
            "customer_id": env["customer_id"],
            "invoice_date": today.isoformat(),
            "due_date": today.isoformat(),
            "lines": [{"description": "Web Development", "quantity": "1.0000", "unit_price": "800.0000"}],
        },
        headers=headers,
    )
    inv_id = inv_res.json()["id"]
    await client.post(f"/api/v1/invoices/{inv_id}/issue", headers=headers)

    # 2. Record Partial Payment of $350.00 -> Remaining balance is $450.00
    await client.post(
        "/api/v1/payments",
        json={
            "customer_id": env["customer_id"],
            "payment_date": today.isoformat(),
            "amount": "350.0000",
            "method": "bank_transfer",
            "allocations": [{"invoice_id": inv_id, "amount": "350.0000"}],
        },
        headers=headers,
    )

    # Clear issuance & payment outbox jobs
    await client.post("/api/v1/notifications/process", headers=headers)

    # 3. Trigger Reminder Scan
    scan_res = await client.post("/api/v1/notifications/run-reminders", headers=headers)
    assert scan_res.status_code == 200
    assert scan_res.json()["reminders_queued"] == 1

    # 4. Inspect the reminder job's payload
    rem_jobs = (await client.get("/api/v1/notifications?event_type=payment_reminder", headers=headers)).json()
    assert len(rem_jobs) == 1
    rem_detail = (await client.get(f"/api/v1/notifications/{rem_jobs[0]['id']}", headers=headers)).json()
    p = rem_detail["payload_json"]
    assert Decimal(p["total_amount"]) == Decimal("800.0000")
    assert Decimal(p["settled_amount"]) == Decimal("350.0000")
    assert Decimal(p["balance_due"]) == Decimal("450.0000")

