"""Central API Router for Ledgerly."""

from fastapi import APIRouter
from app.api.endpoints import (
    accounts,
    auth,
    bills,
    contacts,
    credit_notes,
    demo,
    expenses,
    health,
    invoices,
    items,
    ledger,
    notifications,
    opening_balances,
    organizations,
    payments,
    reports,
    supplier_payments,
    tax_rates,
    vendors,
)

api_router = APIRouter()

# Health endpoints
api_router.include_router(health.router)

# Demonstration & Rapid Verification
api_router.include_router(demo.router)

# Core Master Data & Auth
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(accounts.router)
api_router.include_router(tax_rates.router)
api_router.include_router(contacts.router)
api_router.include_router(vendors.router)
api_router.include_router(items.router)

# Sales & Accounts Receivable
api_router.include_router(invoices.router)
api_router.include_router(payments.router)
api_router.include_router(credit_notes.router)

# Purchases & Accounts Payable
api_router.include_router(bills.router)
api_router.include_router(supplier_payments.router)
api_router.include_router(expenses.router)

# Opening Balances
api_router.include_router(opening_balances.router)

# Notifications & Automation
api_router.include_router(notifications.router)

# Accounting & Reports
api_router.include_router(ledger.router)
api_router.include_router(reports.router)
