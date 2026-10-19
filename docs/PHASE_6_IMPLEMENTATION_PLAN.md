# Ledgerly Phase 6: Extended Accounting Workflows Implementation Plan

**Author:** Senior Backend, Database, and Accounting Systems Engineer  
**Status:** Complete (All 9 Milestones Implemented, Verified, and 54/54 Tests Passing)  
**Branch:** `master` (Migration `0008_extended_accounting_schema`)  
**Final Test Suite:** 54 tests passing in 8.18s  

---

## 1. Executive Overview

Phase 6 extends **Ledgerly** from its core Sales & Billing MVP (Invoices, Receipts, RLS, Transactional Outbox, Reports) into a complete double-entry business accounting application supporting:
1. **Vendor Master Data**: Contact vendor profiling, payment terms, addresses, tax IDs, and soft-deactivation.
2. **Purchase Bills (Accounts Payable)**: Draft, lines, generic tax, immutable posting (`JP-07`), voiding (`JP-08`), duplicate supplier bill prevention, and AP ledger balancing.
3. **Business Expenses**: Non-bill direct disbursements (`JP-11` / `JP-12`) categorized against postable expense accounts and bank/cash payment methods.
4. **Supplier Payments & Bill Allocations**: Disbursement recording (`JP-09` / `JP-10`), multi-bill settlement rollups, over-allocation protection, and reversal safeguards.
5. **Credit Notes**: Sales returns and invoice credits (`JP-05` / `JP-06`) with strict over-crediting prevention and AR subledger reconciliation.
6. **Opening Balances**: Conversion date batch setup (`JP-14`) with historical traceability against Opening Balance Equity (`3030`).
7. **Expanded Financial Reports**: Real-time General Ledger, Profit & Loss (Income Statement), Balance Sheet (Assets = Liabilities + Equity), and Accounts Payable Ageing sub-ledger reconciliation.
8. **Interactive SPA UI Integration**: Dedicated frontend tabs for Purchases, Expenses, Vendor Payments, Credit Notes, and Comprehensive Financial Statements.

---

## 2. Architecture & Design Principles

All Phase 6 components strictly adhere to Ledgerly's core architecture and security invariants:
- **Tenant Isolation & RLS**: Every tenant table carries `organization_id` as part of composite primary and foreign keys. All tables enforce `FORCE ROW LEVEL SECURITY`. Runtime execution strictly uses non-superuser role `ledgerly_app`.
- **Double-Entry Spine**: All financial ledger entries are emitted exclusively via `PostingGateway`. No application service or background worker directly writes to `app.journals` or `app.journal_lines`.
- **Strict Decimals**: All financial calculations use Python `Decimal` with Half-Up rounding.
- **Auditability & Immutability**: Posted documents (bills, credit notes, payments, expenses) are immutable. Corrections occur via atomic reversal journals (`JP-08`, `JP-10`, `JP-12`, `JP-06`), preserving historical integrity.
- **Period Lock Protection**: Transactions with `accounting_date <= organization_settings.lock_date` are rejected by database triggers.

---

## 3. Detailed Milestone Breakdown

### Milestone 1: Vendor Master Data
- **Model Enhancements**: Extend `app.contacts` with `billing_address`, `tax_identifier`, and `payment_terms_days`.
- **Validation**: Ensure `is_vendor=True`, valid email formats, and tenant boundary enforcement.
- **API**: `GET /api/v1/vendors`, `POST /api/v1/vendors`, `GET /api/v1/vendors/{id}`, `PUT /api/v1/vendors/{id}`, `DELETE /api/v1/vendors/{id}` (soft-archival).

### Milestone 2: Purchase Bills (Accounts Payable)
- **Schema**: Create `app.bills` and `app.bill_lines` with composite FKs.
- **Accounting Posting (`JP-07`)**:
  - Debit Expense / Asset accounts (per line)
  - Debit Tax account (generic tax, if applicable)
  - Credit Accounts Payable (`2010`)
