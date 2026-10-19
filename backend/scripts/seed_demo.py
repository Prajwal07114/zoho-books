"""Deterministic Demonstration Seed Script for Ledgerly Core Accounting Spine.

Executes the approved 14-step demo journey directly:
1. Registers Demo User (cfo@ledgerly.com)
2. Provisions Organization (Acme Global Corp)
3. Seeds Chart of Accounts
4. Creates Contacts (Enterprise Customers)
5. Creates Generic Tax Rates
6. Creates Items (Services & Software)
7. Creates and Issues Invoices (JP-01 Journal emission)
8. Records Partial & Full Payments (JP-03 Journal emission)
9. Prints Reconciled Trial Balance & Accounts Receivable Reports
"""

import asyncio
from datetime import date, timedelta
from decimal import Decimal
import os
import sys
import uuid

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal, set_tenant_context
from app.core.security import hash_password
from app.models.accounting import Account, TaxRate
from app.models.identity import User
from app.models.master import Contact, Item
from app.models.organization import Membership, Organization, OrganizationSettings
from app.schemas.invoice import InvoiceCreate, InvoiceLineCreate
from app.schemas.payment import PaymentCreate, PaymentAllocationCreate
from app.services.coa_seed import seed_chart_of_accounts
from app.services.invoice_service import InvoiceService
from app.services.payment_service import PaymentService
from app.services.report_service import ReportService


