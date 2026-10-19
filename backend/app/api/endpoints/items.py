"""Item (Product and Service) API Endpoints."""

import uuid
from typing import List
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_tenant_session
from app.models.accounting import Account
from app.models.master import Item
from app.schemas.item import ItemCreate, ItemResponse

router = APIRouter(prefix="/items", tags=["Catalog Items"])


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    payload: ItemCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> Item:
    """Create a new product or service item."""
    org_id = uuid.UUID(x_organization_id)

    # Determine income account if not provided
    income_acc_id = payload.income_account_id
    if not income_acc_id:
        stmt = select(Account.id).where(
            Account.organization_id == org_id,
            Account.system_key == "sales_income",
        )
        res = await db.execute(stmt)
        income_acc_id = res.scalar_one_or_none()

    item = Item(
        organization_id=org_id,
        name=payload.name,
        item_type=payload.item_type,
        sku=payload.sku,
        sales_price=payload.sales_price,
        income_account_id=income_acc_id,
        default_tax_rate_id=payload.default_tax_rate_id,
        is_active=True,
    )
    db.add(item)
    await db.flush()
    response = ItemResponse.model_validate(item)
    await db.commit()

    return response


@router.get("", response_model=List[ItemResponse])
async def list_items(
    db: AsyncSession = Depends(get_tenant_session),
) -> List[Item]:
    """List active products and services for the active organization."""
    stmt = select(Item).where(Item.is_active == True).order_by(Item.name)
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{item_id}", response_model=ItemResponse)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_session),
) -> Item:
    """Get a specific item by ID."""
    stmt = select(Item).where(Item.id == item_id, Item.is_active == True)
    res = await db.execute(stmt)
    item = res.scalar_one_or_none()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found",
        )
    return item
