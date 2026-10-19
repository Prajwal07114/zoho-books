"""Organization and Membership API Endpoints."""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session, get_user_session
from app.core.database import set_tenant_context
from app.models.identity import User
from app.models.organization import Membership, Organization, OrganizationSettings
from app.schemas.organization import OrganizationCreate, OrganizationResponse
from app.services.coa_seed import seed_chart_of_accounts

router = APIRouter(prefix="/organizations", tags=["Organizations"])


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_session),
) -> Organization:
    """Create a new multi-tenant organization.
    
    Atomically:
    1. Creates organization record with current_user as creator.
    2. Creates default organization settings.
    3. Establishes Owner membership for current_user.
    4. Automatically seeds the standard Chart of Accounts.
    """
    # 1. Create Organization in User Mode
    org = Organization(
        name=payload.name,
        legal_name=payload.legal_name,
        base_currency_code=payload.base_currency_code.upper(),
        time_zone=payload.time_zone,
        created_by=current_user.id,
    )
    db.add(org)
    await db.flush()

    # 2. Switch to Bootstrap Tenant Context for child records
    await set_tenant_context(db, organization_id=org.id, user_id=current_user.id)

    # 3. Create Settings
    settings = OrganizationSettings(
        organization_id=org.id,
        invoice_prefix="INV-",
        next_invoice_number=1,
    )
    db.add(settings)

    # 4. Create Owner Membership
    membership = Membership(
        organization_id=org.id,
        user_id=current_user.id,
        role="owner",
        status="active",
    )
    db.add(membership)
    await db.flush()

    # 5. Automatically Seed Chart of Accounts
    await seed_chart_of_accounts(db, organization_id=org.id)

    response = OrganizationResponse.model_validate(org)
    await db.commit()
    return response


@router.get("", response_model=List[OrganizationResponse])
async def list_user_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_user_session),
) -> List[Organization]:
    """List all organizations where the authenticated user is an active member."""
    stmt = (
        select(Organization)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(
            Membership.user_id == current_user.id,
            Membership.status == "active",
        )
        .order_by(Organization.created_at.desc())
    )
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/current", response_model=OrganizationResponse)
async def get_current_organization(
    db: AsyncSession = Depends(get_tenant_session),
) -> Organization:
    """Get details of the currently selected organization (verified via X-Organization-ID)."""
    stmt = select(Organization)
    res = await db.execute(stmt)
    org = res.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    return org