- **Void Posting (`JP-08`)**:
  - Reversal of JP-07.
- **Idempotency & Deduplication**: Unique constraint on `(organization_id, vendor_id, vendor_invoice_number)` where `state != 'void'`.

### Milestone 3: Business Expenses
- **Schema**: Create `app.expenses` tracking direct expense vouchers.
- **Accounting Posting (`JP-11`)**:
  - Debit Expense Account (`5010` or selected expense account)
  - Debit Tax Account (if applicable)
  - Credit Cash/Bank Account (`1010` / `1020`)
- **Reversal Posting (`JP-12`)**: Reversal of JP-11.

### Milestone 4: Supplier Payments & Bill Allocations
- **Schema**: Create `app.supplier_payments` and `app.supplier_payment_allocations`.
- **Accounting Posting (`JP-09`)**:
  - Debit Accounts Payable (`2010`)
  - Credit Cash/Bank Account (`1010` / `1020`)
- **Reversal Posting (`JP-10`)**: Reversal of JP-09.
- **Invariants**: Over-allocation checks (`amount <= bill.total_amount - bill.settled_amount`), atomic rollup updates on `bills.settled_amount`.

### Milestone 5: Sales Credit Notes
- **Schema**: Create `app.credit_notes` and `app.credit_note_lines` linked to customer invoices.
- **Accounting Posting (`JP-05`)**:
  - Debit Sales Income (`4010`)
  - Debit Tax Payable (`2100`)
  - Credit Accounts Receivable (`1200`)
- **Void Posting (`JP-06`)**: Reversal of JP-05.
- **Invariants**: Credit note amount <= invoice uncredited balance. Update `invoice.credit_noted_amount`.

### Milestone 6: Opening Balances
- **Schema**: Create `app.opening_balance_batches`.
- **Accounting Posting (`JP-14`)**:
  - Debit / Credit respective accounts to establish starting balances.
  - Offsetting balancing line to `Opening Balance Equity` (`3030`).
- **Traceability**: Audit log and period lock enforcement.

### Milestone 7: Expanded Financial Reports
- **General Ledger**: Account detail, opening balance, debit/credit transactions, and closing balance for date range.
- **Profit & Loss (P&L)**: Real-time revenue and expenses aggregation, Net Income calculation.
- **Balance Sheet**: Assets vs Liabilities + Equity verification (Total Assets == Total Liabilities + Equity).
- **Accounts Payable Ageing**: Unsettled bills broken down into Current, 1-30, 31-60, 61-90, 90+ days overdue, reconciling to GL Account `2010`.

### Milestone 8: API & Demo SPA Integration
- Mount new endpoints in FastAPI `api_router`.
- Add tab navigation in `frontend/index.html` for Purchases, Expenses, Supplier Payments, Credit Notes, and Extended Reports.

### Milestone 9: Test Suite & Verification
- Unit and integration tests for all 7 milestones.
- Full regression run ensuring all 43 baseline tests remain green.

---

## 4. Database Migration Plan

We will create migration `0008_extended_accounting_schema.py`:
1. Alter `app.contacts`: add `billing_address` (JSONB), `tax_identifier` (VARCHAR), `payment_terms_days` (SMALLINT).
2. Create `app.bills` & `app.bill_lines` with composite FKs and RLS.
3. Create `app.expenses` with composite FKs and RLS.
4. Create `app.supplier_payments` & `app.supplier_payment_allocations` with composite FKs and RLS.
5. Create `app.credit_notes` & `app.credit_note_lines` with composite FKs and RLS.
6. Create `app.opening_balance_batches` with composite FKs and RLS.
7. Add column `credit_noted_amount` (NUMERIC(19,4) default 0) to `app.invoices`.
8. Enable and force RLS on all newly created tables with permissive policies for `ledgerly_app`.