async def main():
    print("=" * 70)
    print("  LEDGERLY - CORE ACCOUNTING SPINE DEMO SEED")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        # 1. Create Demo User
        email = "cfo@ledgerly.com"
        user_stmt = select(User).where(User.email == email)
        res = await session.execute(user_stmt)
        user = res.scalar_one_or_none()

        if not user:
            user = User(
                email=email,
                full_name="Sarah Jenkins (CFO)",
                password_hash=hash_password("DemoPassword123!"),
                is_active=True,
            )
            session.add(user)
            await session.flush()
            print(f"[SUCCESS] Created Demo User: {user.email} (Password: DemoPassword123!)")
        else:
            print(f"[*] Found Existing Demo User: {user.email}")

        # 2. Create Demo Organization
        org = Organization(
            name="Acme Global Corporation",
            legal_name="Acme Global Corporation Inc.",
            base_currency_code="USD",
            time_zone="America/New_York",
            created_by=user.id,
        )
        session.add(org)
        await session.flush()
        print(f"[SUCCESS] Created Organization: {org.name} [ID: {org.id}]")

        # Set tenant context
        await set_tenant_context(session, organization_id=org.id, user_id=user.id)

        # Settings
        settings = OrganizationSettings(
            organization_id=org.id,
            invoice_prefix="INV-",
            next_invoice_number=1,
        )
        session.add(settings)

        # Membership
        membership = Membership(
            organization_id=org.id,
            user_id=user.id,
            role="owner",
            status="active",
        )
        session.add(membership)
        await session.flush()

        # 3. Seed Chart of Accounts
        accounts = await seed_chart_of_accounts(session, organization_id=org.id)
        print(f"[SUCCESS] Seeded Chart of Accounts: {len(accounts)} standard accounts created.")
        acc_map = {acc.system_key: acc.id for acc in accounts if acc.system_key}

        # 4. Create Customers
        cust1 = Contact(
            organization_id=org.id,
            display_name="TechWave Enterprises",
            email="ap@techwave.io",
            phone="+1-555-0199",
            is_customer=True,
            status="active",
        )
        cust2 = Contact(
            organization_id=org.id,
            display_name="OmniRetail Partners",
            email="finance@omniretail.com",
            phone="+1-555-0244",
            is_customer=True,
            status="active",
        )
        session.add_all([cust1, cust2])
        await session.flush()
        print(f"[SUCCESS] Created Customers: {cust1.display_name}, {cust2.display_name}")

        # 5. Create Tax Rates
        tax_standard = TaxRate(
            organization_id=org.id,
            name="State Sales Tax (8.25%)",
            rate_percent=Decimal("8.250000"),
            tax_account_id=acc_map["tax_payable"],
            is_active=True,
        )
        session.add(tax_standard)
        await session.flush()
        print(f"[SUCCESS] Configured Tax Rate: {tax_standard.name}")

        # 6. Create Catalog Items
        item1 = Item(
            organization_id=org.id,
            name="Financial Advisory & Integration",
            item_type="service",
            sales_price=Decimal("250.0000"),
            default_tax_rate_id=tax_standard.id,
            income_account_id=acc_map["sales_income"],
            is_active=True,
        )
        item2 = Item(
            organization_id=org.id,
            name="Ledgerly Enterprise SaaS License (Annual)",
            item_type="product",
            sales_price=Decimal("4800.0000"),
            default_tax_rate_id=tax_standard.id,
            income_account_id=acc_map["sales_income"],
            is_active=True,
        )
        session.add_all([item1, item2])
        await session.flush()
        print(f"[SUCCESS] Created Catalog Items: {item1.name}, {item2.name}")

        # 7. Create & Issue Invoice 1 (TechWave: $5,196.00 with Tax)
        today = date.today()
        inv1_payload = InvoiceCreate(
            customer_id=cust1.id,
            invoice_date=today - timedelta(days=15),
            due_date=today + timedelta(days=15),
            notes="Net 30. Thank you for your business.",
            lines=[
                InvoiceLineCreate(
                    item_id=item2.id,
                    description="Annual Cloud Accounting Platform Subscription",
                    quantity=Decimal("1.0000"),
                    unit_price=Decimal("4800.0000"),
                    tax_rate_id=tax_standard.id,
                    income_account_id=acc_map["sales_income"],
                )
            ],
        )
        inv1 = await InvoiceService.create_draft_invoice(session, org.id, inv1_payload, user)
        issued_inv1 = await InvoiceService.issue_invoice(session, org.id, inv1.id, user)
        print(f"[SUCCESS] Issued Invoice 1: {issued_inv1.invoice_number} | Total: ${issued_inv1.total_amount:,.2f} | Posted JP-01")

        # 8. Create & Issue Invoice 2 (OmniRetail: $1,082.50 with Tax)
        inv2_payload = InvoiceCreate(
            customer_id=cust2.id,
            invoice_date=today - timedelta(days=5),
            due_date=today + timedelta(days=25),
            notes="Services rendered in Q3.",
            lines=[
                InvoiceLineCreate(
                    item_id=item1.id,
                    description="Professional Accounting Migration (4 hrs)",
                    quantity=Decimal("4.0000"),
                    unit_price=Decimal("250.0000"),
                    tax_rate_id=tax_standard.id,
                    income_account_id=acc_map["sales_income"],
                )
            ],
        )
        inv2 = await InvoiceService.create_draft_invoice(session, org.id, inv2_payload, user)
        issued_inv2 = await InvoiceService.issue_invoice(session, org.id, inv2.id, user)
        print(f"[SUCCESS] Issued Invoice 2: {issued_inv2.invoice_number} | Total: ${issued_inv2.total_amount:,.2f} | Posted JP-01")

        # 9. Record Partial Payment of $2,500.00 on Invoice 1
        pay_payload = PaymentCreate(
            customer_id=cust1.id,
            payment_date=today,
            amount=Decimal("2500.0000"),
            deposit_account_id=acc_map["bank"],
            method="wire_transfer",
            reference="WIRE-REF-884920",
            notes="First tranche received via Federal Wire.",
            allocations=[
                PaymentAllocationCreate(
                    invoice_id=issued_inv1.id,
                    amount=Decimal("2500.0000"),
                )
            ],
        )
        payment = await PaymentService.record_payment(session, org.id, pay_payload, user)
        print(f"[SUCCESS] Recorded Payment: ${payment.amount:,.2f} on {issued_inv1.invoice_number} | Posted JP-03")

        # Commit all transactions
        await session.commit()

        # Re-set tenant context for reporting queries in fresh transaction
        await set_tenant_context(session, organization_id=org.id, user_id=user.id)

        # 10. Generate Reports
        print("\n" + "=" * 70)
        print("  AUTHORITATIVE FINANCIAL REPORTS (REAL-TIME)")
        print("=" * 70)

        # Trial Balance
        tb = await ReportService.generate_trial_balance(session, org.id)
        print("\n--- TRIAL BALANCE (Double-Entry Net Zero Verification) ---")
        print(f"{'Code':<8} {'Account Name':<35} {'Type':<12} {'Debit':>12} {'Credit':>12}")
        print("-" * 83)
        for r in tb.accounts:
            if r.debit > 0 or r.credit > 0:
                print(f"{r.code:<8} {r.name:<35} {r.account_type:<12} ${r.debit:>11,.2f} ${r.credit:>11,.2f}")
        print("-" * 83)
        print(f"{'TOTALS':<56} ${tb.total_debits:>11,.2f} ${tb.total_credits:>11,.2f}")
        print(f"Status: {'[BALANCED - NET ZERO EQUALITY HOLDS]' if tb.is_balanced else '[UNBALANCED - DEFECT]'}\n")

        # Accounts Receivable Summary
        ar = await ReportService.generate_ar_report(session, org.id)
        print("--- ACCOUNTS RECEIVABLE REPORT (Sub-Ledger to GL Reconciliation) ---")
        print(f"{'Customer':<30} {'Invoiced':>14} {'Settled':>14} {'Outstanding':>14}")
        print("-" * 76)
        for c in ar.customers:
            print(f"{c.customer_name:<30} ${c.total_invoiced:>13,.2f} ${c.total_settled:>13,.2f} ${c.outstanding_balance:>13,.2f}")
        print("-" * 76)
        print(f"{'TOTAL SUB-LEDGER RECEIVABLE':<60} ${ar.total_outstanding:>13,.2f}")
        print(f"{'GENERAL LEDGER AR CONTROL (1200)':<60} ${ar.gl_control_account_balance:>13,.2f}")
        print(f"Reconciliation: {'[100% RECONCILED - ZERO DISCREPANCY]' if ar.is_reconciled else '[DISCREPANCY DETECTED]'}")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
