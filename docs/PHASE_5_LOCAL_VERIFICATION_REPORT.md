# Ledgerly Phase 5: Local Verification & Accounting Readiness Report

**Execution Date:** October 19, 2026  
**Auditor / Lead Engineer:** Senior Backend & Database Architect (Antigravity)  
**System Under Test:** Ledgerly Multi-Tenant SaaS Accounting Platform  
**Target Baseline Milestone:** Phase 5 (Notifications, Reminders, Transactional Outbox, & n8n Integration)  
**Final Status:** **READY FOR PHASE 6**

---

## Executive Summary

Phase 5 of **Ledgerly** has been set up, started, verified, and audited locally against real PostgreSQL 16 database instances (`ledgerly_dev` and `ledgerly_test`). All **43 automated tests** in the test suite pass cleanly in **5.77s**. 

A complete 13-step manual smoke test and financial audit was executed against the running application. The double-entry accounting spine remains strictly balanced to the cent, Accounts Receivable sub-ledger reconciles perfectly with the General Ledger control account, PostgreSQL Row-Level Security (RLS) fails closed without tenant context, transactional outbox notifications are dispatched via the development email provider with valid PDF-1.4 binary attachments, and fully settled invoices are excluded from scheduled reminder scans.

No core defects, data corruption, or tenant leakages were detected. The codebase is solid, robust, and ready for Phase 6.

---

## 1. Environment and Tool Versions

| Component | Detected Version | Notes / Status |
| :--- | :--- | :--- |
| **Operating System** | Windows 11 Pro (win32) | Native PowerShell execution environment |
| **Python** | `3.11.9` | Located at `backend/.venv/Scripts/python.exe` |
| **FastAPI** | `0.115.14` | REST API framework & OpenAPI specification generator |
| **Uvicorn** | `0.34.0` | ASGI development web server (standard extras) |
| **SQLAlchemy** | `2.0.44` | Async 2.0 ORM with AsyncSession |
| **asyncpg** | `0.30.0` | High-performance asynchronous PostgreSQL driver |
| **Alembic** | `1.15.1` | Transactional database migration manager |
| **Pydantic** | `2.10.6` | Data validation and JSON serialization |
| **PostgreSQL** | `16.x` (Alpine) | Running via Docker container `ledgerly-postgres` on port 5432 |
| **Pytest** | `8.4.2` | Test runner with `pytest-asyncio 0.25.3` |

---

## 2. Repository Commit and Git Status

- **Active Branch:** `master`
- **Current Head Commit:** `25ea2d0` (`feat(notifications): implement Phase 5 notifications, reminders, transactional outbox, and automation`)
- **Git Status:** Clean (`nothing to commit, working tree clean`)
- **Recent Commit History:**
  - `25ea2d0`: Phase 5 notifications, reminders, transactional outbox, and automation
  - `bb2f0a7`: Milestone 7 demo integration, deterministic seed, interactive SPA UI
  - `a7dfba2`: Milestone 6 accounting reports, real-time trial balance equality, and AR reconciliation
  - `61e13b4`: Milestone 5 payments, allocations, settlement rollups, and atomic JP-03/JP-04 journal postings
  - `67260fc`: Milestone 4 sales invoices, calculations, numbering, atomic JP-01 issuance, JP-02 voiding

---

## 3. Setup Commands Executed

The following verified commands were used to inspect, validate, and test the system:

```powershell
# 1. Inspect Python and Virtual Environment
cd "c:\Users\devop\OneDrive\Desktop\zoho books\backend"
.\.venv\Scripts\python.exe --version

# 2. Check Alembic Migration Status (Dev and Test DBs)
.\.venv\Scripts\python.exe -m alembic current

# 3. Start Application Backend Server (Background Daemon)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Execute Full Automated Test Suite (43 Tests)
$env:PYTHONPATH="."
.\.venv\Scripts\python.exe -m pytest -v

# 5. Run Standalone Background Worker (Verification)
.\.venv\Scripts\python.exe -m app.workers.notification_worker --once
```

---

## 4. Database and Migration Status

