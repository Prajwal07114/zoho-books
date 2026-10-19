"""Sales Credit Note API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.schemas.credit_note import (
    CreditNoteCreate,
    CreditNoteResponse,
    CreditNoteVoidRequest,
)
from app.services.credit_note_service import CreditNoteService

router = APIRouter(prefix="/credit-notes", tags=["Credit Notes"])


@router.post("", response_model=CreditNoteResponse, status_code=status.HTTP_201_CREATED)
async def create_credit_note(
    payload: CreditNoteCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> CreditNoteResponse:
    """Create draft credit note linked to an issued invoice."""
    org_id = uuid.UUID(x_organization_id)
    cn = await CreditNoteService.create_draft_credit_note(db, org_id, payload, current_user)
    full_cn = await CreditNoteService.get_credit_note(db, org_id, cn.id)
    response = CreditNoteResponse.model_validate(full_cn)
    await db.commit()
    return response


@router.get("", response_model=List[CreditNoteResponse])
async def list_credit_notes(
    customer_id: Optional[uuid.UUID] = Query(None, description="Filter by customer ID"),
    invoice_id: Optional[uuid.UUID] = Query(None, description="Filter by invoice ID"),
    state: Optional[str] = Query(None, description="Filter by state (draft, issued, void)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[CreditNoteResponse]:
    """List credit notes."""
    org_id = uuid.UUID(x_organization_id)
    credit_notes = await CreditNoteService.list_credit_notes(
        db, org_id, customer_id=customer_id, invoice_id=invoice_id, state=state, limit=limit, offset=offset
    )
    return [CreditNoteResponse.model_validate(c) for c in credit_notes]


@router.get("/{credit_note_id}", response_model=CreditNoteResponse)
async def get_credit_note(
    credit_note_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> CreditNoteResponse:
    """Get credit note by ID."""
    org_id = uuid.UUID(x_organization_id)
    cn = await CreditNoteService.get_credit_note(db, org_id, credit_note_id)
    return CreditNoteResponse.model_validate(cn)


@router.post("/{credit_note_id}/issue", response_model=CreditNoteResponse)
async def issue_credit_note(
    credit_note_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> CreditNoteResponse:
    """Issue draft credit note to general ledger (JP-05)."""
    org_id = uuid.UUID(x_organization_id)
    cn = await CreditNoteService.issue_credit_note(db, org_id, credit_note_id, current_user)
    response = CreditNoteResponse.model_validate(cn)
    await db.commit()
    return response


@router.post("/{credit_note_id}/void", response_model=CreditNoteResponse)
async def void_credit_note(
    credit_note_id: uuid.UUID,
    payload: CreditNoteVoidRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> CreditNoteResponse:
    """Void issued credit note and emit reversal journal (JP-06)."""
    org_id = uuid.UUID(x_organization_id)
    cn = await CreditNoteService.void_credit_note(db, org_id, credit_note_id, payload.reason, current_user)
    response = CreditNoteResponse.model_validate(cn)
    await db.commit()
    return response
