"""Chart of Accounts Seeding Service."""

import uuid
from typing import List, TypedDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting import Account


class AccountTemplate(TypedDict):
    code: str
    name: str
    account_type: str
    normal_balance: str
    system_key: str


STANDARD_CHART_OF_ACCOUNTS: List[AccountTemplate] = [
    # Assets
    {
        "code": "1010",
        "name": "Cash on Hand",
        "account_type": "asset",
        "normal_balance": "debit",
        "system_key": "cash",
    },
    {
        "code": "1020",
        "name": "Operating Bank Account",
        "account_type": "asset",
        "normal_balance": "debit",
        "system_key": "bank",
    },
    {
        "code": "1200",
        "name": "Accounts Receivable",
        "account_type": "asset",
        "normal_balance": "debit",
        "system_key": "accounts_receivable",
    },
    # Liabilities
    {
        "code": "2010",
        "name": "Accounts Payable",
        "account_type": "liability",
        "normal_balance": "credit",
        "system_key": "accounts_payable",
    },
    {
        "code": "2100",
        "name": "Tax Payable",
        "account_type": "liability",
        "normal_balance": "credit",
        "system_key": "tax_payable",
    },
    # Equity
    {
        "code": "3010",
        "name": "Owner's Equity",
        "account_type": "equity",
        "normal_balance": "credit",
        "system_key": "owners_equity",
    },
    {
        "code": "3020",
        "name": "Retained Earnings",
        "account_type": "equity",
        "normal_balance": "credit",
        "system_key": "retained_earnings",
    },
    {
        "code": "3030",
        "name": "Opening Balance Equity",
        "account_type": "equity",
        "normal_balance": "credit",
        "system_key": "opening_balance_equity",
    },
    # Revenue / Income
    {
        "code": "4010",
        "name": "Sales Income",
        "account_type": "income",
        "normal_balance": "credit",
        "system_key": "sales_income",
    },
    # Expenses
    {
        "code": "5010",
        "name": "General Expenses",
        "account_type": "expense",
        "normal_balance": "debit",
        "system_key": "general_expense",
    },
    {
        "code": "5090",
        "name": "Rounding Adjustment",
        "account_type": "expense",
        "normal_balance": "debit",
        "system_key": "rounding",
    },
]


async def seed_chart_of_accounts(
    session: AsyncSession,
    organization_id: uuid.UUID,
) -> List[Account]:
    """Seed the standard 11 Chart of Accounts for an organization.
    
    Idempotent: skips accounts that already exist for this organization.
    """
    # Fetch existing account codes for this organization
    stmt = select(Account.code).where(Account.organization_id == organization_id)
    res = await session.execute(stmt)
    existing_codes = set(res.scalars().all())

    created_accounts: List[Account] = []
    for item in STANDARD_CHART_OF_ACCOUNTS:
        if item["code"] in existing_codes:
            continue

        acc = Account(
            organization_id=organization_id,
            code=item["code"],
            name=item["name"],
            account_type=item["account_type"],
            normal_balance=item["normal_balance"],
            is_system=True,
            system_key=item["system_key"],
            is_postable=True,
            status="active",
        )
        session.add(acc)
        created_accounts.append(acc)

    if created_accounts:
        await session.flush()

    return created_accounts