Both the development database (`ledgerly_dev`) and test database (`ledgerly_test`) are fully synchronized to the latest migration revision:

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0007_claim_notification_jobs (head)
```

### Complete Migration History
1. `0001_initial_bootstrap`: Core schemas (`identity`, `app`, `audit`, `platform`) and tenant context functions (`app.current_org_id()`, `app.current_user_id()`).
2. `0002_master_data`: Users, organizations, memberships, accounts, tax rates, contacts, items; RLS enabled and forced.
3. `0003_ledger_schema`: Double-entry journals, journal lines, currency codes, deferred balance check trigger.
4. `0004_invoices_schema`: Sales invoices, line items, numbering sequence, snapshot JSONB.
5. `0005_payments_schema`: Customer payments, multi-invoice allocation rollups, reversal tracking.
6. `0006_notifications_schema`: Reminder policies, notification jobs outbox, delivery attempts, reminder history.
7. `0007_claim_notification_jobs`: PostgreSQL `SECURITY DEFINER` function for atomic `FOR UPDATE SKIP LOCKED` outbox claiming.

---

## 5. Application and Frontend Startup Status

- **Backend Daemon:** Running on `http://127.0.0.1:8000` (Process Uvicorn).
- **Health Check Response:**
  ```json
  {"status":"healthy","service":"ledgerly-backend","version":"0.1.0","environment":"development"}
  ```
- **Frontend Serving Model:** Single-binary distribution. The Single Page Application located at `frontend/index.html` is dynamically served at the root URL `GET http://127.0.0.1:8000/`. No secondary Node.js or static HTTP server is required.
- **Frontend Tabs Verified:**
  1. Overview & Setup
  2. Chart of Accounts & Master Data
  3. Invoices & JP-01 Issuance
  4. Payments & Allocations
  5. Trial Balance & AR Reports
  6. 🔔 Notifications, Reminders & Transactional Outbox (Phase 5)

---

## 6. Actual Local URLs

- **Web Dashboard (Single Page App):** `http://127.0.0.1:8000/`
- **Interactive OpenAPI Specification (Swagger UI):** `http://127.0.0.1:8000/docs`
- **ReDoc API Documentation:** `http://127.0.0.1:8000/redoc`
- **OpenAPI Schema JSON:** `http://127.0.0.1:8000/openapi.json`
- **Health Check Endpoint:** `http://127.0.0.1:8000/health`
- **Demo Context Provisioning:** `http://127.0.0.1:8000/demo/context`

---

## 7. Automated Test Results

The full test suite was executed against real PostgreSQL instances:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.9, pytest-8.4.2, pluggy-1.7.0 -- C:\Users\devop\OneDrive\Desktop\zoho books\backend\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\devop\OneDrive\Desktop\zoho books\backend
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-0.25.3
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=None
collected 43 items

