"""Sales Invoice API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.models.invoice import Invoice
from app.schemas.invoice import InvoiceCreate, InvoiceResponse, InvoiceVoidRequest
from app.services.invoice_service import InvoiceService

router = APIRouter(prefix="/invoices", tags=["Sales Invoices"])


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    payload: InvoiceCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> InvoiceResponse:
    """Create a new sales invoice in draft state."""
    org_id = uuid.UUID(x_organization_id)
    invoice = await InvoiceService.create_draft_invoice(
        db=db,
        organization_id=org_id,
        payload=payload,
        current_user=current_user,
    )
    # Load lines for serialization before commit
    stmt = (
        select(Invoice)
        .where(Invoice.organization_id == org_id, Invoice.id == invoice.id)
        .options(selectinload(Invoice.lines))
    )
    res = await db.execute(stmt)
    full_inv = res.scalar_one()

    response = InvoiceResponse.model_validate(full_inv)
    await db.commit()
    return response


@router.get("", response_model=List[InvoiceResponse])
async def list_invoices(
    state: Optional[str] = Query(None, description="Filter by invoice state (draft, issued, void)"),
    customer_id: Optional[uuid.UUID] = Query(None, description="Filter by customer ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[Invoice]:
    """List invoices for the active organization."""
    stmt = select(Invoice).options(selectinload(Invoice.lines))
    if state:
        stmt = stmt.where(Invoice.state == state)
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)

    stmt = stmt.order_by(Invoice.invoice_date.desc(), Invoice.id.desc())
    res = await db.execute(stmt)
    return list(res.scalars().all())


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_session),
) -> Invoice:
    """Get a single invoice by ID with its lines."""
    stmt = (
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.lines))
    )
    res = await db.execute(stmt)
    invoice = res.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


@router.post("/{invoice_id}/issue", response_model=InvoiceResponse)
async def issue_invoice(
    invoice_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> InvoiceResponse:
    """Issue a sales invoice atomically: assign number, freeze snapshot, post JP-01 journal."""
    org_id = uuid.UUID(x_organization_id)
    invoice = await InvoiceService.issue_invoice(
        db=db,
        organization_id=org_id,
        invoice_id=invoice_id,
        current_user=current_user,
    )
    response = InvoiceResponse.model_validate(invoice)
    await db.commit()
    return response


@router.post("/{invoice_id}/void", response_model=InvoiceResponse)
async def void_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceVoidRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> InvoiceResponse:
    """Void an issued invoice atomically: verify no payments, post JP-02 reversal journal."""
    org_id = uuid.UUID(x_organization_id)
    invoice = await InvoiceService.void_invoice(
        db=db,
        organization_id=org_id,
        invoice_id=invoice_id,
        void_reason=payload.reason,
        current_user=current_user,
    )
    response = InvoiceResponse.model_validate(invoice)
    await db.commit()
    return response
