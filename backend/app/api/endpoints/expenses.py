"""Direct Business Expense API Endpoints."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.identity import User
from app.schemas.expense import ExpenseCreate, ExpenseResponse, ExpenseVoidRequest
from app.services.expense_service import ExpenseService

router = APIRouter(prefix="/expenses", tags=["Direct Expenses"])


@router.post("", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
async def record_expense(
    payload: ExpenseCreate,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> ExpenseResponse:
    """Record direct business expense and emit JP-11 journal."""
    org_id = uuid.UUID(x_organization_id)
    expense = await ExpenseService.record_expense(db, org_id, payload, current_user)
    response = ExpenseResponse.model_validate(expense)
    await db.commit()
    return response


@router.get("", response_model=List[ExpenseResponse])
async def list_expenses(
    vendor_id: Optional[uuid.UUID] = Query(None, description="Filter by vendor ID"),
    payment_status: Optional[str] = Query(None, description="Filter by status (posted, void)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> List[ExpenseResponse]:
    """List business expenses."""
    org_id = uuid.UUID(x_organization_id)
    expenses = await ExpenseService.list_expenses(
        db, org_id, vendor_id=vendor_id, payment_status=payment_status, limit=limit, offset=offset
    )
    return [ExpenseResponse.model_validate(e) for e in expenses]


@router.get("/{expense_id}", response_model=ExpenseResponse)
async def get_expense(
    expense_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    db: AsyncSession = Depends(get_tenant_session),
) -> ExpenseResponse:
    """Get expense by ID."""
    org_id = uuid.UUID(x_organization_id)
    expense = await ExpenseService.get_expense(db, org_id, expense_id)
    return ExpenseResponse.model_validate(expense)


@router.post("/{expense_id}/void", response_model=ExpenseResponse)
async def void_expense(
    expense_id: uuid.UUID,
    payload: ExpenseVoidRequest,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
) -> ExpenseResponse:
    """Void expense and emit reversal journal (JP-12)."""
    org_id = uuid.UUID(x_organization_id)
    expense = await ExpenseService.void_expense(db, org_id, expense_id, payload.reason, current_user)
    response = ExpenseResponse.model_validate(expense)
    await db.commit()
    return response