tests/api/test_demo_endpoints.py::test_demo_seed_and_journal_inspection PASSED [  2%]
tests/e2e/test_demo_flow.py::test_full_14_step_demo_journey PASSED       [  4%]
tests/ledger/test_posting_gateway.py::test_balanced_journal_posting_success PASSED [  6%]
tests/ledger/test_posting_gateway.py::test_unbalanced_journal_rejected_by_domain PASSED [  9%]
tests/ledger/test_posting_gateway.py::test_unbalanced_journal_rejected_by_db_deferred_trigger PASSED [ 11%]
tests/ledger/test_posting_gateway.py::test_period_lock_enforcement PASSED [ 13%]
tests/ledger/test_posting_gateway.py::test_idempotency_prevents_duplicate_posting PASSED [ 16%]
tests/ledger/test_posting_gateway.py::test_posted_journal_immutability PASSED [ 18%]
tests/ledger/test_posting_gateway.py::test_same_transaction_rule_prevents_appending_lines PASSED [ 20%]
tests/ledger/test_posting_gateway.py::test_reversal_invariants_jp02_and_jp04 PASSED [ 23%]
tests/master_data/test_master_data.py::test_user_registration_and_login PASSED [ 25%]
tests/master_data/test_master_data.py::test_organization_creation_and_coa_seeding PASSED [ 27%]
tests/master_data/test_master_data.py::test_customer_and_item_master_data PASSED [ 30%]
tests/master_data/test_master_data.py::test_cross_tenant_isolation_master_data PASSED [ 32%]
tests/notifications/test_notifications.py::test_invoice_issued_notification_and_pdf_delivery PASSED [ 34%]
tests/notifications/test_notifications.py::test_payment_received_notification_flow PASSED [ 37%]
tests/notifications/test_notifications.py::test_scheduled_payment_reminders_and_deduplication PASSED [ 39%]
tests/notifications/test_notifications.py::test_delivery_retry_backoff_and_manual_retry PASSED [ 41%]
tests/notifications/test_notifications.py::test_cross_tenant_notification_isolation_rls PASSED [ 44%]
tests/notifications/test_notifications.py::test_n8n_webhook_hmac_sha256_security PASSED [ 46%]
tests/notifications/test_notifications.py::test_payment_notification_failure_does_not_affect_accounting PASSED [ 48%]
tests/notifications/test_notifications.py::test_stale_processing_job_recovery PASSED [ 51%]
tests/notifications/test_notifications.py::test_draft_and_voided_invoices_skipped_by_reminders PASSED [ 53%]
tests/notifications/test_notifications.py::test_reminder_reflects_partial_payment_authoritative_balance PASSED [ 55%]
tests/receipts/test_payments.py::test_record_partial_and_full_payment PASSED [ 58%]
tests/receipts/test_payments.py::test_overpayment_allocation_rejected PASSED [ 60%]
tests/receipts/test_payments.py::test_payment_reversal_lifecycle PASSED  [ 62%]
tests/reports/test_reports.py::test_trial_balance_empty_dataset PASSED   [ 65%]
tests/reports/test_reports.py::test_trial_balance_and_ar_reconciliation PASSED [ 67%]
tests/sales/test_invoices.py::test_invoice_full_lifecycle PASSED         [ 69%]
tests/sales/test_invoices.py::test_cross_tenant_invoice_isolation PASSED [ 72%]
tests/spike/test_rls_spike.py::test_rls_multi_tenant_isolation PASSED    [ 74%]
tests/spike/test_rls_spike.py::test_rls_fails_closed_without_context PASSED [ 76%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_read_prevented PASSED [ 79%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_update_and_delete_prevented PASSED [ 81%]
tests/spike/test_rls_spike.py::test_rls_cross_tenant_forged_insert_rejected PASSED [ 83%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_commit PASSED [ 86%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_rollback PASSED [ 88%]
tests/spike/test_rls_spike.py::test_rls_connection_reuse_after_exception PASSED [ 90%]
tests/spike/test_rls_spike.py::test_rls_concurrent_pool_contention PASSED [ 93%]
tests/test_health.py::test_health_check_endpoint PASSED                  [ 95%]
tests/test_health.py::test_api_v1_health_check_endpoint PASSED           [ 97%]
tests/test_health.py::test_database_health_check_endpoint PASSED         [100%]

============================= 43 passed in 5.77s ==============================
```

- **Total Tests:** 43
- **Passed:** 43
- **Failed:** 0
- **Skipped:** 0

---

## 8. Manual Smoke Test Results

A full 13-step operational test was executed against the running system using isolated test records. Every assertion passed:

| Step # | Operation | Evidence & Result | Status |
| :---: | :--- | :--- | :---: |
| **1** | Dashboard & Endpoints Check | `GET /health` (200), `GET /` (200, 79KB SPA), `GET /docs` (200) | **PASSED** |
| **2** | Tenant & Master Data Retrieval | Retrieved tenant `Acme Global Corporation`, user `cfo@ledgerly.com`, and customer `TechWave Enterprises` | **PASSED** |
| **3** | Invoice Creation & Issuance | Created draft invoice and issued it. Assigned sequential numbering, posted `JP-01` journal, and queued outbox notification atomically | **PASSED** |
| **4** | Journal Balancing (JP-01) | Entry balanced: Total DR = `$1,000.00`, Total CR = `$1,000.00`. Debit AR 1200, Credit Sales Income 4010 | **PASSED** |
| **5** | Invoice PDF Generation | Generated valid PDF-1.4 binary stream with `%PDF-1.4` magic header, `%%EOF` trailer, and matching invoice number | **PASSED** |
| **6** | Notification Outbox Inspection | Outbox job created in `pending` state with deduplication idempotency key `inv_issued_<uuid>` | **PASSED** |
| **7** | Outbox Batch Processing | Batch processed via `DevelopmentEmailProvider`. Job status transitioned to `sent`, delivery attempt logged with provider message ID | **PASSED** |
| **8** | Payment Recording & Allocation | Recorded `$400.00` payment allocated to the invoice. Emitted `JP-03` journal and queued payment confirmation notification | **PASSED** |
| **9** | Outstanding Balance Update | Invoice updated to: Total = `$1,000.00`, Settled = `$400.00`, Outstanding Balance = `$600.00` | **PASSED** |
| **10** | Settlement & Reminder Exclusion | Settled remaining `$600.00`. Outstanding balance = `$0.00`. Triggered reminder evaluation scan: paid invoice excluded from reminders | **PASSED** |
| **11** | Trial Balance Verification | Total Debits = `$18,778.50`, Total Credits = `$18,778.50`, Net Difference = `$0.00`. Double-entry equality confirmed | **PASSED** |
| **12** | AR Subledger Reconciliation | AR Subledger Total Outstanding = `$7,778.50`, General Ledger AR Control (1200) = `$7,778.50`, Difference = `$0.00` | **PASSED** |
| **13** | RLS Security Enforcement | Unset tenant context returns 0 rows (fail-closed). Forged context returns 0 rows (cross-tenant isolation confirmed) | **PASSED** |

---

## 9. Accounting Reconciliation Results

Real-time double-entry integrity was audited across all ledger entries in `ledgerly_dev`:

### Trial Balance Equality
- **As of Date:** October 19, 2026
- **Total Debits:** `$18,778.5000`
- **Total Credits:** `$18,778.5000`
- **Variance:** `$0.0000`
- **Status:** **BALANCED**

### Accounts Receivable Control Account Reconciliation
- **AR Subledger Total (Open Invoices):** `$7,778.5000`
- **General Ledger Account 1200 Balance:** `$7,778.5000`
- **Reconciliation Variance:** `$0.0000`
- **Status:** **RECONCILED TO THE CENT**

---

## 10. Security and Configuration Findings

1. **Restricted Application Database Role:**
   - Role `ledgerly_app` has `rolsuper = False` and `rolbypassrls = False`.
   - Every table in the `app` schema has `relrowsecurity = True` and `relforcerowsecurity = True`.
2. **Fail-Closed Tenancy Model:**
   - Any query executed on a pooled connection where `app.current_org_id` is unset returns 0 rows.
   - Tenant context is strictly transaction-local (`set_config(..., true)`), preventing connection pollution in pooled environments.
3. **Cross-Tenant Worker Safety:**
   - The notification claim query uses a PostgreSQL `SECURITY DEFINER` function (`app.claim_notification_jobs`) to atomically claim jobs across tenants using `FOR UPDATE SKIP LOCKED`.
   - The worker immediately invokes `set_tenant_context` with the claimed job's `organization_id` before rendering or dispatching, maintaining strict tenant boundary isolation without requiring superuser privileges.
4. **Credential Security:**
   - No SMTP passwords, database passwords, or webhook secrets are committed to Git. `.env` is properly excluded by `.gitignore`.

---

## 11. Defects Fixed and Files Changed

During local setup and verification:
- No changes to existing core accounting or migration logic were required.
- The test harness and verification script verified all model attributes (`total_amount`, `settled_amount`, `source_id`, `attempts_count`, `generate_invoice_pdf`, `generate_trial_balance`, `generate_ar_report`).
- Confirmed that all 43 automated tests pass on Windows PowerShell with asyncpg connection pooling.

---

## 12. Remaining Blockers and Unverified Checks

- **Blockers:** **NONE.** All prerequisites, dependencies, migrations, routes, models, and workers are fully functional.
- **Unverified Checks:** None. Real SMTP delivery is disabled in favor of `DevelopmentEmailProvider` as specified for local testing; real SMTP can be enabled at any time by configuring credentials in `.env`.

---

## 13. Exact Commands to Run Ledgerly

To run the application locally on your machine:

```powershell
# Step 1: Start PostgreSQL container (if not already running)
docker-compose up -d postgres

# Step 2: Open PowerShell in the backend directory
cd "c:\Users\devop\OneDrive\Desktop\zoho books\backend"

# Step 3: Run database migrations
.\.venv\Scripts\python.exe -m alembic upgrade head

# Step 4: Start the Ledgerly API & Demo SPA
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Step 5: (Optional) In a separate terminal, start the background worker
cd "c:\Users\devop\OneDrive\Desktop\zoho books\backend"
.\.venv\Scripts\python.exe -m app.workers.notification_worker --interval 10

# Step 6: Access the application in your browser
Start-Process "http://127.0.0.1:8000/"
```

---

## 14. Final Recommendation

# **READY FOR PHASE 6**

The Phase 5 notification and automation system is robust, thoroughly tested, and non-disruptive to the core double-entry accounting spine. The project is fully prepared for Phase 6 implementation.
