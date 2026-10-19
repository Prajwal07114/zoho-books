"""Contact (Customer and Vendor) API Endpoints."""

import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.models.master import Contact
from app.schemas.contact import ContactCreate, ContactResponse

router = APIRouter(prefix="/contacts", tags=["Contacts"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> Contact:
    """Create a new customer or vendor for the active organization."""
    org_id = uuid.UUID(x_organization_id)

    contact = Contact(
        organization_id=org_id,
        display_name=payload.display_name,
        email=payload.email,
        phone=payload.phone,
        is_customer=payload.is_customer,
        is_vendor=payload.is_vendor,
        status="active",
    )
    db.add(contact)
    await db.flush()
    response = ContactResponse.model_validate(contact)
    await db.commit()

    return response


@router.get("", response_model=List[ContactResponse])
async def list_contacts(
    is_customer: Optional[bool] = Query(None, description="Filter by customer flag"),
    is_vendor: Optional[bool] = Query(None, description="Filter by vendor flag"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[Contact]:
    """List active contacts for the active organization."""
    stmt = select(Contact).where(Contact.status == "active")
    if is_customer is not None:
        stmt = stmt.where(Contact.is_customer == is_customer)
    if is_vendor is not None:
        stmt = stmt.where(Contact.is_vendor == is_vendor)

    stmt = stmt.order_by(Contact.display_name)
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_session),
) -> Contact:
    """Get a specific contact by ID."""
    stmt = select(Contact).where(Contact.id == contact_id, Contact.status == "active")
    res = await db.execute(stmt)
    contact = res.scalar_one_or_none()
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found",
        )
    return contact
