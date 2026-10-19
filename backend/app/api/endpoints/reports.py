"""Financial Reports API Endpoints."""

from datetime import date
from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.schemas.reports import (
    AccountsPayableReport,
    AccountsReceivableReport,
    BalanceSheetReport,
    GeneralLedgerReport,
    ProfitAndLossReport,
    TrialBalanceReport,
)
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/trial-balance", response_model=TrialBalanceReport)
async def get_trial_balance(
    as_of_date: Optional[date] = Query(None, description="Report as-of date (default: today)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> TrialBalanceReport:
    """Generate real-time Trial Balance report proving double-entry balance equality."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_trial_balance(
        db=db,
        organization_id=org_id,
        as_of_date=as_of_date,
    )


@router.get("/ar-summary", response_model=AccountsReceivableReport)
async def get_ar_summary(
    as_of_date: Optional[date] = Query(None, description="Report as-of date (default: today)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> AccountsReceivableReport:
    """Generate Accounts Receivable sub-ledger report and reconcile with General Ledger control balance."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_ar_report(
        db=db,
        organization_id=org_id,
        as_of_date=as_of_date,
    )


@router.get("/ap-summary", response_model=AccountsPayableReport)
async def get_ap_summary(
    as_of_date: Optional[date] = Query(None, description="Report as-of date (default: today)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> AccountsPayableReport:
    """Generate Accounts Payable sub-ledger report and reconcile with General Ledger control balance."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_ap_report(
        db=db,
        organization_id=org_id,
        as_of_date=as_of_date,
    )


@router.get("/general-ledger", response_model=GeneralLedgerReport)
async def get_general_ledger(
    from_date: Optional[date] = Query(None, description="Start date (default: Jan 1 of current year)"),
    to_date: Optional[date] = Query(None, description="End date (default: today)"),
    account_id: Optional[uuid.UUID] = Query(None, description="Optional account ID filter"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> GeneralLedgerReport:
    """Generate General Ledger report showing opening balances, running balances, and transactions."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_general_ledger(
        db=db,
        organization_id=org_id,
        from_date=from_date,
        to_date=to_date,
        account_id=account_id,
    )


@router.get("/profit-and-loss", response_model=ProfitAndLossReport)
async def get_profit_and_loss(
    from_date: Optional[date] = Query(None, description="Start date (default: Jan 1 of current year)"),
    to_date: Optional[date] = Query(None, description="End date (default: today)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> ProfitAndLossReport:
    """Generate Profit and Loss report for revenue and expense accounts."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_profit_and_loss(
        db=db,
        organization_id=org_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/balance-sheet", response_model=BalanceSheetReport)
async def get_balance_sheet(
    as_of_date: Optional[date] = Query(None, description="Report as-of date (default: today)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> BalanceSheetReport:
    """Generate Balance Sheet report verifying Assets = Liabilities + Equity + Current Earnings."""
    org_id = uuid.UUID(x_organization_id)
    return await ReportService.generate_balance_sheet(
        db=db,
        organization_id=org_id,
        as_of_date=as_of_date,
    )
