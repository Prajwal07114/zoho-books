"""Tax Rate Configuration API Endpoints."""

import uuid
from typing import List
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.models.accounting import Account, TaxRate
from app.schemas.tax_rate import TaxRateCreate, TaxRateResponse

router = APIRouter(prefix="/tax-rates", tags=["Tax Rates"])


@router.post("", response_model=TaxRateResponse, status_code=status.HTTP_201_CREATED)
async def create_tax_rate(
    payload: TaxRateCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> TaxRate:
    """Create a new generic percentage tax rate for the active organization."""
    org_id = uuid.UUID(x_organization_id)

    tax_acc_id = payload.tax_account_id
    if not tax_acc_id:
        stmt = select(Account.id).where(
            Account.organization_id == org_id,
            Account.system_key == "tax_payable",
        )
        res = await db.execute(stmt)
        tax_acc_id = res.scalar_one_or_none()

    tax_rate = TaxRate(
        organization_id=org_id,
        name=payload.name,
        rate_percent=payload.rate_percent,
        tax_account_id=tax_acc_id,
        is_active=True,
    )
    db.add(tax_rate)
    await db.flush()
    response = TaxRateResponse.model_validate(tax_rate)
    await db.commit()

    return response


@router.get("", response_model=List[TaxRateResponse])
async def list_tax_rates(
    db: AsyncSession = Depends(get_tenant_session),
) -> List[TaxRate]:
    """List all active tax rates for the organization."""
    stmt = select(TaxRate).where(TaxRate.is_active == True).order_by(TaxRate.name)
    res = await db.execute(stmt)
    return list(res.scalars().all())
