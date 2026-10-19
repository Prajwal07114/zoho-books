"""Vendor Master Data Service."""

from typing import List, Optional
import uuid
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.master import Contact
from app.schemas.vendor import VendorCreate, VendorUpdate


class VendorService:
    @staticmethod
    async def create_vendor(
        db: AsyncSession,
        organization_id: uuid.UUID,
        payload: VendorCreate,
    ) -> Contact:
        """Create a new vendor in the organization."""
        contact = Contact(
            organization_id=organization_id,
            display_name=payload.display_name.strip(),
            email=payload.email,
            phone=payload.phone,
            is_customer=False,
            is_vendor=True,
            tax_identifier=payload.tax_identifier,
            payment_terms_days=payload.payment_terms_days,
            billing_address=payload.billing_address,
            status="active",
        )
        db.add(contact)
        await db.flush()
        return contact

    @staticmethod
    async def get_vendor(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> Contact:
        """Fetch an active or inactive vendor by ID."""
        stmt = select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.id == vendor_id,
            Contact.is_vendor == True,
        )
        res = await db.execute(stmt)
        vendor = res.scalar_one_or_none()
        if not vendor:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Vendor {vendor_id} not found",
            )
        return vendor

    @staticmethod
    async def list_vendors(
        db: AsyncSession,
        organization_id: uuid.UUID,
        status_filter: Optional[str] = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> List[Contact]:
        """List vendors with optional status filter."""
        stmt = (
            select(Contact)
            .where(
                Contact.organization_id == organization_id,
                Contact.is_vendor == True,
            )
            .order_by(Contact.display_name)
            .limit(limit)
            .offset(offset)
        )
        if status_filter:
            stmt = stmt.where(Contact.status == status_filter)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @staticmethod
    async def update_vendor(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: uuid.UUID,
        payload: VendorUpdate,
    ) -> Contact:
        """Update an existing vendor."""
        vendor = await VendorService.get_vendor(db, organization_id, vendor_id)

        if payload.display_name is not None:
            vendor.display_name = payload.display_name.strip()
        if payload.email is not None:
            vendor.email = payload.email
        if payload.phone is not None:
            vendor.phone = payload.phone
        if payload.tax_identifier is not None:
            vendor.tax_identifier = payload.tax_identifier
        if payload.payment_terms_days is not None:
            vendor.payment_terms_days = payload.payment_terms_days
        if payload.billing_address is not None:
            vendor.billing_address = payload.billing_address
        if payload.status is not None:
            vendor.status = payload.status

        await db.flush()
        return vendor

    @staticmethod
    async def deactivate_vendor(
        db: AsyncSession,
        organization_id: uuid.UUID,
        vendor_id: uuid.UUID,
    ) -> Contact:
        """Soft-deactivate a vendor to preserve historical transactions."""
        vendor = await VendorService.get_vendor(db, organization_id, vendor_id)
        vendor.status = "inactive"
        await db.flush()
        return vendor
