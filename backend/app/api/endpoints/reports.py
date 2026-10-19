"""Financial Reports API Endpoints."""

from datetime import date
from typing import Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.schemas.reports import AccountsReceivableReport, TrialBalanceReport
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
