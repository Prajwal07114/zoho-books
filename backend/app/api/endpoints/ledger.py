"""API Endpoints for Double-Entry Journals and Ledger inspection."""

from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_tenant_session
from app.models.accounting import Account, Journal, JournalLine
from app.models.identity import User
from app.schemas.ledger import JournalLineRead, JournalRead

router = APIRouter(prefix="/journals", tags=["Double-Entry Journals"])


@router.get("", response_model=List[JournalRead])
async def list_journals(
    source_type: Optional[str] = Query(None, description="Filter by source_type (e.g. invoice, payment)"),
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
):
    """List double-entry journals for the active organization."""
    org_id = uuid.UUID(x_organization_id)
    stmt = (
        select(Journal)
        .where(Journal.organization_id == org_id)
        .order_by(Journal.created_at.desc())
    )
    if source_type:
        stmt = stmt.where(Journal.source_type == source_type)

    result = await db.execute(stmt)
    journals = result.scalars().all()

    # Pre-fetch accounts for mapping codes/names
    acc_result = await db.execute(select(Account).where(Account.organization_id == org_id))
    accounts_by_id = {acc.id: acc for acc in acc_result.scalars().all()}

    output = []
    for j in journals:
        # Fetch lines for this journal
        line_stmt = (
            select(JournalLine)
            .where(
                JournalLine.organization_id == org_id,
                JournalLine.journal_id == j.id,
            )
            .order_by(JournalLine.line_no)
        )
        line_res = await db.execute(line_stmt)
        lines = line_res.scalars().all()

        line_reads = [
            JournalLineRead(
                line_no=l.line_no,
                account_id=l.account_id,
                account_code=accounts_by_id.get(l.account_id).code if l.account_id in accounts_by_id else None,
                account_name=accounts_by_id.get(l.account_id).name if l.account_id in accounts_by_id else None,
                debit=l.debit,
                credit=l.credit,
                accounting_date=l.accounting_date,
                contact_id=l.contact_id,
                description=l.description,
            )
            for l in lines
        ]

        output.append(
            JournalRead(
                id=j.id,
                organization_id=j.organization_id,
                journal_number=j.journal_number,
                accounting_date=j.accounting_date,
                memo=j.memo,
                source_type=j.source_type,
                source_id=j.source_id,
                currency_code=j.currency_code,
                total_amount=j.total_amount,
                reverses_journal_id=j.reverses_journal_id,
                created_at=j.created_at,
                lines=line_reads,
            )
        )

    return output


@router.get("/{journal_id}", response_model=JournalRead)
async def get_journal(
    journal_id: uuid.UUID,
    x_organization_id: str = Header(..., alias="X-Organization-ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_session),
):
    """Retrieve an authoritative journal entry with its balanced lines."""
    org_id = uuid.UUID(x_organization_id)
    stmt = select(Journal).where(
        Journal.organization_id == org_id,
        Journal.id == journal_id,
    )
    result = await db.execute(stmt)
    journal = result.scalar_one_or_none()
    if not journal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal {journal_id} not found in this organization.",
        )

    line_stmt = (
        select(JournalLine)
        .where(
            JournalLine.organization_id == org_id,
            JournalLine.journal_id == journal.id,
        )
        .order_by(JournalLine.line_no)
    )
    line_res = await db.execute(line_stmt)
    lines = line_res.scalars().all()

    acc_result = await db.execute(select(Account).where(Account.organization_id == org_id))
    accounts_by_id = {acc.id: acc for acc in acc_result.scalars().all()}

    line_reads = [
        JournalLineRead(
            line_no=l.line_no,
            account_id=l.account_id,
            account_code=accounts_by_id.get(l.account_id).code if l.account_id in accounts_by_id else None,
            account_name=accounts_by_id.get(l.account_id).name if l.account_id in accounts_by_id else None,
            debit=l.debit,
            credit=l.credit,
            accounting_date=l.accounting_date,
            contact_id=l.contact_id,
            description=l.description,
        )
        for l in lines
    ]

    return JournalRead(
        id=journal.id,
        organization_id=journal.organization_id,
        journal_number=journal.journal_number,
        accounting_date=journal.accounting_date,
        memo=journal.memo,
        source_type=journal.source_type,
        source_id=journal.source_id,
        currency_code=journal.currency_code,
        total_amount=journal.total_amount,
        reverses_journal_id=journal.reverses_journal_id,
        created_at=journal.created_at,
        lines=line_reads,
    )
