"""Central API Router for Ledgerly."""

from fastapi import APIRouter
from app.api.endpoints import (
    accounts,
    auth,
    contacts,
    health,
    items,
    organizations,
    tax_rates,
    invoices,
    payments,
    reports,
    ledger,
    demo,
    notifications,
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
api_router.include_router(items.router)

# Sales & Billing
api_router.include_router(invoices.router)
api_router.include_router(payments.router)

# Notifications & Automation
api_router.include_router(notifications.router)

# Accounting & Reports
api_router.include_router(ledger.router)
api_router.include_router(reports.router)
